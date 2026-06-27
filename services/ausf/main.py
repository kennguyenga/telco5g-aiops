"""
AUSF — Authentication Server Function

Runs the 5G AKA challenge/response protocol:
  1. AMF sends UE's SUPI to AUSF
  2. AUSF asks UDM for an auth vector (RAND, XRES, AUTN)
  3. AUSF sends RAND+AUTN as challenge to AMF (which forwards to UE)
  4. UE computes RES, sends back to AMF -> AUSF
  5. AUSF compares RES vs XRES -> auth success/fail
"""
import sys, os
import time
import secrets
from fastapi import HTTPException, Request
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nf_common import create_nf_app, NFClient, get_nf_urls, trace_context_from_request


app, tel, failure = create_nf_app("ausf", 8002)
client = NFClient(tel, get_nf_urls())


# ── In-flight auth challenges ─────────────────────────────────────────
PENDING: dict[str, dict] = {}  # auth_ctx_id -> {supi, expected_res, expires}
CTX_TTL = 30.0  # seconds


class AuthInitRequest(BaseModel):
    supi: str
    plmn: str = "00101"


class AuthConfirmRequest(BaseModel):
    auth_ctx_id: str
    res: str  # UE's response


@app.post("/auth/init")
async def auth_init(req: AuthInitRequest, request: Request):
    """Step 1-3: AMF asks AUSF to start auth. AUSF gets vector from UDM
    and returns the challenge (RAND+AUTN) to AMF."""
    trace_id, parent_span = trace_context_from_request(request)
    async with tel.span("auth_init", trace_id=trace_id,
                        parent_span_id=parent_span, supi=req.supi) as ctx:
        try:
            av = await client.call("udm", "POST",
                                    f"/subscribers/{req.supi}/auth-vector",
                                    trace_id=ctx["trace_id"],
                                    parent_span_id=ctx["span_id"])
        except HTTPException as e:
            tel.error(f"auth init failed (UDM): {e.detail}", supi=req.supi)
            tel.inc("auth_init_failures_total", reason="udm_error")
            raise

        # Store expected response for confirmation step
        ctx_id = secrets.token_hex(8)
        PENDING[ctx_id] = {
            "supi": req.supi,
            "expected_res": av["expected_res"],
            "expires": time.time() + CTX_TTL,
        }
        tel.info("auth challenge issued", supi=req.supi, ctx_id=ctx_id)
        tel.inc("auth_init_total")
        tel.gauge("pending_auths", len(PENDING))

        return {
            "auth_ctx_id": ctx_id,
            "rand": av["rand"],
            "autn": av["autn"],
        }


@app.post("/auth/confirm")
async def auth_confirm(req: AuthConfirmRequest, request: Request):
    """Step 4-5: AMF sends UE's RES; AUSF compares with XRES."""
    trace_id, parent_span = trace_context_from_request(request)
    async with tel.span("auth_confirm", trace_id=trace_id,
                        parent_span_id=parent_span, ctx_id=req.auth_ctx_id) as ctx:
        # Cleanup expired contexts opportunistically
        now = time.time()
        expired = [k for k, v in PENDING.items() if v["expires"] < now]
        for k in expired:
            PENDING.pop(k, None)

        pending = PENDING.pop(req.auth_ctx_id, None)
        tel.gauge("pending_auths", len(PENDING))

        if not pending:
            tel.warn(f"auth context expired or unknown: {req.auth_ctx_id}")
            tel.inc("auth_confirm_failures_total", reason="ctx_expired")
            raise HTTPException(404, "auth context expired or unknown")

        if req.res != pending["expected_res"]:
            tel.warn("AKA mismatch", supi=pending["supi"])
            tel.inc("auth_confirm_failures_total", reason="aka_mismatch")
            raise HTTPException(401, "AKA mismatch")

        tel.info("auth success", supi=pending["supi"])
        tel.inc("auth_success_total")
        return {"status": "authenticated", "supi": pending["supi"]}
