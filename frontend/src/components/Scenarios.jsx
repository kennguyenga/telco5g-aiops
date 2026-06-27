import { useState, useEffect } from 'react'
import { api } from '../api.js'
import { Panel, Button, Loading, ErrorBox, Tag } from './ui.jsx'

const SEVERITY_COLORS = {
  low:      'phosphor',
  medium:   'amber',
  high:     'amber',
  critical: 'alert',
}

export default function Scenarios() {
  const [catalog, setCatalog] = useState(null)
  const [state, setState] = useState(null)
  const [history, setHistory] = useState([])
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(null)  // scenario id currently launching

  const loadCatalog = async () => {
    try { setCatalog((await api.scenariosList()).scenarios) } catch (e) { setErr(e.message) }
  }
  const loadState = async () => {
    try {
      const [s, h] = await Promise.all([api.scenarioState(), api.scenarioHistory()])
      setState(s); setHistory(h.history || [])
    } catch (e) { setErr(e.message) }
  }

  useEffect(() => { loadCatalog(); loadState() }, [])
  useEffect(() => {
    const id = setInterval(loadState, 2000)
    return () => clearInterval(id)
  }, [])

  const runScenario = async (scenarioId) => {
    setBusy(scenarioId); setErr(null)
    try { await api.scenarioRun(scenarioId) } catch (e) { setErr(e.message) }
    setBusy(null); loadState()
  }

  const stopScenario = async () => {
    try { await api.scenarioStop() } catch (e) { setErr(e.message) }
    loadState()
  }

  return (
    <div className="space-y-6">
      <div className="animate-slide-up">
        <Tag color="amber">SCENARIO LIBRARY</Tag>
        <h1 className="text-4xl font-bold text-paper mt-2">
          Failure <span className="text-amber-signal">Scenarios</span>
        </h1>
        <p className="text-ink-400 mt-2 max-w-3xl">
          Scripted multi-step failure scenarios that combine fault injection with UE load to
          create realistic patterns. Use these to test the LLM agent's diagnosis quality and
          the ML engine's anomaly detection. Each scenario emits markers in logs so the agent
          can be benchmarked against ground truth.
        </p>
      </div>

      {err && <ErrorBox message={err} />}

      {/* ── Active scenario panel ──────────────────────────────────── */}
      {state?.running && (
        <Panel title="ACTIVE SCENARIO" subtitle={state.scenario_id} accent="amber">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-3">
              <div className="w-3 h-3 rounded-full bg-amber-signal animate-pulse" />
              <span className="mono text-amber-signal tracking-widest">RUNNING</span>
              <Tag color="amber">{state.scenario_id?.toUpperCase()}</Tag>
            </div>
            <Button onClick={stopScenario} variant="alert">■ STOP</Button>
          </div>

          <div className="bg-ink-900/60 border border-ink-700 p-3 max-h-72 overflow-y-auto">
            <div className="mono text-[10px] text-ink-400 tracking-widest mb-2">LIVE TRANSCRIPT</div>
            {state.logs?.length ? (
              <div className="space-y-1">
                {state.logs.map((l, i) => (
                  <div key={i} className="mono text-[11px] flex gap-3">
                    <span className="text-ink-400 shrink-0">
                      {new Date(l.timestamp * 1000).toISOString().split('T')[1].split('.')[0]}
                    </span>
                    <span className={
                      l.message.startsWith('INJECT') ? 'text-alert' :
                      l.message.startsWith('CLEAR') ? 'text-phosphor' :
                      l.message.startsWith('ATTACH') ? 'text-amber-signal' :
                      l.message.startsWith('Phase') ? 'text-paper font-bold' :
                      'text-ink-400'
                    }>{l.message}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="mono text-[11px] text-ink-400">starting...</div>
            )}
          </div>
        </Panel>
      )}

      {/* ── Catalog ────────────────────────────────────────────────── */}
      {!catalog ? <Loading message="LOADING SCENARIO CATALOG" /> : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {catalog.map((s) => (
            <ScenarioCard
              key={s.id} scenario={s}
              onRun={() => runScenario(s.id)}
              busy={busy === s.id}
              disabled={state?.running}
            />
          ))}
        </div>
      )}

      {/* ── History ────────────────────────────────────────────────── */}
      {history.length > 0 && (
        <Panel title="RUN HISTORY" subtitle={`${history.length} past runs`}>
          <div className="space-y-2 max-h-96 overflow-y-auto">
            {history.map((run, i) => (
              <details key={i} className="border border-ink-700 bg-ink-900/40">
                <summary className="cursor-pointer px-3 py-2 mono text-xs flex justify-between items-center hover:bg-ink-700/30">
                  <div className="flex items-center gap-3">
                    <Tag color="ink">{new Date(run.started_at * 1000).toLocaleTimeString()}</Tag>
                    <span className="text-paper tracking-widest">{run.scenario_id?.toUpperCase()}</span>
                  </div>
                  <span className="text-ink-400 text-[10px]">
                    {Math.round(run.ended_at - run.started_at)}s · {run.logs?.length || 0} events
                  </span>
                </summary>
                <div className="px-3 py-2 border-t border-ink-700 bg-ink-900/60 max-h-48 overflow-y-auto">
                  {run.logs?.map((l, j) => (
                    <div key={j} className="mono text-[10px] flex gap-2 text-ink-400">
                      <span>{new Date(l.timestamp * 1000).toISOString().split('T')[1].split('.')[0]}</span>
                      <span className="text-paper">{l.message}</span>
                    </div>
                  ))}
                </div>
              </details>
            ))}
          </div>
        </Panel>
      )}
    </div>
  )
}

function ScenarioCard({ scenario, onRun, busy, disabled }) {
  const sevColor = SEVERITY_COLORS[scenario.severity] || 'ink'
  return (
    <div className="bg-ink-800/60 border border-ink-700 p-4 relative">
      <span className="absolute top-0 left-0 w-2 h-2 border-t border-l border-phosphor-dim opacity-60" />
      <span className="absolute top-0 right-0 w-2 h-2 border-t border-r border-phosphor-dim opacity-60" />
      <span className="absolute bottom-0 left-0 w-2 h-2 border-b border-l border-phosphor-dim opacity-60" />
      <span className="absolute bottom-0 right-0 w-2 h-2 border-b border-r border-phosphor-dim opacity-60" />

      <div className="flex items-start justify-between mb-3">
        <div>
          <div className="mono text-[10px] text-ink-400 tracking-widest mb-1">{scenario.id}</div>
          <h3 className="mono font-bold text-paper text-lg tracking-widest">{scenario.name}</h3>
        </div>
        <div className="flex flex-col items-end gap-1">
          <Tag color={sevColor}>{scenario.severity?.toUpperCase()}</Tag>
          <span className="mono text-[10px] text-ink-400">{scenario.duration_s}s</span>
        </div>
      </div>

      <p className="text-xs text-ink-400 mb-3 leading-relaxed">{scenario.description}</p>

      <div className="mb-3">
        <div className="mono text-[9px] text-phosphor-dim tracking-widest mb-1">EXPECTED SYMPTOMS</div>
        <ul className="space-y-0.5">
          {scenario.expected_symptoms?.map((sym, i) => (
            <li key={i} className="text-[10px] text-ink-400 flex gap-1.5">
              <span className="text-phosphor-dim">▸</span>{sym}
            </li>
          ))}
        </ul>
      </div>

      <Button onClick={onRun} disabled={busy || disabled} variant="amber" size="sm">
        {busy ? 'STARTING...' : disabled ? 'WAIT — OTHER SCENARIO RUNNING' : '▶ RUN SCENARIO'}
      </Button>
    </div>
  )
}
