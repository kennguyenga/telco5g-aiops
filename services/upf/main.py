"""
UPF — User Plane Function

Simulates the 5G data plane. Doesn't move actual packets; instead generates
realistic KPIs (throughput, packet loss, jitter, bearer counts) that change
based on:
  • Number of active bearers (= sessions established by SMF)
  • Failure injection (packet loss, latency)
  • Background load fluctuation

KPIs are exposed via standard /metrics so the collector picks them up.
A background task updates them every 1s so the ML engine sees realistic
time-series.
"""
import asyncio
import sys, os
import random
import time
from contextlib import asynccontextmanager
from fastapi import HTTPException, Request
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nf_common import create_nf_app, trace_context_from_request


# ── Bearer state (one per PDU session served) ────────────────────────
class Bearer(BaseModel):
    bearer_id: str
    pdu_id: str
    supi: str
    qos_5qi: int = 9        # 5QI 1=voice, 5=video, 9=internet
    dl_mbps: float = 0.0
    ul_mbps: float = 0.0
    state: str = "ACTIVE"   # ACTIVE | RELEASED


BEARERS: dict[str, Bearer] = {}


# ── KPI engine state ─────────────────────────────────────────────────
class KPIState:
    """Aggregated KPIs that the metric engine updates every tick."""
    total_dl_mbps: float = 0.0
    total_ul_mbps: float = 0.0
    packet_loss_pct: float = 0.0
    jitter_ms: float = 0.0
    n3_throughput_mbps: float = 0.0
    n6_throughput_mbps: float = 0.0
    bearers_5qi_1: int = 0   # voice
    bearers_5qi_5: int = 0   # video
    bearers_5qi_9: int = 0   # internet


kpi = KPIState()


# ── Health KPI background task ───────────────────────────────────────
@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(_kpi_loop())
    yield
    task.cancel()


async def _kpi_loop():
    """Update KPIs every 1s based on bearer count + failure injection."""
    while True:
        try:
            active = [b for b in BEARERS.values() if b.state == "ACTIVE"]
            n_active = len(active)

            # Per-bearer throughput by 5QI (rough approximation)
            qos_rates = {1: 0.1, 5: 5.0, 9: 2.0}  # Mbps DL per bearer
            for b in active:
                base = qos_rates.get(b.qos_5qi, 1.0)
                # Add jitter ±20%
                b.dl_mbps = base * random.uniform(0.8, 1.2)
                b.ul_mbps = b.dl_mbps * 0.3  # UL typically lower

            kpi.total_dl_mbps = sum(b.dl_mbps for b in active)
            kpi.total_ul_mbps = sum(b.ul_mbps for b in active)

            # Packet loss climbs with load + failure injection
            base_loss = min(2.0, n_active * 0.01)  # 0.01% per bearer
            # Failure injection adds to it
            inject_loss = failure.corruption_rate * 100  # % loss from corruption knob
            inject_latency_loss = min(5.0, failure.extra_latency_ms / 200)  # latency → small loss
            kpi.packet_loss_pct = round(base_loss + inject_loss + inject_latency_loss, 3)

            # Jitter rises with packet loss
            kpi.jitter_ms = round(2.0 + kpi.packet_loss_pct * 5 + random.uniform(0, 1.5), 2)

            # N3 (RAN→UPF) and N6 (UPF→Internet) — same throughput in steady state
            kpi.n3_throughput_mbps = round(kpi.total_dl_mbps, 2)
            kpi.n6_throughput_mbps = round(kpi.total_dl_mbps * 0.95, 2)  # ~5% headers

            # Counts by 5QI
            kpi.bearers_5qi_1 = sum(1 for b in active if b.qos_5qi == 1)
            kpi.bearers_5qi_5 = sum(1 for b in active if b.qos_5qi == 5)
            kpi.bearers_5qi_9 = sum(1 for b in active if b.qos_5qi == 9)

            # Push to metrics so collector scrapes them
            tel.gauge("active_bearers", n_active)
            tel.gauge("dl_throughput_mbps", kpi.total_dl_mbps)
            tel.gauge("ul_throughput_mbps", kpi.total_ul_mbps)
            tel.gauge("packet_loss_pct", kpi.packet_loss_pct)
            tel.gauge("jitter_ms", kpi.jitter_ms)
            tel.gauge("n3_throughput_mbps", kpi.n3_throughput_mbps)
            tel.gauge("n6_throughput_mbps", kpi.n6_throughput_mbps)
            tel.gauge("bearers_5qi_1_voice", kpi.bearers_5qi_1)
            tel.gauge("bearers_5qi_5_video", kpi.bearers_5qi_5)
            tel.gauge("bearers_5qi_9_internet", kpi.bearers_5qi_9)
        except Exception as e:
            tel.error(f"kpi loop error: {e}")
        await asyncio.sleep(1.0)


app, tel, failure = create_nf_app("upf", 8006)
# Attach lifespan after app creation
app.router.lifespan_context = lifespan


class BearerCreateRequest(BaseModel):
    pdu_id: str
    supi: str
    qos_5qi: int = 9


@app.post("/bearers")
async def create_bearer(req: BearerCreateRequest, request: Request):
    """Called by SMF to install a forwarding rule for a PDU session."""
    trace_id, parent = trace_context_from_request(request)
    async with tel.span("create_bearer", trace_id=trace_id,
                        parent_span_id=parent, supi=req.supi):
        import secrets
        bearer_id = f"brr-{secrets.token_hex(4)}"
        b = Bearer(bearer_id=bearer_id, pdu_id=req.pdu_id,
                   supi=req.supi, qos_5qi=req.qos_5qi)
        BEARERS[bearer_id] = b
        tel.info("bearer installed", supi=req.supi, pdu_id=req.pdu_id,
                 qos_5qi=req.qos_5qi)
        tel.inc("bearers_installed_total")
        return b


@app.delete("/bearers/{bearer_id}")
async def release_bearer(bearer_id: str):
    b = BEARERS.get(bearer_id)
    if not b:
        raise HTTPException(404, "bearer not found")
    b.state = "RELEASED"
    tel.info("bearer released", bearer_id=bearer_id, supi=b.supi)
    tel.inc("bearers_released_total")
    return {"status": "released", "bearer_id": bearer_id}


@app.get("/bearers")
async def list_bearers(state: str = "ACTIVE"):
    return {
        "total": len(BEARERS),
        "active": sum(1 for b in BEARERS.values() if b.state == "ACTIVE"),
        "bearers": [b for b in BEARERS.values() if b.state == state][:200],
    }


@app.get("/kpi")
async def get_kpi():
    """Live KPI snapshot — UI can poll this for real-time data plane charts."""
    return {
        "timestamp": time.time(),
        "active_bearers": sum(1 for b in BEARERS.values() if b.state == "ACTIVE"),
        "dl_mbps": kpi.total_dl_mbps,
        "ul_mbps": kpi.total_ul_mbps,
        "packet_loss_pct": kpi.packet_loss_pct,
        "jitter_ms": kpi.jitter_ms,
        "n3_throughput_mbps": kpi.n3_throughput_mbps,
        "n6_throughput_mbps": kpi.n6_throughput_mbps,
        "bearers_by_qos": {
            "5qi_1_voice": kpi.bearers_5qi_1,
            "5qi_5_video": kpi.bearers_5qi_5,
            "5qi_9_internet": kpi.bearers_5qi_9,
        },
    }
