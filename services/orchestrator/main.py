"""
Orchestrator — subscriber simulator + failure injection control plane.

Exposed APIs:
  POST /api/subscribers/attach      — register N UEs
  POST /api/subscribers/detach      — deregister UEs
  POST /api/subscribers/load        — generate sustained load pattern
  POST /api/subscribers/random-action — random churn over time
  POST /api/failures/inject         — inject failure into specific NF
  GET  /api/failures/state          — current failure config of all NFs
  POST /api/failures/clear          — clear all injected failures
  GET  /api/topology                — return NF topology + current health

Failure catalog:
  Service-level: nf_crash, nf_slowdown, nf_error_rate, nf_unhealthy
  Network-level: latency_spike, packet_loss (via slowdown + error_rate proxy)
  Auth/security: invalid_supi, aka_mismatch, expired_credential
"""
import asyncio
import os
import sys
import time
import random
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from nf_common import get_nf_urls

NF_URLS = get_nf_urls()
AMF_URL = NF_URLS["amf"]

app = FastAPI(title="5G AIOps Orchestrator")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# Track simulator state
class SimState:
    active_load_task: Optional[asyncio.Task] = None
    last_load_config: Optional[dict] = None
    attached_supis: set[str] = set()


sim = SimState()


# ============================================================================
# SUBSCRIBER LIFECYCLE
# ============================================================================
class AttachRequest(BaseModel):
    count: int = 1
    start_index: int = 1
    parallelism: int = 5


class DetachRequest(BaseModel):
    supis: Optional[list[str]] = None  # None = detach all attached
    count: Optional[int] = None  # detach first N


def _supi(i: int) -> str:
    return f"imsi-00101{i:010d}"


def _key(i: int) -> str:
    return f"{i:032x}"


async def _attach_one(client: httpx.AsyncClient, supi: str, key: str) -> dict:
    """Run full registration + session establishment for one UE."""
    started = time.time()
    try:
        r = await client.post(
            f"{AMF_URL}/ue/register",
            json={"supi": supi, "plmn": "00101", "ue_auth_key": key},
            timeout=10,
        )
        if r.status_code != 200:
            return {"supi": supi, "status": "failed", "stage": "register",
                    "code": r.status_code, "detail": r.text[:200],
                    "duration_ms": (time.time() - started) * 1000}

        r2 = await client.post(
            f"{AMF_URL}/ue/session",
            json={"supi": supi, "apn": "internet"},
            timeout=10,
        )
        if r2.status_code != 200:
            return {"supi": supi, "status": "registered_no_session", "stage": "session",
                    "code": r2.status_code, "detail": r2.text[:200],
                    "duration_ms": (time.time() - started) * 1000}

        sim.attached_supis.add(supi)
        return {"supi": supi, "status": "attached",
                "duration_ms": (time.time() - started) * 1000}
    except (httpx.RequestError, httpx.TimeoutException) as e:
        return {"supi": supi, "status": "error", "detail": str(e),
                "duration_ms": (time.time() - started) * 1000}


@app.post("/api/subscribers/attach")
async def attach(req: AttachRequest):
    """Attach N subscribers in parallel batches."""
    sem = asyncio.Semaphore(req.parallelism)

    async def bounded(supi, key):
        async with sem:
            return await _attach_one(client, supi, key)

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[
            bounded(_supi(i), _key(i))
            for i in range(req.start_index, req.start_index + req.count)
        ])

    summary = {
        "requested": req.count,
        "attached": sum(1 for r in results if r["status"] == "attached"),
        "failed": sum(1 for r in results if r["status"] in ("failed", "error")),
        "no_session": sum(1 for r in results if r["status"] == "registered_no_session"),
        "avg_duration_ms": sum(r["duration_ms"] for r in results) / max(1, len(results)),
        "results": results[:10],  # truncate
    }
    return summary


@app.post("/api/subscribers/detach")
async def detach(req: DetachRequest):
    """Detach subscribers."""
    if req.supis:
        targets = req.supis
    else:
        targets = list(sim.attached_supis)
        if req.count:
            targets = targets[:req.count]

    async def detach_one(client, supi):
        try:
            r = await client.post(f"{AMF_URL}/ue/deregister",
                                   json={"supi": supi}, timeout=10)
            sim.attached_supis.discard(supi)
            return {"supi": supi, "status": "detached" if r.status_code == 200 else "failed"}
        except Exception as e:
            return {"supi": supi, "status": "error", "detail": str(e)}

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[detach_one(client, s) for s in targets])

    return {
        "requested": len(targets),
        "detached": sum(1 for r in results if r["status"] == "detached"),
        "failed": sum(1 for r in results if r["status"] != "detached"),
        "results": results[:10],
    }


# ============================================================================
# CONTINUOUS LOAD GENERATOR
# ============================================================================
class LoadRequest(BaseModel):
    attach_per_second: int = 5
    detach_per_second: int = 2
    duration_seconds: int = 60
    max_active: int = 200


@app.post("/api/subscribers/load")
async def start_load(req: LoadRequest):
    """Run sustained churn for N seconds."""
    if sim.active_load_task and not sim.active_load_task.done():
        sim.active_load_task.cancel()

    sim.last_load_config = req.model_dump()
    sim.active_load_task = asyncio.create_task(_load_runner(req))
    return {"status": "started", "config": req.model_dump()}


async def _load_runner(cfg: LoadRequest):
    """Background load generator."""
    end_time = time.time() + cfg.duration_seconds
    next_supi = 1
    async with httpx.AsyncClient() as client:
        while time.time() < end_time:
            tick_start = time.time()

            # Spawn attaches
            attach_count = min(
                cfg.attach_per_second,
                cfg.max_active - len(sim.attached_supis),
            )
            attach_tasks = []
            for _ in range(max(0, attach_count)):
                while _supi(next_supi) in sim.attached_supis:
                    next_supi = (next_supi % 1000) + 1
                attach_tasks.append(_attach_one(client, _supi(next_supi), _key(next_supi)))
                next_supi = (next_supi % 1000) + 1

            # Spawn detaches
            detach_targets = random.sample(
                list(sim.attached_supis),
                min(cfg.detach_per_second, len(sim.attached_supis)),
            ) if sim.attached_supis else []
            async def _detach(supi):
                try:
                    await client.post(f"{AMF_URL}/ue/deregister",
                                       json={"supi": supi}, timeout=5)
                except Exception:
                    pass
                sim.attached_supis.discard(supi)

            await asyncio.gather(*attach_tasks, *(_detach(s) for s in detach_targets),
                                  return_exceptions=True)

            # Sleep to maintain ~1Hz tick
            elapsed = time.time() - tick_start
            await asyncio.sleep(max(0, 1.0 - elapsed))


@app.post("/api/subscribers/load/stop")
async def stop_load():
    if sim.active_load_task and not sim.active_load_task.done():
        sim.active_load_task.cancel()
        return {"status": "stopped"}
    return {"status": "not_running"}


@app.get("/api/subscribers/state")
async def subscriber_state():
    return {
        "attached_count": len(sim.attached_supis),
        "load_running": sim.active_load_task is not None and not sim.active_load_task.done(),
        "last_load_config": sim.last_load_config,
    }


# ============================================================================
# FAILURE INJECTION
# ============================================================================
class FailureInjectRequest(BaseModel):
    nf: str  # "amf", "smf", ...
    failure_type: str  # see below
    intensity: float = 1.0  # 0-1 for probabilistic, or scaled value
    error_codes: Optional[list[str]] = None  # for "coded_error" type


FAILURE_PRESETS = {
    "nf_crash": lambda i, codes=None: {"unhealthy": True, "blackhole": True},
    "nf_slowdown": lambda i, codes=None: {"extra_latency_ms": int(i * 2000)},
    "nf_error_rate": lambda i, codes=None: {"error_rate": min(1.0, i)},
    "nf_unhealthy": lambda i, codes=None: {"unhealthy": True},
    "packet_corruption": lambda i, codes=None: {"corruption_rate": min(1.0, i)},
    "intermittent": lambda i, codes=None: {"error_rate": min(1.0, i * 0.3)},
    # New: emit specific 5G error codes (proper application/problem+json responses)
    "coded_error": lambda i, codes=None: {
        "error_codes": list(codes or []),
        "error_code_rate": min(1.0, i),
    },
}


@app.post("/api/failures/inject")
async def inject_failure(req: FailureInjectRequest):
    """Inject a failure into a specific NF."""
    if req.nf not in NF_URLS:
        raise HTTPException(400, f"unknown NF: {req.nf}")
    if req.failure_type not in FAILURE_PRESETS:
        raise HTTPException(400, f"unknown failure type: {req.failure_type}. "
                                  f"Available: {list(FAILURE_PRESETS.keys())}")
    if req.failure_type == "coded_error" and not req.error_codes:
        raise HTTPException(400, "coded_error requires error_codes list")

    cfg = FAILURE_PRESETS[req.failure_type](req.intensity, req.error_codes)

    async with httpx.AsyncClient(timeout=3) as client:
        try:
            r = await client.post(f"{NF_URLS[req.nf]}/failure", json=cfg)
            return {
                "status": "injected",
                "nf": req.nf,
                "failure_type": req.failure_type,
                "intensity": req.intensity,
                "error_codes": req.error_codes,
                "applied_config": r.json(),
            }
        except httpx.RequestError as e:
            raise HTTPException(503, f"could not reach {req.nf}: {e}")


@app.post("/api/failures/clear")
async def clear_failures(nf: Optional[str] = None):
    """Clear failures on one NF or all."""
    targets = [nf] if nf else list(NF_URLS.keys())
    cleared_cfg = {
        "error_rate": 0.0, "extra_latency_ms": 0, "blackhole": False,
        "corruption_rate": 0.0, "unhealthy": False,
        "error_codes": [], "error_code_rate": 0.0,
    }

    async def clear_one(client, nf_name):
        try:
            r = await client.post(f"{NF_URLS[nf_name]}/failure", json=cleared_cfg, timeout=3)
            return {"nf": nf_name, "status": "cleared"}
        except Exception as e:
            return {"nf": nf_name, "status": "error", "detail": str(e)}

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[clear_one(client, n) for n in targets])
    return {"results": results}


@app.get("/api/failures/state")
async def failures_state():
    """Read current failure config from each NF."""
    async def get_one(client, nf_name):
        try:
            r = await client.get(f"{NF_URLS[nf_name]}/failure", timeout=2)
            return {"nf": nf_name, "config": r.json(), "reachable": True}
        except Exception:
            return {"nf": nf_name, "reachable": False}

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[get_one(client, n) for n in NF_URLS])
    return {"nfs": results}


# ============================================================================
# CALL FLOW TRACING — for the subscriber lifecycle visualizer
# ============================================================================
import uuid


@app.get("/api/subscribers/state-summary")
async def proxy_subscriber_state_summary():
    """Proxy to UDM /subscribers/state/summary (UDM is not directly exposed)."""
    udm_url = NF_URLS.get("udm", "http://udm:8003")
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(f"{udm_url}/subscribers/state/summary", timeout=10)
            return r.json() if r.status_code == 200 else {"error": r.text[:300]}
        except Exception as e:
            raise HTTPException(503, f"UDM unreachable: {e}")


class SetSubscriberStateRequest(BaseModel):
    state: str
    count: Optional[int] = None
    supis: Optional[list[str]] = None
    reason: Optional[str] = None


@app.post("/api/subscribers/set-state")
async def proxy_set_subscriber_state(req: SetSubscriberStateRequest):
    """Bulk-set subscriber state via UDM."""
    udm_url = NF_URLS.get("udm", "http://udm:8003")
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(
                f"{udm_url}/subscribers/state/bulk",
                json=req.model_dump(exclude_none=True),
                timeout=15,
            )
            return r.json() if r.status_code == 200 else {"error": r.text[:300]}
        except Exception as e:
            raise HTTPException(503, f"UDM unreachable: {e}")


@app.post("/api/subscribers/reset-state")
async def proxy_reset_subscribers():
    """Reset every subscriber to ACTIVE."""
    udm_url = NF_URLS.get("udm", "http://udm:8003")
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(f"{udm_url}/subscribers/state/reset", timeout=15)
            return r.json() if r.status_code == 200 else {"error": r.text[:300]}
        except Exception as e:
            raise HTTPException(503, f"UDM unreachable: {e}")


class TracedFlowRequest(BaseModel):
    supi: Optional[str] = None       # If None, picks a random unattached SUPI
    flow: str = "attach"             # "attach" | "detach" | "attach_and_session"
    apn: str = "internet"


@app.post("/api/callflow/trace")
async def trace_callflow(req: TracedFlowRequest):
    """
    Run an attach or detach with a known trace_id, then return both the
    trace_id and the spans collected so far. The UI uses this to render
    a sequence diagram of the inter-NF messaging.
    """
    trace_id = uuid.uuid4().hex

    # Pick SUPI if not provided
    if req.supi:
        supi = req.supi
        # Derive the matching auth key from imsi-001010000000XXX → XXX
        try:
            i = int(supi.replace("imsi-00101", ""))
        except ValueError:
            raise HTTPException(400, f"invalid SUPI format: {supi}")
    else:
        # Random SUPI in the provisioned range
        i = random.randint(1, 1000)
        supi = f"imsi-00101{i:010d}"
    key = f"{i:032x}"

    started_at = time.time()
    error = None

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            if req.flow == "attach" or req.flow == "attach_and_session":
                r = await client.post(
                    f"{AMF_URL}/ue/register",
                    json={"supi": supi, "plmn": "00101", "ue_auth_key": key},
                    headers={"X-Trace-Id": trace_id},
                )
                attach_status = r.status_code
                attach_body = r.json() if r.status_code == 200 else {"error": r.text[:300]}
                sim.attached_supis.add(supi) if r.status_code == 200 else None

                if req.flow == "attach_and_session" and r.status_code == 200:
                    r2 = await client.post(
                        f"{AMF_URL}/ue/session",
                        json={"supi": supi, "apn": req.apn},
                        headers={"X-Trace-Id": trace_id},
                    )
                    session_status = r2.status_code
                    session_body = r2.json() if r2.status_code == 200 else {"error": r2.text[:300]}
                else:
                    session_status = None
                    session_body = None

            elif req.flow == "detach":
                r = await client.post(
                    f"{AMF_URL}/ue/deregister",
                    json={"supi": supi},
                    headers={"X-Trace-Id": trace_id},
                )
                attach_status = r.status_code
                attach_body = r.json() if r.status_code == 200 else {"error": r.text[:300]}
                sim.attached_supis.discard(supi) if r.status_code == 200 else None
                session_status = None
                session_body = None

            else:
                raise HTTPException(400, f"unknown flow: {req.flow}")
        except (httpx.RequestError, httpx.TimeoutException) as e:
            error = str(e)
            attach_status = None
            attach_body = None
            session_status = None
            session_body = None

    # Wait briefly for collector to scrape (collector polls every 5s).
    # We need to give it a chance to see the spans we just generated.
    await asyncio.sleep(1.0)

    return {
        "trace_id": trace_id,
        "supi": supi,
        "flow": req.flow,
        "started_at": started_at,
        "duration_ms": (time.time() - started_at) * 1000,
        "attach_status": attach_status,
        "attach_body": attach_body,
        "session_status": session_status,
        "session_body": session_body,
        "error": error,
    }


@app.get("/api/topology")
async def topology():
    """Return NF topology + connection graph + current health."""
    edges = [
        ("amf", "ausf"),
        ("ausf", "udm"),
        ("amf", "udm"),
        ("amf", "smf"),
        ("amf", "nrf"),
        ("smf", "nrf"),
        ("ausf", "nrf"),
        ("udm", "nrf"),
        ("smf", "pcf"),  # SMF queries PCF for policy
        ("smf", "upf"),  # SMF installs bearers in UPF
        ("pcf", "nrf"),  # PCF discovers via NRF
        ("upf", "nrf"),  # UPF discovers via NRF
    ]

    async def health_one(client, nf):
        try:
            r = await client.get(f"{NF_URLS[nf]}/healthz", timeout=2)
            return r.status_code == 200
        except Exception:
            return False

    async with httpx.AsyncClient() as client:
        health_results = await asyncio.gather(
            *[health_one(client, nf) for nf in NF_URLS]
        )
    health = dict(zip(NF_URLS.keys(), health_results))

    return {
        "nodes": [
            {"id": nf, "label": nf.upper(),
             "healthy": health[nf], "url": NF_URLS[nf]}
            for nf in NF_URLS
        ],
        "edges": [{"from": a, "to": b} for a, b in edges],
    }


@app.get("/healthz")
def health():
    return {"status": "ok"}


# ============================================================================
# SCENARIO LIBRARY
# ============================================================================
import importlib.util as _ilu
_scen_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scenarios.py")
_scen_spec = _ilu.spec_from_file_location("scenarios", _scen_path)
scn = _ilu.module_from_spec(_scen_spec)
sys.modules["scenarios"] = scn  # required for @dataclass inside scenarios.py to resolve cls.__module__
_scen_spec.loader.exec_module(scn)


@app.get("/api/scenarios")
def list_scenarios():
    """Return scenario catalog."""
    return {"scenarios": scn.get_catalog()}


@app.post("/api/scenarios/{scenario_id}/run")
async def start_scenario(scenario_id: str):
    """Trigger a scenario by id."""
    try:
        result = await scn.run_scenario(scenario_id, NF_URLS, AMF_URL)
        return result
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/api/scenarios/stop")
async def stop_running_scenario():
    """Cancel the currently-running scenario."""
    return await scn.stop_scenario()


@app.get("/api/scenarios/state")
def scenario_state():
    """Current run status + live logs."""
    return scn.get_state()


@app.get("/api/scenarios/history")
def scenario_history(limit: int = 10):
    """Past scenario runs with full transcript."""
    return scn.get_history(limit)
