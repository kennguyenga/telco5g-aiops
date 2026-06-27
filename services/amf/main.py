"""
AMF — Access and Mobility Management Function

The "front door" for UEs. Handles:
  • Initial registration (REGISTER)
  • Authentication flow (delegates to AUSF)
  • Subscription profile fetch (from UDM)
  • PDU session establishment requests (forwards to SMF)
  • Deregistration

Subscriber lifecycle states:
  DEREGISTERED -> REGISTERING -> AUTHENTICATING -> REGISTERED -> DEREGISTERING -> DEREGISTERED
"""
import sys, os
import secrets
import time
import hashlib
from fastapi import HTTPException, Request
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nf_common import (
    create_nf_app, NFClient, get_nf_urls,
    UEContext, trace_context_from_request,
)


app, tel, failure = create_nf_app("amf", 8004)
client = NFClient(tel, get_nf_urls())


# ── UE context table ──────────────────────────────────────────────────
UES: dict[str, UEContext] = {}


class RegisterRequest(BaseModel):
    supi: str
    plmn: str = "00101"
    # Real UEs derive RES from K via MILENAGE — UE simulator does the same toy hash.
    ue_auth_key: str  # the UE's stored secret K (so it can compute RES)


class SessionRequest(BaseModel):
    supi: str
    apn: str = "internet"


class DeregisterRequest(BaseModel):
    supi: str


@app.post("/ue/register")
async def register(req: RegisterRequest, request: Request):
    """Full registration flow: REGISTER -> AUTH -> SUBSCRIPTION -> REGISTERED."""
    # Allow caller to pass an externally-generated trace_id for correlation.
    # Falls back to header, then auto-generated inside tel.span().
    trace_id, _ = trace_context_from_request(request)
    if not trace_id:
        trace_id = request.headers.get("X-Trace-Id")
    async with tel.span("ue_register", trace_id=trace_id, supi=req.supi) as ctx:
        # State init
        ue = UES.get(req.supi) or UEContext(supi=req.supi)
        ue.state = "REGISTERING"
        ue.amf_ue_id = secrets.token_hex(8)
        ue.last_activity = time.time()
        UES[req.supi] = ue
        tel.info("UE registering", supi=req.supi)
        tel.inc("registrations_started_total")
        tel.gauge("active_ues", len([u for u in UES.values() if u.state == "REGISTERED"]))

        # ── Step 1: Initiate auth via AUSF ────────────────────────────
        ue.state = "AUTHENTICATING"
        ue.auth_attempts += 1
        try:
            challenge = await client.call("ausf", "POST", "/auth/init",
                                           json={"supi": req.supi, "plmn": req.plmn},
                                           trace_id=ctx["trace_id"],
                                           parent_span_id=ctx["span_id"])
        except HTTPException as e:
            ue.state = "DEREGISTERED"
            tel.error(f"auth init failed: {e.detail}", supi=req.supi)
            tel.inc("registrations_failed_total", reason="ausf_unreachable")
            raise

        # ── Step 2: UE computes RES (we simulate it here) ─────────────
        # In real life the UE has the K and computes RES itself.
        ue_res = _toy_milenage(challenge["rand"], req.ue_auth_key)

        # ── Step 3: Confirm auth with AUSF ────────────────────────────
        try:
            await client.call("ausf", "POST", "/auth/confirm",
                               json={"auth_ctx_id": challenge["auth_ctx_id"],
                                     "res": ue_res},
                               trace_id=ctx["trace_id"],
                               parent_span_id=ctx["span_id"])
        except HTTPException as e:
            ue.state = "DEREGISTERED"
            tel.warn(f"auth failed: {e.detail}", supi=req.supi)
            tel.inc("registrations_failed_total", reason="auth_rejected")
            raise

        # ── Step 4: Fetch subscription profile from UDM ───────────────
        try:
            profile = await client.call("udm", "GET",
                                         f"/subscribers/{req.supi}/profile",
                                         trace_id=ctx["trace_id"],
                                         parent_span_id=ctx["span_id"])
        except HTTPException as e:
            ue.state = "DEREGISTERED"
            tel.error(f"profile fetch failed: {e.detail}", supi=req.supi)
            tel.inc("registrations_failed_total", reason="udm_profile_error")
            raise

        # ── Step 5: REGISTERED ────────────────────────────────────────
        ue.state = "REGISTERED"
        ue.last_activity = time.time()
        tel.info("UE registered successfully", supi=req.supi, profile=profile)
        tel.inc("registrations_success_total")
        tel.gauge("active_ues", len([u for u in UES.values() if u.state == "REGISTERED"]))

        return {
            "status": "registered",
            "supi": req.supi,
            "amf_ue_id": ue.amf_ue_id,
            "profile": profile,
            "trace_id": ctx["trace_id"],
        }


@app.post("/ue/session")
async def establish_session(req: SessionRequest, request: Request):
    """Establish a PDU session via SMF."""
    trace_id, _ = trace_context_from_request(request)
    if not trace_id:
        trace_id = request.headers.get("X-Trace-Id")
    async with tel.span("ue_session", trace_id=trace_id, supi=req.supi) as ctx:
        ue = UES.get(req.supi)
        if not ue or ue.state != "REGISTERED":
            tel.warn(f"session request for non-registered UE: {req.supi}")
            tel.inc("sessions_failed_total", reason="not_registered")
            raise HTTPException(403, "UE not registered")

        try:
            session = await client.call("smf", "POST", "/sessions",
                                         json={"supi": req.supi, "apn": req.apn},
                                         trace_id=ctx["trace_id"],
                                         parent_span_id=ctx["span_id"])
        except HTTPException as e:
            tel.inc("sessions_failed_total", reason="smf_error")
            raise

        ue.pdu_sessions.append(session["pdu_id"])
        ue.last_activity = time.time()
        tel.info("PDU session established", supi=req.supi, pdu_id=session["pdu_id"])
        tel.inc("sessions_success_total")
        return {**session, "trace_id": ctx["trace_id"]}


@app.post("/ue/deregister")
async def deregister(req: DeregisterRequest, request: Request):
    """Deregister UE."""
    trace_id, _ = trace_context_from_request(request)
    if not trace_id:
        trace_id = request.headers.get("X-Trace-Id")
    async with tel.span("ue_deregister", trace_id=trace_id, supi=req.supi) as ctx:
        ue = UES.get(req.supi)
        if not ue:
            raise HTTPException(404, "UE unknown")
        ue.state = "DEREGISTERING"
        # Release sessions
        for pdu_id in ue.pdu_sessions:
            try:
                await client.call("smf", "DELETE", f"/sessions/{pdu_id}",
                                   trace_id=ctx["trace_id"],
                                   parent_span_id=ctx["span_id"])
            except Exception as e:
                tel.warn(f"session release failed: {e}", pdu_id=pdu_id)
        ue.pdu_sessions = []
        ue.state = "DEREGISTERED"
        tel.info("UE deregistered", supi=req.supi)
        tel.inc("deregistrations_total")
        tel.gauge("active_ues", len([u for u in UES.values() if u.state == "REGISTERED"]))
        return {"status": "deregistered", "trace_id": ctx["trace_id"]}


@app.get("/ue")
async def list_ues(state: str = None, limit: int = 100):
    """List UE contexts (for dashboard)."""
    out = list(UES.values())
    if state:
        out = [u for u in out if u.state == state]
    return {
        "total": len(UES),
        "by_state": {s: sum(1 for u in UES.values() if u.state == s)
                     for s in ["DEREGISTERED", "REGISTERING", "AUTHENTICATING",
                              "REGISTERED", "DEREGISTERING"]},
        "ues": out[:limit],
    }


@app.get("/ue/{supi}")
async def get_ue(supi: str):
    ue = UES.get(supi)
    if not ue:
        raise HTTPException(404, "UE not found")
    return ue


def _toy_milenage(rand: str, key: str) -> str:
    """Same toy AKA function as UDM. UE and HSS share K and run this."""
    return hashlib.sha256((rand + key).encode()).hexdigest()[:16]
