"""
ML Engine — anomaly detection + failure prediction on telemetry streams.

Pulls metrics from collector, runs Isolation Forest per-NF, exposes:
  POST /api/ml/anomalies  — detect anomalies in recent metrics
  POST /api/ml/forecast   — predict next-15min request rate per NF
"""
import os
import sys
import time
from typing import Optional

import httpx
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sklearn.ensemble import IsolationForest
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

COLLECTOR_URL = os.getenv("COLLECTOR_URL", "http://collector:9000")

app = FastAPI(title="5G AIOps ML Engine")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/healthz")
def health():
    return {"status": "ok"}


def _features_from_history(history: list[dict]) -> tuple[np.ndarray, list[float]]:
    """Convert metric snapshots into (features, timestamps).
    Features: [request_rate, error_rate, p99_latency]"""
    features = []
    timestamps = []
    prev_total = None
    prev_err = None
    prev_ts = None

    for sample in history:
        ts = sample.get("timestamp")
        counters = sample.get("counters", {})
        histograms = sample.get("histograms", {})

        total = sum(v for k, v in counters.items() if k.startswith("requests_total"))
        errors = sum(v for k, v in counters.items() if "fail" in k or "error" in k)
        p99 = next(
            (h.get("p99", 0) for k, h in histograms.items()
             if k.startswith("request_duration_ms")),
            0,
        )

        if prev_total is not None and prev_ts and ts > prev_ts:
            dt = ts - prev_ts
            req_rate = (total - prev_total) / dt
            err_rate = (errors - prev_err) / dt if prev_err is not None else 0
            features.append([req_rate, err_rate, p99])
            timestamps.append(ts)

        prev_total = total
        prev_err = errors
        prev_ts = ts

    return np.array(features) if features else np.empty((0, 3)), timestamps


@app.post("/api/ml/anomalies")
async def detect_anomalies(nf: Optional[str] = None, window_seconds: int = 600):
    """Run Isolation Forest on each NF's recent metrics."""
    nfs = [nf] if nf else ["amf", "smf", "ausf", "udm", "nrf", "upf", "pcf"]
    results = []

    async with httpx.AsyncClient(timeout=10) as client:
        for nf_name in nfs:
            try:
                r = await client.get(
                    f"{COLLECTOR_URL}/api/metrics/{nf_name}",
                    params={"window_seconds": window_seconds},
                )
                history = r.json().get("history", [])
            except Exception as e:
                results.append({"nf": nf_name, "error": str(e)})
                continue

            X, ts = _features_from_history(history)
            if len(X) < 20:
                results.append({
                    "nf": nf_name,
                    "samples": len(X),
                    "anomalies": [],
                    "note": "not enough data",
                })
                continue

            scaler = StandardScaler()
            Xs = scaler.fit_transform(X)
            model = IsolationForest(contamination=0.1, random_state=42, n_estimators=100)
            preds = model.fit_predict(Xs)
            scores = model.score_samples(Xs)

            anomalies = []
            for i, (p, s) in enumerate(zip(preds, scores)):
                if p == -1:  # anomaly
                    anomalies.append({
                        "timestamp": ts[i],
                        "score": round(float(-s), 4),
                        "request_rate": round(float(X[i][0]), 2),
                        "error_rate": round(float(X[i][1]), 4),
                        "p99_latency_ms": round(float(X[i][2]), 1),
                    })

            results.append({
                "nf": nf_name,
                "samples": int(len(X)),
                "anomalies": anomalies,
                "anomaly_count": len(anomalies),
                "anomaly_rate": round(len(anomalies) / max(1, len(X)), 3),
            })

    return {"timestamp": time.time(), "results": results}


@app.post("/api/ml/forecast")
async def forecast(nf: str, metric: str = "requests_total", horizon_seconds: int = 900):
    """Forecast a metric N seconds into the future using ridge regression."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"{COLLECTOR_URL}/api/metrics/{nf}/series/{metric}",
            params={"window_seconds": 1800},  # 30min history
        )
    points = r.json().get("points", [])
    if len(points) < 30:
        return {"error": "insufficient history", "samples": len(points)}

    # Build features: time-since-start, hour-sin/cos
    t0 = points[0]["t"]
    X = np.array([
        [
            (p["t"] - t0),
            np.sin(2 * np.pi * (p["t"] % 86400) / 86400),
            np.cos(2 * np.pi * (p["t"] % 86400) / 86400),
        ]
        for p in points
    ])
    y = np.array([p["v"] for p in points])

    model = Ridge(alpha=1.0).fit(X, y)
    y_pred = model.predict(X)
    residual_std = float(np.std(y - y_pred))

    # Forecast horizon
    last_t = points[-1]["t"]
    future_ts = np.arange(last_t + 30, last_t + horizon_seconds + 1, 30)
    Xf = np.array([
        [
            (t - t0),
            np.sin(2 * np.pi * (t % 86400) / 86400),
            np.cos(2 * np.pi * (t % 86400) / 86400),
        ]
        for t in future_ts
    ])
    yf = model.predict(Xf)

    return {
        "nf": nf,
        "metric": metric,
        "fit_quality": {
            "mae": round(float(np.mean(np.abs(y - y_pred))), 2),
            "rmse": round(float(np.sqrt(np.mean((y - y_pred) ** 2))), 2),
        },
        "forecast": [
            {
                "t": float(t),
                "predicted": round(float(v), 2),
                "lower": round(float(v - 1.96 * residual_std), 2),
                "upper": round(float(v + 1.96 * residual_std), 2),
            }
            for t, v in zip(future_ts, yf)
        ],
    }


# ============================================================================
# FAILURE PATTERN CLASSIFIER
# ============================================================================
# Maps observed error-code distributions to known failure scenarios + recommends
# remediations. RECOMMEND-ONLY — never executes anything.
#
# This is a rule-based classifier rather than ML-trained because the codes
# have explicit semantic meaning and we want explainable output. With more
# data (real telco traces) we'd swap in a multi-class logistic regression or
# decision tree trained on labeled scenarios.
# ============================================================================

# Knowledge base: scenario fingerprint → diagnosis + remediation suggestions.
# Each entry is matched if MOST of `signature_codes` are present and elevated.
KNOWN_PATTERNS = [
    {
        "id": "auth-reject-storm",
        "name": "Auth-Reject Storm",
        "severity": "high",
        "signature_codes": {"ausf": ["AUTH_REJECTED"], "udm": ["UE_AUTH_KEY_REVOKED"]},
        "description": "Subscribers have revoked auth keys; UEs are getting MAC failures.",
        "likely_root_cause": "Bulk subscriber state set to AUTH_KEY_REVOKED (admin error or scenario)",
        "recommended_actions": [
            "POST /api/orchestrator/subscribers/state/reset on UDM (clears all blocks)",
            "Verify whether key revocation was intentional via audit log",
            "If unintentional: re-provision affected SUPIs",
        ],
    },
    {
        "id": "dnn-mismatch",
        "name": "DNN/APN Configuration Mismatch",
        "severity": "medium",
        "signature_codes": {"pcf": ["DNN_NOT_SUPPORTED"]},
        "description": "PCF rejecting policy decisions due to unknown APN/DNN.",
        "likely_root_cause": "PCF is configured to reject some APNs that AMF is sending.",
        "recommended_actions": [
            "Clear PCF failure injection: POST /api/orchestrator/failures/clear?nf=pcf",
            "Verify APN list in subscriber profile vs PCF templates",
            "Check that 'internet' APN is configured in PCF",
        ],
    },
    {
        "id": "congestion-cascade",
        "name": "User-Plane Congestion Cascade",
        "severity": "critical",
        "signature_codes": {"upf": ["INSUFFICIENT_RESOURCES", "NF_CONGESTION"]},
        "description": "UPF is rejecting bearer installs; SMF cascades the failure to UEs.",
        "likely_root_cause": "UPF resource exhaustion, real or injected.",
        "recommended_actions": [
            "Clear UPF failure: POST /api/orchestrator/failures/clear?nf=upf",
            "Reduce attach rate (Subscribers tab → Stop Load)",
            "Scale UPF capacity in production (add UPF instances)",
        ],
    },
    {
        "id": "roaming-restriction",
        "name": "Roaming Restriction",
        "severity": "medium",
        "signature_codes": {"udm": ["ROAMING_NOT_ALLOWED"]},
        "description": "Many subscribers are tagged ROAMING_NOT_ALLOWED — registrations failing for those SUPIs.",
        "likely_root_cause": "Bulk subscriber-level state change (operator action or scenario).",
        "recommended_actions": [
            "Reset subscriber states: POST /api/orchestrator/subscribers/state/reset",
            "Investigate why roaming was disabled (intentional vs misconfiguration)",
            "If intentional: filter UE attach attempts by allowed PLMN at AMF",
        ],
    },
    {
        "id": "slice-capacity",
        "name": "Slice Capacity Exhaustion",
        "severity": "high",
        "signature_codes": {"smf": ["INSUFFICIENT_SLICE_RESOURCES"]},
        "description": "SMF can't accept new sessions on the requested slice.",
        "likely_root_cause": "Slice has hit its capacity ceiling, or SMF fault injection active.",
        "recommended_actions": [
            "Clear SMF failure: POST /api/orchestrator/failures/clear?nf=smf",
            "Add another SMF/UPF pair for the saturated slice",
            "Throttle new session establishment until existing sessions drain",
        ],
    },
    {
        "id": "subscription-kaleidoscope",
        "name": "Mixed Subscriber-State Issues",
        "severity": "critical",
        "signature_codes": {
            "udm": ["ILLEGAL_UE", "ROAMING_NOT_ALLOWED", "UE_AUTH_KEY_REVOKED", "USER_NOT_ALLOWED"],
        },
        "description": "Multiple distinct subscriber-level error codes are elevated — heterogeneous fault.",
        "likely_root_cause": "Multiple subscribers have been tagged with different non-ACTIVE states.",
        "recommended_actions": [
            "Reset all subscriber states: POST /api/orchestrator/subscribers/state/reset",
            "Audit recent state changes via UDM logs",
            "Consider partial re-provisioning if intentional",
        ],
    },
    {
        "id": "auth-vector-exhaustion",
        "name": "Auth Vector / UDM Failure",
        "severity": "critical",
        "signature_codes": {"udm": ["USER_NOT_FOUND", "INTERNAL_ERROR"]},
        "description": "UDM unable to serve auth vectors — broad auth failure.",
        "likely_root_cause": "UDM unavailable, fault-injected, or subscriber DB issue.",
        "recommended_actions": [
            "Check UDM /healthz",
            "Clear any UDM fault injection",
            "Verify subscribers exist (GET /subscribers)",
        ],
    },
    {
        "id": "context-loss",
        "name": "UE/Session Context Loss",
        "severity": "medium",
        "signature_codes": {"amf": ["CONTEXT_NOT_FOUND"], "smf": ["CONTEXT_NOT_FOUND"]},
        "description": "UEs are referencing contexts that AMF/SMF don't have.",
        "likely_root_cause": "Recent NF restart wiped in-memory state, or stale UE state.",
        "recommended_actions": [
            "Have UEs re-attach (Subscribers tab → Detach all, then re-attach)",
            "Check for recent NF restarts in logs",
        ],
    },
]


def _gather_error_code_counts(summary: dict) -> dict[str, dict[str, float]]:
    """From collector summary, extract per-NF error code counts.
    Returns: {nf: {code: count}}"""
    out = {}
    for nf, data in (summary.get("nfs") or {}).items():
        if not isinstance(data, dict):
            continue
        # Counters with the labeled name "errors_by_code_total"
        # The Telemetry class flattens labels into the counter key like:
        # errors_by_code_total{code=AUTH_REJECTED}
        codes = {}
        for k, v in data.items():
            if not isinstance(k, str):
                continue
            if k.startswith("errors_by_code_total"):
                # parse out the code label
                if "{" in k and "code=" in k:
                    code = k.split("code=", 1)[1].rstrip("}").strip()
                    codes[code] = codes.get(code, 0) + (v or 0)
        if codes:
            out[nf] = codes
    return out


def _match_pattern(observed: dict[str, dict[str, float]], pattern: dict) -> tuple[float, list[str]]:
    """Return (match_score, matched_codes) for a pattern against observed data.
    Score is fraction of pattern's signature codes that are present and >0."""
    sig = pattern["signature_codes"]
    total = 0
    hit = 0
    matched = []
    for nf, codes in sig.items():
        for code in codes:
            total += 1
            count = (observed.get(nf, {}) or {}).get(code, 0)
            if count > 0:
                hit += 1
                matched.append(f"{nf}:{code}={int(count)}")
    return (hit / total if total else 0.0), matched


@app.post("/api/ml/classify-failure")
async def classify_failure():
    """Diagnose the current failure pattern and suggest remediations.
    Reads collector summary, extracts per-NF error-code counters, matches
    against the knowledge base of known patterns, returns ranked diagnoses
    with remediation suggestions. RECOMMEND-ONLY — never executes anything."""
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(f"{COLLECTOR_URL}/api/summary", timeout=10)
            summary = r.json()
        except Exception as e:
            raise HTTPException(503, f"collector unreachable: {e}")

    observed = _gather_error_code_counts(summary)
    total_errors = sum(c for nf in observed.values() for c in nf.values())

    if total_errors == 0:
        return {
            "verdict": "healthy",
            "summary": "No error codes detected across any NF.",
            "observed": {},
            "matches": [],
            "total_errors": 0,
        }

    # Score every pattern
    scored = []
    for pat in KNOWN_PATTERNS:
        score, matched = _match_pattern(observed, pat)
        if score > 0:
            scored.append({
                "id": pat["id"],
                "name": pat["name"],
                "severity": pat["severity"],
                "match_score": round(score, 3),
                "matched_codes": matched,
                "description": pat["description"],
                "likely_root_cause": pat["likely_root_cause"],
                "recommended_actions": pat["recommended_actions"],
            })
    # Sort by match_score descending, then severity rank
    sev_rank = {"low": 1, "medium": 2, "high": 3, "critical": 4}
    scored.sort(key=lambda x: (x["match_score"], sev_rank.get(x["severity"], 0)),
                reverse=True)

    return {
        "verdict": "issue_detected" if scored else "unknown_pattern",
        "summary": f"Detected {total_errors} errors across {len(observed)} NFs."
                   + (f" Top match: {scored[0]['name']} ({scored[0]['match_score']*100:.0f}%)." if scored else " No known pattern matched."),
        "observed": observed,
        "matches": scored,
        "total_errors": total_errors,
    }
