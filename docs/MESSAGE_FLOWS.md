# 5G AIOps — Message Flows

**Version 2.0** · Sequence diagrams for the major workflows.

GitHub renders Mermaid blocks natively. ASCII fallback follows each.

---

## 1. UE Registration (happy path)

```mermaid
sequenceDiagram
  participant UE
  participant AMF
  participant NRF
  participant AUSF
  participant UDM

  UE->>AMF: POST /api/orchestrator/subscribers/attach<br/>{supi: imsi-001010000000042}

  Note over AMF: Look up AUSF in NRF
  AMF->>NRF: GET /nf-instances?type=AUSF
  NRF-->>AMF: 200 [{nf_instance_id, base_url}]

  AMF->>AUSF: POST /authentications {supi}
  AUSF->>UDM: POST /subscribers/{supi}/auth-vector
  Note over UDM: _check_state(supi, "auth")<br/>State == ACTIVE → continue
  UDM-->>AUSF: 200 {rand, autn, expected_res}
  AUSF-->>AMF: 200 {challenge}

  AMF->>UE: AUTH_REQ (RAND, AUTN)
  UE-->>AMF: AUTH_RESP (RES)

  AMF->>AUSF: POST /authentications/{id}/verify {res}
  AUSF-->>AMF: 200 {result: success}

  AMF->>UDM: GET /subscribers/{supi}/profile
  UDM-->>AMF: 200 {nssai, apn, plmn}

  AMF-->>UE: REGISTRATION ACCEPT
  Note over AMF: UE state: REGISTERED<br/>metric: registrations_total++
```

---

## 2. UE Registration with subscriber-level failure (AUTH_KEY_REVOKED)

```mermaid
sequenceDiagram
  participant UE
  participant AMF
  participant AUSF
  participant UDM

  Note over UDM: SUBSCRIBER_STATE[supi-X] = "AUTH_KEY_REVOKED"<br/>(operator set this earlier via bulk API)

  UE->>AMF: attach(supi-X)
  AMF->>AUSF: POST /authentications
  AUSF->>UDM: POST /subscribers/supi-X/auth-vector

  Note over UDM: _check_state(supi-X, "auth")<br/>state == AUTH_KEY_REVOKED<br/>→ raise nf_error("UE_AUTH_KEY_REVOKED")

  UDM-->>AUSF: 403 application/problem+json<br/>{cause: UE_AUTH_KEY_REVOKED,<br/> nasCause: 20, ...}

  Note over AUSF: NFClient extracts cause from body<br/>span.attributes.error_code = "UE_AUTH_KEY_REVOKED"<br/>tel.inc("errors_by_code_total", code=...)

  AUSF-->>AMF: 403 problem+json (forwarded)

  Note over AMF: registrations_failed_total++<br/>span shows red return arrow<br/>labeled "UE_AUTH_KEY_REVOKED"

  AMF-->>UE: REGISTRATION REJECT (cause #20)
```

---

## 3. UE Registration with NF-level coded error (AUTH_REJECTED)

```mermaid
sequenceDiagram
  participant OP as Operator
  participant ORCH as Orchestrator
  participant AUSF
  participant UE
  participant AMF
  participant UDM

  OP->>ORCH: POST /api/failures/inject<br/>{nf: ausf, type: coded_error,<br/> error_codes: [AUTH_REJECTED],<br/> error_code_rate: 0.5}
  ORCH->>AUSF: POST /failure {error_codes: [AUTH_REJECTED], rate: 0.5}
  Note over AUSF: failure.error_codes = ["AUTH_REJECTED"]<br/>failure.error_code_rate = 0.5

  Note right of AUSF: 50% of subsequent requests<br/>now return AUTH_REJECTED

  UE->>AMF: attach(supi-Y)
  AMF->>AUSF: POST /authentications
  Note over AUSF: middleware rolls dice → 0.5 hit<br/>random.choice(error_codes) = "AUTH_REJECTED"
  AUSF-->>AMF: 403 problem+json<br/>{cause: AUTH_REJECTED, ...}

  Note over AMF: span error_code = AUTH_REJECTED
  AMF-->>UE: REGISTRATION REJECT (cause #20)
```

---

## 4. PDU Session Establishment (happy path)

```mermaid
sequenceDiagram
  participant UE
  participant AMF
  participant SMF
  participant PCF
  participant UPF

  Note over UE,AMF: UE already REGISTERED

  UE->>AMF: PDU_SESSION_ESTABLISHMENT_REQUEST<br/>{dnn: internet}
  AMF->>SMF: POST /pdu-sessions {supi, dnn}

  SMF->>PCF: POST /policies {supi, dnn}
  PCF-->>SMF: 200 {qos_rules, charging_rules}

  SMF->>UPF: POST /pdu-sessions/{id}/bearers<br/>{teid, qfi, ...}
  UPF-->>SMF: 200 {bearer installed}

  SMF-->>AMF: 200 {pdu_session_id, ip}
  AMF-->>UE: PDU_SESSION_ESTABLISHMENT_ACCEPT

  loop every 1s
    UPF->>UPF: emit KPIs (throughput, jitter, packet_loss)
  end
```

---

## 5. ML Classifier diagnosis flow

```mermaid
sequenceDiagram
  participant UI as Error Codes Tab
  participant ML as ML Engine
  participant COLL as Collector

  UI->>ML: POST /api/ml/classify-failure
  ML->>COLL: GET /api/summary
  COLL-->>ML: {nfs: {ausf: {..., errors_by_code_total{code=AUTH_REJECTED}: 47}, ...}}

  Note over ML: Extract per-NF error codes from summary<br/>observed = {ausf: {AUTH_REJECTED: 47},<br/>            udm: {UE_AUTH_KEY_REVOKED: 12}}

  loop For each pattern in KNOWN_PATTERNS
    Note over ML: Compute match_score:<br/>fraction of signature codes<br/>that are present in observed
  end

  Note over ML: Sort by match_score desc<br/>Filter score > 0

  ML-->>UI: {<br/>  matches: [<br/>    {id: "auth-reject-storm",<br/>     match_score: 1.0,<br/>     recommended_actions: [...]}<br/>    ...<br/>  ]<br/>}

  UI->>UI: Render PatternMatchCard<br/>(severity, evidence, remediation)
```

---

## 6. LLM Agent autonomous remediation (mock provider — default)

```mermaid
sequenceDiagram
  participant UI as Agent UI
  participant LLM as LLM Agent
  participant GEM as Gemini API
  participant COLL as Collector
  participant UDM
  participant ML as ML Engine
  participant ORCH as Orchestrator

  UI->>LLM: POST /api/llm/remediate<br/>{user_goal: "Investigate and fix"}

  Note over LLM: _effective_provider() == "mock"<br/>→ _agent_loop_mock() (keyless playbook)<br/>real providers swap in the same loop shape

  rect rgba(60, 80, 60, 0.2)
    Note over LLM,GEM: Iteration 1
    LLM->>GEM: generateContent {sys, contents, tools}
    GEM-->>LLM: functionCall: query_error_codes
    LLM->>COLL: GET /api/summary
    COLL-->>LLM: {ausf: {AUTH_REJECTED: 47}, ...}
    Note over LLM: tool_results[] += {...}<br/>contents += functionResponse
  end

  rect rgba(60, 80, 60, 0.2)
    Note over LLM,GEM: Iteration 2
    LLM->>GEM: generateContent (with prior context)
    GEM-->>LLM: functionCall: query_subscriber_states
    LLM->>UDM: GET /subscribers/state/summary
    UDM-->>LLM: {by_state: {AUTH_KEY_REVOKED: 100, ACTIVE: 900, ...}}
  end

  rect rgba(60, 80, 60, 0.2)
    Note over LLM,GEM: Iteration 3
    LLM->>GEM: generateContent
    GEM-->>LLM: functionCall: classify_failure
    LLM->>ML: POST /api/ml/classify-failure
    ML-->>LLM: {top_match: "auth-reject-storm", score: 0.95}
  end

  rect rgba(80, 60, 60, 0.2)
    Note over LLM,GEM: Iteration 4 — REMEDIATION
    LLM->>GEM: generateContent
    GEM-->>LLM: functionCall: reset_subscribers
    LLM->>UDM: POST /subscribers/state/reset
    UDM-->>LLM: {reset: 100, total: 1000}
  end

  rect rgba(60, 80, 60, 0.2)
    Note over LLM,GEM: Iteration 5 — VERIFICATION
    LLM->>GEM: generateContent
    GEM-->>LLM: functionCall: query_error_codes
    LLM->>COLL: GET /api/summary
    COLL-->>LLM: errors trending down
  end

  rect rgba(60, 60, 80, 0.2)
    Note over LLM,GEM: Iteration 6 — FINAL ANSWER
    LLM->>GEM: generateContent
    GEM-->>LLM: text: "Fixed: 100 subscribers had AUTH_KEY_REVOKED state.<br/>Reset to ACTIVE. Errors are now trending down."
    Note over LLM: stop_reason: end_turn
  end

  LLM-->>UI: {transcript: [...], iterations: 6, provider: gemini}
  UI->>UI: Render transcript with tool calls + results
```

### Total cost on Gemini Flash free tier

```
6 iterations × ~1500 tokens avg = 9000 tokens
Free tier limit: 1,000,000 tokens/min, 1500 requests/day
This run: 6 requests, 9k tokens → well within limits
Cost: $0
```

### ASCII fallback (Gemini agent loop)

```
┌──────┐    ┌─────────┐    ┌──────────┐    ┌─────┐
│ User │    │   LLM   │    │  Gemini  │    │ NFs │
└──┬───┘    └────┬────┘    └────┬─────┘    └──┬──┘
   │             │              │             │
   │ remediate   │              │             │
   ├────────────>│              │             │
   │             │  generateContent           │
   │             ├─────────────>│             │
   │             │              │             │
   │             │<─query_error_codes call    │
   │             │              │             │
   │             ├──── GET /api/summary ──────┼──>│
   │             │              │             │  │
   │             │<───── { error counts } ────┼──┤
   │             │              │             │
   │             │  generateContent (turn 2)  │
   │             ├─────────────>│             │
   │             │              │             │
   │             │  ... 5 more iterations ... │
   │             │              │             │
   │             │<──── final answer text ────┤
   │<── transcript ──────────────────────────┤
```

---

## 7. Multi-step scenario execution (auth-reject-storm)

```mermaid
sequenceDiagram
  participant OP as Operator
  participant ORCH as Orchestrator
  participant UDM
  participant AMF
  participant AUSF

  OP->>ORCH: POST /api/scenarios/auth-reject-storm/run

  Note over ORCH: Phase 1: revoke 50 keys
  ORCH->>UDM: POST /subscribers/state/bulk<br/>{state: AUTH_KEY_REVOKED, count: 50}
  UDM-->>ORCH: {updated: 50}

  Note over ORCH: Phase 2: attach burst
  loop 80 attaches @ 15 parallel
    ORCH->>AMF: POST /attach
    AMF->>AUSF: POST /authentications
    AUSF->>UDM: POST /auth-vector
    alt SUPI in revoked set (~50/950)
      UDM-->>AUSF: 403 UE_AUTH_KEY_REVOKED
    else SUPI healthy
      UDM-->>AUSF: 200 OK
    end
  end

  Note over ORCH: Phase 3: hold 15s for telemetry to accumulate
  ORCH-->>ORCH: sleep(15)

  Note over ORCH: Phase 4: reset
  ORCH->>UDM: POST /subscribers/state/reset
  UDM-->>ORCH: {reset: 50}

  ORCH-->>OP: {scenario: completed, duration_s: 30}
```

This scenario is what the LLM agent then has to diagnose. The expected ML match is "auth-reject-storm" with high confidence.

---

## 8. Call Flow trace generation

```mermaid
sequenceDiagram
  participant UI as Call Flow Tab
  participant ORCH as Orchestrator
  participant AMF
  participant AUSF
  participant UDM
  participant COLL as Collector

  UI->>ORCH: POST /api/callflow/trace<br/>{flow_type: "attach_only", supi?: random}

  ORCH->>ORCH: trace_id = uuid4()
  ORCH->>AMF: POST /attach<br/>(headers: X-Trace-Id, X-Parent-Span-Id)

  Note over AMF: tel.span("attach") emits span<br/>with trace_id, parent_span_id

  AMF->>AUSF: POST /authentications<br/>(propagated trace context)
  AUSF->>UDM: POST /auth-vector<br/>(propagated trace context)

  alt subscriber blocked
    UDM-->>AUSF: 403 UE_AUTH_KEY_REVOKED<br/>(span attributes.error_code set)
    AUSF-->>AMF: 403 (forwarded)
    AMF-->>ORCH: 403
  else healthy
    UDM-->>AUSF: 200<br/>(span attributes empty)
    AUSF-->>AMF: 200
    AMF-->>ORCH: 200
  end

  ORCH-->>UI: {trace_id, status}

  UI->>COLL: GET /api/traces?trace_id={id}
  Note over COLL: Wait ~5s for spans to be scraped
  COLL-->>UI: [{nf, op, status, attributes, parent_span_id}, ...]

  UI->>UI: Render SVG sequence diagram<br/>Red arrows show span.attributes.error_code<br/>(e.g. "UE_AUTH_KEY_REVOKED")
```

---

## 9. Provider switching (Gemini → Anthropic)

This is an operational flow, not a runtime one:

```mermaid
sequenceDiagram
  participant OP as Operator
  participant FILE as /opt/aiops5g/.env
  participant DOCKER as Docker
  participant LLM as llm_agent container
  participant UI as User Browser

  OP->>FILE: edit LLM_PROVIDER=anthropic<br/>add ANTHROPIC_API_KEY=sk-ant-...
  OP->>DOCKER: docker compose restart llm_agent

  DOCKER->>LLM: STOP
  DOCKER->>LLM: START with new env vars

  LLM->>LLM: import time:<br/>LLM_PROVIDER = "anthropic"<br/>load Claude config

  UI->>LLM: GET /healthz
  LLM-->>UI: {provider: "anthropic", model: "claude-haiku-4-5", api_key_configured: true}

  Note over UI: Provider badge updates:<br/>"◉ ANTHROPIC API claude-haiku-4-5"
```

The frontend does not need to redeploy — it just polls `/api/llm/healthz` and updates the badge.

---

## 10. Cold-start sequence

```mermaid
sequenceDiagram
  participant DOCKER as Docker Compose
  participant NRF
  participant NFs as Other 6 NFs
  participant COLL as Collector
  participant CTL as Other Control
  participant FE as Frontend

  DOCKER->>NRF: start
  NRF->>NRF: lifespan: provision built-in registry
  NRF-->>DOCKER: ready

  DOCKER->>NFs: start (parallel)

  par All 6 NFs
    NFs->>NRF: POST /nf-instances (register self)
    NRF-->>NFs: 200 {nf_instance_id}
    Note over NFs: lifespan: spawn heartbeat task<br/>every 30s POST again
  end

  Note over NFs: UDM also: provision 1000 subscribers<br/>SMF also: load policies cache<br/>UPF also: spawn KPI emitter task

  DOCKER->>COLL: start (depends_on: NFs)
  COLL->>COLL: lifespan: spawn scraper task<br/>every 1s for each NF

  DOCKER->>CTL: start (orchestrator, ml_engine, llm_agent)
  DOCKER->>FE: start (depends on control plane)

  FE->>FE: nginx start, serve SPA
  Note over DOCKER: ~30 sec cold start<br/>(builds skip on warm start)
```

---

## 11. Summary of message-flow concepts

| Concept | Where it lives in the code |
|---|---|
| Inter-NF SBI calls | `nf_common.NFClient.call()` |
| Trace context propagation | X-Trace-Id, X-Parent-Span-Id headers (NFClient adds them) |
| Span emission | `Telemetry.span()` async context manager |
| Error code on red arrows | `span.attributes["error_code"]` (set by NFClient on 4xx/5xx) |
| Subscriber state checks | `udm.main._check_state(supi, lookup_kind)` |
| Coded error injection | `nf_common.middleware` lines 270-290 |
| ML pattern matching | `ml_engine.main.KNOWN_PATTERNS` + `_match_pattern()` |
| LLM provider dispatch | `llm_agent.main.remediate()` lines 350-360 |
| Gemini agent loop | `llm_agent.main._agent_loop_gemini()` |
| Tool execution | `llm_agent.main._execute_tool()` |
