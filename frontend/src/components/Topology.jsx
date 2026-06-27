import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { Panel, Loading, ErrorBox, Tag, Stat } from './ui.jsx'

const NODE_POS = {
  amf:  { x: 360, y: 80,  role: 'access' },
  ausf: { x: 130, y: 200, role: 'auth' },
  udm:  { x: 360, y: 320, role: 'data' },
  smf:  { x: 590, y: 200, role: 'session' },
  nrf:  { x: 700, y: 80,  role: 'registry' },
  pcf:  { x: 590, y: 380, role: 'policy' },
  upf:  { x: 360, y: 460, role: 'user plane' },
}

export default function Topology() {
  const [topo, setTopo] = useState(null)
  const [summary, setSummary] = useState(null)
  const [err, setErr] = useState(null)

  const load = async () => {
    setErr(null)
    try {
      const [t, s] = await Promise.all([api.topology(), api.summary()])
      setTopo(t); setSummary(s)
    } catch (e) { setErr(e.message) }
  }
  useEffect(() => { load(); const id = setInterval(load, 5000); return () => clearInterval(id) }, [])

  if (err) return <ErrorBox message={err} />
  if (!topo || !summary) return <Loading message="LOADING TOPOLOGY" />

  const healthOf = (nf) => topo.nodes.find((n) => n.id === nf)?.healthy
  const upCount = topo.nodes.filter((n) => n.healthy).length

  return (
    <div className="space-y-6">
      <div className="animate-slide-up">
        <Tag color="phosphor">5G CORE</Tag>
        <h1 className="text-4xl font-bold text-paper mt-2">Network <span className="text-phosphor">Topology</span></h1>
        <p className="text-ink-400 mt-2 max-w-2xl">Service-Based Architecture (SBA) with five Network Functions communicating via HTTP. Healthy NFs glow green; unreachable NFs flash red.</p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Panel><Stat label="NFs ONLINE" value={`${upCount}/${topo.nodes.length}`} accent={upCount === topo.nodes.length ? 'phosphor' : 'amber'} /></Panel>
        <Panel><Stat label="ERRORS / 5MIN" value={summary.total_errors_5m} accent={summary.total_errors_5m > 0 ? 'alert' : 'phosphor'} /></Panel>
        <Panel><Stat label="ACTIVE UEs" value={summary.nfs.amf?.active_ues || 0} accent="paper" /></Panel>
        <Panel><Stat label="ACTIVE SESSIONS" value={summary.nfs.smf?.active_sessions || 0} accent="paper" /></Panel>
      </div>

      <Panel title="SERVICE MESH" subtitle="Live NF interconnect — animations show health status">
        <div className="bg-ink-900/50 border border-ink-700 p-4">
          <svg viewBox="0 0 820 540" className="w-full h-auto" style={{ maxHeight: 560 }}>
            <defs>
              <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
                <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#1a2129" strokeWidth="0.5" />
              </pattern>
            </defs>
            <rect width="820" height="540" fill="url(#grid)" />

            {topo.edges.map(({ from, to }, i) => {
              const p1 = NODE_POS[from], p2 = NODE_POS[to]
              if (!p1 || !p2) return null
              const both = healthOf(from) && healthOf(to)
              return (
                <line key={i} x1={p1.x} y1={p1.y} x2={p2.x} y2={p2.y}
                  stroke={both ? '#4fd488' : '#FF5C5C'}
                  strokeOpacity={both ? 0.4 : 0.6}
                  strokeWidth="1.5"
                  strokeDasharray={both ? '0' : '4 4'}>
                  {!both && <animate attributeName="stroke-opacity" values="0.6;0.2;0.6" dur="1s" repeatCount="indefinite" />}
                </line>
              )
            })}

            {topo.nodes.map((node) => {
              const pos = NODE_POS[node.id]
              if (!pos) return null
              const color = node.healthy ? '#7FFFB2' : '#FF5C5C'
              return (
                <g key={node.id}>
                  {node.healthy && (
                    <circle cx={pos.x} cy={pos.y} r="40" fill="none" stroke={color} strokeWidth="1" opacity="0.3">
                      <animate attributeName="r" from="34" to="46" dur="2.5s" repeatCount="indefinite" />
                      <animate attributeName="opacity" from="0.5" to="0" dur="2.5s" repeatCount="indefinite" />
                    </circle>
                  )}
                  <circle cx={pos.x} cy={pos.y} r="32" fill="#0a0e12" stroke={color} strokeWidth="2.5" />
                  <text x={pos.x} y={pos.y - 4} textAnchor="middle" fontSize="14" fontFamily="JetBrains Mono" fontWeight="700" fill={color}>{node.label}</text>
                  <text x={pos.x} y={pos.y + 12} textAnchor="middle" fontSize="9" fontFamily="JetBrains Mono" fill="#4a5866">:{node.url.split(':').pop()}</text>
                  <text x={pos.x} y={pos.y + 56} textAnchor="middle" fontSize="9" fontFamily="JetBrains Mono" fill="#8a97a4" letterSpacing="1">{pos.role.toUpperCase()}</text>
                </g>
              )
            })}
          </svg>

          <div className="flex gap-4 mt-3 mono text-[10px] flex-wrap">
            <div className="flex items-center gap-2"><div className="w-3 h-3 rounded-full bg-phosphor" /><span className="text-ink-400">HEALTHY</span></div>
            <div className="flex items-center gap-2"><div className="w-3 h-3 rounded-full bg-alert animate-pulse" /><span className="text-ink-400">UNHEALTHY / UNREACHABLE</span></div>
          </div>
        </div>
      </Panel>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {topo.nodes.map((node) => {
          const m = summary.nfs[node.id] || {}
          return (
            <Panel key={node.id} title={node.label} subtitle={node.healthy ? 'Healthy' : 'Unreachable'} accent={node.healthy ? 'phosphor' : 'amber'}>
              <div className="grid grid-cols-2 gap-3 text-xs">
                <div><div className="text-[9px] text-ink-400 mono tracking-widest">REQUESTS</div><div className="text-paper mono text-lg">{m.request_count?.toFixed(0) || 0}</div></div>
                <div><div className="text-[9px] text-ink-400 mono tracking-widest">ERRORS</div><div className={`mono text-lg ${m.error_count > 0 ? 'text-alert' : 'text-paper'}`}>{m.error_count?.toFixed(0) || 0}</div></div>
                <div className="col-span-2"><div className="text-[9px] text-ink-400 mono tracking-widest">P99 LATENCY</div><div className="text-paper mono text-lg">{m.p99_latency_ms?.toFixed(0) || '—'} <span className="text-[10px] text-ink-400">ms</span></div></div>
              </div>
            </Panel>
          )
        })}
      </div>

      {/* UPF Data Plane KPIs — only show when UPF is up */}
      {summary.nfs.upf?.up && (
        <Panel title="UPF DATA PLANE KPIs" subtitle="Live throughput, packet loss, jitter, bearers" accent="phosphor">
          <UPFKpiGrid upf={summary.nfs.upf} />
        </Panel>
      )}

      {/* PCF Policy stats */}
      {summary.nfs.pcf?.up && (
        <Panel title="PCF POLICY ENGINE" subtitle="Active policies and decision rate" accent="phosphor">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-xs">
            <div>
              <div className="mono text-[9px] text-ink-400 tracking-widest mb-1">ACTIVE POLICIES</div>
              <div className="mono text-2xl text-phosphor">{summary.nfs.pcf?.active_policies?.toFixed(0) || 0}</div>
            </div>
            <div>
              <div className="mono text-[9px] text-ink-400 tracking-widest mb-1">DECISIONS</div>
              <div className="mono text-2xl text-paper">{summary.nfs.pcf?.policy_decisions_total?.toFixed(0) || 0}</div>
            </div>
            <div>
              <div className="mono text-[9px] text-ink-400 tracking-widest mb-1">REQUESTS</div>
              <div className="mono text-2xl text-paper">{summary.nfs.pcf?.request_count?.toFixed(0) || 0}</div>
            </div>
            <div>
              <div className="mono text-[9px] text-ink-400 tracking-widest mb-1">ERRORS</div>
              <div className={`mono text-2xl ${summary.nfs.pcf?.error_count > 0 ? 'text-alert' : 'text-paper'}`}>
                {summary.nfs.pcf?.error_count?.toFixed(0) || 0}
              </div>
            </div>
          </div>
        </Panel>
      )}
    </div>
  )
}

function UPFKpiGrid({ upf }) {
  const dl       = upf.dl_throughput_mbps ?? 0
  const ul       = upf.ul_throughput_mbps ?? 0
  const loss     = upf.packet_loss_pct ?? 0
  const jitter   = upf.jitter_ms ?? 0
  const bearers  = upf.active_bearers ?? 0

  // Color thresholds for SLA-style indicators
  const lossColor   = loss > 1.0 ? 'text-alert' : loss > 0.3 ? 'text-amber-signal' : 'text-phosphor'
  const jitterColor = jitter > 20 ? 'text-alert' : jitter > 10 ? 'text-amber-signal' : 'text-phosphor'

  return (
    <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
      <div>
        <div className="mono text-[9px] text-ink-400 tracking-widest mb-1">DL THROUGHPUT</div>
        <div className="mono text-2xl text-phosphor">{dl.toFixed(1)}</div>
        <div className="mono text-[10px] text-ink-400">Mbps</div>
      </div>
      <div>
        <div className="mono text-[9px] text-ink-400 tracking-widest mb-1">UL THROUGHPUT</div>
        <div className="mono text-2xl text-phosphor">{ul.toFixed(1)}</div>
        <div className="mono text-[10px] text-ink-400">Mbps</div>
      </div>
      <div>
        <div className="mono text-[9px] text-ink-400 tracking-widest mb-1">PACKET LOSS</div>
        <div className={`mono text-2xl ${lossColor}`}>{loss.toFixed(2)}</div>
        <div className="mono text-[10px] text-ink-400">%</div>
      </div>
      <div>
        <div className="mono text-[9px] text-ink-400 tracking-widest mb-1">JITTER</div>
        <div className={`mono text-2xl ${jitterColor}`}>{jitter.toFixed(1)}</div>
        <div className="mono text-[10px] text-ink-400">ms</div>
      </div>
      <div>
        <div className="mono text-[9px] text-ink-400 tracking-widest mb-1">ACTIVE BEARERS</div>
        <div className="mono text-2xl text-paper">{bearers.toFixed(0)}</div>
        <div className="mono text-[10px] text-ink-400">sessions</div>
      </div>
    </div>
  )
}
