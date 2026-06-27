"""
SMF — Session Management Function

Coordinates PDU session establishment:
  1. Receive session request from AMF
  2. Get policy from PCF (QoS, charging rules)
  3. Install bearer in UPF
  4. Return session info to AMF

If PCF or UPF fails, the session goes into PENDING state — a great
failure signal for the LLM agent to detect.
"""
import sys, os
import secrets
import time
from fastapi import HTTPException, Request
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nf_common import (
    create_nf_app, NFClient, get_nf_urls,
    PDUSession, trace_context_from_request,
)


app, tel, failure = create_nf_app("smf", 8005)
client = NFClient(tel, get_nf_urls())


SESSIONS: dict[str, PDUSession] = {}


class SessionCreateRequest(BaseModel):
    supi: str
    apn: str = "internet"


@app.post("/sessions")
async def create_session(req: SessionCreateRequest, request: Request):
    """Create a PDU session. Calls PCF for policy then UPF to install bearer."""
    trace_id, parent = trace_context_from_request(request)
    async with tel.span("create_session", trace_id=trace_id,
                        parent_span_id=parent, supi=req.supi) as ctx:
        pdu_id = f"pdu-{secrets.token_hex(6)}"
        session = PDUSession(
            pdu_id=pdu_id, supi=req.supi, apn=req.apn,
            state="PENDING", qos_flow="5qi-9", upf_assigned="upf-01",
        )
        SESSIONS[pdu_id] = session
        tel.info("PDU session pending", supi=req.supi, pdu_id=pdu_id, apn=req.apn)
        tel.inc("sessions_pending_total")

        # ── Step 1: Get policy from PCF ───────────────────────────────
        try:
            policy = await client.call(
                "pcf", "POST", "/policies/decide",
                json={"supi": req.supi, "apn": req.apn},
                trace_id=ctx["trace_id"], parent_span_id=ctx["span_id"],
            )
            session.qos_flow = f"5qi-{policy['qos_5qi']}"
        except HTTPException as e:
            tel.error(f"PCF policy failed: {e.detail}", supi=req.supi, pdu_id=pdu_id)
            tel.inc("sessions_failed_total", reason="pcf_error")
            session.state = "FAILED"
            raise HTTPException(503, f"PCF unavailable: {e.detail}")

        # ── Step 2: Install bearer in UPF ─────────────────────────────
        try:
            bearer = await client.call(
                "upf", "POST", "/bearers",
                json={"pdu_id": pdu_id, "supi": req.supi,
                      "qos_5qi": policy["qos_5qi"]},
                trace_id=ctx["trace_id"], parent_span_id=ctx["span_id"],
            )
        except HTTPException as e:
            tel.error(f"UPF bearer install failed: {e.detail}",
                      supi=req.supi, pdu_id=pdu_id)
            tel.inc("sessions_failed_total", reason="upf_error")
            # Try to clean up policy
            try:
                await client.call("pcf", "DELETE", f"/policies/{req.supi}",
                                   trace_id=ctx["trace_id"])
            except Exception:
                pass
            session.state = "FAILED"
            raise HTTPException(503, f"UPF unavailable: {e.detail}")

        # ── Step 3: All good ──────────────────────────────────────────
        session.state = "ACTIVE"
        session.bearer_id = bearer["bearer_id"]
        tel.info("PDU session active", supi=req.supi, pdu_id=pdu_id,
                 bearer_id=bearer["bearer_id"], qos=policy["qos_5qi"])
        tel.inc("sessions_created_total")
        tel.gauge("active_sessions", len([s for s in SESSIONS.values()
                                           if s.state == "ACTIVE"]))
        return session


@app.delete("/sessions/{pdu_id}")
async def release_session(pdu_id: str, request: Request):
    """Release session — tears down bearer + revokes policy."""
    trace_id, _ = trace_context_from_request(request)
    s = SESSIONS.get(pdu_id)
    if not s:
        raise HTTPException(404, "session not found")

    # Best-effort cleanup
    if hasattr(s, "bearer_id") and s.bearer_id:
        try:
            await client.call("upf", "DELETE", f"/bearers/{s.bearer_id}",
                               trace_id=trace_id)
        except Exception as e:
            tel.warn(f"bearer release failed: {e}", pdu_id=pdu_id)

    try:
        await client.call("pcf", "DELETE", f"/policies/{s.supi}", trace_id=trace_id)
    except Exception as e:
        tel.warn(f"policy revoke failed: {e}", supi=s.supi)

    s.state = "RELEASED"
    tel.info("PDU session released", pdu_id=pdu_id, supi=s.supi)
    tel.inc("sessions_released_total")
    tel.gauge("active_sessions", len([s for s in SESSIONS.values()
                                       if s.state == "ACTIVE"]))
    return {"status": "released", "pdu_id": pdu_id}


@app.get("/sessions")
async def list_sessions(state: str = "ACTIVE", limit: int = 100):
    out = [s for s in SESSIONS.values() if s.state == state]
    return {
        "total": len(SESSIONS),
        "by_state": {
            st: sum(1 for s in SESSIONS.values() if s.state == st)
            for st in ["PENDING", "ACTIVE", "FAILED", "RELEASED"]
        },
        "sessions": out[:limit],
    }
