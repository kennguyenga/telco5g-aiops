import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { Panel, Button, Select, Loading, ErrorBox, Tag } from './ui.jsx'

const NFs = ['all', 'amf', 'smf', 'ausf', 'udm', 'nrf', 'upf', 'pcf']
const LEVELS = [
  { value: '', label: 'ALL LEVELS' },
  { value: 'info', label: 'INFO' },
  { value: 'warn', label: 'WARN' },
  { value: 'error', label: 'ERROR' },
]

export default function Telemetry() {
  const [logs, setLogs] = useState([])
  const [nf, setNf] = useState('all')
  const [level, setLevel] = useState('')
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [err, setErr] = useState(null)

  const load = async () => {
    setErr(null)
    try {
      const params = { limit: 200 }
      if (nf !== 'all') params.nf = nf
      if (level) params.level = level
      const r = await api.logs(params)
      setLogs(r.logs || [])
    } catch (e) { setErr(e.message) }
  }

  useEffect(() => { load() }, [nf, level])
  useEffect(() => {
    if (!autoRefresh) return
    const id = setInterval(load, 3000)
    return () => clearInterval(id)
  }, [autoRefresh, nf, level])

  return (
    <div className="space-y-6">
      <div className="animate-slide-up">
        <Tag color="phosphor">OBSERVABILITY</Tag>
        <h1 className="text-4xl font-bold text-paper mt-2">Logs & <span className="text-phosphor">Traces</span></h1>
        <p className="text-ink-400 mt-2 max-w-2xl">Structured JSON logs scraped every 5s from each NF by the collector. Each log entry has trace context (trace_id, span_id) and SUPI when applicable.</p>
      </div>

      <Panel title="LOG STREAM" subtitle={`${logs.length} entries`}>
        <div className="flex flex-wrap gap-3 mb-4 pb-4 border-b border-ink-700">
          <div className="w-32"><Select value={nf} onChange={setNf} options={NFs.map((n) => ({ value: n, label: n.toUpperCase() }))} /></div>
          <div className="w-40"><Select value={level} onChange={setLevel} options={LEVELS} /></div>
          <Button onClick={load} variant="ghost" size="sm">REFRESH</Button>
          <Button onClick={() => setAutoRefresh(!autoRefresh)} variant={autoRefresh ? 'primary' : 'ghost'} size="sm">
            {autoRefresh ? '● AUTO' : '○ AUTO'}
          </Button>
        </div>

        {err && <ErrorBox message={err} />}
        {!err && logs.length === 0 && <div className="text-center text-ink-400 mono text-sm py-12">NO LOGS YET. ATTACH SOME UEs OR INJECT FAILURES.</div>}

        <div className="space-y-1 max-h-[600px] overflow-y-auto">
          {logs.map((log, i) => (
            <div key={i} className={`mono text-[11px] p-2 border-l-2 ${
              log.level === 'error' ? 'border-alert bg-alert/5' :
              log.level === 'warn' ? 'border-amber-signal bg-amber-signal/5' :
              'border-ink-600 bg-ink-900/40'
            }`}>
              <div className="flex items-start gap-3 flex-wrap">
                <span className="text-ink-400 shrink-0">{new Date(log.timestamp * 1000).toISOString().split('T')[1].split('.')[0]}</span>
                <Tag color={log.level === 'error' ? 'alert' : log.level === 'warn' ? 'amber' : 'phosphor'}>{log.nf?.toUpperCase()}</Tag>
                <span className={`shrink-0 ${log.level === 'error' ? 'text-alert' : log.level === 'warn' ? 'text-amber-signal' : 'text-ink-400'}`}>[{log.level}]</span>
                <span className="text-paper flex-1">{log.message}</span>
              </div>
              {(log.supi || log.trace_id || (log.extra && Object.keys(log.extra).length > 0)) && (
                <div className="mt-1 ml-20 text-[10px] text-ink-400 flex gap-3 flex-wrap">
                  {log.supi && <span>supi=<span className="text-phosphor-dim">{log.supi}</span></span>}
                  {log.trace_id && <span>trace=<span className="text-phosphor-dim">{log.trace_id?.slice(0, 12)}</span></span>}
                  {log.extra && Object.entries(log.extra).slice(0, 3).map(([k, v]) => (
                    <span key={k}>{k}=<span className="text-phosphor-dim">{typeof v === 'object' ? JSON.stringify(v).slice(0, 30) : String(v).slice(0, 40)}</span></span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </Panel>
    </div>
  )
}
