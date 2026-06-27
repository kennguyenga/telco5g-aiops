"""
Telemetry Collector

Periodically scrapes /metrics, /logs, /traces from each NF and exposes
unified APIs. Single source of truth for ML pipeline and LLM agent.

In a real system this would be Prometheus + Loki + Tempo. Here it's a
lightweight in-memory aggregator.
"""
import asyncio
import os
import sys
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nf_common import get_nf_urls


SCRAPE_INTERVAL = 5.0  # seconds
NF_TYPES = ["nrf", "ausf", "udm", "amf", "smf", "upf", "pcf"]


class CollectorState:
    """Global collector state."""
    # Time-series of metric snapshots: nf -> deque of (timestamp, snapshot dict)
    METRIC_HISTORY: dict[str, deque] = {nf: deque(maxlen=720) for nf in NF_TYPES}  # 1h @ 5s
    # Latest log buffer per NF
    LOGS: dict[str, deque] = {nf: deque(maxlen=2000) for nf in NF_TYPES}
    # Recent traces (across all NFs)
    SPANS: deque = deque(maxlen=2000)
    # NF reachability (last successful scrape time)
    LAST_SEEN: dict[str, float] = {nf: 0.0 for nf in NF_TYPES}


state = CollectorState()


async def scrape_loop():
    """Background task: every SCRAPE_INTERVAL, pull from all NFs."""
    nf_urls = get_nf_urls()
    async with httpx.AsyncClient(timeout=3.0) as client:
        while True:
            t0 = time.time()
            await asyncio.gather(
                *(_scrape_one(client, nf, url) for nf, url in nf_urls.items()),
                return_exceptions=True,
            )
            # Sleep to maintain interval
            elapsed = time.time() - t0
            await asyncio.sleep(max(0, SCRAPE_INTERVAL - elapsed))


async def _scrape_one(client: httpx.AsyncClient, nf: str, url: str):
    """Scrape one NF. Tolerate failures — that IS telemetry signal."""
    now = time.time()
    try:
        # Metrics
        r = await client.get(f"{url}/metrics")
        if r.status_code == 200:
            state.METRIC_HISTORY[nf].append((now, r.json()))
            state.LAST_SEEN[nf] = now

        # Logs (only fetch new ones)
        last_log_ts = 0.0
        if state.LOGS[nf]:
            last_log_ts = state.LOGS[nf][-1].get("timestamp", 0.0)
        r = await client.get(f"{url}/logs", params={"since": last_log_ts, "limit": 200})
        if r.status_code == 200:
            for log in r.json().get("logs", []):
                state.LOGS[nf].append(log)

        # Traces
        r = await client.get(f"{url}/traces", params={"limit": 50})
        if r.status_code == 200:
            for span in r.json().get("spans", []):
                state.SPANS.append(span)
    except (httpx.RequestError, httpx.HTTPError):
        # NF down — record as missed scrape via gap in METRIC_HISTORY
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(scrape_loop())
    yield
    task.cancel()


app = FastAPI(title="5G AIOps Telemetry Collector", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/healthz")
def health():
    return {"status": "ok"}


@app.get("/api/nfs/status")
def nfs_status():
    """Per-NF up/down status with freshness."""
    now = time.time()
    out = []
    for nf in NF_TYPES:
        last = state.LAST_SEEN[nf]
        age = now - last if last > 0 else None
        # Up if scraped within 2x interval
        up = last > 0 and age < SCRAPE_INTERVAL * 2
        out.append({
            "nf": nf,
            "up": up,
            "last_seen": last,
            "age_seconds": age,
        })
    return {"nfs": out, "timestamp": now}


@app.get("/api/metrics/{nf}")
def get_metrics(nf: str, window_seconds: int = 300):
    """Return metrics history for an NF over the last N seconds."""
    if nf not in NF_TYPES:
        return {"error": "unknown NF"}
    cutoff = time.time() - window_seconds
    history = [(ts, snap) for ts, snap in state.METRIC_HISTORY[nf] if ts >= cutoff]
    return {
        "nf": nf,
        "window_seconds": window_seconds,
        "samples": len(history),
        "history": [{"timestamp": ts, **snap} for ts, snap in history],
    }


@app.get("/api/metrics/{nf}/series/{metric_name}")
def get_metric_series(nf: str, metric_name: str, window_seconds: int = 300):
    """Time-series of a single metric. Useful for charts and ML."""
    if nf not in NF_TYPES:
        return {"error": "unknown NF"}
    cutoff = time.time() - window_seconds
    points = []
    for ts, snap in state.METRIC_HISTORY[nf]:
        if ts < cutoff:
            continue
        # Search counters, gauges, histogram avgs
        v = snap.get("counters", {}).get(metric_name)
        if v is None:
            v = snap.get("gauges", {}).get(metric_name)
        if v is None:
            h = snap.get("histograms", {}).get(metric_name)
            if h:
                v = h.get("avg")
        if v is not None:
            points.append({"t": ts, "v": v})
    return {"nf": nf, "metric": metric_name, "points": points}


@app.get("/api/logs")
def get_logs(
    nf: Optional[str] = None,
    level: Optional[str] = None,
    supi: Optional[str] = None,
    since: Optional[float] = None,
    limit: int = 500,
):
    """Unified log search across NFs."""
    out = []
    nfs = [nf] if nf else NF_TYPES
    for n in nfs:
        for log in state.LOGS[n]:
            if level and log.get("level") != level:
                continue
            if supi and log.get("supi") != supi:
                continue
            if since and log.get("timestamp", 0) < since:
                continue
            out.append(log)
    out.sort(key=lambda l: l.get("timestamp", 0), reverse=True)
    return {"logs": out[:limit], "total": len(out)}


@app.get("/api/traces")
def get_traces(trace_id: Optional[str] = None, limit: int = 100):
    """Get spans, optionally filtered by trace_id."""
    out = list(state.SPANS)
    if trace_id:
        out = [s for s in out if s.get("trace_id") == trace_id]
    out.sort(key=lambda s: s.get("start_time", 0), reverse=True)
    return {"spans": out[:limit]}


@app.get("/api/traces/recent")
def recent_traces(supi: Optional[str] = None, limit: int = 30):
    """List recent distinct traces (one summary per trace_id), optionally filtered by SUPI.
    Each entry has trace_id, start/end times, span count, NFs touched, status."""
    # Group spans by trace_id
    by_trace: dict[str, list] = {}
    for span in state.SPANS:
        tid = span.get("trace_id")
        if not tid:
            continue
        if supi:
            # Match supi via attributes or operation if it's recorded
            attrs = span.get("attributes") or {}
            if attrs.get("supi") != supi:
                continue
        by_trace.setdefault(tid, []).append(span)

    summaries = []
    for tid, spans in by_trace.items():
        spans_sorted = sorted(spans, key=lambda s: s.get("start_time", 0))
        first = spans_sorted[0]
        last = spans_sorted[-1]
        nfs = sorted(set(s.get("nf") for s in spans))
        # Find SUPI from any span attributes
        supi_found = None
        root_op = None
        for s in spans_sorted:
            a = s.get("attributes") or {}
            if a.get("supi") and not supi_found:
                supi_found = a.get("supi")
            # Root op is the one whose parent_span_id is None
            if not s.get("parent_span_id") and not root_op:
                root_op = s.get("operation")
        has_error = any(s.get("status") == "error" for s in spans)
        summaries.append({
            "trace_id": tid,
            "supi": supi_found,
            "operation": root_op or first.get("operation"),
            "started_at": first.get("start_time"),
            "ended_at": last.get("end_time"),
            "duration_ms": (last.get("end_time", 0) - first.get("start_time", 0)) * 1000,
            "span_count": len(spans),
            "nfs_touched": nfs,
            "status": "error" if has_error else "ok",
        })
    summaries.sort(key=lambda s: s.get("started_at", 0), reverse=True)
    return {"traces": summaries[:limit], "total": len(summaries)}


@app.get("/api/summary")
def system_summary():
    """High-level system summary for dashboard. Used by ML and LLM."""
    now = time.time()

    # Aggregate counters across NFs (last 60s)
    summary = {"timestamp": now, "nfs": {}}
    for nf in NF_TYPES:
        last = state.LAST_SEEN[nf]
        snap = state.METRIC_HISTORY[nf][-1][1] if state.METRIC_HISTORY[nf] else None
        nf_summary = {
            "up": last > 0 and (now - last) < SCRAPE_INTERVAL * 2,
            "last_seen_seconds_ago": (now - last) if last > 0 else None,
        }
        if snap:
            counters = snap.get("counters", {})
            histograms = snap.get("histograms", {})
            nf_summary.update({
                "request_count": sum(v for k, v in counters.items()
                                     if k.startswith("requests_total")),
                "error_count": sum(v for k, v in counters.items()
                                   if "fail" in k or "error" in k),
                "p99_latency_ms": next(
                    (h.get("p99") for k, h in histograms.items()
                     if k.startswith("request_duration_ms")),
                    None,
                ),
            })
            # Domain-specific counters
            for dom_metric in ["registrations_success_total",
                               "registrations_failed_total",
                               "auth_success_total",
                               "auth_confirm_failures_total",
                               "sessions_created_total",
                               "sessions_failed_total",
                               "active_ues",
                               "active_sessions",
                               # UPF KPIs
                               "active_bearers",
                               "dl_throughput_mbps",
                               "ul_throughput_mbps",
                               "packet_loss_pct",
                               "jitter_ms",
                               # PCF
                               "active_policies",
                               "policy_decisions_total"]:
                v = counters.get(dom_metric) or snap.get("gauges", {}).get(dom_metric)
                if v is not None:
                    nf_summary[dom_metric] = v

        summary["nfs"][nf] = nf_summary

    # Top errors in last 5 minutes
    cutoff = now - 300
    recent_errors = []
    for nf in NF_TYPES:
        for log in state.LOGS[nf]:
            if log.get("level") in ("error", "warn") and log.get("timestamp", 0) > cutoff:
                recent_errors.append(log)
    recent_errors.sort(key=lambda l: l.get("timestamp", 0), reverse=True)
    summary["recent_errors"] = recent_errors[:20]
    summary["total_errors_5m"] = len(recent_errors)

    return summary
