import { useState, useEffect, useMemo } from 'react'
import { api } from '../api.js'
import { Panel, Button, Loading, ErrorBox, Tag, Select } from './ui.jsx'

// ─── NF lane order — left to right in the diagram ───────────────────
const LANES = [
  { id: 'ue',   label: 'UE',    accent: 'paper'    },
  { id: 'amf',  label: 'AMF',   accent: 'phosphor' },
  { id: 'ausf', label: 'AUSF',  accent: 'phosphor' },
  { id: 'udm',  label: 'UDM',   accent: 'phosphor' },
  { id: 'smf',  label: 'SMF',   accent: 'phosphor' },
  { id: 'pcf',  label: 'PCF',   accent: 'phosphor' },
  { id: 'upf',  label: 'UPF',   accent: 'phosphor' },
]
const LANE_X = (i) => 60 + i * 140
const LANE_W = LANES.length * 140 + 20

// Map (caller_nf, target_nf) -> (fromIdx, toIdx). Direction is inferred from
// the span "operation" name: "call_X_/path" means caller → X.
const NF_INDEX = Object.fromEntries(LANES.map((l, i) => [l.id, i]))

export default function CallFlow() {
  const [mode, setMode] = useState('live') // live | history | diff
  return (
    <div className="space-y-6">
      <div className="animate-slide-up">
        <Tag color="phosphor">SUBSCRIBER LIFECYCLE</Tag>
        <h1 className="text-4xl font-bold text-paper mt-2">
          Call <span className="text-phosphor">Flow</span>
        </h1>
        <p className="text-ink-400 mt-2 max-w-3xl">
          See the AKA exchange and PDU session establishment as a sequence diagram.
          Each arrow is an inter-NF HTTP call with its actual latency. Failures highlight in red.
        </p>
      </div>

      <div className="flex gap-2">
        <Button onClick={() => setMode('live')} variant={mode === 'live' ? 'primary' : 'ghost'}>
          ▶ LIVE TRACE
        </Button>
        <Button onClick={() => setMode('history')} variant={mode === 'history' ? 'primary' : 'ghost'}>
          ≡ HISTORY
        </Button>
        <Button onClick={() => setMode('diff')} variant={mode === 'diff' ? 'primary' : 'ghost'}>
          ⇆ DIFF
        </Button>
      </div>

      {mode === 'live' && <LiveTrace />}
      {mode === 'history' && <HistoryBrowser />}
      {mode === 'diff' && <DiffView />}
    </div>
  )
}

// ============================================================================
// LIVE TRACE — trigger an attach/detach and watch the spans appear
// ============================================================================
function LiveTrace() {
  const [supi, setSupi] = useState('')             // empty = random
  const [flow, setFlow] = useState('attach_and_session')
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState(null)
  const [spans, setSpans] = useState([])
  const [err, setErr] = useState(null)

  const trigger = async () => {
    setRunning(true); setErr(null); setSpans([]); setResult(null)
    try {
      const body = { flow, apn: 'internet' }
      if (supi.trim()) body.supi = supi.trim()
      const r = await api.traceCallflow(body)
      setResult(r)
      // Now poll for spans (collector scrapes every 5s)
      for (let i = 0; i < 8; i++) {
        await new Promise(res => setTimeout(res, 1500))
        try {
          const s = await api.traceSpans(r.trace_id)
          if (s.spans?.length) setSpans(s.spans)
          if (s.spans?.length >= 4) break  // good enough
        } catch (e) { /* keep trying */ }
      }
    } catch (e) { setErr(e.message) }
    setRunning(false)
  }

  return (
    <div className="space-y-6">
      <Panel title="TRIGGER A FLOW" subtitle="Run an attach or detach with tracing enabled">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div>
            <Select label="FLOW TYPE" value={flow} onChange={setFlow} options={[
              { value: 'attach',              label: 'Attach only (REGISTER + AKA)' },
              { value: 'attach_and_session',  label: 'Attach + PDU session' },
              { value: 'detach',              label: 'Detach (must be attached first)' },
            ]} />
          </div>
          <div>
            <div className="mono text-[10px] text-ink-400 tracking-widest mb-1">SUPI</div>
            <input
              type="text"
              placeholder="leave blank for random"
              value={supi}
              onChange={(e) => setSupi(e.target.value)}
              className="mono text-xs bg-ink-900 border border-ink-600 text-paper px-3 py-2 w-full focus:border-phosphor outline-none"
            />
            <div className="text-[10px] text-ink-400 mt-1">e.g. imsi-001010000000001 (1-1000 provisioned)</div>
          </div>
          <div className="flex items-end">
            <Button onClick={trigger} disabled={running}>
              {running ? 'TRACING...' : '▶ TRACE FLOW'}
            </Button>
          </div>
        </div>
      </Panel>

      {err && <ErrorBox message={err} />}
      {running && !result && <Loading message="EXECUTING FLOW" />}
      {result && (
        <TraceResult result={result} spans={spans} stillPolling={running && spans.length < 4} />
      )}
    </div>
  )
}

function TraceResult({ result, spans, stillPolling }) {
  const isOk = (result.flow === 'detach' ? result.attach_status === 200 :
                result.attach_status === 200 &&
                (result.flow === 'attach' || result.session_status === 200))
  return (
    <div className="space-y-4">
      <Panel
        title="FLOW RESULT"
        subtitle={`${result.flow.toUpperCase()} for ${result.supi} · trace ${result.trace_id?.slice(0, 16)}…`}
        accent={isOk ? 'phosphor' : 'amber'}
      >
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
          <Stat2 label="STATUS" value={isOk ? 'SUCCESS' : 'FAILED'} color={isOk ? 'text-phosphor' : 'text-alert'} />
          <Stat2 label="DURATION" value={`${result.duration_ms?.toFixed(0)} ms`} color="text-paper" />
          <Stat2 label="ATTACH HTTP" value={result.attach_status || '—'}
            color={result.attach_status === 200 ? 'text-phosphor' : 'text-alert'} />
          <Stat2 label="SESSION HTTP" value={result.session_status || '—'}
            color={result.session_status === 200 ? 'text-phosphor' :
                   result.session_status ? 'text-alert' : 'text-ink-400'} />
        </div>
        {result.error && (
          <div className="mt-3 p-2 border border-alert/50 bg-alert/5 mono text-[11px] text-alert">
            {result.error}
          </div>
        )}
      </Panel>

      <Panel title="SEQUENCE DIAGRAM"
             subtitle={`${spans.length} spans collected${stillPolling ? ' (polling for more...)' : ''}`}>
        {spans.length === 0 ? (
          stillPolling
            ? <Loading message="WAITING FOR COLLECTOR" />
            : <div className="text-center text-ink-400 mono text-sm py-12">
                NO SPANS COLLECTED. Spans appear after the collector scrapes (~5s).
              </div>
        ) : (
          <SequenceDiagram spans={spans} />
        )}
      </Panel>
    </div>
  )
}

function Stat2({ label, value, color = 'text-paper' }) {
  return (
    <div>
      <div className="mono text-[9px] text-ink-400 tracking-widest mb-1">{label}</div>
      <div className={`mono text-lg ${color}`}>{value}</div>
    </div>
  )
}

// ============================================================================
// HISTORY — list recent traces, click to view
// ============================================================================
function HistoryBrowser() {
  const [traces, setTraces] = useState(null)
  const [filterSupi, setFilterSupi] = useState('')
  const [selected, setSelected] = useState(null)
  const [spans, setSpans] = useState([])
  const [err, setErr] = useState(null)

  const load = async () => {
    setErr(null)
    try {
      const r = await api.recentTraces(filterSupi.trim() || undefined)
      setTraces(r.traces || [])
    } catch (e) { setErr(e.message) }
  }
  useEffect(() => { load() }, [])

  const open = async (trace) => {
    setSelected(trace); setSpans([])
    try {
      const r = await api.traceSpans(trace.trace_id)
      setSpans(r.spans || [])
    } catch (e) { setErr(e.message) }
  }

  return (
    <div className="space-y-6">
      <Panel title="RECENT TRACES" subtitle="Browse all captured call flows">
        <div className="flex gap-3 mb-4 pb-4 border-b border-ink-700">
          <input
            type="text"
            placeholder="filter by SUPI (optional)"
            value={filterSupi}
            onChange={(e) => setFilterSupi(e.target.value)}
            className="mono text-xs bg-ink-900 border border-ink-600 text-paper px-3 py-2 flex-1 focus:border-phosphor outline-none"
          />
          <Button onClick={load} variant="ghost">REFRESH</Button>
        </div>
        {err && <ErrorBox message={err} />}
        {!traces ? <Loading /> : traces.length === 0 ? (
          <div className="text-center text-ink-400 mono text-sm py-8">
            NO TRACES YET. Run an attach (Subscribers tab or Live trace) first.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full mono text-[11px]">
              <thead>
                <tr className="border-b border-ink-700 text-ink-400 tracking-widest text-[9px]">
                  <th className="text-left py-2">TIME</th>
                  <th className="text-left">OPERATION</th>
                  <th className="text-left">SUPI</th>
                  <th className="text-right">SPANS</th>
                  <th className="text-right">DURATION</th>
                  <th className="text-left pl-3">NFs TOUCHED</th>
                  <th className="text-right">STATUS</th>
                </tr>
              </thead>
              <tbody>
                {traces.map((t) => (
                  <tr
                    key={t.trace_id}
                    onClick={() => open(t)}
                    className={`border-b border-ink-700/50 cursor-pointer hover:bg-ink-700/30 ${
                      selected?.trace_id === t.trace_id ? 'bg-phosphor/5' : ''}`}
                  >
                    <td className="text-ink-400 py-1.5">
                      {t.started_at ? new Date(t.started_at * 1000).toLocaleTimeString() : '—'}
                    </td>
                    <td className="text-paper">{t.operation || '—'}</td>
                    <td className="text-phosphor-dim">{t.supi || '—'}</td>
                    <td className="text-right text-paper">{t.span_count}</td>
                    <td className="text-right text-paper">{t.duration_ms?.toFixed(0)}ms</td>
                    <td className="pl-3 text-ink-400">{t.nfs_touched?.join(', ')}</td>
                    <td className="text-right">
                      <Tag color={t.status === 'ok' ? 'phosphor' : 'alert'}>{t.status?.toUpperCase()}</Tag>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      {selected && (
        <Panel
          title={`TRACE ${selected.trace_id?.slice(0, 16)}…`}
          subtitle={`${selected.operation} · ${selected.supi || 'unknown SUPI'} · ${spans.length} spans`}
        >
          {spans.length === 0 ? <Loading /> : <SequenceDiagram spans={spans} />}
        </Panel>
      )}
    </div>
  )
}

// ============================================================================
// DIFF — compare two traces side by side
// ============================================================================
function DiffView() {
  const [traces, setTraces] = useState(null)
  const [leftId, setLeftId] = useState('')
  const [rightId, setRightId] = useState('')
  const [leftSpans, setLeftSpans] = useState([])
  const [rightSpans, setRightSpans] = useState([])
  const [err, setErr] = useState(null)

  useEffect(() => {
    api.recentTraces().then(r => setTraces(r.traces || [])).catch(e => setErr(e.message))
  }, [])

  useEffect(() => {
    if (leftId) api.traceSpans(leftId).then(r => setLeftSpans(r.spans || [])).catch(() => {})
  }, [leftId])
  useEffect(() => {
    if (rightId) api.traceSpans(rightId).then(r => setRightSpans(r.spans || [])).catch(() => {})
  }, [rightId])

  const traceOptions = (traces || []).map(t => ({
    value: t.trace_id,
    label: `${t.status === 'ok' ? '✓' : '✗'} ${t.operation || '—'} · ${t.supi?.slice(-6) || '?'} · ${t.duration_ms?.toFixed(0)}ms · ${t.trace_id?.slice(0, 8)}`,
  }))

  return (
    <div className="space-y-6">
      <Panel title="DIFF MODE" subtitle="Compare any two traces side by side">
        {err && <ErrorBox message={err} />}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <Select label="LEFT TRACE" value={leftId} onChange={setLeftId}
              options={[{ value: '', label: 'select a trace...' }, ...traceOptions]} />
          </div>
          <div>
            <Select label="RIGHT TRACE" value={rightId} onChange={setRightId}
              options={[{ value: '', label: 'select a trace...' }, ...traceOptions]} />
          </div>
        </div>
      </Panel>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Panel title="LEFT" accent={leftSpans.some(s => s.status === 'error') ? 'amber' : 'phosphor'}>
          {leftSpans.length ? <SequenceDiagram spans={leftSpans} compact /> :
            <div className="text-center text-ink-400 mono text-sm py-8">SELECT A TRACE</div>}
        </Panel>
        <Panel title="RIGHT" accent={rightSpans.some(s => s.status === 'error') ? 'amber' : 'phosphor'}>
          {rightSpans.length ? <SequenceDiagram spans={rightSpans} compact /> :
            <div className="text-center text-ink-400 mono text-sm py-8">SELECT A TRACE</div>}
        </Panel>
      </div>
    </div>
  )
}

// ============================================================================
// SEQUENCE DIAGRAM — the SVG renderer (shared by all three views)
// ============================================================================
function SequenceDiagram({ spans, compact = false }) {
  // Sort spans by start_time
  const sorted = useMemo(() => [...spans].sort((a, b) => a.start_time - b.start_time), [spans])

  if (!sorted.length) return null

  const t0 = sorted[0].start_time
  const tEnd = Math.max(...sorted.map(s => s.end_time))
  const totalMs = (tEnd - t0) * 1000

  // Each span becomes one or two rows in the diagram:
  //   - Spans that represent intra-NF work (e.g. "ue_register" in AMF) → activation box on that lane
  //   - Spans that represent inter-NF calls (operation starts with "call_") → arrow from caller to callee
  // We infer arrows from the operation name ("call_<target>_/path") and from
  // parent_span_id relationships.
  const events = []
  let yCursor = 80

  // Build a map for parent lookup
  const byId = Object.fromEntries(sorted.map(s => [s.span_id, s]))

  sorted.forEach((span, i) => {
    const isCall = span.operation?.startsWith('call_')
    const callerNf = span.nf
    const calleeNf = isCall
      ? span.operation.split('_')[1]   // call_<target>_/path
      : null

    // We treat the first span (no parent) as the "UE -> AMF" arrival
    if (!span.parent_span_id && callerNf === 'amf' && i === 0) {
      events.push({
        type: 'arrow',
        fromNf: 'ue', toNf: 'amf',
        label: span.operation?.toUpperCase().replace(/_/g, ' ') || 'REQUEST',
        latencyMs: span.duration_ms,
        status: span.status,
        y: yCursor,
        spanId: span.span_id,
      })
      yCursor += 50
    }

    if (isCall && calleeNf && NF_INDEX[calleeNf] !== undefined) {
      events.push({
        type: 'arrow',
        fromNf: callerNf, toNf: calleeNf,
        label: span.operation.replace(/^call_[^_]+_/, '').replace(/^\//, '') || 'call',
        latencyMs: span.duration_ms,
        status: span.status,
        y: yCursor,
        spanId: span.span_id,
      })
      yCursor += 38

      // Return arrow
      events.push({
        type: 'arrow-return',
        fromNf: calleeNf, toNf: callerNf,
        label: span.status === 'ok' 
          ? '200 OK' 
          : (span.attributes?.error_code || span.attributes?.cause || 'ERROR'),
        latencyMs: 0,
        status: span.status,
        y: yCursor,
        spanId: span.span_id + '-ret',
      })
      yCursor += 40
    }
  })

  // Final response from AMF back to UE
  const rootSpan = sorted.find(s => !s.parent_span_id)
  if (rootSpan) {
    events.push({
      type: 'arrow-return',
      fromNf: 'amf', toNf: 'ue',
      label: rootSpan.status === 'ok' ? 'OK' : 'FAILED',
      latencyMs: 0,
      status: rootSpan.status,
      y: yCursor,
      spanId: 'final',
    })
    yCursor += 40
  }

  const svgHeight = Math.max(yCursor + 30, 200)

  return (
    <div className="bg-ink-900/50 border border-ink-700 p-4 overflow-x-auto">
      <svg viewBox={`0 0 ${LANE_W} ${svgHeight}`}
           className="w-full"
           style={{ minWidth: compact ? 600 : 1000, maxHeight: compact ? 400 : 600 }}>
        <defs>
          <pattern id="cf-grid" width="40" height="40" patternUnits="userSpaceOnUse">
            <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#1a2129" strokeWidth="0.4" />
          </pattern>
          <marker id="arrowhead-phosphor" markerWidth="8" markerHeight="8" refX="7" refY="3.5"
                  orient="auto" markerUnits="userSpaceOnUse">
            <polygon points="0 0, 8 3.5, 0 7" fill="#7FFFB2" />
          </marker>
          <marker id="arrowhead-amber" markerWidth="8" markerHeight="8" refX="7" refY="3.5"
                  orient="auto" markerUnits="userSpaceOnUse">
            <polygon points="0 0, 8 3.5, 0 7" fill="#FFB547" />
          </marker>
          <marker id="arrowhead-alert" markerWidth="8" markerHeight="8" refX="7" refY="3.5"
                  orient="auto" markerUnits="userSpaceOnUse">
            <polygon points="0 0, 8 3.5, 0 7" fill="#FF5C5C" />
          </marker>
        </defs>
        <rect width={LANE_W} height={svgHeight} fill="url(#cf-grid)" />

        {/* Lane headers + vertical lifelines */}
        {LANES.map((lane, i) => {
          const x = LANE_X(i)
          const color = lane.id === 'ue' ? '#FFB547' : '#7FFFB2'
          return (
            <g key={lane.id}>
              <rect x={x - 35} y={10} width={70} height={32}
                    fill="#0a0e12" stroke={color} strokeWidth="2" />
              <text x={x} y={31} textAnchor="middle" fontSize="14" fontWeight="700"
                    fontFamily="JetBrains Mono" fill={color}>{lane.label}</text>
              <line x1={x} y1={45} x2={x} y2={svgHeight - 10}
                    stroke="#2f3a46" strokeWidth="1" strokeDasharray="3 3" />
            </g>
          )
        })}

        {/* Arrows */}
        {events.map((evt, i) => {
          const fromIdx = NF_INDEX[evt.fromNf]
          const toIdx = NF_INDEX[evt.toNf]
          if (fromIdx === undefined || toIdx === undefined) return null

          const x1 = LANE_X(fromIdx)
          const x2 = LANE_X(toIdx)
          const isReturn = evt.type === 'arrow-return'
          const color = evt.status === 'error' ? '#FF5C5C' :
                        isReturn ? '#FFB547' : '#7FFFB2'
          const markerColor = evt.status === 'error' ? 'alert' :
                              isReturn ? 'amber' : 'phosphor'
          const dashArray = isReturn ? '4 3' : '0'

          // Adjust endpoints so arrow doesn't overlap the lifeline
          const dir = x2 > x1 ? 1 : -1
          const xStart = x1 + dir * 4
          const xEnd = x2 - dir * 8

          // Curve slightly to avoid label overlaps when arrows are close
          const labelX = (x1 + x2) / 2
          const labelY = evt.y - 6

          return (
            <g key={i}>
              <line
                x1={xStart} y1={evt.y} x2={xEnd} y2={evt.y}
                stroke={color} strokeWidth="1.5" strokeDasharray={dashArray}
                markerEnd={`url(#arrowhead-${markerColor})`}
              />
              {/* Label background */}
              <rect
                x={labelX - Math.min(evt.label.length * 3.5, 70)}
                y={labelY - 9}
                width={Math.min(evt.label.length * 7, 140)}
                height={14}
                fill="#0a0e12"
                stroke={color}
                strokeOpacity={0.3}
                strokeWidth="0.5"
              />
              <text
                x={labelX} y={labelY + 1}
                textAnchor="middle"
                fontSize="9"
                fontFamily="JetBrains Mono"
                fill={color}
              >
                {evt.label.length > 22 ? evt.label.slice(0, 22) + '…' : evt.label}
              </text>
              {/* Latency annotation below arrow */}
              {evt.latencyMs > 0 && (
                <text x={labelX} y={evt.y + 12} textAnchor="middle"
                      fontSize="8" fontFamily="JetBrains Mono" fill="#4a5866">
                  {evt.latencyMs.toFixed(1)}ms
                </text>
              )}
            </g>
          )
        })}
      </svg>

      {/* Legend / metadata */}
      <div className="flex flex-wrap gap-4 mt-3 mono text-[10px]">
        <div className="flex items-center gap-1">
          <div className="w-3 h-0.5 bg-phosphor" /><span className="text-ink-400">REQUEST</span>
        </div>
        <div className="flex items-center gap-1">
          <div className="w-3 h-0.5 bg-amber-signal" style={{ backgroundImage: 'repeating-linear-gradient(90deg, #FFB547 0 4px, transparent 4px 7px)', height: 1 }} />
          <span className="text-ink-400">RESPONSE</span>
        </div>
        <div className="flex items-center gap-1">
          <div className="w-3 h-0.5 bg-alert" /><span className="text-ink-400">ERROR</span>
        </div>
        <div className="text-ink-400 ml-auto">
          Total: <span className="text-paper">{totalMs.toFixed(0)}ms</span> · {sorted.length} spans
        </div>
      </div>
    </div>
  )
}
