import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { Panel, Button, Select, Loading, ErrorBox, Tag } from './ui.jsx'

const NF_ORDER = ['amf', 'ausf', 'udm', 'smf', 'upf', 'pcf', 'nrf']

const SERVICE_OF = {
  topology: 'orchestrator', failures: 'orchestrator', scenarioState: 'orchestrator',
  summary: 'collector', logs: 'collector',
}

function relTime(ts) {
  if (!ts) return ''
  const secs = Math.max(0, Math.floor(Date.now() / 1000 - ts))
  if (secs < 60) return `${secs}s ago`
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`
  return `${Math.floor(secs / 3600)}h ago`
}

function faultLabel(cfg) {
  if (!cfg) return null
  if (cfg.blackhole) return 'crash · blackhole'
  if (cfg.unhealthy) return 'reporting unhealthy'
  if (cfg.error_codes && cfg.error_codes.length) return `5G codes: ${cfg.error_codes.join(', ')}`
  if (cfg.error_code_rate > 0) return `coded errors ${Math.round(cfg.error_code_rate * 100)}%`
  if (cfg.extra_latency_ms > 0) return `+${cfg.extra_latency_ms}ms latency`
  if (cfg.error_rate > 0) return `${Math.round(cfg.error_rate * 100)}% 5xx`
  if (cfg.corruption_rate > 0) return `${Math.round(cfg.corruption_rate * 100)}% corruption`
  return null
}

function Kpi({ label, value, tone = 'paper', sub }) {
  const tones = { paper: 'text-paper', ok: 'text-ok', alert: 'text-alert', amber: 'text-amber-signal', phosphor: 'text-phosphor', muted: 'text-ink-400' }
  return (
    <Panel className="px-5 py-4">
      <div className="text-[11px] text-ink-400 tracking-wide mb-1.5">{label}</div>
      <div className={`mono text-3xl font-bold ${tones[tone]}`}>{value}</div>
      {sub && <div className="text-[11px] text-ink-400 mt-1">{sub}</div>}
    </Panel>
  )
}

export default function Operations({ onNavigate }) {
  const [data, setData] = useState({ topology: null, summary: null, failures: null, logs: null, scenarioState: null })
  const [failed, setFailed] = useState({})
  const [catalog, setCatalog] = useState([])
  const [picked, setPicked] = useState('')
  const [busy, setBusy] = useState(false)
  const [firstLoad, setFirstLoad] = useState(true)

  const load = async () => {
    const calls = {
      topology: api.topology(),
      summary: api.summary(),
      failures: api.failuresState(),
      logs: api.logs({ limit: 60 }),
      scenarioState: api.scenarioState(),
    }
    const keys = Object.keys(calls)
    const settled = await Promise.allSettled(keys.map((k) => calls[k]))
    setData((prev) => {
      const next = { ...prev }
      const fails = {}
      settled.forEach((res, i) => {
        const k = keys[i]
        if (res.status === 'fulfilled') next[k] = res.value
        else fails[k] = res.reason?.message || 'error'
      })
      setFailed(fails)
      return next
    })
    setFirstLoad(false)
  }

  useEffect(() => {
    api.scenariosList()
      .then((r) => { const c = r.scenarios || []; setCatalog(c); if (c[0]) setPicked(c[0].id) })
      .catch(() => {})
    load()
    const id = setInterval(load, 4000)
    return () => clearInterval(id)
  }, [])

  const { topology: topo, summary, failures, logs, scenarioState } = data

  if (firstLoad) return <Loading message="Loading operations" />

  if (!topo && !summary) {
    return <ErrorBox message={failed.topology || failed.summary || 'No backend response'} />
  }

  const nodes = topo?.nodes || []
  const up = nodes.filter((n) => n.healthy).length
  const total = nodes.length || 7
  const errors5m = summary?.total_errors_5m ?? 0
  const activeUes = summary?.nfs?.amf?.active_ues ?? 0

  const injected = NF_ORDER
    .map((nf) => ({ nf, label: faultLabel(failures?.nfs?.[nf]) }))
    .filter((x) => x.label)

  const logItems = (logs?.logs || logs || [])
  const errItems = (summary?.recent_errors || [])
  const src = logItems.length ? logItems : errItems
  const warnErr = src.filter((e) => e.level === 'warn' || e.level === 'error')
  const feed = (warnErr.length ? warnErr : src)
    .slice().sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0)).slice(0, 14)

  const failedServices = [...new Set(Object.keys(failed).map((k) => SERVICE_OF[k] || k))]

  const runScenario = async () => {
    if (!picked) return
    setBusy(true)
    try { await api.scenarioRun(picked) } catch (_) {}
    setBusy(false); load()
  }
  const clearAll = async () => { setBusy(true); try { await api.clearFailures() } catch (_) {} setBusy(false); load() }
  const clearOne = async (nf) => { try { await api.clearFailures(nf) } catch (_) {} load() }

  return (
    <div className="space-y-6 animate-fade-in">
      <div>
        <h1 className="text-2xl font-bold text-paper">Operations</h1>
        <p className="text-ink-400 text-sm mt-1">
          Live state of the simulated 5G core. Inject a scenario, watch the core react, then let the agent investigate and remediate.
        </p>
      </div>

      {failedServices.length > 0 && (
        <div className="flex items-center gap-3 bg-amber-signal/10 border border-amber-signal/40 rounded-card px-4 py-2.5">
          <span className="w-2 h-2 rounded-full bg-amber-signal animate-pulse-slow shrink-0" />
          <span className="text-sm text-amber-signal">
            Some data is unavailable — {failedServices.join(', ')} not responding. Showing the latest values that loaded.
          </span>
        </div>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <Kpi label="Core health" value={topo ? `${up}/${total}` : '—'} tone={!topo ? 'muted' : up === total ? 'ok' : up === 0 ? 'alert' : 'amber'} sub="network functions up" />
        <Kpi label="Active injections" value={failures ? injected.length : '—'} tone={!failures ? 'muted' : injected.length ? 'amber' : 'ok'} sub={failures ? (injected.length ? 'faults in effect' : 'none') : 'unavailable'} />
        <Kpi label="Errors / 5 min" value={summary ? errors5m : '—'} tone={!summary ? 'muted' : errors5m > 0 ? 'alert' : 'ok'} sub="across all NFs" />
        <Kpi label="Active UEs" value={summary ? activeUes : '—'} tone="paper" sub="registered on AMF" />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-6">
          <Panel title="Active injections" subtitle={!failures ? 'orchestrator unavailable' : injected.length ? `${injected.length} fault${injected.length > 1 ? 's' : ''} currently applied` : 'Nothing injected'} accent={injected.length ? 'amber' : 'phosphor'}>
            {!failures ? (
              <div className="text-center py-8 text-ink-400 text-sm">Can't reach the orchestrator to read fault state.</div>
            ) : injected.length === 0 ? (
              <div className="text-center py-8 text-ink-400 text-sm">The core is clean. Inject a scenario from the sidebar to create an incident.</div>
            ) : (
              <div className="space-y-2">
                {injected.map(({ nf, label }) => (
                  <div key={nf} className="flex items-center justify-between bg-ink-900/60 border border-ink-700 rounded-lg px-4 py-2.5">
                    <div className="flex items-center gap-3">
                      <span className="w-2 h-2 rounded-full bg-amber-signal animate-pulse-slow" />
                      <span className="mono text-sm text-paper uppercase">{nf}</span>
                      <span className="text-sm text-ink-400">{label}</span>
                    </div>
                    <Button size="sm" variant="ghost" onClick={() => clearOne(nf)}>Clear</Button>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          <Panel title="Recent activity" subtitle="Warnings and errors from the core, newest first">
            {feed.length === 0 ? (
              <div className="text-center py-8 text-ink-400 text-sm">{failed.logs && failed.summary ? 'Telemetry unavailable.' : 'No activity yet.'}</div>
            ) : (
              <div className="divide-y divide-ink-700">
                {feed.map((e, i) => (
                  <div key={i} className="flex items-start gap-3 py-2.5">
                    <Tag color={e.level === 'error' ? 'alert' : e.level === 'warn' ? 'amber' : 'ink'}>{e.level || 'info'}</Tag>
                    <span className="mono text-[11px] text-phosphor uppercase pt-0.5 w-12 shrink-0">{e.nf || '—'}</span>
                    <span className="text-sm text-paper/90 flex-1 leading-snug">{e.message}</span>
                    <span className="text-[11px] text-ink-400 pt-0.5 shrink-0">{relTime(e.timestamp)}</span>
                  </div>
                ))}
              </div>
            )}
          </Panel>
        </div>

        <div className="space-y-6">
          <Panel title="Inject scenario" subtitle="Curated multi-step incident for the agent to solve" accent="amber">
            {scenarioState?.running && (
              <div className="mb-3 flex items-center gap-2 bg-amber-signal/10 border border-amber-signal/40 rounded-lg px-3 py-2">
                <span className="w-2 h-2 rounded-full bg-amber-signal animate-pulse" />
                <span className="text-xs text-amber-signal">Running: {scenarioState.scenario_id}</span>
              </div>
            )}
            <div className="space-y-3">
              {catalog.length > 0 ? (
                <Select value={picked} onChange={setPicked} options={catalog.map((s) => ({ value: s.id, label: s.name || s.id }))} />
              ) : (
                <div className="text-xs text-ink-400">No scenarios available.</div>
              )}
              <Button variant="primary" size="lg" onClick={runScenario} disabled={busy || !picked} className="w-full">
                {busy ? 'Working…' : 'Inject scenario'}
              </Button>
              <Button variant="ghost" size="lg" onClick={clearAll} disabled={busy} className="w-full">Clear all injections</Button>
            </div>
            {picked && catalog.find((s) => s.id === picked)?.description && (
              <p className="text-[11px] text-ink-400 mt-3 leading-snug">{catalog.find((s) => s.id === picked).description}</p>
            )}
          </Panel>

          <Panel title="Resolve with the agent" subtitle="LLM SRE — investigates and fixes">
            <p className="text-sm text-ink-400 mb-3 leading-snug">
              Hand the current incident to the agent. It reads logs, classifies the pattern, clears faults, and verifies recovery.
            </p>
            <Button variant="primary" size="lg" onClick={() => onNavigate && onNavigate('agent')} className="w-full">Open agent</Button>
          </Panel>
        </div>
      </div>
    </div>
  )
}
