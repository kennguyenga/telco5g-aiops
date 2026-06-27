import { useState } from 'react'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, ScatterChart, Scatter, ZAxis, Area, ComposedChart, Legend } from 'recharts'
import { api } from '../api.js'
import { Panel, Button, Select, Loading, ErrorBox, Stat, Tag } from './ui.jsx'

const NFs = ['amf', 'smf', 'ausf', 'udm', 'nrf']

export default function MLView() {
  const [nf, setNf] = useState('amf')
  const [anomalies, setAnomalies] = useState(null)
  const [forecast, setForecast] = useState(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState(null)

  const runAnomalies = async () => {
    setLoading(true); setErr(null)
    try { setAnomalies(await api.detectAnomalies()) } catch (e) { setErr(e.message) }
    setLoading(false)
  }

  const runForecast = async () => {
    setLoading(true); setErr(null)
    try { setForecast(await api.forecast(nf, 'requests_total')) } catch (e) { setErr(e.message) }
    setLoading(false)
  }

  return (
    <div className="space-y-6">
      <div className="animate-slide-up">
        <Tag color="phosphor">ML ENGINE</Tag>
        <h1 className="text-4xl font-bold text-paper mt-2">
          Anomaly <span className="text-phosphor">Detection</span>
        </h1>
        <p className="text-ink-400 mt-2 max-w-3xl">
          Isolation Forest runs on each NF's metric stream (request rate, error rate, p99 latency).
          Ridge regression forecasts request volume 15 min ahead with 95% confidence bands.
          Run a load + inject a failure first to generate interesting data.
        </p>
      </div>

      {err && <ErrorBox message={err} />}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Panel title="ANOMALY SCAN" subtitle="Isolation Forest across all NFs">
          <div className="flex gap-2 mb-4">
            <Button onClick={runAnomalies} disabled={loading}>▶ SCAN ALL NFs</Button>
          </div>
          {loading && <Loading message="ANALYZING TELEMETRY" />}
          {!loading && anomalies && (
            <div className="space-y-2">
              {anomalies.results.map((r) => (
                <div key={r.nf} className={`border p-3 ${
                  r.anomaly_count > 0 ? 'border-alert/50 bg-alert/5' : 'border-ink-700 bg-ink-900/40'
                }`}>
                  <div className="flex items-center justify-between mb-2">
                    <span className="mono font-bold text-paper tracking-widest">{r.nf?.toUpperCase()}</span>
                    {r.error ? <Tag color="alert">ERROR</Tag>
                      : r.note ? <Tag color="ink">{r.note?.toUpperCase()}</Tag>
                      : r.anomaly_count > 0 ? <Tag color="alert">{r.anomaly_count} ANOMALIES</Tag>
                      : <Tag color="phosphor">CLEAN</Tag>}
                  </div>
                  {r.samples > 0 && (
                    <div className="grid grid-cols-3 gap-2 text-[10px] mono">
                      <div><span className="text-ink-400">SAMPLES:</span> <span className="text-paper">{r.samples}</span></div>
                      <div><span className="text-ink-400">ANOMALIES:</span> <span className={r.anomaly_count > 0 ? 'text-alert' : 'text-paper'}>{r.anomaly_count}</span></div>
                      <div><span className="text-ink-400">RATE:</span> <span className="text-paper">{((r.anomaly_rate || 0) * 100).toFixed(1)}%</span></div>
                    </div>
                  )}
                  {r.anomalies && r.anomalies.length > 0 && (
                    <div className="mt-3 max-h-32 overflow-y-auto">
                      <table className="w-full mono text-[10px]">
                        <thead><tr className="text-ink-400 tracking-widest text-[9px]">
                          <th className="text-left">TIME</th><th className="text-right">SCORE</th>
                          <th className="text-right">REQ/S</th><th className="text-right">ERR/S</th><th className="text-right">P99 ms</th>
                        </tr></thead>
                        <tbody>
                          {r.anomalies.slice(0, 8).map((a, i) => (
                            <tr key={i} className="border-t border-ink-700/50">
                              <td className="text-ink-400 py-1">{new Date(a.timestamp * 1000).toLocaleTimeString()}</td>
                              <td className="text-right text-alert">{a.score}</td>
                              <td className="text-right text-paper">{a.request_rate}</td>
                              <td className="text-right text-paper">{a.error_rate.toFixed(2)}</td>
                              <td className="text-right text-paper">{a.p99_latency_ms.toFixed(0)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
          {!loading && !anomalies && (
            <div className="text-center text-ink-400 mono text-sm py-12">PRESS SCAN TO RUN ISOLATION FOREST</div>
          )}
        </Panel>

        <Panel title="REQUEST FORECAST" subtitle="Ridge regression — next 15 min">
          <div className="flex gap-2 mb-4">
            <div className="w-32"><Select value={nf} onChange={setNf} options={NFs.map((n) => ({ value: n, label: n.toUpperCase() }))} /></div>
            <Button onClick={runForecast} disabled={loading}>▶ FORECAST</Button>
          </div>
          {loading && <Loading message="FITTING MODEL" />}
          {!loading && forecast && !forecast.error && (
            <ForecastChart forecast={forecast} />
          )}
          {!loading && forecast && forecast.error && (
            <div className="border border-amber-dim/50 bg-amber-signal/5 p-3 mono text-xs text-amber-signal">
              {forecast.error} (samples: {forecast.samples || 0})
            </div>
          )}
          {!loading && !forecast && (
            <div className="text-center text-ink-400 mono text-sm py-12">SELECT NF & PRESS FORECAST</div>
          )}
        </Panel>
      </div>

      <Panel title="HOW IT WORKS">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 text-xs">
          <div>
            <div className="mono text-phosphor tracking-widest text-[11px] mb-2">ISOLATION FOREST</div>
            <p className="text-ink-400 leading-relaxed">
              Builds 100 random decision trees over [request_rate, error_rate, p99_latency].
              Anomalies are isolated in fewer splits than normal points — short paths = anomaly.
              Operates on the rate-of-change of cumulative counters, so it adapts to baseline load levels.
            </p>
          </div>
          <div>
            <div className="mono text-phosphor tracking-widest text-[11px] mb-2">RIDGE FORECAST</div>
            <p className="text-ink-400 leading-relaxed">
              Linear regression with L2 regularization on time + cyclic hour-of-day features.
              Predicts the metric 15 min ahead. Confidence band = ±1.96σ of training residuals
              (95% under normality assumption). Useful for predicting capacity exhaustion before it happens.
            </p>
          </div>
        </div>
      </Panel>
    </div>
  )
}

function ForecastChart({ forecast }) {
  const data = forecast.forecast.map((p) => ({
    t: new Date(p.t * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    predicted: p.predicted,
    band: [p.lower, p.upper],
  }))
  return (
    <>
      <div className="grid grid-cols-3 gap-3 mb-4 text-xs mono">
        <div><div className="text-[9px] text-ink-400 tracking-widest">MAE</div><div className="text-paper">{forecast.fit_quality.mae}</div></div>
        <div><div className="text-[9px] text-ink-400 tracking-widest">RMSE</div><div className="text-paper">{forecast.fit_quality.rmse}</div></div>
        <div><div className="text-[9px] text-ink-400 tracking-widest">HORIZON</div><div className="text-paper">{forecast.forecast.length} pts</div></div>
      </div>
      <div style={{ width: '100%', height: 240 }}>
        <ResponsiveContainer>
          <ComposedChart data={data}>
            <defs>
              <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#FFB547" stopOpacity={0.3} />
                <stop offset="100%" stopColor="#FFB547" stopOpacity={0.05} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="#232c36" strokeDasharray="3 3" />
            <XAxis dataKey="t" stroke="#4a5866" tick={{ fontFamily: 'JetBrains Mono', fontSize: 10 }} />
            <YAxis stroke="#4a5866" tick={{ fontFamily: 'JetBrains Mono', fontSize: 10 }} />
            <Tooltip contentStyle={{ background: '#11161c', border: '1px solid #2f3a46', fontFamily: 'JetBrains Mono', fontSize: 11 }} />
            <Area type="monotone" dataKey="band" stroke="none" fill="url(#bg)" name="95% CI" />
            <Line type="monotone" dataKey="predicted" stroke="#FFB547" strokeWidth={2} dot={false} strokeDasharray="4 2" name="Forecast" />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </>
  )
}
