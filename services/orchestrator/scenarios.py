"""
Failure Scenario Library

Each scenario is a sequence of (timestep, action) tuples that:
  1. Inject failures with specific timing and intensity
  2. Optionally generate UE load
  3. Emit scenario_id markers in logs (so the LLM can ground-truth)

Scenarios run as background asyncio tasks. The orchestrator exposes:
  POST /api/scenarios/{id}/run    — start a scenario
  POST /api/scenarios/stop        — cancel running scenario
  GET  /api/scenarios             — catalog
  GET  /api/scenarios/state       — current scenario status
  GET  /api/scenarios/history     — past runs
"""
from __future__ import annotations
import asyncio
import time
import random
from dataclasses import dataclass, field
from typing import Callable, Optional

import httpx


# ============================================================================
# SCENARIO REGISTRY
# ============================================================================
@dataclass
class Scenario:
    id: str
    name: str
    severity: str       # low | medium | high | critical
    duration_s: int
    description: str
    expected_symptoms: list[str]
    runner: Callable    # async (ctx) -> None


SCENARIOS: dict[str, Scenario] = {}


def register(scenario: Scenario):
    SCENARIOS[scenario.id] = scenario


# ============================================================================
# RUNTIME STATE
# ============================================================================
@dataclass
class RunContext:
    """Passed to scenario runners; provides helpers for fault injection
    and load generation, plus shared state."""
    nf_urls: dict[str, str]
    amf_url: str
    client: httpx.AsyncClient
    scenario_id: str
    log: Callable        # log(msg) — adds to scenario history
    started_at: float = field(default_factory=time.time)

    async def inject(self, nf: str, **cfg):
        """Push a failure config to an NF."""
        self.log(f"INJECT {nf} ← {cfg}")
        await self.client.post(f"{self.nf_urls[nf]}/failure", json=cfg, timeout=5)

    async def clear(self, nf: str):
        """Clear all failures on an NF."""
        cleared = {"error_rate": 0.0, "extra_latency_ms": 0, "blackhole": False,
                   "corruption_rate": 0.0, "unhealthy": False}
        self.log(f"CLEAR {nf}")
        await self.client.post(f"{self.nf_urls[nf]}/failure", json=cleared, timeout=5)

    async def clear_all(self):
        for nf in self.nf_urls:
            try: await self.clear(nf)
            except Exception: pass

    async def attach_burst(self, n: int, parallelism: int = 5,
                           supis: list = None):
        """Attach N UEs concurrently. Returns success count.
        If `supis` is provided, attach those exact SUPIs (deterministic demo).
        Otherwise pick a random window of N consecutive SUPIs (v10 behavior)."""
        sem = asyncio.Semaphore(parallelism)
        async def one(supi: str):
            async with sem:
                try: i = int(supi.split("imsi-00101", 1)[1])
                except (ValueError, IndexError): i = 1
                key = f"{i:032x}"
                try:
                    r = await self.client.post(
                        f"{self.amf_url}/ue/register",
                        json={"supi": supi, "plmn": "00101", "ue_auth_key": key},
                        timeout=8,
                    )
                    if r.status_code == 200:
                        await self.client.post(
                            f"{self.amf_url}/ue/session",
                            json={"supi": supi, "apn": "internet"},
                            timeout=8,
                        )
                        return True
                except Exception:
                    pass
                return False

        # If specific SUPIs supplied, target them (used by code-aware scenarios
        # that need guaranteed overlap with blocked subscribers).
        # Otherwise pick a random window — this is what v10 did and what the
        # scenarios already accommodated by raising block count high enough
        # that overlap was probable.
        if supis:
            target_supis = list(supis)[:n]
            while len(target_supis) < n:
                idx = len(target_supis) + 1
                target_supis.append(f"imsi-00101{idx:010d}")
        else:
            start = random.randint(1, max(1, 1000 - n))
            target_supis = [f"imsi-00101{start + i:010d}" for i in range(n)]

        results = await asyncio.gather(*[one(s) for s in target_supis])
        ok = sum(1 for r in results if r)
        self.log(f"ATTACH burst: {ok}/{n} succeeded")
        return ok

    async def sleep(self, seconds: float):
        await asyncio.sleep(seconds)

    # ── Subscriber-state helpers (UDM API) ──────────────────────────
    async def block_subscribers(self, count: int, state: str, reason: str = ""):
        """Move N random ACTIVE subscribers to a non-ACTIVE state."""
        self.log(f"BLOCK {count} subscribers → {state}")
        try:
            r = await self.client.post(
                f"{self.nf_urls['udm']}/subscribers/state/bulk",
                json={"state": state, "count": count, "reason": reason},
                timeout=10,
            )
            data = r.json()
            self.log(f"  → {data.get('updated')} subscribers updated")
            return data.get("supis", [])
        except Exception as e:
            self.log(f"  ✗ block failed: {e}")
            return []

    async def reset_subscribers(self):
        """Reset every subscriber back to ACTIVE."""
        self.log("RESET all subscriber states → ACTIVE")
        try:
            r = await self.client.post(
                f"{self.nf_urls['udm']}/subscribers/state/reset", timeout=10)
            self.log(f"  → {r.json().get('reset')} subscribers reset")
        except Exception as e:
            self.log(f"  ✗ reset failed: {e}")


# ============================================================================
# SCENARIO IMPLEMENTATIONS
# ============================================================================
async def _scn_auth_storm(ctx: RunContext):
    """50 UEs hit a throttled UDM at once → cascading auth failures."""
    ctx.log("Phase 1: throttle UDM (1.5s latency, 30% errors)")
    await ctx.inject("udm", extra_latency_ms=1500, error_rate=0.3)
    await ctx.sleep(2)
    ctx.log("Phase 2: launch 50-UE attach storm")
    await ctx.attach_burst(50, parallelism=20)
    ctx.log("Phase 3: hold for 20s to let metrics accumulate")
    await ctx.sleep(20)
    ctx.log("Phase 4: clear UDM faults")
    await ctx.clear("udm")


async def _scn_slow_roast(ctx: RunContext):
    """AUSF latency creeps from 100ms → 5000ms over 60s."""
    ctx.log("Phase 1: start gradual AUSF latency ramp")
    # Background load
    load_task = asyncio.create_task(_continuous_load(ctx, rate=2, duration=70))
    for step in range(10):
        latency = 100 + step * 500
        await ctx.inject("ausf", extra_latency_ms=latency)
        await ctx.sleep(6)
    ctx.log("Phase 2: clear AUSF, let load drain")
    await ctx.clear("ausf")
    await load_task


async def _scn_silent_udm(ctx: RunContext):
    """UDM intermittent (15% errors) — only some UEs fail. Hard to spot."""
    ctx.log("Phase 1: inject 15% error rate on UDM")
    await ctx.inject("udm", error_rate=0.15)
    ctx.log("Phase 2: continuous load for 60s")
    await _continuous_load(ctx, rate=3, duration=60)
    ctx.log("Phase 3: clear UDM")
    await ctx.clear("udm")


async def _scn_pdu_collapse(ctx: RunContext):
    """SMF unhealthy → no new sessions establish."""
    ctx.log("Phase 1: register 30 UEs successfully (baseline)")
    await ctx.attach_burst(30, parallelism=10)
    await ctx.sleep(5)
    ctx.log("Phase 2: mark SMF unhealthy + 80% errors")
    await ctx.inject("smf", unhealthy=True, error_rate=0.8)
    ctx.log("Phase 3: try to attach 20 more UEs (will fail at session step)")
    await ctx.attach_burst(20, parallelism=5)
    await ctx.sleep(15)
    ctx.log("Phase 4: clear SMF")
    await ctx.clear("smf")


async def _scn_upf_overload(ctx: RunContext):
    """UPF packet corruption climbs as bearers accumulate."""
    ctx.log("Phase 1: register 40 UEs to build up bearers")
    await ctx.attach_burst(40, parallelism=10)
    await ctx.sleep(5)
    ctx.log("Phase 2: ramp UPF corruption rate")
    for level in [0.05, 0.10, 0.20, 0.35]:
        await ctx.inject("upf", corruption_rate=level)
        ctx.log(f"  UPF corruption at {int(level*100)}%")
        await ctx.sleep(8)
    ctx.log("Phase 3: clear UPF")
    await ctx.clear("upf")


async def _scn_policy_blackhole(ctx: RunContext):
    """PCF crashes → SMF can't get policies → sessions stuck PENDING."""
    ctx.log("Phase 1: blackhole PCF (hangs all requests)")
    await ctx.inject("pcf", blackhole=True, unhealthy=True)
    await ctx.sleep(2)
    ctx.log("Phase 2: try 25 UE attaches (registration ok, sessions hang)")
    await ctx.attach_burst(25, parallelism=10)
    await ctx.sleep(15)
    ctx.log("Phase 3: clear PCF")
    await ctx.clear("pcf")


async def _scn_cascade_failure(ctx: RunContext):
    """NRF slows → service discovery fails → entire core degrades."""
    ctx.log("Phase 1: NRF latency at 3s (service discovery degraded)")
    await ctx.inject("nrf", extra_latency_ms=3000, error_rate=0.4)
    ctx.log("Phase 2: load that depends on multiple NF lookups")
    await _continuous_load(ctx, rate=4, duration=40)
    ctx.log("Phase 3: clear NRF")
    await ctx.clear("nrf")


async def _scn_auth_then_recover(ctx: RunContext):
    """AUSF errors for 30s, then auto-recovers. Tests detection of resolved issues."""
    ctx.log("Phase 1: AUSF returns 70% errors")
    await ctx.inject("ausf", error_rate=0.7)
    await _continuous_load(ctx, rate=3, duration=25)
    ctx.log("Phase 2: AUSF recovers")
    await ctx.clear("ausf")
    ctx.log("Phase 3: continue normal load to confirm recovery")
    await _continuous_load(ctx, rate=3, duration=20)


async def _scn_flash_crowd(ctx: RunContext):
    """200 UEs attach in 10s. Tests system under sudden load (no faults)."""
    ctx.log("Phase 1: 200-UE flash crowd")
    await ctx.attach_burst(200, parallelism=40)
    ctx.log("Phase 2: hold for 10s")
    await ctx.sleep(10)


async def _scn_byzantine_pcf(ctx: RunContext):
    """PCF corruption — returns wrong policies. Subtle UPF bearers misconfigured."""
    ctx.log("Phase 1: PCF corruption rate 50% (mangled responses)")
    await ctx.inject("pcf", corruption_rate=0.5)
    ctx.log("Phase 2: attach 30 UEs — some sessions will get garbage policies")
    await ctx.attach_burst(30, parallelism=10)
    await ctx.sleep(15)
    ctx.log("Phase 3: clear PCF")
    await ctx.clear("pcf")


# ── Helper: continuous low-rate load ─────────────────────────────────
async def _continuous_load(ctx: RunContext, rate: int, duration: int):
    """Send `rate` attach attempts per second for `duration` seconds."""
    end = time.time() + duration
    next_supi = random.randint(1, 800)
    while time.time() < end:
        tasks = []
        for _ in range(rate):
            i = next_supi
            next_supi = (next_supi % 999) + 1
            supi = f"imsi-00101{i:010d}"
            key = f"{i:032x}"
            tasks.append(_one_attach(ctx, supi, key))
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(1.0)


async def _one_attach(ctx: RunContext, supi: str, key: str):
    try:
        await ctx.client.post(
            f"{ctx.amf_url}/ue/register",
            json={"supi": supi, "plmn": "00101", "ue_auth_key": key},
            timeout=5,
        )
    except Exception:
        pass


# ============================================================================
# REGISTER ALL SCENARIOS
# ============================================================================
register(Scenario(
    id="auth-storm", name="AUTH STORM", severity="high", duration_s=30,
    description="UDM throttled while 50 UEs attach simultaneously. Tests detection of "
                "auth-path congestion and partial failure cascades.",
    expected_symptoms=[
        "UDM p99 latency spike", "AUSF auth_init failures",
        "AMF registrations_failed_total climbs", "Active UEs grows slower than expected",
    ],
    runner=_scn_auth_storm,
))

register(Scenario(
    id="slow-roast", name="SLOW ROAST", severity="medium", duration_s=70,
    description="AUSF latency ramps gradually from 100ms to 5000ms over 60s. "
                "Tests early-warning detection vs reactive alerting.",
    expected_symptoms=[
        "AUSF p99 latency monotonically increases",
        "AMF registration duration climbs", "UE attach rate falls",
    ],
    runner=_scn_slow_roast,
))

register(Scenario(
    id="silent-udm", name="SILENT UDM", severity="medium", duration_s=65,
    description="UDM returns 500 to ~15% of requests. Most UEs succeed — failures "
                "are statistical, not deterministic. Hard to catch without good metrics.",
    expected_symptoms=[
        "UDM error rate elevated but not catastrophic",
        "registrations_failed_total grows steadily but slowly",
    ],
    runner=_scn_silent_udm,
))

register(Scenario(
    id="pdu-collapse", name="PDU COLLAPSE", severity="high", duration_s=30,
    description="SMF marked unhealthy + 80% errors. Existing UEs stay registered but "
                "no new PDU sessions can be established.",
    expected_symptoms=[
        "SMF unhealthy", "sessions_failed_total spikes",
        "active_ues stable but active_sessions flat", "AMF session calls fail",
    ],
    runner=_scn_pdu_collapse,
))

register(Scenario(
    id="upf-overload", name="UPF OVERLOAD", severity="medium", duration_s=45,
    description="UPF packet corruption ramps as bearers accumulate. KPIs degrade — "
                "throughput stable but loss/jitter climb.",
    expected_symptoms=[
        "UPF packet_loss_pct grows", "UPF jitter_ms grows",
        "Bearers stay active but quality degrades",
    ],
    runner=_scn_upf_overload,
))

register(Scenario(
    id="policy-blackhole", name="POLICY BLACKHOLE", severity="critical", duration_s=25,
    description="PCF blackholes all requests. SMF can't get policies, every new "
                "session attempt hangs at the PCF call until timeout.",
    expected_symptoms=[
        "PCF unreachable", "sessions_pending_total stays high",
        "SMF latency spikes (waiting on PCF timeout)",
    ],
    runner=_scn_policy_blackhole,
))

register(Scenario(
    id="cascade-failure", name="CASCADE FAILURE", severity="critical", duration_s=50,
    description="NRF (service registry) becomes slow. Every NF that does service "
                "discovery degrades. Multi-NF symptoms.",
    expected_symptoms=[
        "NRF p99 latency very high", "Multiple NFs show elevated errors",
        "Hard to pinpoint root cause without correlation",
    ],
    runner=_scn_cascade_failure,
))

register(Scenario(
    id="auth-then-recover", name="AUTH RECOVERY", severity="medium", duration_s=55,
    description="AUSF errors for 30s, then resolves. Tests whether the agent "
                "correctly detects that the issue is gone (not just was present).",
    expected_symptoms=[
        "AUSF errors spike then return to zero",
        "Telemetry shows clear before/after pattern",
    ],
    runner=_scn_auth_then_recover,
))

register(Scenario(
    id="flash-crowd", name="FLASH CROWD", severity="low", duration_s=20,
    description="200 UEs attach in 10s. No injected faults — pure capacity test. "
                "Latency should rise but no errors should occur.",
    expected_symptoms=[
        "AMF p99 latency rises", "Request rate spikes",
        "Eventually settles back to normal",
    ],
    runner=_scn_flash_crowd,
))

register(Scenario(
    id="byzantine-pcf", name="BYZANTINE PCF", severity="medium", duration_s=30,
    description="PCF returns mangled responses 50% of the time. Sessions establish "
                "but with garbage policies. The hardest scenario to detect — outputs "
                "look successful at first.",
    expected_symptoms=[
        "PCF requests_total normal", "But corruption_rate elevated",
        "Some bearers may fail or have wrong QoS",
    ],
    runner=_scn_byzantine_pcf,
))


# ============================================================================
# RUNTIME ENGINE
# ============================================================================
class ScenarioRuntime:
    """Tracks active and historical scenario runs."""
    def __init__(self):
        self.active: Optional[asyncio.Task] = None
        self.active_id: Optional[str] = None
        self.active_logs: list[dict] = []
        self.history: list[dict] = []

    def is_running(self):
        return self.active is not None and not self.active.done()


runtime = ScenarioRuntime()


def get_catalog() -> list[dict]:
    """Return scenario catalog for UI."""
    return [
        {
            "id": s.id, "name": s.name, "severity": s.severity,
            "duration_s": s.duration_s, "description": s.description,
            "expected_symptoms": s.expected_symptoms,
        }
        for s in SCENARIOS.values()
    ]


def _log(msg: str):
    """Append to active logs and stdout."""
    entry = {"timestamp": time.time(), "message": msg}
    runtime.active_logs.append(entry)
    print(f"[scenario {runtime.active_id}] {msg}", flush=True)


async def run_scenario(scenario_id: str, nf_urls: dict, amf_url: str):
    """Run a scenario by id. Cancels any running scenario first."""
    if scenario_id not in SCENARIOS:
        raise ValueError(f"unknown scenario: {scenario_id}")

    if runtime.is_running():
        runtime.active.cancel()
        try: await runtime.active
        except (asyncio.CancelledError, Exception): pass

    scenario = SCENARIOS[scenario_id]
    runtime.active_id = scenario_id
    runtime.active_logs = []
    _log(f"START {scenario.name}: {scenario.description}")

    async def _runner():
        async with httpx.AsyncClient() as client:
            ctx = RunContext(
                nf_urls=nf_urls, amf_url=amf_url, client=client,
                scenario_id=scenario_id, log=_log,
            )
            try:
                await scenario.runner(ctx)
                _log("COMPLETE — clearing all faults")
                await ctx.clear_all()
            except asyncio.CancelledError:
                _log("CANCELLED — clearing all faults")
                await ctx.clear_all()
                raise
            except Exception as e:
                _log(f"ERROR: {e} — clearing all faults")
                await ctx.clear_all()
            finally:
                runtime.history.insert(0, {
                    "scenario_id": runtime.active_id,
                    "started_at": ctx.started_at,
                    "ended_at": time.time(),
                    "logs": list(runtime.active_logs),
                })
                runtime.history = runtime.history[:50]
                runtime.active = None
                runtime.active_id = None

    runtime.active = asyncio.create_task(_runner())
    return {"started": scenario.id, "name": scenario.name}


async def stop_scenario():
    if not runtime.is_running():
        return {"status": "not_running"}
    runtime.active.cancel()
    return {"status": "cancelling", "scenario_id": runtime.active_id}


def get_state():
    return {
        "running": runtime.is_running(),
        "scenario_id": runtime.active_id,
        "logs": list(runtime.active_logs),
    }


def get_history(limit: int = 10):
    return {"history": runtime.history[:limit]}


# ============================================================================
# CODE-LEVEL SCENARIOS — exercise specific 5G error codes + subscriber state
# ============================================================================
async def _scn_auth_reject_storm(ctx: RunContext):
    """50 subscribers get AUTH_KEY_REVOKED → those exact SUPIs trigger AUTH_REJECTED."""
    ctx.log("Phase 1: revoke auth keys for 50 random subscribers")
    blocked_supis = await ctx.block_subscribers(50, "AUTH_KEY_REVOKED",
                                                  reason="auth-reject-storm scenario")
    await ctx.sleep(2)
    ctx.log(f"Phase 2: attach the {len(blocked_supis)} blocked SUPIs + 30 healthy ones")
    # Mix blocked + healthy so we see both REJECT and SUCCESS in logs
    await ctx.attach_burst(80, parallelism=15, supis=blocked_supis)
    ctx.log("Phase 3: hold 15s for telemetry to accumulate")
    await ctx.sleep(15)
    ctx.log("Phase 4: reset subscriber states")
    await ctx.reset_subscribers()


async def _scn_dnn_mismatch(ctx: RunContext):
    """PCF emits DNN_NOT_SUPPORTED 40% of the time → many sessions fail."""
    ctx.log("Phase 1: configure PCF to emit DNN_NOT_SUPPORTED on 40% of requests")
    await ctx.inject("pcf", error_codes=["DNN_NOT_SUPPORTED"], error_code_rate=0.4)
    await ctx.sleep(2)
    ctx.log("Phase 2: attach + session for 40 UEs")
    await ctx.attach_burst(40, parallelism=10)
    await ctx.sleep(15)
    ctx.log("Phase 3: clear PCF")
    await ctx.clear("pcf")


async def _scn_congestion_cascade(ctx: RunContext):
    """UPF returns INSUFFICIENT_RESOURCES → SMF cascades → user-visible failures."""
    ctx.log("Phase 1: register 30 UEs successfully (baseline)")
    await ctx.attach_burst(30, parallelism=10)
    await ctx.sleep(3)
    ctx.log("Phase 2: UPF starts returning INSUFFICIENT_RESOURCES (60% rate)")
    await ctx.inject("upf",
                     error_codes=["INSUFFICIENT_RESOURCES", "NF_CONGESTION"],
                     error_code_rate=0.6)
    ctx.log("Phase 3: 50 more UEs try to attach + establish session")
    await ctx.attach_burst(50, parallelism=15)
    ctx.log("Phase 4: hold 12s")
    await ctx.sleep(12)
    ctx.log("Phase 5: clear UPF")
    await ctx.clear("upf")


async def _scn_roaming_restriction(ctx: RunContext):
    """200 subscribers get ROAMING_NOT_ALLOWED state. Tests bulk subscriber-level fault."""
    ctx.log("Phase 1: apply ROAMING_NOT_ALLOWED to 200 subscribers")
    blocked = await ctx.block_subscribers(200, "ROAMING_NOT_ALLOWED",
                                           reason="roaming-restriction scenario")
    await ctx.sleep(2)
    ctx.log(f"Phase 2: attach the {len(blocked)} blocked SUPIs")
    await ctx.attach_burst(min(100, len(blocked)), parallelism=15, supis=blocked)
    ctx.log("Phase 3: hold")
    await ctx.sleep(10)
    ctx.log("Phase 4: reset")
    await ctx.reset_subscribers()


async def _scn_slice_capacity_exhausted(ctx: RunContext):
    """SMF starts returning INSUFFICIENT_SLICE_RESOURCES — capacity-style fault."""
    ctx.log("Phase 1: SMF emits INSUFFICIENT_SLICE_RESOURCES on 50% of session req")
    await ctx.inject("smf",
                     error_codes=["INSUFFICIENT_SLICE_RESOURCES"],
                     error_code_rate=0.5)
    await ctx.sleep(2)
    ctx.log("Phase 2: 60 attaches + sessions — many will fail at session step")
    await ctx.attach_burst(60, parallelism=15)
    await ctx.sleep(20)
    ctx.log("Phase 3: clear SMF")
    await ctx.clear("smf")


async def _scn_subscription_kaleidoscope(ctx: RunContext):
    """Mixed subscriber states — different errors at different points in the flow."""
    ctx.log("Phase 1: distribute non-ACTIVE states across the subscriber pool")
    b1 = await ctx.block_subscribers(30, "BLOCKED",              reason="kaleidoscope")
    b2 = await ctx.block_subscribers(30, "ROAMING_NOT_ALLOWED",  reason="kaleidoscope")
    b3 = await ctx.block_subscribers(30, "AUTH_KEY_REVOKED",     reason="kaleidoscope")
    b4 = await ctx.block_subscribers(30, "SUSPENDED",            reason="kaleidoscope")
    all_blocked = b1 + b2 + b3 + b4
    await ctx.sleep(2)
    ctx.log(f"Phase 2: attach the {len(all_blocked)} blocked SUPIs (mixed errors)")
    await ctx.attach_burst(min(100, len(all_blocked)), parallelism=15, supis=all_blocked)
    await ctx.sleep(10)
    ctx.log("Phase 3: reset")
    await ctx.reset_subscribers()


# ── Register the new scenarios ───────────────────────────────────────
register(Scenario(
    id="auth-reject-storm", name="AUTH REJECT STORM", severity="high", duration_s=30,
    description="50 random subscribers have their auth keys revoked, then 80 UEs "
                "try to attach. Many will hit the blocked subscribers and receive "
                "AUTH_REJECTED / UE_AUTH_KEY_REVOKED.",
    expected_symptoms=[
        "AUSF errors_by_code AUTH_REJECTED rises",
        "UDM errors_by_code UE_AUTH_KEY_REVOKED rises",
        "AMF registrations_failed_total grows",
        "Subscriber gauge: subscribers_in_state_auth_key_revoked = 50",
    ],
    runner=_scn_auth_reject_storm,
))

register(Scenario(
    id="dnn-mismatch", name="DNN MISMATCH", severity="medium", duration_s=20,
    description="PCF returns DNN_NOT_SUPPORTED on 40% of policy decisions. "
                "Sessions fail at the policy step.",
    expected_symptoms=[
        "PCF errors_by_code DNN_NOT_SUPPORTED elevated",
        "SMF sessions_failed_total{reason=pcf_error} grows",
        "Registrations succeed but PDU sessions don't",
    ],
    runner=_scn_dnn_mismatch,
))

register(Scenario(
    id="congestion-cascade", name="CONGESTION CASCADE", severity="critical", duration_s=20,
    description="UPF emits INSUFFICIENT_RESOURCES 60% of the time. SMF rolls back "
                "policies but failures cascade to UEs.",
    expected_symptoms=[
        "UPF errors_by_code INSUFFICIENT_RESOURCES rises",
        "SMF sessions_failed_total{reason=upf_error} rises",
        "PCF policy_revocations_total grows (rollback triggered)",
    ],
    runner=_scn_congestion_cascade,
))

register(Scenario(
    id="roaming-restriction", name="ROAMING RESTRICTION", severity="medium", duration_s=35,
    description="200 subscribers tagged ROAMING_NOT_ALLOWED in UDM. Continuous "
                "load — every attempt against a tagged subscriber fails immediately.",
    expected_symptoms=[
        "UDM errors_by_code ROAMING_NOT_ALLOWED steady",
        "Subscriber gauge: subscribers_in_state_roaming_not_allowed = 200",
        "registrations_failed_total grows roughly proportional to load × 0.2",
    ],
    runner=_scn_roaming_restriction,
))

register(Scenario(
    id="slice-capacity-exhausted", name="SLICE CAPACITY EXHAUSTED",
    severity="high", duration_s=25,
    description="SMF returns INSUFFICIENT_SLICE_RESOURCES on 50% of session "
                "establishment requests. Tests slice-level capacity exhaustion.",
    expected_symptoms=[
        "SMF errors_by_code INSUFFICIENT_SLICE_RESOURCES elevated",
        "active_sessions plateaus despite continued attach attempts",
        "AMF sessions_failed_total grows",
    ],
    runner=_scn_slice_capacity_exhausted,
))

register(Scenario(
    id="subscription-kaleidoscope", name="SUBSCRIPTION KALEIDOSCOPE",
    severity="critical", duration_s=30,
    description="Mixed subscriber states — 30 BLOCKED, 30 ROAMING_NOT_ALLOWED, "
                "30 AUTH_KEY_REVOKED, 30 SUSPENDED. Heterogeneous failure pattern. "
                "Hardest to diagnose because symptoms are spread across multiple codes.",
    expected_symptoms=[
        "Multiple distinct error codes elevated simultaneously",
        "ILLEGAL_UE, ROAMING_NOT_ALLOWED, AUTH_KEY_REVOKED, USER_NOT_ALLOWED all rising",
        "120 subscribers in non-ACTIVE state",
    ],
    runner=_scn_subscription_kaleidoscope,
))
