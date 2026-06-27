# 5G AIOps — Low Level Design

**Version 2.0** · Implementation details for each component.

This document describes the actual code structure, key interfaces, data models, and call paths. It assumes you've read [HLD.md](./HLD.md).

## 1. Codebase layout

```
aiops5g/
├── docker-compose.yml          # 12 services; 11 backends share one image (SERVICE_NAME picks role)
├── Dockerfile                  # multi-target: each NF and control plane
├── README.md
├── deploy/
│   ├── bootstrap.sh           # one-command Ubuntu→running stack
│   ├── setup-https.sh         # nginx + Let's Encrypt
│   └── README.md              # deploy guide
├── docs/
│   ├── HLD.md                 # this document's parent
│   ├── LLD.md                 # this file
│   ├── ARCHITECTURE.md
│   ├── MESSAGE_FLOWS.md
│   ├── GEMINI.md
│   └── OLLAMA.md
├── services/
│   ├── nf_common/             # shared library for all NFs
│   │   ├── __init__.py        # FailureConfig, Telemetry, NFClient, nf_error
│   │   └── errors.py          # 25 5G error codes
│   ├── nrf/main.py            # NF: service registry
│   ├── ausf/main.py           # NF: authentication
│   ├── udm/main.py            # NF: subscriber DB + state machine
│   ├── amf/main.py            # NF: UE state coordination
│   ├── smf/main.py            # NF: session establishment
│   ├── upf/main.py            # NF: user plane (KPI gen)
│   ├── pcf/main.py            # NF: policy decisions
│   ├── collector/main.py      # telemetry aggregator
│   ├── orchestrator/
│   │   ├── main.py            # operator API
│   │   └── scenarios.py       # 16 scripted scenarios
│   ├── ml_engine/main.py      # anomaly detection + classifier
│   ├── llm_agent/main.py      # multi-provider LLM agent
│   └── requirements.txt
└── frontend/
    ├── Dockerfile
    ├── nginx.conf             # /api/* proxy to control plane services
    ├── package.json
    ├── vite.config.js
    └── src/
        ├── App.jsx            # 9-tab router
        ├── api.js             # API client
        ├── index.css          # Tailwind + custom palette
        ├── main.jsx
        └── components/
            ├── Topology.jsx
            ├── Subscribers.jsx
            ├── CallFlow.jsx
            ├── Failures.jsx
            ├── ErrorCodes.jsx
            ├── Scenarios.jsx
            ├── Telemetry.jsx
            ├── MLView.jsx
            ├── Operations.jsx # NOC landing: KPIs, injections, activity, inject sidebar
            ├── Agent.jsx      # LLM agent UI with provider badge
            └── ui.jsx         # shared primitives (Panel, Button, etc.)
```

## 2. Shared library: `nf_common`

### 2.1 FailureConfig

```python
@dataclass
class FailureConfig:
    error_rate: float = 0.0            # generic 500s
    extra_latency_ms: int = 0          # injected delay
    blackhole: bool = False            # drop requests
    corruption_rate: float = 0.0
    unhealthy: bool = False             # /healthz returns 503
    error_codes: list = []             # NEW: 5G coded errors
    error_code_rate: float = 0.0       # NEW: rate of coded errors
```

### 2.2 Failure middleware

Every NF runs this middleware on every request:

```python
@app.middleware("http")
async def fault_injection(request, call_next):
    # 1. Latency
    if failure.extra_latency_ms > 0:
        await asyncio.sleep(failure.extra_latency_ms / 1000)
    # 2. Blackhole
    if failure.blackhole:
        raise HTTPException(503, "blackhole")
    # 3. Generic 500
    if failure.error_rate > 0 and random.random() < failure.error_rate:
        raise HTTPException(500, "injected fault")
    # 4. 5G coded error (NEW)
    if failure.error_code_rate > 0 and failure.error_codes:
        if random.random() < failure.error_code_rate:
            cause = random.choice(failure.error_codes)
            return JSONResponse(
                status_code=ErrorCode.lookup(cause).http_status,
                content=problem_json(cause),
                media_type="application/problem+json",
            )
    return await call_next(request)
```

### 2.3 NFClient (inter-NF SBI calls)

```python
class NFClient:
    async def call(self, nf, path, method="GET", trace_id=None, ...):
        url = f"http://{nf}:800X{path}"
        async with self.tel.span(f"call_{nf}_{path}", trace_id=trace_id) as ctx:
            try:
                resp = await self.client.request(method, url, ...)
                if resp.status_code >= 400:
                    # Extract 5G cause from problem+json body
                    cause = self._extract_cause(resp)
                    if cause:
                        ctx["attributes"]["error_code"] = cause
                    raise HTTPException(resp.status_code, ...)
                return resp.json()
            except httpx.RequestError:
                ctx["attributes"]["error_code"] = "UPSTREAM_TIMEOUT"
                raise HTTPException(503, ...)
```

The span attribute `error_code` flows through to the call flow visualizer, where it appears as a label on the red return arrow.

### 2.4 errors.py — 25 5G cause codes

```python
@dataclass(frozen=True)
class ErrorCode:
    cause: str          # "AUTH_REJECTED"
    http_status: int    # 403
    nas_cause: int      # 20 (per TS 24.501)
    title: str          # "Authentication rejected"
    detail: str         # "RES verification failed"
    nf_owners: tuple    # ("ausf",)
    category: str       # "auth"

CATALOG: tuple[ErrorCode, ...] = (
    # ── AUTH ──
    ErrorCode("AUTH_REJECTED", 403, 20, ...),
    ErrorCode("MAC_FAILURE", 403, 20, ...),
    ErrorCode("SYNCH_FAILURE", 403, 21, ...),
    ErrorCode("UE_AUTH_KEY_REVOKED", 403, 20, ...),
    # ── SUBSCRIPTION ──
    ErrorCode("USER_NOT_FOUND", 404, 9, ...),
    ErrorCode("USER_NOT_ALLOWED", 403, 7, ...),
    ErrorCode("ILLEGAL_UE", 403, 3, ...),
    ErrorCode("ROAMING_NOT_ALLOWED", 403, 13, ...),
    # ── SESSION ──
    ErrorCode("DNN_NOT_SUPPORTED", 404, 55, ...),
    ErrorCode("INSUFFICIENT_SLICE_RESOURCES", 503, 54, ...),
    # ── RESOURCE ──
    ErrorCode("INSUFFICIENT_RESOURCES", 503, 67, ...),
    ErrorCode("NF_CONGESTION", 503, 22, ...),
    # ── REQUEST ──
    ErrorCode("MANDATORY_IE_MISSING", 400, 96, ...),
    ErrorCode("INTERNAL_ERROR", 500, 0, ...),
    # ... 25 total
)

def problem_json(cause: str, supi=None, trace_id=None, detail_override=None) -> dict:
    """Build a 3GPP-compliant application/problem+json body."""
    e = lookup(cause)
    return {
        "type": f"https://3gpp.org/sbi/problem/{e.cause}",
        "title": e.title,
        "status": e.http_status,
        "cause": e.cause,
        "nasCause": e.nas_cause,
        "detail": detail_override or e.detail,
        "supi": supi,
        "traceId": trace_id,
    }

def nf_error(cause, supi=None, tel=None, ...) -> HTTPException:
    """Raise a properly-formatted 3GPP HTTP error from anywhere in NF code."""
    body = problem_json(cause, supi=supi, ...)
    if tel: tel.inc("errors_by_code_total", code=cause)
    return HTTPException(status_code=lookup(cause).http_status, detail=body)
```

## 3. UDM subscriber state machine

```python
SUBSCRIBER_STATES = (
    "ACTIVE", "BLOCKED", "ROAMING_NOT_ALLOWED",
    "AUTH_KEY_REVOKED", "PROVISIONING_PENDING", "SUSPENDED",
)

# State → (5G cause to emit, lookup scope it affects)
STATE_TO_ERROR = {
    "BLOCKED":              ("ILLEGAL_UE",         "any"),
    "ROAMING_NOT_ALLOWED":  ("ROAMING_NOT_ALLOWED","any"),
    "AUTH_KEY_REVOKED":     ("UE_AUTH_KEY_REVOKED","auth"),
    "PROVISIONING_PENDING": ("SUBSCRIPTION_NOT_FOUND", "profile"),
    "SUSPENDED":            ("USER_NOT_ALLOWED",   "any"),
}

def _check_state(supi, lookup_kind):
    state = SUBSCRIBER_STATE.get(supi, "ACTIVE")
    if state == "ACTIVE": return
    cause, scope = STATE_TO_ERROR.get(state, (None, None))
    if scope == "any" or scope == lookup_kind:
        raise nf_error(cause, supi=supi, tel=tel)
```

State persists in memory until explicitly reset via `POST /subscribers/state/reset` or `POST /subscribers/{supi}/state {state: "ACTIVE"}`.

UDM endpoints:
- `GET /subscribers/{supi}` — calls `_check_state("any")`
- `POST /subscribers/{supi}/auth-vector` — calls `_check_state("auth")`
- `GET /subscribers/{supi}/profile` — calls `_check_state("profile")`
- `POST /subscribers/state/bulk` — set N random subscribers to a state
- `POST /subscribers/state/reset` — all back to ACTIVE
- `GET /subscribers/state/summary` — counts per state

## 4. ML pattern classifier

```python
KNOWN_PATTERNS = [
    {
        "id": "auth-reject-storm",
        "signature_codes": {"ausf": ["AUTH_REJECTED"], "udm": ["UE_AUTH_KEY_REVOKED"]},
        "recommended_actions": [
            "POST /api/orchestrator/subscribers/state/reset",
            ...
        ],
    },
    # 7 more patterns: dnn-mismatch, congestion-cascade, roaming-restriction,
    # slice-capacity, subscription-kaleidoscope, auth-vector-exhaustion,
    # context-loss
]

def _match_pattern(observed, pattern):
    """Return (match_score 0-1, matched_codes list)."""
    sig = pattern["signature_codes"]
    hits = sum(1 for nf, codes in sig.items() for c in codes
               if observed.get(nf, {}).get(c, 0) > 0)
    total = sum(len(codes) for codes in sig.values())
    return hits / total if total else 0.0, [...]

@app.post("/api/ml/classify-failure")
async def classify_failure():
    summary = await get_collector_summary()
    observed = extract_error_codes(summary)
    matches = [match_pattern(observed, p) for p in KNOWN_PATTERNS]
    return {
        "verdict": "issue_detected" if matches else "healthy",
        "matches": ranked_matches,  # by score desc
    }
```

**Recommend-only by design.** The classifier never executes; it returns ranked recommendations for the LLM agent or operator.

## 5. LLM Agent — multi-provider

### 5.1 Configuration

```python
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "mock")  # mock | gemini | ollama | anthropic
LLM_FALLBACK_MOCK = os.getenv("LLM_FALLBACK_MOCK", "1") not in ("0", "false", "no")

# Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Ollama
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

# Anthropic
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")


def _effective_provider() -> str:
    """Provider actually used, after mock-fallback resolution."""
    if LLM_PROVIDER == "mock":
        return "mock"
    if not _provider_has_credentials(LLM_PROVIDER) and LLM_FALLBACK_MOCK:
        return "mock"          # selected provider has no key → degrade to mock
    return LLM_PROVIDER
```

The default is **mock**, so the stack runs keyless. `diagnose`, `remediate`, and
`/healthz` all branch on `_effective_provider()`. A catch-all exception handler
on the app turns any unhandled error into a logged traceback plus a JSON error
body (type + message), so failures are debuggable rather than opaque 500s.

### 5.1a Mock provider (keyless playbook)

`_agent_loop_mock` and `_mock_diagnose` reproduce the agent's behaviour without
an LLM. The remediate loop runs a fixed five-step playbook against the **real**
tools — investigate (`get_topology`, `query_error_codes`, `list_failures`) →
deepen (`classify_failure`, `query_subscriber_states`) → remediate
(`reset_subscribers` for subscriber-state codes, `clear_failure` per faulted NF)
→ verify → summarise — emitting the same Anthropic-style transcript the real
providers produce. `_mock_diagnose` derives root cause / severity / evidence /
recommended actions directly from the telemetry. Because it calls the live
tools, it genuinely fixes injected faults.

### 5.2 Tool definitions (canonical Anthropic-style)

9 tools available to the agent:

```python
TOOLS = [
    {"name": "read_logs",            ...},
    {"name": "query_metrics",        ...},
    {"name": "get_topology",         ...},
    {"name": "list_failures",        ...},
    {"name": "clear_failure",        ...},
    {"name": "query_error_codes",    ...},
    {"name": "query_subscriber_states", ...},
    {"name": "reset_subscribers",    ...},
    {"name": "classify_failure",     ...},
]
```

### 5.3 Tool format conversion

For each provider, convert canonical tools → provider's native format:

**Gemini** (functionDeclarations):
```python
def _tools_to_gemini(tools):
    return [{"functionDeclarations": [
        {"name": t["name"],
         "description": t["description"],
         "parameters": t["input_schema"]}
        for t in tools
    ]}]
```

**Ollama** (OpenAI-compatible):
```python
def _tools_to_ollama(tools):
    return [{"type": "function",
             "function": {"name": t["name"],
                          "description": t["description"],
                          "parameters": t["input_schema"]}}
            for t in tools]
```

**Anthropic**: passes through unchanged.

### 5.4 Agent loop pattern (Gemini example)

```python
async def _agent_loop_gemini(req, transcript, client):
    contents = [{"role": "user", "parts": [{"text": req.user_goal}]}]
    system_inst = {"parts": [{"text": SYSTEM_PROMPT_AGENT}]}

    for iteration in range(req.max_iterations):
        # Call Gemini
        r = await client.post(
            f"{GEMINI_BASE}/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}",
            json={"systemInstruction": system_inst,
                  "contents": contents,
                  "tools": gemini_tools,
                  "generationConfig": {...}})
        body = r.json()
        parts = body["candidates"][0]["content"]["parts"]

        # Build canonical transcript turn
        turn_content = []
        function_calls = []
        for p in parts:
            if "text" in p:
                turn_content.append({"type": "text", "text": p["text"]})
            elif "functionCall" in p:
                turn_content.append({"type": "tool_use", "name": p["functionCall"]["name"],
                                     "input": p["functionCall"]["args"]})
                function_calls.append(p["functionCall"])
        transcript.append({"iteration": iteration, "content": turn_content,
                           "stop_reason": "tool_use" if function_calls else "end_turn"})

        if not function_calls:
            return  # final answer

        # Append model turn + execute tools
        contents.append({"role": "model", "parts": parts})
        response_parts = []
        for fc in function_calls:
            result = await _execute_tool(fc["name"], fc["args"], client)
            response_parts.append({"functionResponse": {
                "name": fc["name"], "response": {"result": json.dumps(result)[:3000]}
            }})
            transcript[-1]["tool_results"].append({...})
        contents.append({"role": "user", "parts": response_parts})
```

The Ollama and Anthropic loops have the same shape, just different request/response formats.

### 5.5 _execute_tool

```python
async def _execute_tool(name, args, client):
    if name == "query_error_codes":
        r = await client.get(f"{COLLECTOR_URL}/api/summary")
        return extract_error_codes(r.json())
    if name == "reset_subscribers":
        r = await client.post(f"{UDM_URL}/subscribers/state/reset")
        return r.json()
    if name == "classify_failure":
        r = await client.post(f"{ML_ENGINE_URL}/api/ml/classify-failure")
        return r.json()
    # ... etc
```

The same function is called regardless of which provider invoked the tool — provider just translates name+args.

## 6. Frontend — key components

### 6.1 Agent.jsx — provider-aware UI

```jsx
function ProviderInfo() {
  const [info, setInfo] = useState(null)
  useEffect(() => {
    fetch('/api/llm/healthz').then(r => r.json()).then(setInfo)
  }, [])

  const isGemini = info?.provider === 'gemini'
  const isOllama = info?.provider === 'ollama'
  const isAnthropic = info?.provider === 'anthropic'

  const ready = isGemini ? info.api_key_configured && info.gemini_reachable
              : isOllama ? info.ollama_reachable && info.model_loaded
              : isAnthropic ? info.api_key_configured : false

  return (
    <Badge>{isGemini ? '◉ GEMINI (FREE)' : isOllama ? '◉ OLLAMA (LOCAL)' : '◉ ANTHROPIC API'}</Badge>
    {ready ? '✓ Ready' : <ErrorMsg />}
  )
}
```

### 6.2 CallFlow.jsx — error code on red arrows

When rendering a return arrow:
```jsx
events.push({
  type: 'arrow-return',
  fromNf: calleeNf, toNf: callerNf,
  label: span.status === 'ok'
       ? '200 OK'
       : (span.attributes?.error_code || span.attributes?.cause || 'ERROR'),
  status: span.status,
})
```

So a red arrow shows `AUTH_REJECTED` or `UE_AUTH_KEY_REVOKED` instead of generic "ERROR".

### 6.3 ErrorCodes.jsx — three sub-tabs

- **LIVE COUNTERS**: auto-refresh every 3s, queries `/api/collector/summary`, extracts `errors_by_code_total{code=X}` counters, displays per-NF table grouped by cause code
- **ML CLASSIFY**: button to call `/api/ml/classify-failure`, renders ranked PatternMatchCards
- **SUBSCRIBER STATE**: live state distribution, bulk-set form, reset-all button

## 7. Deployment artifacts

### 7.1 docker-compose.yml structure

```yaml
services:
  # 7 NFs
  nrf: { build: ., environment: { SERVICE_NAME: nrf, PORT: 8001 }, ... }
  ausf: { ... PORT: 8002 ... }
  udm: { ... PORT: 8003 ... }
  amf: { ... PORT: 8004 ... }
  smf: { ... PORT: 8005 ... }
  upf: { ... PORT: 8006 ... }
  pcf: { ... PORT: 8007 ... }

  # 4 control plane
  collector:    { ports: ["19000:9000"], depends_on: [nrf, ausf, udm, amf, smf, upf, pcf] }
  orchestrator: { ports: ["19001:9001"], depends_on: [collector] }
  ml_engine:    { ports: ["19002:9002"], depends_on: [collector] }
  llm_agent:
    environment:
      LLM_PROVIDER: ${LLM_PROVIDER:-gemini}
      GEMINI_API_KEY: ${GEMINI_API_KEY:-}
      GEMINI_MODEL: ${GEMINI_MODEL:-gemini-1.5-flash}
      OLLAMA_URL: http://ollama:11434
      OLLAMA_MODEL: ${OLLAMA_MODEL:-llama3.1:8b}
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}
      CLAUDE_MODEL: ${CLAUDE_MODEL:-claude-haiku-4-5}
    ports: ["19003:9003"]
    depends_on: [collector, orchestrator]

  # 1 frontend
  frontend: { ports: ["5173:80"], depends_on: [orchestrator, collector, ml_engine, llm_agent] }

  # OPT-IN (only when --profile ollama is passed)
  ollama:      { profiles: ["ollama"], image: ollama/ollama, ports: ["11434:11434"], volumes: [ollama_models:/root/.ollama] }
  ollama_init: { profiles: ["ollama"], depends_on: { ollama: { condition: service_healthy } }, command: pull model }

volumes:
  ollama_models:
```

### 7.2 nginx /api proxy (frontend container's internal nginx)

```nginx
location /api/orchestrator/  { proxy_pass http://orchestrator:9001/api/; }
location /api/collector/     { proxy_pass http://collector:9000/api/; }
location /api/ml/            { proxy_pass http://ml_engine:9002/api/ml/; }
location /api/llm/           { proxy_pass http://llm_agent:9003/api/llm/; }
location /api/subscribers/   { proxy_pass http://orchestrator:9001/api/subscribers/; }
```

### 7.3 Public-facing nginx (separate, on the VPS host)

```nginx
server {
    listen 443 ssl;
    server_name aiops.kennguyen.dev;
    ssl_certificate     /etc/letsencrypt/live/aiops.kennguyen.dev/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/aiops.kennguyen.dev/privkey.pem;
    location / {
        proxy_pass http://127.0.0.1:5173;  # frontend container
        proxy_http_version 1.1;
        proxy_set_header Host $host;
    }
}
server { listen 80; server_name aiops.kennguyen.dev; return 301 https://$host$request_uri; }
```

## 8. Key code files (line counts approx.)

| File | Lines | Role |
|---|---|---|
| `services/nf_common/__init__.py` | 450 | Shared scaffolding |
| `services/nf_common/errors.py` | 130 | 25-code catalog |
| `services/udm/main.py` | 230 | Subscriber DB + state machine |
| `services/orchestrator/main.py` | 410 | Operator API |
| `services/orchestrator/scenarios.py` | 580 | 16 scripted scenarios |
| `services/ml_engine/main.py` | 320 | Anomaly + classifier |
| `services/llm_agent/main.py` | 660 | 3-provider agent (was 460 single-provider) |
| `frontend/src/App.jsx` | 110 | Tab router |
| `frontend/src/components/Agent.jsx` | 290 | LLM agent UI |
| `frontend/src/components/ErrorCodes.jsx` | 350 | 3 sub-tabs |
| `frontend/src/components/CallFlow.jsx` | 540 | SVG sequence diagrams |
| **Total** | **~6500** | |

## 9. Testing approach

The simulator is its own test harness: scenarios trigger predictable failure patterns, the ML classifier matches them with known-good signatures, and the LLM agent is graded on whether it picks the right tools.

To test a specific change:
```bash
# Verify backend parses + loads
cd services && python -c "import udm.main, orchestrator.main, ml_engine.main, llm_agent.main"

# Verify frontend builds
cd frontend && npm run build

# End-to-end smoke test
docker compose up -d
curl http://localhost:19001/api/scenarios/auth-reject-storm/run
sleep 30
curl http://localhost:19002/api/ml/classify-failure  # should match
```

## 10. Operational notes

- **State is in memory only.** All scenario history, subscriber states, telemetry vanish on container restart. This is intentional for the simulator; real deployments would add Postgres/Redis.
- **Logs are JSON to stdout.** `docker compose logs <svc>` shows everything.
- **No authentication.** The orchestrator API is single-tenant. Don't expose to untrusted networks.
- **Outbound traffic.** Only the LLM Agent talks to the internet — to Gemini/Anthropic API. Ollama mode keeps everything internal.
