// API client. In dev, Vite proxies. In prod, set VITE_API_BASE if needed.
const BASE = import.meta.env.VITE_API_BASE || ''

async function req(path, options = {}) {
  const r = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!r.ok) {
    const raw = await r.text()
    let msg = raw.slice(0, 300)
    try {
      const j = JSON.parse(raw)
      // FastAPI/agent error bodies: {error,type,hint} or {detail}
      if (j.error) msg = `${j.type ? j.type + ': ' : ''}${j.error}${j.hint ? ` — ${j.hint}` : ''}`
      else if (typeof j.detail === 'string') msg = j.detail
      else if (j.detail?.cause) msg = j.detail.cause
    } catch { /* not JSON — keep raw text */ }
    throw new Error(`${r.status}: ${msg}`)
  }
  return r.json()
}

export const api = {
  // Orchestrator
  topology:           ()      => req('/api/orchestrator/topology'),
  attach:             (body)  => req('/api/orchestrator/subscribers/attach',   { method: 'POST', body: JSON.stringify(body) }),
  detach:             (body)  => req('/api/orchestrator/subscribers/detach',   { method: 'POST', body: JSON.stringify(body) }),
  startLoad:          (body)  => req('/api/orchestrator/subscribers/load',     { method: 'POST', body: JSON.stringify(body) }),
  stopLoad:           ()      => req('/api/orchestrator/subscribers/load/stop',{ method: 'POST' }),
  subscriberState:    ()      => req('/api/orchestrator/subscribers/state'),
  injectFailure:      (body)  => req('/api/orchestrator/failures/inject',      { method: 'POST', body: JSON.stringify(body) }),
  clearFailures:      (nf)    => req(`/api/orchestrator/failures/clear${nf ? `?nf=${nf}` : ''}`, { method: 'POST' }),
  failuresState:      ()      => req('/api/orchestrator/failures/state'),

  // Scenarios
  scenariosList:      ()      => req('/api/orchestrator/scenarios'),
  scenarioRun:        (id)    => req(`/api/orchestrator/scenarios/${id}/run`, { method: 'POST' }),
  scenarioStop:       ()      => req('/api/orchestrator/scenarios/stop', { method: 'POST' }),
  scenarioState:      ()      => req('/api/orchestrator/scenarios/state'),
  scenarioHistory:    ()      => req('/api/orchestrator/scenarios/history'),

  // Call flows
  traceCallflow:      (body)  => req('/api/orchestrator/callflow/trace', { method: 'POST', body: JSON.stringify(body) }),
  recentTraces:       (supi)  => req(`/api/collector/traces/recent${supi ? `?supi=${supi}` : ''}`),
  traceSpans:         (traceId) => req(`/api/collector/traces?trace_id=${traceId}`),

  // Error codes & subscriber state (UDM)
  classifyFailure:    ()      => req('/api/ml/classify-failure', { method: 'POST' }),
  subscriberStateSummary: ()  => req('/api/orchestrator/subscribers/state-summary'),
  setSubscriberState: (body)  => req('/api/orchestrator/subscribers/set-state', { method: 'POST', body: JSON.stringify(body) }),
  resetSubscribers:   ()      => req('/api/orchestrator/subscribers/reset-state', { method: 'POST' }),

  // Collector
  nfStatus:           ()      => req('/api/collector/nfs/status'),
  metrics:            (nf)    => req(`/api/collector/metrics/${nf}?window_seconds=300`),
  logs:               (params = {}) => {
    const q = new URLSearchParams(params).toString()
    return req(`/api/collector/logs${q ? '?' + q : ''}`)
  },
  summary:            ()      => req('/api/collector/summary'),

  // ML
  detectAnomalies:    (nf)    => req(`/api/ml/anomalies${nf ? `?nf=${nf}` : ''}`, { method: 'POST' }),
  forecast:           (nf, metric = 'requests_total') => req(`/api/ml/forecast?nf=${nf}&metric=${metric}`, { method: 'POST' }),

  // LLM
  llmHealth:          ()      => req('/api/llm/healthz'),
  diagnose:           (extra) => req('/api/llm/diagnose', { method: 'POST', body: JSON.stringify({ extra_context: extra || null }) }),
  remediate:          (goal, maxIter = 8) => req('/api/llm/remediate', { method: 'POST', body: JSON.stringify({ user_goal: goal, max_iterations: maxIter }) }),
}
