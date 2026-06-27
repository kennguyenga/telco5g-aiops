"""
UDM — Unified Data Management

The 5G subscriber database. Stores SUPI, auth keys, subscription profiles,
and per-subscriber state. AUSF queries UDM for auth credentials; AMF queries
UDM for subscription data.

Subscriber state machine
------------------------
Each subscriber has a state that affects how UDM responds:

  ACTIVE             — normal operation (default)
  BLOCKED            — administratively blocked → ILLEGAL_UE
  ROAMING_NOT_ALLOWED — roaming restriction → ROAMING_NOT_ALLOWED
  AUTH_KEY_REVOKED   — K has been revoked → UE_AUTH_KEY_REVOKED on auth-vector
  PROVISIONING_PENDING — record exists but not finalized → SUBSCRIPTION_NOT_FOUND
  SUSPENDED          — billing/admin hold → USER_NOT_ALLOWED

State transitions are persistent within a session and only changed by:
  - Operator API: POST /subscribers/{supi}/state {state: NEW_STATE}
  - Bulk API:    POST /subscribers/state/bulk
  - Reset:       POST /subscribers/state/reset
"""
import hashlib
import secrets
import sys
import os
from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nf_common import create_nf_app, Subscriber, AuthVector, nf_error


app, tel, failure = create_nf_app("udm", 8003)


# ── Subscriber state model ──────────────────────────────────────────
SUBSCRIBER_STATES = (
    "ACTIVE",
    "BLOCKED",
    "ROAMING_NOT_ALLOWED",
    "AUTH_KEY_REVOKED",
    "PROVISIONING_PENDING",
    "SUSPENDED",
)


# Map state → (cause to emit, which lookup it affects)
# "any" = affects all UDM lookups for this SUPI
# "auth" = only affects auth-vector calls
STATE_TO_ERROR = {
    "BLOCKED":              ("ILLEGAL_UE",         "any"),
    "ROAMING_NOT_ALLOWED":  ("ROAMING_NOT_ALLOWED","any"),
    "AUTH_KEY_REVOKED":     ("UE_AUTH_KEY_REVOKED","auth"),
    "PROVISIONING_PENDING": ("SUBSCRIPTION_NOT_FOUND", "profile"),
    "SUSPENDED":            ("USER_NOT_ALLOWED",   "any"),
}


# Per-SUPI state. Defaults to "ACTIVE" for any provisioned subscriber.
SUBSCRIBER_STATE: dict[str, str] = {}


class SubscriberStateUpdate(BaseModel):
    state: str
    reason: Optional[str] = None


class BulkStateUpdate(BaseModel):
    """Set state on multiple SUPIs in one call."""
    state: str
    supis: Optional[list[str]] = None    # if None, applies to count random ones
    count: Optional[int] = None          # number of random SUPIs to affect
    reason: Optional[str] = None


SUBSCRIBERS: dict[str, Subscriber] = {}


def _provision_default_subscribers(n: int = 1000):
    for i in range(1, n + 1):
        supi = f"imsi-00101{i:010d}"
        SUBSCRIBERS[supi] = Subscriber(
            supi=supi,
            auth_key=f"{i:032x}",
            plmn="00101",
            nssai=["sst=1"],
            apn="internet",
        )
        SUBSCRIBER_STATE[supi] = "ACTIVE"
    tel.info(f"provisioned {n} subscribers")
    tel.gauge("provisioned_subscribers", n)


_provision_default_subscribers()


def _check_state(supi: str, lookup_kind: str):
    """Raise the right 5G error if subscriber is in a non-ACTIVE state."""
    state = SUBSCRIBER_STATE.get(supi, "ACTIVE")
    if state == "ACTIVE":
        return
    mapping = STATE_TO_ERROR.get(state)
    if not mapping:
        return
    cause, scope = mapping
    if scope == "any" or scope == lookup_kind:
        tel.inc("subscriber_state_blocks_total", state=state, supi=supi)
        raise nf_error(cause, supi=supi, tel=tel,
                       detail=f"Subscriber in state {state}")


# ============================================================================
# Lookup endpoints — apply subscriber state
# ============================================================================
@app.get("/subscribers/{supi}")
async def get_subscriber(supi: str):
    sub = SUBSCRIBERS.get(supi)
    if not sub:
        tel.warn(f"unknown SUPI: {supi}", supi=supi)
        tel.inc("supi_not_found_total")
        raise nf_error("USER_NOT_FOUND", supi=supi, tel=tel)
    _check_state(supi, "any")
    tel.inc("subscriber_lookups_total")
    return sub


@app.post("/subscribers/{supi}/auth-vector")
async def generate_auth_vector(supi: str):
    sub = SUBSCRIBERS.get(supi)
    if not sub:
        tel.inc("auth_vector_failures_total", reason="unknown_supi")
        raise nf_error("USER_NOT_FOUND", supi=supi, tel=tel)
    _check_state(supi, "auth")
    rand = secrets.token_hex(16)
    expected_res = _toy_milenage(rand, sub.auth_key)
    autn = secrets.token_hex(16)
    av = AuthVector(rand=rand, expected_res=expected_res, autn=autn)
    tel.info(f"auth vector generated", supi=supi)
    tel.inc("auth_vectors_generated_total")
    return av


@app.get("/subscribers/{supi}/profile")
async def get_subscription_profile(supi: str):
    sub = SUBSCRIBERS.get(supi)
    if not sub:
        raise nf_error("USER_NOT_FOUND", supi=supi, tel=tel)
    _check_state(supi, "profile")
    return {
        "supi": sub.supi,
        "nssai": sub.nssai,
        "apn": sub.apn,
        "plmn": sub.plmn,
    }


@app.get("/subscribers")
async def list_subscribers(limit: int = 50, offset: int = 0,
                           state: Optional[str] = None):
    keys = sorted(SUBSCRIBERS.keys())
    if state:
        keys = [k for k in keys if SUBSCRIBER_STATE.get(k, "ACTIVE") == state]
    page = keys[offset : offset + limit]
    return {
        "total": len(SUBSCRIBERS),
        "filtered": len(keys),
        "offset": offset,
        "limit": limit,
        "subscribers": [
            {**SUBSCRIBERS[k].model_dump(), "state": SUBSCRIBER_STATE.get(k, "ACTIVE")}
            for k in page
        ],
    }


# ============================================================================
# State management endpoints — operator-facing
# ============================================================================
@app.post("/subscribers/{supi}/state")
async def set_subscriber_state(supi: str, req: SubscriberStateUpdate):
    if supi not in SUBSCRIBERS:
        raise nf_error("USER_NOT_FOUND", supi=supi, tel=tel)
    if req.state not in SUBSCRIBER_STATES:
        raise HTTPException(400, f"unknown state: {req.state}. valid: {list(SUBSCRIBER_STATES)}")
    old = SUBSCRIBER_STATE.get(supi, "ACTIVE")
    SUBSCRIBER_STATE[supi] = req.state
    tel.info(f"subscriber state changed",
             supi=supi, old=old, new=req.state, reason=req.reason or "")
    tel.inc("subscriber_state_changes_total", new_state=req.state)
    _refresh_state_gauges()
    return {"supi": supi, "state": req.state, "old": old}


@app.get("/subscribers/{supi}/state")
async def get_subscriber_state(supi: str):
    if supi not in SUBSCRIBERS:
        raise nf_error("USER_NOT_FOUND", supi=supi, tel=tel)
    return {"supi": supi, "state": SUBSCRIBER_STATE.get(supi, "ACTIVE")}


@app.post("/subscribers/state/bulk")
async def bulk_set_state(req: BulkStateUpdate):
    if req.state not in SUBSCRIBER_STATES:
        raise HTTPException(400, f"unknown state: {req.state}")
    if req.supis:
        targets = [s for s in req.supis if s in SUBSCRIBERS]
    elif req.count:
        import random
        candidates = [s for s, st in SUBSCRIBER_STATE.items() if st == "ACTIVE"]
        targets = random.sample(candidates, min(req.count, len(candidates)))
    else:
        raise HTTPException(400, "must supply either supis or count")

    for s in targets:
        SUBSCRIBER_STATE[s] = req.state
    tel.warn(f"bulk subscriber state change",
             count=len(targets), state=req.state, reason=req.reason or "")
    tel.inc("subscriber_state_changes_total", new_state=req.state, count=len(targets))
    _refresh_state_gauges()
    return {"updated": len(targets), "state": req.state, "supis": targets[:50]}


@app.post("/subscribers/state/reset")
async def reset_all_states():
    """Reset every subscriber back to ACTIVE."""
    affected = sum(1 for s in SUBSCRIBER_STATE.values() if s != "ACTIVE")
    for k in SUBSCRIBER_STATE:
        SUBSCRIBER_STATE[k] = "ACTIVE"
    tel.info(f"reset all subscriber states", affected=affected)
    tel.inc("subscriber_state_resets_total")
    _refresh_state_gauges()
    return {"reset": affected, "total": len(SUBSCRIBER_STATE)}


@app.get("/subscribers/state/summary")
async def state_summary():
    """Count subscribers in each state."""
    counts = {st: 0 for st in SUBSCRIBER_STATES}
    for s in SUBSCRIBER_STATE.values():
        counts[s] = counts.get(s, 0) + 1
    return {
        "total": len(SUBSCRIBER_STATE),
        "by_state": counts,
        "non_active_count": sum(c for s, c in counts.items() if s != "ACTIVE"),
    }


def _refresh_state_gauges():
    counts = {st: 0 for st in SUBSCRIBER_STATES}
    for s in SUBSCRIBER_STATE.values():
        counts[s] = counts.get(s, 0) + 1
    for st, c in counts.items():
        tel.gauge(f"subscribers_in_state_{st.lower()}", c)


_refresh_state_gauges()


def _toy_milenage(rand: str, key: str) -> str:
    return hashlib.sha256((rand + key).encode()).hexdigest()[:16]
