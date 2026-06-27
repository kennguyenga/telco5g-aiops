import { useState, useEffect } from 'react'
import { api } from '../api.js'
import { Panel, Button, Loading, ErrorBox, Tag } from './ui.jsx'

export default function Agent() {
  const [mode, setMode] = useState('diagnose') // 'diagnose' | 'remediate'
  const [goal, setGoal] = useState('Investigate any active issues in the 5G core and remediate them.')
  const [diagnosis, setDiagnosis] = useState(null)
  const [transcript, setTranscript] = useState(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState(null)

  const runDiagnose = async () => {
    setLoading(true); setErr(null); setDiagnosis(null); setTranscript(null)
    try { setDiagnosis(await api.diagnose()) }
    catch (e) { setErr(e.message) }
    setLoading(false)
  }

  const runRemediate = async () => {
    setLoading(true); setErr(null); setDiagnosis(null); setTranscript(null)
    try { setTranscript(await api.remediate(goal, 8)) }
    catch (e) { setErr(e.message) }
    setLoading(false)
  }

  return (
    <div className="space-y-6">
      <div className="animate-slide-up">
        <Tag color="phosphor">LLM AGENT</Tag>
        <h1 className="text-4xl font-bold text-paper mt-2">
          Claude <span className="text-phosphor">SRE</span>
        </h1>
        <p className="text-ink-400 mt-2 max-w-3xl">
          Claude analyzes 5G core telemetry to identify root causes and remediate failures.
          <span className="block mt-2"><strong className="text-phosphor-dim">Classifier mode</strong> — single-shot diagnosis with no actions.</span>
          <span className="block"><strong className="text-amber-signal">Agent mode</strong> — autonomous loop that calls tools (read_logs, query_metrics, get_topology, list_failures, clear_failure) to investigate and fix issues.</span>
        </p>
      </div>

      {err && <ErrorBox message={err} />}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <Panel title="MODE SELECTOR">
          <div className="space-y-3">
            <button
              onClick={() => setMode('diagnose')}
              className={`w-full text-left p-3 border ${mode === 'diagnose' ? 'border-phosphor bg-phosphor/5' : 'border-ink-700 hover:border-ink-500'}`}>
              <div className="mono text-[10px] text-phosphor tracking-widest mb-1">CLASSIFIER</div>
              <div className="text-xs text-paper">Single-shot diagnosis</div>
              <div className="text-[10px] text-ink-400 mt-1">No tool use. Returns root cause + recommended actions.</div>
            </button>
            <button
              onClick={() => setMode('remediate')}
              className={`w-full text-left p-3 border ${mode === 'remediate' ? 'border-amber-signal bg-amber-signal/5' : 'border-ink-700 hover:border-ink-500'}`}>
              <div className="mono text-[10px] text-amber-signal tracking-widest mb-1">AUTONOMOUS AGENT</div>
              <div className="text-xs text-paper">Investigate + remediate</div>
              <div className="text-[10px] text-ink-400 mt-1">Calls tools in a loop. Up to 8 iterations.</div>
            </button>

            {mode === 'remediate' && (
              <div className="pt-4 border-t border-ink-700">
                <div className="mono text-[10px] text-ink-400 tracking-widest mb-2">GOAL</div>
                <textarea value={goal} onChange={(e) => setGoal(e.target.value)} rows={3}
                  className="w-full mono text-[11px] bg-ink-900 border border-ink-600 text-paper p-2 focus:border-phosphor outline-none resize-none" />
              </div>
            )}

            <div className="pt-3">
              {mode === 'diagnose'
                ? <Button onClick={runDiagnose} disabled={loading}>▶ DIAGNOSE</Button>
                : <Button onClick={runRemediate} disabled={loading} variant="amber">▶ START AGENT</Button>}
            </div>
          </div>

          <div className="mt-6 pt-4 border-t border-ink-700 text-[10px] text-ink-400">
            <div className="mono text-phosphor-dim tracking-widest mb-2">PROVIDER</div>
            <ProviderInfo />
          </div>
        </Panel>

        <div className="lg:col-span-2">
          {loading && (
            <Panel><Loading message={mode === 'diagnose' ? 'LLM ANALYZING' : 'AGENT INVESTIGATING'} /></Panel>
          )}
          {!loading && diagnosis && <DiagnosisView diagnosis={diagnosis} />}
          {!loading && transcript && <AgentTranscript transcript={transcript} />}
          {!loading && !diagnosis && !transcript && !err && (
            <Panel>
              <div className="py-12 text-center">
                <div className="mono text-4xl text-ink-500 mb-3">◉</div>
                <div className="mono text-sm text-ink-400 tracking-wider">
                  PRESS <span className="text-phosphor">{mode === 'diagnose' ? 'DIAGNOSE' : 'START AGENT'}</span> TO BEGIN
                </div>
                <div className="text-[11px] text-ink-400 mt-3 max-w-md mx-auto">
                  Tip: inject a failure (Failures tab) and run some load (Subscribers tab) first to give Claude something to investigate.
                </div>
              </div>
            </Panel>
          )}
        </div>
      </div>
    </div>
  )
}

function DiagnosisView({ diagnosis }) {
  const d = diagnosis.diagnosis
  return (
    <div className="space-y-4">
      <Panel title="ROOT CAUSE ANALYSIS" accent={d.severity === 'critical' || d.severity === 'high' ? 'amber' : 'phosphor'}>
        {d.raw ? (
          <pre className="mono text-[11px] text-paper whitespace-pre-wrap">{d.raw}</pre>
        ) : (
          <div className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <div>
                <div className="mono text-[9px] text-ink-400 tracking-widest mb-1">AFFECTED NF</div>
                <div className="mono text-paper">{d.affected_nf || '—'}</div>
              </div>
              <div>
                <div className="mono text-[9px] text-ink-400 tracking-widest mb-1">SEVERITY</div>
                <div className={`mono ${
                  d.severity === 'critical' ? 'text-alert' :
                  d.severity === 'high' ? 'text-alert' :
                  d.severity === 'medium' ? 'text-amber-signal' :
                  'text-phosphor'}`}>{(d.severity || '—').toUpperCase()}</div>
              </div>
              <div>
                <div className="mono text-[9px] text-ink-400 tracking-widest mb-1">CONFIDENCE</div>
                <div className="mono text-paper">{((d.confidence || 0) * 100).toFixed(0)}%</div>
              </div>
            </div>

            <div>
              <div className="mono text-[10px] text-phosphor tracking-widest mb-2">ROOT CAUSE</div>
              <div className="text-paper bg-ink-900/60 border border-ink-700 p-3">{d.root_cause || '—'}</div>
            </div>

            {d.evidence && d.evidence.length > 0 && (
              <div>
                <div className="mono text-[10px] text-phosphor tracking-widest mb-2">EVIDENCE</div>
                <ul className="space-y-1">
                  {d.evidence.map((e, i) => (
                    <li key={i} className="text-[12px] text-paper flex gap-2"><span className="text-phosphor-dim">▸</span>{e}</li>
                  ))}
                </ul>
              </div>
            )}

            {d.recommended_actions && d.recommended_actions.length > 0 && (
              <div>
                <div className="mono text-[10px] text-amber-signal tracking-widest mb-2">RECOMMENDED ACTIONS</div>
                <ul className="space-y-1">
                  {d.recommended_actions.map((a, i) => (
                    <li key={i} className="text-[12px] text-paper flex gap-2"><span className="text-amber-signal">→</span>{a}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </Panel>
    </div>
  )
}

function AgentTranscript({ transcript }) {
  return (
    <div className="space-y-3">
      <Panel title={`AGENT TRANSCRIPT · ${transcript.iterations} ITERATIONS`}>
        <div className="space-y-3 max-h-[700px] overflow-y-auto">
          {transcript.transcript.map((step, i) => (
            <AgentStep key={i} step={step} index={i} />
          ))}
        </div>
      </Panel>
    </div>
  )
}

function AgentStep({ step, index }) {
  const textBlocks = (step.content || []).filter((c) => c.type === 'text')
  const toolUses = (step.content || []).filter((c) => c.type === 'tool_use')
  const isFinal = step.stop_reason !== 'tool_use'

  return (
    <div className={`border ${isFinal ? 'border-phosphor/50 bg-phosphor/5' : 'border-ink-700 bg-ink-900/40'}`}>
      <div className="px-3 py-2 border-b border-ink-700 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Tag color={isFinal ? 'phosphor' : 'amber'}>STEP {index + 1}</Tag>
          {isFinal && <Tag color="phosphor">FINAL</Tag>}
          <span className="mono text-[10px] text-ink-400 tracking-widest">{step.stop_reason?.toUpperCase()}</span>
        </div>
      </div>

      <div className="p-3 space-y-3">
        {textBlocks.map((block, i) => (
          <div key={i} className="text-[12px] text-paper whitespace-pre-wrap font-sans leading-relaxed">{block.text}</div>
        ))}

        {toolUses.map((tu, i) => {
          const result = (step.tool_results || []).find((r) => r.tool === tu.name && JSON.stringify(r.input) === JSON.stringify(tu.input))
          return (
            <div key={i} className="border-l-2 border-amber-signal pl-3 ml-1">
              <div className="flex items-center gap-2 mb-1">
                <span className="mono text-[10px] text-amber-signal tracking-widest">TOOL CALL</span>
                <span className="mono text-[12px] text-paper">{tu.name}</span>
              </div>
              <pre className="mono text-[10px] text-phosphor-dim bg-ink-900/60 p-2 mb-2 overflow-x-auto">
                {JSON.stringify(tu.input, null, 2)}
              </pre>
              {result && (
                <>
                  <div className="mono text-[10px] text-ink-400 tracking-widest mb-1">RESULT</div>
                  <pre className="mono text-[10px] text-ink-400 bg-ink-900/40 p-2 max-h-32 overflow-auto">
                    {result.result_preview}
                  </pre>
                </>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// Shows which LLM is currently powering the agent (Ollama or Claude)
function ProviderInfo() {
  const [info, setInfo] = useState(null)
  useEffect(() => {
    fetch('/api/llm/healthz')
      .then(r => r.json())
      .then(setInfo)
      .catch(() => setInfo({ provider: 'unknown', error: 'unreachable' }))
  }, [])
  if (!info) return <div className="text-ink-400">Loading...</div>

  const provider = info.provider || 'unknown'
  const isGemini = provider === 'gemini'
  const isOllama = provider === 'ollama'
  const isAnthropic = provider === 'anthropic'

  const ready = isGemini ? (info.api_key_configured && info.gemini_reachable !== false)
              : isOllama ? (info.ollama_reachable && info.model_loaded)
              : isAnthropic ? info.api_key_configured
              : false

  const badge = isGemini ? '◉ GEMINI (FREE)'
              : isOllama ? '◉ OLLAMA (LOCAL)'
              : isAnthropic ? '◉ ANTHROPIC API'
              : '◉ UNKNOWN'
  const badgeColor = isGemini ? 'border-phosphor text-phosphor'
                  : isOllama ? 'border-phosphor text-phosphor'
                  : 'border-amber-signal text-amber-signal'

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <span className={`mono text-[10px] px-2 py-0.5 border ${badgeColor}`}>
          {badge}
        </span>
        <span className="mono text-[10px] text-paper">{info.model}</span>
      </div>
      {isGemini && !info.api_key_configured && (
        <div className="text-alert">⚠ GEMINI_API_KEY not set in .env. Get free key at <span className="text-phosphor-dim">aistudio.google.com/apikey</span></div>
      )}
      {isGemini && info.api_key_configured && info.gemini_reachable === false && (
        <div className="text-alert">⚠ Gemini unreachable: {info.gemini_error}</div>
      )}
      {isOllama && info.ollama_reachable === false && (
        <div className="text-alert">⚠ Ollama unreachable. Check `docker compose ps ollama`.</div>
      )}
      {isOllama && info.ollama_reachable && !info.model_loaded && (
        <div className="text-amber-signal">⚠ Model still downloading. Wait ~5min for first start.</div>
      )}
      {isAnthropic && !info.api_key_configured && (
        <div className="text-alert">⚠ ANTHROPIC_API_KEY not set in .env</div>
      )}
      {ready && <div className="text-phosphor">✓ Ready</div>}
    </div>
  )
}
