"""
5G error code catalog.

Maps 3GPP cause names to (HTTP status, NAS cause #, semantic owner, description).
Per TS 29.500 / 29.503 (SBI HTTP problem+json format)
and TS 24.501 (5GMM/5GSM cause values).
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ErrorCode:
    cause: str              # canonical cause name (UPPER_SNAKE_CASE)
    http_status: int        # SBI HTTP response code
    nas_cause: int          # NAS-level 5GMM/5GSM cause value
    title: str              # human title
    detail: str             # default detail (NF can override)
    nf_owners: tuple        # which NFs typically emit this
    category: str           # "auth" | "subscription" | "session" | "network" | "policy" | "resource" | "request"


# ── Catalog ──────────────────────────────────────────────────────────
# Keep ordered roughly by category for readable rendering in UI.
CATALOG: tuple[ErrorCode, ...] = (
    # ── AUTH (AUSF / AMF) ─────────────────────────────────────────────
    ErrorCode("AUTH_REJECTED",        403, 20, "Authentication rejected",
              "RES verification failed",                            ("ausf",), "auth"),
    ErrorCode("MAC_FAILURE",          403, 20, "MAC failure",
              "Message authentication code mismatch",               ("ausf", "amf"), "auth"),
    ErrorCode("SYNCH_FAILURE",        403, 21, "Sequence number out of sync",
              "AUTN SQN not in acceptable window — re-sync needed", ("ausf",), "auth"),
    ErrorCode("UE_AUTH_KEY_REVOKED",  403, 20, "UE authentication key revoked",
              "Subscriber's K has been administratively revoked",   ("udm", "ausf"), "auth"),
    ErrorCode("NON_5G_AUTH_UNACCEPTABLE", 403, 26, "Non-5G authentication unacceptable",
              "UE attempted EAP-AKA on a 5G-AKA-only subscriber",   ("ausf",), "auth"),

    # ── SUBSCRIPTION (UDM) ────────────────────────────────────────────
    ErrorCode("USER_NOT_FOUND",       404, 9,  "User not found",
              "SUPI not provisioned in UDM",                         ("udm",), "subscription"),
    ErrorCode("USER_NOT_ALLOWED",     403, 7,  "User not allowed",
              "Subscription does not permit 5GS access",             ("udm",), "subscription"),
    ErrorCode("ILLEGAL_UE",           403, 3,  "Illegal UE",
              "UE is administratively blocked",                      ("udm", "amf"), "subscription"),
    ErrorCode("PLMN_NOT_ALLOWED",     403, 11, "PLMN not allowed",
              "Visited PLMN not in allowed list for this subscriber",("udm", "amf"), "subscription"),
    ErrorCode("ROAMING_NOT_ALLOWED",  403, 13, "Roaming not allowed",
              "Subscriber not permitted to roam in this TAI",        ("udm", "amf"), "subscription"),
    ErrorCode("SUBSCRIPTION_NOT_FOUND", 404, 9, "Subscription data not found",
              "No subscription profile for this SUPI",               ("udm",), "subscription"),

    # ── SESSION / DNN (SMF / PCF) ─────────────────────────────────────
    ErrorCode("DNN_NOT_SUPPORTED",    404, 55, "DNN not supported",
              "Requested APN/DNN is unknown or not allowed",         ("smf", "pcf"), "session"),
    ErrorCode("PDU_TYPE_NOT_ALLOWED", 403, 50, "PDU session type not allowed",
              "Only IPv4 permitted on this APN",                     ("smf",), "session"),
    ErrorCode("INVALID_PDU_SESSION_ID", 409, 43, "Invalid PDU session identity",
              "Stale or unknown PDU session reference",              ("smf",), "session"),
    ErrorCode("CONTEXT_NOT_FOUND",    409, 10, "Context not found",
              "UE/session context missing — UE may need to re-attach",("amf", "smf"), "session"),
    ErrorCode("INSUFFICIENT_SLICE_RESOURCES", 503, 54, "Insufficient resources for slice",
              "Network slice has reached its capacity limit",        ("smf", "amf"), "resource"),

    # ── POLICY (PCF) ──────────────────────────────────────────────────
    ErrorCode("OPERATION_NOT_ALLOWED", 403, 38, "Operation not allowed",
              "Policy denies this operation for the subscriber",     ("pcf",), "policy"),
    ErrorCode("POLICY_NOT_FOUND",     404, 0,  "Policy not found",
              "No policy template matches this APN",                 ("pcf",), "policy"),

    # ── RESOURCE / OVERLOAD (any) ─────────────────────────────────────
    ErrorCode("INSUFFICIENT_RESOURCES", 503, 67, "Insufficient resources",
              "NF cannot allocate requested resource",               ("upf", "smf"), "resource"),
    ErrorCode("NF_CONGESTION",        503, 22, "NF congestion",
              "NF is in overload and rejecting new work",            ("amf", "ausf", "udm", "smf", "upf", "pcf", "nrf"), "resource"),
    ErrorCode("TOO_MANY_REQUESTS",    429, 22, "Too many requests",
              "Rate limit exceeded for this client",                 ("amf", "ausf", "udm", "smf", "upf", "pcf", "nrf"), "resource"),

    # ── REQUEST / PROTOCOL (any) ──────────────────────────────────────
    ErrorCode("MANDATORY_IE_MISSING", 400, 96, "Mandatory IE missing",
              "Required information element not present in request", ("amf", "ausf", "udm", "smf", "upf", "pcf"), "request"),
    ErrorCode("SEMANTIC_ERROR",       400, 95, "Semantically incorrect message",
              "Request body fails schema validation",                ("amf", "ausf", "udm", "smf", "upf", "pcf"), "request"),
    ErrorCode("UPSTREAM_TIMEOUT",     504, 0,  "Upstream timeout",
              "Downstream NF did not respond within timeout",        ("amf", "ausf", "smf", "udm"), "network"),
    ErrorCode("INTERNAL_ERROR",       500, 0,  "Internal server error",
              "Unexpected condition — see logs for trace_id",        ("amf", "ausf", "udm", "smf", "upf", "pcf", "nrf"), "request"),
)


_BY_CAUSE = {e.cause: e for e in CATALOG}


def lookup(cause: str) -> Optional[ErrorCode]:
    """Get an ErrorCode by canonical cause name."""
    return _BY_CAUSE.get(cause)


def codes_for_nf(nf: str) -> list[ErrorCode]:
    """List error codes a given NF can emit."""
    return [e for e in CATALOG if nf in e.nf_owners]


def all_causes() -> list[str]:
    return [e.cause for e in CATALOG]


def problem_json(cause: str, *, supi: Optional[str] = None,
                 trace_id: Optional[str] = None,
                 detail_override: Optional[str] = None,
                 invalid_params: Optional[list] = None) -> dict:
    """Build a 3GPP-compliant application/problem+json body."""
    e = lookup(cause)
    if not e:
        # Fallback for unknown causes
        return {
            "type": "https://3gpp.org/sbi/problem/UNKNOWN",
            "title": "Unknown error",
            "status": 500,
            "cause": cause,
            "detail": detail_override or "Unknown cause",
        }
    body = {
        "type": f"https://3gpp.org/sbi/problem/{e.cause}",
        "title": e.title,
        "status": e.http_status,
        "cause": e.cause,
        "nasCause": e.nas_cause,
        "detail": detail_override or e.detail,
    }
    if supi:
        body["supi"] = supi
    if trace_id:
        body["traceId"] = trace_id
    if invalid_params:
        body["invalidParams"] = invalid_params
    return body


# ── Categories for UI grouping ───────────────────────────────────────
CATEGORIES = ("auth", "subscription", "session", "policy", "resource", "network", "request")
