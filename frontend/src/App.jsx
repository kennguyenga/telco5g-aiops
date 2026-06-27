import { useState, useEffect } from 'react'
import { api } from './api.js'
import Operations from './components/Operations.jsx'
import Topology from './components/Topology.jsx'
import Subscribers from './components/Subscribers.jsx'
import CallFlow from './components/CallFlow.jsx'
import Failures from './components/Failures.jsx'
import ErrorCodes from './components/ErrorCodes.jsx'
import Scenarios from './components/Scenarios.jsx'
import Telemetry from './components/Telemetry.jsx'
import MLView from './components/MLView.jsx'
import Agent from './components/Agent.jsx'

// Primary navigation. Sections with `tabs` show a secondary row.
const NAV = [
  { id: 'operations', label: 'Operations', tabs: [
      { id: 'overview', label: 'Overview' },
      { id: 'topology', label: 'Topology' },
  ] },
  { id: 'inventory',  label: 'Inventory' },        // subscribers
  { id: 'callflow',   label: 'Call flows' },
  { id: 'telemetry',  label: 'Telemetry' },
  { id: 'errors',     label: 'Error codes' },
  { id: 'ml',         label: 'ML engine' },
  { id: 'agent',      label: 'Agent' },
  { id: 'chaos',      label: 'Chaos lab', tabs: [
      { id: 'inject', label: 'Inject' },
      { id: 'scenarios', label: 'Scenarios' },
  ] },
]

function Chip({ dot, label, value }) {
  const dotColor = { ok: 'bg-ok', warn: 'bg-amber-signal', bad: 'bg-alert', idle: 'bg-ink-500' }[dot] || 'bg-ink-500'
  return (
    <div className="flex items-center gap-2 bg-ink-800/80 border border-ink-700 rounded-full pl-2.5 pr-3 py-1">
      <span className={`w-2 h-2 rounded-full ${dotColor} ${dot === 'ok' || dot === 'warn' ? 'animate-pulse-slow' : ''}`} />
      <span className="text-[11px] text-ink-400">{label}</span>
      <span className="text-[11px] font-semibold text-paper">{value}</span>
    </div>
  )
}

function useStatus() {
  const [s, setS] = useState({ llm: null, core: null, telemetry: null, errors: null })
  useEffect(() => {
    let alive = true
    const poll = async () => {
      const out = {}
      try { const h = await api.llmHealth(); out.llm = h } catch { out.llm = { down: true } }
      try {
        const [t, sum] = await Promise.all([api.topology(), api.summary()])
        const up = (t.nodes || []).filter((n) => n.healthy).length
        out.core = { up, total: (t.nodes || []).length || 7 }
        out.telemetry = { flowing: !!sum.nfs, errors: sum.total_errors_5m ?? 0 }
      } catch { out.core = { down: true }; out.telemetry = { down: true } }
      if (alive) setS(out)
    }
    poll(); const id = setInterval(poll, 6000)
    return () => { alive = false; clearInterval(id) }
  }, [])
  return s
}

export default function App() {
  const [section, setSection] = useState('operations')
  const [tab, setTab] = useState('overview')
  const status = useStatus()

  const current = NAV.find((n) => n.id === section)
  const go = (sec, t) => { setSection(sec); setTab(t || (NAV.find((n) => n.id === sec)?.tabs?.[0]?.id) || null) }

  // Header chips, each backed by a real endpoint.
  const llm = status.llm
  const core = status.core
  const tel = status.telemetry

  return (
    <div className="min-h-screen flex flex-col">
      {/* Header */}
      <header className="border-b border-ink-700 bg-ink-900/80 backdrop-blur-sm sticky top-0 z-40">
        <div className="max-w-[1700px] mx-auto px-6 py-3.5 flex flex-wrap items-center gap-x-6 gap-y-3 justify-between">
          <div>
            <h1 className="text-lg font-bold text-paper tracking-tight">
              5G AIOps <span className="text-phosphor">Network Operations Console</span>
            </h1>
            <div className="text-[11px] text-ink-400">Simulated 5G core · LLM SRE agent</div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Chip dot={llm ? (llm.down ? 'bad' : 'ok') : 'idle'} label="LLM"
              value={llm ? (llm.down ? 'offline' : (llm.fallback ? `${llm.provider} · fallback` : llm.provider)) : '…'} />
            <Chip dot={core ? (core.down ? 'bad' : core.up === core.total ? 'ok' : core.up === 0 ? 'bad' : 'warn') : 'idle'}
              label="Core" value={core ? (core.down ? 'down' : `${core.up}/${core.total} up`) : '…'} />
            <Chip dot={tel ? (tel.down ? 'bad' : 'ok') : 'idle'} label="Telemetry"
              value={tel ? (tel.down ? 'idle' : 'flowing') : '…'} />
            <Chip dot={tel ? (tel.down ? 'idle' : tel.errors > 0 ? 'bad' : 'ok') : 'idle'}
              label="Errors 5m" value={tel && !tel.down ? tel.errors : '…'} />
          </div>
        </div>

        {/* Primary nav */}
        <nav className="max-w-[1700px] mx-auto px-6 flex gap-1 overflow-x-auto">
          {NAV.map((n) => (
            <button key={n.id} onClick={() => go(n.id)}
              className={`px-3.5 py-2.5 text-sm whitespace-nowrap border-b-2 transition-colors ${
                section === n.id ? 'border-phosphor text-paper font-semibold' : 'border-transparent text-ink-400 hover:text-paper'
              }`}>
              {n.label}
            </button>
          ))}
        </nav>
      </header>

      {/* Secondary tabs */}
      {current?.tabs && (
        <div className="border-b border-ink-700 bg-ink-900/40">
          <div className="max-w-[1700px] mx-auto px-6 flex gap-1">
            {current.tabs.map((t) => (
              <button key={t.id} onClick={() => setTab(t.id)}
                className={`px-3 py-2 text-[13px] whitespace-nowrap border-b-2 transition-colors ${
                  tab === t.id ? 'border-phosphor text-phosphor font-medium' : 'border-transparent text-ink-400 hover:text-paper'
                }`}>
                {t.label}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Content */}
      <main className="flex-1 max-w-[1700px] mx-auto w-full px-6 py-7">
        <div key={`${section}-${tab}`} className="animate-fade-in">
          {section === 'operations' && tab === 'overview' && <Operations onNavigate={go} />}
          {section === 'operations' && tab === 'topology' && <Topology />}
          {section === 'inventory' && <Subscribers />}
          {section === 'callflow' && <CallFlow />}
          {section === 'telemetry' && <Telemetry />}
          {section === 'errors' && <ErrorCodes />}
          {section === 'ml' && <MLView />}
          {section === 'agent' && <Agent />}
          {section === 'chaos' && tab === 'inject' && <Failures />}
          {section === 'chaos' && tab === 'scenarios' && <Scenarios />}
        </div>
      </main>

      <footer className="border-t border-ink-700 px-6 py-3 text-[11px] text-ink-400 flex flex-wrap justify-between gap-2">
        <span>FastAPI · scikit-learn · React — simulated 5G core</span>
        <span>Not for production use</span>
      </footer>
    </div>
  )
}
