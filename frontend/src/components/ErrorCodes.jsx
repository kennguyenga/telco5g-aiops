import { useState, useEffect } from 'react'
import { api } from '../api.js'
import { Panel, Button, Loading, ErrorBox, Tag, Select, Slider } from './ui.jsx'

const SUBSCRIBER_STATES = [
  { value: 'BLOCKED',              cause: 'ILLEGAL_UE',         desc: 'Administratively blocked' },
  { value: 'ROAMING_NOT_ALLOWED',  cause: 'ROAMING_NOT_ALLOWED', desc: 'Roaming restriction' },
  { value: 'AUTH_KEY_REVOKED',     cause: 'UE_AUTH_KEY_REVOKED', desc: 'Auth key revoked' },
  { value: 'SUSPENDED',            cause: 'USER_NOT_ALLOWED',   desc: 'Billing/admin hold' },
  { value: 'PROVISIONING_PENDING', cause: 'SUBSCRIPTION_NOT_FOUND', desc: 'Record not finalized' },
]

const SEVERITY_COLORS = { low: 'phosphor', medium: 'amber', high: 'amber', critical: 'alert' }

export default function ErrorCodes() {
  const [tab, setTab] = useState('live')
  return (
    <div className="space-y-6">
      <div className="animate-slide-up">
        <Tag color="amber">5G ERROR CODES</Tag>
        <h1 className="text-4xl font-bold text-paper mt-2">
          Error <span className="text-amber-signal">Catalog & Live State</span>
        </h1>
        <p className="text-ink-400 mt-2 max-w-3xl">
          Per-NF emission of TS 29.500 / 24.501 5G cause codes. ML pattern classifier
          matches the live distribution against known scenarios and recommends remediations
          (LLM agent or operator decides whether to apply them).
        </p>
      </div>

      <div className="flex gap-2 border-b border-ink-700 pb-1">
        <Button onClick={() => setTab('live')}     variant={tab==='live' ? 'primary' : 'ghost'}>≡ LIVE COUNTERS</Button>
        <Button onClick={() => setTab('classify')} variant={tab==='classify' ? 'primary' : 'ghost'}>▲ ML CLASSIFY</Button>
        <Button onClick={() => setTab('subs')}     variant={tab==='subs' ? 'primary' : 'ghost'}>◯ SUBSCRIBER STATE</Button>
      </div>

      {tab === 'live'     && <LiveCounters />}
      {tab === 'classify' && <ClassifyView />}
      {tab === 'subs'     && <SubscriberStateView />}
    </div>
  )
}

// ============================================================================
// LIVE COUNTERS — per-NF error code counters from collector summary
// ============================================================================
function LiveCounters() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)

  const load = async () => {
    setErr(null)
    try {
      const summary = await fetch('/api/collector/summary').then(r => r.json())
      // Extract errors_by_code_total{code=X} from each NF
      const out = {}
      let total = 0
      for (const [nf, m] of Object.entries(summary.nfs || {})) {
        if (typeof m !== 'object' || !m) continue
        for (const [k, v] of Object.entries(m)) {
          if (typeof k !== 'string') continue
          if (k.startsWith('errors_by_code_total') && k.includes('code=')) {
            const code = k.split('code=')[1].replace(/\}$/, '').trim()
            out[nf] = out[nf] || {}
            out[nf][code] = (out[nf][code] || 0) + v
            total += v
          }
        }
      }
      setData({ byNf: out, total })
    } catch (e) { setErr(e.message) }
  }

  useEffect(() => { load() }, [])
  useEffect(() => {
    const id = setInterval(load, 3000)
    return () => clearInterval(id)
  }, [])

  if (err) return <ErrorBox message={err} />
  if (!data) return <Loading message="LOADING ERROR COUNTERS" />

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat label="TOTAL ERRORS" value={data.total} color={data.total > 0 ? 'text-alert' : 'text-phosphor'} />
        <Stat label="NFs AFFECTED" value={Object.keys(data.byNf).length} color="text-paper" />
        <Stat label="DISTINCT CODES" value={countDistinctCodes(data.byNf)} color="text-paper" />
        <div className="flex items-end justify-end">
          <Button onClick={load} variant="ghost" size="sm">REFRESH</Button>
        </div>
      </div>

      {Object.keys(data.byNf).length === 0 ? (
        <Panel title="NO ERRORS DETECTED">
          <div className="py-8 text-center mono text-sm text-ink-400">
            <div className="text-3xl text-phosphor mb-2">✓</div>
            All NFs are emitting clean responses.
            <div className="text-[10px] mt-2">
              Inject a coded error or run a code-aware scenario (auth-reject-storm, dnn-mismatch,
              congestion-cascade, etc.) to populate this view.
            </div>
          </div>
        </Panel>
      ) : (
        <Panel title="ERROR CODE COUNTERS BY NF">
          <table className="w-full mono text-[11px]">
            <thead>
              <tr className="border-b border-ink-700 text-ink-400 tracking-widest text-[9px]">
                <th className="text-left py-2">NF</th>
                <th className="text-left">CAUSE CODE</th>
                <th className="text-right">COUNT</th>
                <th className="text-right pr-2">SHARE</th>
              </tr>
            </thead>
            <tbody>
              {flattenForTable(data).map((row, i) => (
                <tr key={i} className="border-b border-ink-700/50">
                  <td className="py-1.5 text-paper tracking-widest">{row.nf.toUpperCase()}</td>
                  <td>
                    <Tag color={severityForCode(row.code)}>{row.code}</Tag>
                  </td>
                  <td className="text-right text-paper">{row.count}</td>
                  <td className="text-right pr-2 text-ink-400">
                    {data.total > 0 ? `${((row.count / data.total) * 100).toFixed(1)}%` : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}
    </div>
  )
}

// ============================================================================
// CLASSIFY VIEW — ML failure classifier
// ============================================================================
function ClassifyView() {
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState(null)

  const classify = async () => {
    setLoading(true); setErr(null)
    try { setResult(await api.classifyFailure()) } catch (e) { setErr(e.message) }
    setLoading(false)
  }

  return (
    <div className="space-y-4">
      <Panel title="ML FAILURE PATTERN CLASSIFIER" subtitle="Recommend-only — never auto-executes">
        <div className="flex items-center justify-between mb-4">
          <div className="text-xs text-ink-400 max-w-2xl">
            Matches the live error-code distribution against a knowledge base of known
            failure patterns. Returns ranked diagnoses with remediation suggestions for
            the LLM agent or operator to apply.
          </div>
          <Button onClick={classify} disabled={loading}>{loading ? 'ANALYZING...' : '▶ CLASSIFY'}</Button>
        </div>

        {err && <ErrorBox message={err} />}
        {loading && <Loading message="MATCHING PATTERNS" />}

        {result && !loading && (
          <div className="space-y-3">
            {result.verdict === 'healthy' && (
              <div className="border border-phosphor/40 bg-phosphor/5 p-4 text-center">
                <div className="text-3xl text-phosphor mb-2">✓</div>
                <div className="mono text-phosphor tracking-widest">{result.summary}</div>
              </div>
            )}

            {result.verdict !== 'healthy' && (
              <>
                <div className="border border-amber-signal/40 bg-amber-signal/5 p-3 mono text-xs text-amber-signal">
                  {result.summary}
                </div>

                {result.matches?.length > 0 ? (
                  <div className="space-y-3">
                    {result.matches.map((m, i) => <PatternMatchCard key={i} match={m} rank={i+1} />)}
                  </div>
                ) : (
                  <div className="border border-ink-700 p-4 mono text-xs text-ink-400">
                    No known pattern matched. Errors detected ({result.total_errors})
                    but the distribution doesn't match any signature in the knowledge base.
                    Use the LLM Agent for reasoning-based diagnosis.
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {!result && !loading && !err && (
          <div className="text-center text-ink-400 mono text-sm py-8">
            PRESS CLASSIFY TO ANALYZE LIVE ERROR DISTRIBUTION
          </div>
        )}
      </Panel>
    </div>
  )
}

function PatternMatchCard({ match, rank }) {
  const sevColor = SEVERITY_COLORS[match.severity] || 'ink'
  return (
    <div className="border border-ink-700 bg-ink-900/40 relative">
      <span className="absolute top-0 left-0 w-2 h-2 border-t border-l border-phosphor-dim opacity-60" />
      <span className="absolute top-0 right-0 w-2 h-2 border-t border-r border-phosphor-dim opacity-60" />
      <div className="px-4 py-3 border-b border-ink-700 flex items-start justify-between">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="mono text-[10px] text-ink-400 tracking-widest">RANK {rank}</span>
            <Tag color={sevColor}>{match.severity?.toUpperCase()}</Tag>
            <span className="mono text-[10px] text-amber-signal">{(match.match_score * 100).toFixed(0)}% MATCH</span>
          </div>
          <h3 className="mono font-bold text-paper text-base tracking-widest">{match.name}</h3>
          <div className="mono text-[10px] text-ink-400 mt-0.5">{match.id}</div>
        </div>
      </div>
      <div className="px-4 py-3 space-y-3">
        <p className="text-xs text-paper">{match.description}</p>

        <div>
          <div className="mono text-[9px] text-phosphor-dim tracking-widest mb-1">LIKELY ROOT CAUSE</div>
          <p className="text-xs text-ink-400">{match.likely_root_cause}</p>
        </div>

        {match.matched_codes?.length > 0 && (
          <div>
            <div className="mono text-[9px] text-phosphor-dim tracking-widest mb-1">EVIDENCE (matched codes)</div>
            <div className="flex flex-wrap gap-1">
              {match.matched_codes.map((c, i) => (
                <span key={i} className="mono text-[10px] px-2 py-0.5 border border-amber-signal/40 text-amber-signal">{c}</span>
              ))}
            </div>
          </div>
        )}

        <div>
          <div className="mono text-[9px] text-amber-signal tracking-widest mb-1">RECOMMENDED ACTIONS (operator/LLM applies)</div>
          <ol className="space-y-1">
            {match.recommended_actions?.map((a, i) => (
              <li key={i} className="text-[11px] text-paper flex gap-2">
                <span className="mono text-amber-signal">{i + 1}.</span>
                <span>{a}</span>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </div>
  )
}

// ============================================================================
// SUBSCRIBER STATE VIEW — bulk-set subscriber states + reset
// ============================================================================
function SubscriberStateView() {
  const [summary, setSummary] = useState(null)
  const [state, setState] = useState('BLOCKED')
  const [count, setCount] = useState(20)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)

  const load = async () => {
    try { setSummary(await api.subscriberStateSummary()) } catch (e) { setErr(e.message) }
  }
  useEffect(() => { load() }, [])
  useEffect(() => { const id = setInterval(load, 3000); return () => clearInterval(id) }, [])

  const apply = async () => {
    setBusy(true); setErr(null)
    try { await api.setSubscriberState({ state, count: Number(count) }) } catch (e) { setErr(e.message) }
    setBusy(false); load()
  }

  const reset = async () => {
    setBusy(true); setErr(null)
    try { await api.resetSubscribers() } catch (e) { setErr(e.message) }
    setBusy(false); load()
  }

  return (
    <div className="space-y-4">
      <Panel title="SUBSCRIBER STATE OVERVIEW" subtitle="Live counts from UDM">
        {err && <ErrorBox message={err} />}
        {!summary ? <Loading /> : (
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            <Stat label="TOTAL" value={summary.total} color="text-paper" />
            <Stat label="ACTIVE" value={summary.by_state?.ACTIVE || 0} color="text-phosphor" />
            <Stat label="NON-ACTIVE" value={summary.non_active_count || 0} color={summary.non_active_count > 0 ? 'text-amber-signal' : 'text-ink-400'} />
            {Object.entries(summary.by_state || {}).filter(([k]) => k !== 'ACTIVE').map(([k, v]) => (
              <Stat key={k} label={k} value={v} color={v > 0 ? 'text-alert' : 'text-ink-400'} />
            ))}
          </div>
        )}
      </Panel>

      <Panel title="BULK-SET SUBSCRIBER STATE" subtitle="Move N random ACTIVE subscribers into a non-ACTIVE state">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <Select label="STATE" value={state} onChange={setState}
            options={SUBSCRIBER_STATES.map(s => ({ value: s.value, label: `${s.value} → ${s.cause}` }))} />
          <div>
            <div className="mono text-[10px] text-ink-400 tracking-widest mb-1">COUNT</div>
            <input type="number" value={count} onChange={(e) => setCount(e.target.value)}
              min={1} max={1000}
              className="mono text-xs bg-ink-900 border border-ink-600 text-paper px-3 py-2 w-full focus:border-phosphor outline-none" />
            <div className="text-[10px] text-ink-400 mt-1">
              {SUBSCRIBER_STATES.find(s => s.value === state)?.desc}
            </div>
          </div>
          <div className="flex items-end gap-2">
            <Button onClick={apply} disabled={busy} variant="alert">⚠ APPLY</Button>
            <Button onClick={reset} disabled={busy} variant="ghost">↺ RESET ALL</Button>
          </div>
        </div>

        <div className="mt-4 pt-4 border-t border-ink-700">
          <div className="mono text-[10px] text-phosphor-dim tracking-widest mb-2">STATE → 5G CAUSE CODE MAPPING</div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-[11px]">
            {SUBSCRIBER_STATES.map(s => (
              <div key={s.value} className="flex justify-between border-b border-ink-700/50 py-1">
                <span className="mono text-paper">{s.value}</span>
                <span className="mono text-amber-signal">{s.cause}</span>
              </div>
            ))}
          </div>
        </div>
      </Panel>
    </div>
  )
}

// ============================================================================
// HELPERS
// ============================================================================
function Stat({ label, value, color = 'text-paper' }) {
  return (
    <div className="border border-ink-700 bg-ink-900/40 px-3 py-2">
      <div className="mono text-[9px] text-ink-400 tracking-widest mb-1">{label}</div>
      <div className={`mono text-2xl ${color}`}>{value}</div>
    </div>
  )
}

function flattenForTable(data) {
  const rows = []
  for (const [nf, codes] of Object.entries(data.byNf)) {
    for (const [code, count] of Object.entries(codes)) {
      rows.push({ nf, code, count })
    }
  }
  rows.sort((a, b) => b.count - a.count)
  return rows
}

function countDistinctCodes(byNf) {
  const set = new Set()
  for (const codes of Object.values(byNf)) {
    for (const code of Object.keys(codes)) set.add(code)
  }
  return set.size
}

// Heuristic severity coloring for code chips
function severityForCode(code) {
  const critical = ['INSUFFICIENT_RESOURCES', 'NF_CONGESTION', 'INSUFFICIENT_SLICE_RESOURCES', 'INTERNAL_ERROR']
  const high = ['AUTH_REJECTED', 'UE_AUTH_KEY_REVOKED', 'CONTEXT_NOT_FOUND', 'UPSTREAM_TIMEOUT']
  if (critical.includes(code)) return 'alert'
  if (high.includes(code)) return 'amber'
  return 'amber'
}
