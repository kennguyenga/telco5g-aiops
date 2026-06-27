import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { Panel, Button, Slider, Loading, ErrorBox, Stat, Tag } from './ui.jsx'

export default function Subscribers() {
  const [state, setState] = useState(null)
  const [count, setCount] = useState(10)
  const [parallelism, setParallelism] = useState(5)
  const [attachPerSec, setAttachPerSec] = useState(5)
  const [detachPerSec, setDetachPerSec] = useState(2)
  const [duration, setDuration] = useState(60)
  const [maxActive, setMaxActive] = useState(200)
  const [running, setRunning] = useState(false)
  const [lastResult, setLastResult] = useState(null)
  const [err, setErr] = useState(null)

  const loadState = async () => {
    try {
      const s = await api.subscriberState()
      setState(s)
    } catch (e) { setErr(e.message) }
  }

  useEffect(() => { loadState(); const id = setInterval(loadState, 2000); return () => clearInterval(id) }, [])

  const doAttach = async () => {
    setRunning(true); setErr(null)
    try { setLastResult(await api.attach({ count, start_index: 1, parallelism })) }
    catch (e) { setErr(e.message) }
    setRunning(false)
  }

  const doDetach = async () => {
    setRunning(true); setErr(null)
    try { setLastResult(await api.detach({ count: count })) }
    catch (e) { setErr(e.message) }
    setRunning(false)
  }

  const startLoad = async () => {
    setRunning(true); setErr(null)
    try {
      await api.startLoad({
        attach_per_second: attachPerSec, detach_per_second: detachPerSec,
        duration_seconds: duration, max_active: maxActive,
      })
    } catch (e) { setErr(e.message) }
    setRunning(false)
  }

  const stopLoad = async () => {
    try { await api.stopLoad() } catch (e) { setErr(e.message) }
  }

  return (
    <div className="space-y-6">
      <div className="animate-slide-up">
        <Tag color="phosphor">SUBSCRIBER SIM</Tag>
        <h1 className="text-4xl font-bold text-paper mt-2">UE <span className="text-phosphor">Lifecycle</span></h1>
        <p className="text-ink-400 mt-2 max-w-2xl">Attach UEs (REGISTER → AKA auth → PROFILE → PDU session) and detach them. Run sustained churn to stress-test the core.</p>
      </div>

      {err && <ErrorBox message={err} />}

      <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
        <Panel><Stat label="ATTACHED UEs" value={state?.attached_count ?? '—'} accent="phosphor" /></Panel>
        <Panel><Stat label="LOAD RUNNING" value={state?.load_running ? 'YES' : 'NO'} accent={state?.load_running ? 'amber' : 'paper'} /></Panel>
        <Panel><Stat label="POOL SIZE" value="1000" unit="UEs" accent="paper" /></Panel>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Panel title="MANUAL CONTROL" subtitle="One-shot batch attach or detach">
          <div className="space-y-5">
            <Slider label="COUNT" value={count} onChange={setCount} min={1} max={200} step={1} unit="UEs" />
            <Slider label="PARALLELISM" value={parallelism} onChange={setParallelism} min={1} max={20} step={1} help="Concurrent attach requests" />
            <div className="flex gap-2">
              <Button onClick={doAttach} disabled={running}>▶ ATTACH {count}</Button>
              <Button onClick={doDetach} disabled={running} variant="amber">◀ DETACH {count}</Button>
            </div>
          </div>
        </Panel>

        <Panel title="LOAD GENERATOR" subtitle="Sustained churn pattern">
          <div className="space-y-4">
            <Slider label="ATTACH RATE" value={attachPerSec} onChange={setAttachPerSec} min={1} max={20} step={1} unit="/s" />
            <Slider label="DETACH RATE" value={detachPerSec} onChange={setDetachPerSec} min={0} max={20} step={1} unit="/s" />
            <Slider label="DURATION" value={duration} onChange={setDuration} min={10} max={600} step={10} unit="s" />
            <Slider label="MAX ACTIVE" value={maxActive} onChange={setMaxActive} min={10} max={500} step={10} unit="UEs" />
            <div className="flex gap-2">
              <Button onClick={startLoad} disabled={running || state?.load_running}>▶ START LOAD</Button>
              <Button onClick={stopLoad} disabled={!state?.load_running} variant="alert">■ STOP</Button>
            </div>
          </div>
        </Panel>
      </div>

      {lastResult && (
        <Panel title="LAST RESULT">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-xs mono">
            <div><div className="text-[9px] text-ink-400 tracking-widest mb-1">REQUESTED</div><div className="text-paper text-lg">{lastResult.requested}</div></div>
            <div><div className="text-[9px] text-ink-400 tracking-widest mb-1">ATTACHED / DETACHED</div><div className="text-phosphor text-lg">{lastResult.attached ?? lastResult.detached ?? 0}</div></div>
            <div><div className="text-[9px] text-ink-400 tracking-widest mb-1">FAILED</div><div className={`text-lg ${lastResult.failed > 0 ? 'text-alert' : 'text-paper'}`}>{lastResult.failed ?? 0}</div></div>
            <div><div className="text-[9px] text-ink-400 tracking-widest mb-1">AVG DURATION</div><div className="text-paper text-lg">{lastResult.avg_duration_ms?.toFixed(0) || '—'} ms</div></div>
          </div>
          {lastResult.results && lastResult.results.length > 0 && (
            <div className="mt-4 pt-4 border-t border-ink-700 max-h-48 overflow-auto">
              <div className="mono text-[9px] text-ink-400 tracking-widest mb-2">SAMPLE RESULTS</div>
              <table className="w-full mono text-[10px]">
                <tbody>
                  {lastResult.results.map((r, i) => (
                    <tr key={i} className="border-b border-ink-700/50">
                      <td className="py-1 text-ink-400">{r.supi}</td>
                      <td className="py-1"><span className={r.status === 'attached' || r.status === 'detached' ? 'text-phosphor' : 'text-alert'}>{r.status}</span></td>
                      <td className="py-1 text-right text-paper">{r.duration_ms?.toFixed(0)}ms</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      )}
    </div>
  )
}
