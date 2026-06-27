"""
PCF — Policy Control Function

Provides QoS policies and charging rules per session. SMF queries PCF when
establishing a PDU session to get policy decisions. PCF also supports policy
updates (e.g. throttle a UE) that propagate to UPF.
"""
import sys, os
import time
from fastapi import HTTPException, Request
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nf_common import create_nf_app, trace_context_from_request


app, tel, failure = create_nf_app("pcf", 8007)


# ── Default policy templates by APN ──────────────────────────────────
POLICY_TEMPLATES = {
    "internet": {
        "qos_5qi": 9,
        "max_dl_mbps": 100,
        "max_ul_mbps": 50,
        "priority_level": 8,
        "charging_rule": "internet-default",
    },
    "voice": {
        "qos_5qi": 1,
        "max_dl_mbps": 0.1,
        "max_ul_mbps": 0.1,
        "priority_level": 2,  # higher priority (lower number)
        "charging_rule": "voice-default",
    },
    "video": {
        "qos_5qi": 5,
        "max_dl_mbps": 25,
        "max_ul_mbps": 10,
        "priority_level": 4,
        "charging_rule": "video-default",
    },
}

# ── Per-subscriber active policies ───────────────────────────────────
ACTIVE_POLICIES: dict[str, dict] = {}


class PolicyRequest(BaseModel):
    supi: str
    apn: str = "internet"


@app.post("/policies/decide")
async def decide_policy(req: PolicyRequest, request: Request):
    """Called by SMF when establishing a session."""
    trace_id, parent = trace_context_from_request(request)
    async with tel.span("decide_policy", trace_id=trace_id,
                        parent_span_id=parent, supi=req.supi):
        if req.apn not in POLICY_TEMPLATES:
            tel.warn(f"unknown APN: {req.apn}", supi=req.supi)
            tel.inc("policy_decisions_failed_total", reason="unknown_apn")
            raise HTTPException(404, f"no policy for APN {req.apn}")

        template = POLICY_TEMPLATES[req.apn]
        policy = {
            "supi": req.supi,
            "apn": req.apn,
            "issued_at": time.time(),
            **template,
        }
        ACTIVE_POLICIES[req.supi] = policy
        tel.info("policy issued", supi=req.supi, apn=req.apn,
                 qos_5qi=template["qos_5qi"])
        tel.inc("policy_decisions_total", apn=req.apn)
        tel.gauge("active_policies", len(ACTIVE_POLICIES))
        return policy


@app.delete("/policies/{supi}")
async def revoke_policy(supi: str):
    if supi not in ACTIVE_POLICIES:
        raise HTTPException(404, "no policy")
    ACTIVE_POLICIES.pop(supi)
    tel.info("policy revoked", supi=supi)
    tel.inc("policy_revocations_total")
    tel.gauge("active_policies", len(ACTIVE_POLICIES))
    return {"status": "revoked", "supi": supi}


@app.get("/policies/{supi}")
async def get_policy(supi: str):
    p = ACTIVE_POLICIES.get(supi)
    if not p:
        raise HTTPException(404, "no policy")
    return p


@app.get("/policies")
async def list_policies(limit: int = 100):
    return {
        "total": len(ACTIVE_POLICIES),
        "by_apn": {
            apn: sum(1 for p in ACTIVE_POLICIES.values() if p["apn"] == apn)
            for apn in POLICY_TEMPLATES
        },
        "policies": list(ACTIVE_POLICIES.values())[:limit],
    }
