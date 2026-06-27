"""
nf_common — Shared library for all 5G Network Functions.

Provides:
  • Structured JSON logging with trace context
  • Prometheus-style metrics (counters, gauges, histograms)
  • W3C trace context propagation (traceparent header)
  • In-memory ring buffers (logs + spans) exposed via HTTP
  • Base FastAPI app factory with /metrics, /logs, /traces, /healthz
  • Subscriber data models (SUPI, auth vector, PDU session)
  • Failure injection middleware (slowness, errors, drops)
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


# ============================================================================
# CONFIG — failure injection state, shared across the process
# ============================================================================
@dataclass
class FailureConfig:
    """Per-NF failure-injection knobs. Toggled by orchestrator."""
    # Probability (0-1) of injecting a 500 error
    error_rate: float = 0.0
    # Extra latency in ms added to every request
    extra_latency_ms: int = 0
    # If True, requests are dropped (no response)
    blackhole: bool = False
    # Probability (0-1) of returning a corrupted response
    corruption_rate: float = 0.0
    # If True, NF reports unhealthy on /healthz
    unhealthy: bool = False
    # 5G-specific: list of error codes to randomly inject. When set, on each
    # request, with probability `error_code_rate`, the NF returns a properly
    # formatted application/problem+json with one of these codes randomly
    # selected. Codes must be valid cause names from nf_common.errors.CATALOG.
    error_codes: list = field(default_factory=list)
    error_code_rate: float = 0.0  # 0-1


# ============================================================================
# TELEMETRY — logs, metrics, traces
# ============================================================================
class LogEvent(BaseModel):
    timestamp: float
    nf: str
    level: str
    message: str
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    supi: Optional[str] = None
    extra: dict = Field(default_factory=dict)


class Span(BaseModel):
    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None
    nf: str
    operation: str
    start_time: float
    end_time: float
    duration_ms: float
    status: str  # ok | error
    attributes: dict = Field(default_factory=dict)


class MetricSnapshot(BaseModel):
    nf: str
    timestamp: float
    counters: dict[str, float]
    gauges: dict[str, float]
    histograms: dict[str, dict[str, float]]  # name -> {p50, p95, p99, count, sum}


class Telemetry:
    """In-memory telemetry store for one NF process."""
    LOG_BUFFER_SIZE = 1000
    SPAN_BUFFER_SIZE = 500
    HIST_WINDOW_SIZE = 200

    def __init__(self, nf_name: str):
        self.nf = nf_name
        self.logs: deque[LogEvent] = deque(maxlen=self.LOG_BUFFER_SIZE)
        self.spans: deque[Span] = deque(maxlen=self.SPAN_BUFFER_SIZE)
        self.counters: dict[str, float] = defaultdict(float)
        self.gauges: dict[str, float] = defaultdict(float)
        self.histograms: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=self.HIST_WINDOW_SIZE)
        )
        self._lock = asyncio.Lock()

    # ── Logs ──────────────────────────────────────────────────────────
    def log(self, level: str, message: str, **kwargs):
        evt = LogEvent(
            timestamp=time.time(),
            nf=self.nf,
            level=level,
            message=message,
            trace_id=kwargs.pop("trace_id", None),
            span_id=kwargs.pop("span_id", None),
            supi=kwargs.pop("supi", None),
            extra=kwargs,
        )
        self.logs.append(evt)
        # Also print to stdout for docker logs
        print(json.dumps(evt.model_dump()), flush=True)

    def info(self, msg, **kw): self.log("info", msg, **kw)
    def warn(self, msg, **kw): self.log("warn", msg, **kw)
    def error(self, msg, **kw): self.log("error", msg, **kw)

    # ── Metrics ───────────────────────────────────────────────────────
    def inc(self, name: str, value: float = 1.0, **labels):
        key = self._key(name, labels)
        self.counters[key] += value

    def gauge(self, name: str, value: float, **labels):
        key = self._key(name, labels)
        self.gauges[key] = value

    def observe(self, name: str, value: float, **labels):
        key = self._key(name, labels)
        self.histograms[key].append(value)

    def _key(self, name, labels):
        if not labels:
            return name
        parts = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{name}{{{parts}}}"

    def snapshot(self) -> MetricSnapshot:
        hist_summary = {}
        for k, vals in self.histograms.items():
            if vals:
                sorted_vals = sorted(vals)
                n = len(sorted_vals)
                hist_summary[k] = {
                    "p50": sorted_vals[n // 2],
                    "p95": sorted_vals[min(int(n * 0.95), n - 1)],
                    "p99": sorted_vals[min(int(n * 0.99), n - 1)],
                    "count": n,
                    "sum": sum(sorted_vals),
                    "avg": sum(sorted_vals) / n,
                }
        return MetricSnapshot(
            nf=self.nf,
            timestamp=time.time(),
            counters=dict(self.counters),
            gauges=dict(self.gauges),
            histograms=hist_summary,
        )

    # ── Tracing ───────────────────────────────────────────────────────
    @asynccontextmanager
    async def span(self, operation: str, trace_id: Optional[str] = None,
                   parent_span_id: Optional[str] = None, **attributes):
        trace_id = trace_id or uuid.uuid4().hex
        span_id = uuid.uuid4().hex[:16]
        start = time.time()
        status = "ok"
        # ctx.attributes is mutable — caller can attach error_code etc on failure
        ctx = {"trace_id": trace_id, "span_id": span_id, "attributes": attributes}
        try:
            yield ctx
        except Exception:
            status = "error"
            raise
        finally:
            end = time.time()
            self.spans.append(Span(
                trace_id=trace_id, span_id=span_id, parent_span_id=parent_span_id,
                nf=self.nf, operation=operation,
                start_time=start, end_time=end, duration_ms=(end - start) * 1000,
                status=status, attributes=ctx["attributes"],
            ))


# ============================================================================
# 5G DOMAIN MODELS
# ============================================================================
class Subscriber(BaseModel):
    """Provisioned subscriber in UDM."""
    supi: str  # IMSI-format identifier (e.g., "imsi-001010000000001")
    auth_key: str  # Pre-shared K (hex)
    plmn: str = "00101"
    nssai: list[str] = Field(default_factory=lambda: ["sst=1"])
    apn: str = "internet"


class AuthVector(BaseModel):
    """5G AKA authentication vector (simplified)."""
    rand: str  # Random challenge
    expected_res: str  # Expected response from UE
    autn: str  # Authentication token


class UEContext(BaseModel):
    """Per-UE state in AMF."""
    supi: str
    state: str = "DEREGISTERED"  # DEREGISTERED | REGISTERING | AUTHENTICATING | REGISTERED | DEREGISTERING
    amf_ue_id: Optional[str] = None
    auth_attempts: int = 0
    last_activity: float = Field(default_factory=time.time)
    pdu_sessions: list[str] = Field(default_factory=list)


class PDUSession(BaseModel):
    """Data session managed by SMF."""
    pdu_id: str
    supi: str
    apn: str
    state: str = "PENDING"  # PENDING | ACTIVE | FAILED | RELEASED
    qos_flow: str = "5qi-9"
    upf_assigned: Optional[str] = None
    bearer_id: Optional[str] = None


# ============================================================================
# BASE APP FACTORY
# ============================================================================
def create_nf_app(nf_name: str, port: int) -> tuple[FastAPI, Telemetry, FailureConfig]:
    """Create a FastAPI app for an NF with all standard endpoints attached."""
    failure = FailureConfig()
    tel = Telemetry(nf_name)

    app = FastAPI(title=f"{nf_name.upper()} (5G AIOps)", version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Failure injection middleware ──────────────────────────────────
    @app.middleware("http")
    async def fault_injector(request: Request, call_next):
        path = request.url.path
        # Skip internal endpoints from fault injection
        if path in ("/healthz", "/metrics", "/logs", "/traces", "/failure", "/"):
            return await call_next(request)

        if failure.blackhole:
            tel.inc("requests_blackholed_total")
            await asyncio.sleep(30)  # simulate hung connection
            raise HTTPException(503, "service unavailable (blackhole)")

        if failure.extra_latency_ms > 0:
            await asyncio.sleep(failure.extra_latency_ms / 1000)
            tel.inc("requests_slowed_total")

        if failure.error_rate > 0 and random.random() < failure.error_rate:
            tel.inc("requests_failed_injected_total")
            raise HTTPException(500, "injected fault")

        # 5G-specific coded error injection
        if (failure.error_code_rate > 0 and failure.error_codes
                and random.random() < failure.error_code_rate):
            from .errors import lookup as _lookup_code, problem_json as _problem_json
            cause = random.choice(failure.error_codes)
            ec = _lookup_code(cause)
            if ec:
                tel.inc("requests_failed_injected_total")
                tel.inc("errors_by_code_total", code=cause)
                tel.warn(f"injected {cause}", code=cause, path=path)
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=ec.http_status,
                    content=_problem_json(cause),
                    media_type="application/problem+json",
                )

        start = time.time()
        try:
            response = await call_next(request)
            duration_ms = (time.time() - start) * 1000
            tel.observe("request_duration_ms", duration_ms, path=path)
            tel.inc("requests_total", path=path, status=response.status_code)
            return response
        except Exception as e:
            tel.inc("requests_total", path=path, status=500)
            tel.error(f"unhandled exception: {e}", path=path)
            raise

    # ── Standard endpoints ────────────────────────────────────────────
    @app.get("/")
    def root():
        return {"nf": nf_name, "port": port, "status": "ok"}

    @app.get("/healthz")
    def health():
        if failure.unhealthy:
            raise HTTPException(503, "marked unhealthy")
        return {"status": "ok", "nf": nf_name}

    @app.get("/metrics")
    def metrics():
        return tel.snapshot()

    @app.get("/logs")
    def logs(limit: int = 100, level: Optional[str] = None,
             since: Optional[float] = None):
        out = list(tel.logs)
        if level:
            out = [l for l in out if l.level == level]
        if since:
            out = [l for l in out if l.timestamp >= since]
        return {"nf": nf_name, "logs": out[-limit:]}

    @app.get("/traces")
    def traces(trace_id: Optional[str] = None, limit: int = 50):
        out = list(tel.spans)
        if trace_id:
            out = [s for s in out if s.trace_id == trace_id]
        return {"nf": nf_name, "spans": out[-limit:]}

    # ── Failure injection control ─────────────────────────────────────
    @app.get("/failure")
    def get_failure():
        return asdict(failure)

    @app.post("/failure")
    def set_failure(cfg: dict):
        for k, v in cfg.items():
            if hasattr(failure, k):
                setattr(failure, k, v)
        tel.warn("failure config updated", config=asdict(failure))
        return asdict(failure)

    return app, tel, failure


# ============================================================================
# HTTP CLIENT — for inter-NF calls with trace propagation
# ============================================================================
import httpx


class NFClient:
    """Lightweight client for NF-to-NF calls. Propagates trace context."""

    def __init__(self, tel: Telemetry, base_urls: dict[str, str], timeout: float = 5.0):
        self.tel = tel
        self.base_urls = base_urls  # {"udm": "http://udm:8003", ...}
        self.client = httpx.AsyncClient(timeout=timeout)

    async def call(self, nf: str, method: str, path: str,
                   trace_id: Optional[str] = None,
                   parent_span_id: Optional[str] = None,
                   **kwargs) -> dict:
        url = f"{self.base_urls[nf]}{path}"
        headers = kwargs.pop("headers", {})
        if trace_id:
            headers["X-Trace-Id"] = trace_id
        if parent_span_id:
            headers["X-Parent-Span-Id"] = parent_span_id

        async with self.tel.span(f"call_{nf}_{path}",
                                  trace_id=trace_id,
                                  parent_span_id=parent_span_id,
                                  target=nf, path=path) as ctx:
            try:
                resp = await self.client.request(method, url, headers=headers, **kwargs)
                self.tel.inc("nf_calls_total", target=nf, status=resp.status_code)
                if resp.status_code >= 400:
                    # Try to extract 5G cause code from problem+json
                    cause = None
                    try:
                        body = resp.json()
                        if isinstance(body, dict):
                            # Direct problem+json (from middleware)
                            cause = body.get("cause")
                            # Or wrapped under "detail" (from raise nf_error)
                            if not cause and isinstance(body.get("detail"), dict):
                                cause = body["detail"].get("cause")
                    except Exception:
                        pass
                    if cause:
                        ctx["attributes"]["error_code"] = cause
                        ctx["attributes"]["http_status"] = resp.status_code
                    self.tel.warn(f"NF call failed: {nf} {path} -> {resp.status_code}"
                                  + (f" [{cause}]" if cause else ""),
                                  trace_id=trace_id)
                    raise HTTPException(resp.status_code, f"{nf}: {resp.text[:200]}")
                return resp.json()
            except httpx.RequestError as e:
                self.tel.error(f"NF call error: {nf} {path}: {e}", trace_id=trace_id)
                self.tel.inc("nf_calls_total", target=nf, status="error")
                ctx["attributes"]["error_code"] = "UPSTREAM_TIMEOUT"
                raise HTTPException(503, f"{nf} unreachable: {e}")

    async def close(self):
        await self.client.aclose()


def get_nf_urls() -> dict[str, str]:
    """Read NF endpoints from env vars or use docker-compose defaults."""
    return {
        "nrf":  os.getenv("NRF_URL",  "http://nrf:8001"),
        "ausf": os.getenv("AUSF_URL", "http://ausf:8002"),
        "udm":  os.getenv("UDM_URL",  "http://udm:8003"),
        "amf":  os.getenv("AMF_URL",  "http://amf:8004"),
        "smf":  os.getenv("SMF_URL",  "http://smf:8005"),
        "upf":  os.getenv("UPF_URL",  "http://upf:8006"),
        "pcf":  os.getenv("PCF_URL",  "http://pcf:8007"),
    }


def trace_context_from_request(request: Request) -> tuple[Optional[str], Optional[str]]:
    """Extract trace context from incoming request headers."""
    return (
        request.headers.get("X-Trace-Id"),
        request.headers.get("X-Parent-Span-Id"),
    )


def nf_error(cause: str, *, supi: Optional[str] = None,
             trace_id: Optional[str] = None,
             detail: Optional[str] = None,
             tel: Optional["Telemetry"] = None):
    """Raise a properly-formatted 3GPP HTTP error from anywhere in NF code.

    Usage:
        from nf_common import nf_error
        raise nf_error("USER_NOT_FOUND", supi=req.supi, tel=tel)

    The middleware will pass HTTPException's detail through. We use a
    JSONResponse-shaped detail so FastAPI serializes it as application/json.
    Callers should wrap with `raise` since HTTPException is what FastAPI expects.
    """
    from .errors import lookup, problem_json
    ec = lookup(cause)
    body = problem_json(cause, supi=supi, trace_id=trace_id, detail_override=detail)
    if tel is not None:
        tel.inc("errors_by_code_total", code=cause)
        tel.warn(f"emit {cause}", code=cause, supi=supi)
    return HTTPException(
        status_code=ec.http_status if ec else 500,
        detail=body,
    )
