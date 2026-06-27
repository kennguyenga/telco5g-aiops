# 5G AIOps — High Level Design

**Version 2.0** · LLM-first AIOps platform for a simulated 5G core network.

## 1. System purpose

This system demonstrates closed-loop AIOps on a simulated 5G core. An operator (or autonomous LLM agent) can:

1. Generate realistic 5G traffic by attaching simulated UEs
2. Inject realistic faults — NF-level (slowdowns, errors) or subscriber-level (BLOCKED, ROAMING_NOT_ALLOWED)
3. Observe the impact via per-NF telemetry, error code counters, and 5G call flow visualizations
4. Trigger automated diagnosis: ML pattern classifier matches error distributions to known scenarios
5. Trigger automated remediation: LLM agent (Gemini, Ollama, or Claude) uses 9 tools to investigate and fix issues

The system is intentionally simplified — a **simulator with the same architectural patterns as a real 5G core**, not a production stack. Toy crypto, in-memory state, no SCTP/NGAP, no real radio. The goal is to demonstrate AIOps patterns on a realistic-looking control plane.

## 2. Layered architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     PRESENTATION LAYER                          │
│  React NOC Console (top nav + status chips):                    │
│    Topology · Subscribers · Call Flow · Failures · Error Codes  │
│    Scenarios · Telemetry · ML Engine · LLM Agent                │
└─────────────────────────────────────────────────────────────────┘
                                ↓ HTTPS via nginx
┌─────────────────────────────────────────────────────────────────┐
│                  CONTROL/INTELLIGENCE LAYER                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐           │
│  │ Orchestrator │  │  ML Engine   │  │  LLM Agent   │           │
│  │ (scenarios,  │  │ (Isolation   │  │  (Gemini /   │           │
│  │  fault inj,  │  │  Forest +    │  │   Ollama /   │           │
│  │  attach)     │  │  classifier) │  │   Anthropic) │           │
│  └──────────────┘  └──────────────┘  └──────────────┘           │
│         │                  │                  │                 │
│         └──────────┬───────┴──────────────────┘                 │
│                    ↓                                            │
│           ┌──────────────────┐                                  │
│           │    Collector     │  (logs, metrics, traces, topo)   │
│           └──────────────────┘                                  │
└─────────────────────────────────────────────────────────────────┘
                                ↓ scrapes
┌─────────────────────────────────────────────────────────────────┐
│                   5G NETWORK FUNCTION LAYER                     │
│   ┌─────┐  ┌──────┐  ┌────┐  ┌─────┐  ┌─────┐  ┌────┐  ┌────┐   │
│   │ NRF │  │ AUSF │  │UDM │  │ AMF │  │ SMF │  │UPF │  │PCF │   │
│   │8001 │  │ 8002 │  │8003│  │8004 │  │8005 │  │8006│  │8007│   │
│   └─────┘  └──────┘  └────┘  └─────┘  └─────┘  └────┘  └────┘   │
│                                                                 │
│   All NFs share nf_common library:                              │
│     - FastAPI scaffolding + healthz/metrics/logs/traces         │
│     - Failure injection middleware                              │
│     - 5G error code library (TS 29.500 / 24.501)                │
│     - SBI client (NFClient) for inter-NF calls                  │
└─────────────────────────────────────────────────────────────────┘
```

## 3. LLM provider strategy (Gemini-first decision)

The LLM Agent is the most expensive component to operate if backed by Anthropic. To make this project free for portfolio demos, the agent is **provider-agnostic** and defaults to **Google Gemini Flash**.

### 3.1 Provider comparison

| Provider | Cost | Quality | Speed | Setup |
|---|---|---|---|---|
| **Gemini Flash** (default) | $0 (free: 1500 req/day) | Good | 2-4s/turn | 1 free API key |
| Ollama (Llama 3.1 8B) | $0 LLM, requires 8 GB RAM | Decent | 5-30s/turn | Auto-downloads model |
| Anthropic Claude Haiku | ~$0.005/run | Best | 1-3s/turn | API key + pay-per-use |

### 3.2 Provider abstraction

The agent has four implementations of the same agent loop, all returning the same transcript shape (the mock is keyless and runs the tools as a fixed playbook):

```
┌────────────────────────────────────────────────┐
│           LLM Agent Service (FastAPI)          │
│                                                │
│   POST /api/llm/diagnose  (single-shot)        │
│   POST /api/llm/remediate (tool-using loop)    │
│                                                │
│            ↓ _effective_provider() resolves          │
│            ↓ (mock if no key + fallback)             │
│                                                │
│  ┌──────┐ ┌────────┐ ┌────────┐ ┌────────────┐ │
│  │ Mock │ │ Gemini │ │ Ollama │ │  Anthropic │ │
│  │ loop │ │ loop   │ │ loop   │ │  loop      │ │
│  └──┬───┘ └───┬────┘ └───┬────┘ └─────┬──────┘ │
│     │         │          │            │        │
│     └─────────┴────┬─────┴────────────┘        │
│                    ↓                         │
│              ┌───────────────┐                 │
│              │ _execute_tool │                 │
│              │   (9 tools)   │                 │
│              └───────────────┘                 │
└────────────────────────────────────────────────┘
```

Each real provider loop translates the canonical Anthropic-style tool format into the provider's native format (Gemini's functionCall/functionResponse, Ollama's OpenAI-compatible tools, Anthropic's tool_use blocks). The **mock** loop skips the LLM entirely and runs the same 9 tools as a deterministic investigate→classify→remediate→verify playbook. The transcript shape returned to the frontend is identical across all four, so the UI doesn't change when you switch.

### 3.3 Switching providers

The stack defaults to the keyless **mock**. To use a real provider, change one env var + restart:

```bash
# .env
LLM_PROVIDER=mock        # default — or "gemini", "ollama", "anthropic"
GEMINI_API_KEY=AIzaSy... # only when LLM_PROVIDER=gemini
# If a real provider has no key, the agent falls back to mock
# (LLM_FALLBACK_MOCK=0 disables that and returns a hard error instead).

docker compose restart llm_agent
```

## 4. Data flows

### 4.1 Normal operation (UE attach + session)

```
UE → AMF → AUSF → UDM (auth) → AMF → SMF → PCF (policy) → UPF (bearer) → REGISTERED
```

### 4.2 NF-level coded error injection

```
Operator: "Inject AUTH_REJECTED on AUSF at 50% rate"
  → POST /api/orchestrator/failures/inject
  → orchestrator → AUSF /failure
  → AUSF middleware now returns problem+json on 50% of requests

UE attach hits AUSF:
  AMF → AUSF → returns 403 application/problem+json {"cause": "AUTH_REJECTED"}
              → AMF receives error, propagates to UE
              → metrics: ausf.errors_by_code_total{code=AUTH_REJECTED}++
              → span attribute: error_code=AUTH_REJECTED
```

### 4.3 Subscriber-level fault injection

```
Operator: "Set 100 subscribers to AUTH_KEY_REVOKED"
  → POST /api/subscribers/set-state {state: "AUTH_KEY_REVOKED", count: 100}
  → orchestrator → UDM /subscribers/state/bulk
  → UDM marks 100 random SUPIs as AUTH_KEY_REVOKED

UE attach for blocked SUPI:
  AMF → AUSF → UDM /subscribers/{supi}/auth-vector
              → UDM checks state, raises nf_error("UE_AUTH_KEY_REVOKED")
              → 403 problem+json bubbles up to AMF
              → AMF marks registration failed
```

### 4.4 Diagnosis + remediation flow (Gemini)

```
Operator clicks "START AGENT" (autonomous mode):
  → POST /api/llm/remediate
  → llm_agent picks provider (Gemini default)
  → Gemini receives system prompt + 9 tool definitions

Iter 1: Gemini → query_error_codes → AUTH_REJECTED:47
Iter 2: Gemini → query_subscriber_states → AUTH_KEY_REVOKED:100
Iter 3: Gemini → classify_failure → ML returns "auth-reject-storm" (95% match)
Iter 4: Gemini → reset_subscribers → all 100 SUPIs back to ACTIVE
Iter 5: Gemini → query_error_codes → errors trending down
Iter 6: Gemini → final summary

Total time: 12-25s. Cost on Gemini Flash free tier: $0.
```

## 5. Component responsibilities

### 5.1 Network Functions (NF layer)

| NF | Port | Role |
|---|---|---|
| NRF | 8001 | Service discovery, NF registry |
| AUSF | 8002 | Authentication (5G-AKA, simplified) |
| UDM | 8003 | Subscriber DB + state machine, auth vectors |
| AMF | 8004 | UE state machine, registration coordinator |
| SMF | 8005 | Session establishment, calls PCF + UPF |
| UPF | 8006 | User-plane (simulated KPIs only) |
| PCF | 8007 | Policy decisions, charging rules |

All NFs share `nf_common`:
- `FailureConfig` — per-NF fault injection knobs
- `Telemetry` — log/metric/span buffers
- `errors.py` — 25 5G cause codes per TS 29.500 / 24.501
- `nf_error()` — raises HTTPException with proper application/problem+json

### 5.2 Control plane

**Collector** — telemetry aggregator. Scrapes every NF every 1s. In-memory ring buffers.

**Orchestrator** — operator API + scenario runner. UE attach/detach, fault injection, 16 scripted scenarios, distributed trace generation.

**ML Engine** — anomaly detection + pattern classification. Isolation Forest, Ridge regression forecast, rule-based 8-pattern classifier (recommend-only).

**LLM Agent** — autonomous SRE. 9 tools, provider-agnostic, transcript-based.

### 5.3 Frontend

React NOC console (Vite) — horizontal top-nav with eight sections plus an Operations landing, navy/blue enterprise theme with live header status chips. Data-heavy panels use JetBrains Mono; the shell uses Manrope.

## 6. Deployment topology

```
                        Internet
                            ↓
                Cloudflare DNS (kennguyen.dev)
                            ↓
            Hetzner VPS CPX11 (2 GB RAM, $5/mo)
                            ↓
                        nginx (443)
                            ↓
                Frontend container (port 5173 → 80)
                            ↓ /api proxy
                Internal Docker network
            (12 containers via Docker DNS)
                            ↓
            Google Gemini API (outbound HTTPS only)
```

12 containers in the default deployment:
- 7 NFs + 4 control plane services + 1 frontend

Optional opt-in via profile (`docker compose --profile ollama up`):
- `ollama` + `ollama_init` (only when LLM_PROVIDER=ollama)

## 7. Resource consumption

| Component | RAM | CPU | Disk |
|---|---|---|---|
| 7 NFs | ~50 MB each | <1% each | minimal |
| Collector | ~80 MB | 1-2% | minimal |
| Orchestrator | ~70 MB | <1% | minimal |
| ML Engine | ~150 MB (sklearn) | 1-3% | ~1 MB models |
| LLM Agent | ~80 MB | <1% | minimal |
| Frontend nginx | ~10 MB | <1% | ~600 KB SPA |
| **Total** | **~700 MB** | **~5-10%** | **~3 GB after build** |

Comfortable on a 2 GB VPS. Headroom for spikes.

## 8. Non-goals

This simulator intentionally does NOT:
- Implement real NGAP/SCTP signaling
- Use real Milenage/Tuak crypto
- Persist state across restarts
- Authenticate the API (single-tenant demo)
- Scale horizontally

These are deliberate simplifications for portfolio scope.

## 9. Future extensions

If this grew into something more:
1. Persistent storage (Postgres + Redis)
2. Real NGAP/SCTP signaling
3. Multi-NSSAI slice-aware policy
4. Roaming (VPLMN/HPLMN, SEPP)
5. Federated multi-region orchestrator
6. Operator authentication
