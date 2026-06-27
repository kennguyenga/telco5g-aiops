import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { Panel, Button, Slider, Select, Loading, ErrorBox, Tag } from './ui.jsx'

const FAILURE_TYPES = [
  { value: 'nf_slowdown',     label: 'NF Slowdown (latency injection)',         desc: 'Adds intensity × 2000ms latency' },
  { value: 'nf_error_rate',   label: 'NF Error Rate (5xx)',                     desc: 'Returns generic 500 with this probability' },
  { value: 'nf_unhealthy',    label: 'NF Unhealthy',                            desc: '/healthz returns 503 but service still responds' },
  { value: 'nf_crash',        label: 'NF Crash (full blackhole)',               desc: 'Hangs all requests + reports unhealthy' },
  { value: 'packet_corruption', label: 'Packet Corruption',                     desc: 'Returns mangled response bodies' },
  { value: 'intermittent',    label: 'Intermittent (subtle)',                   desc: 'Lower error rate (intensity × 0.3)' },
  { value: 'coded_error',     label: '⚡ 5G Coded Error (TS 29.500 problem+json)', desc: 'NF returns specific 5G cause codes (e.g. AUTH_REJECTED, INSUFFICIENT_RESOURCES)' },
]

// Common error codes by NF for the coded_error preset. UI lets the user multi-select.
const ERROR_CODES_BY_NF = {
  amf:  ['CONTEXT_NOT_FOUND', 'PLMN_NOT_ALLOWED', 'NF_CONGESTION', 'INTERNAL_ERROR'],
  ausf: ['AUTH_REJECTED', 'MAC_FAILURE', 'SYNCH_FAILURE', 'NON_5G_AUTH_UNACCEPTABLE'],
  udm:  ['USER_NOT_FOUND', 'USER_NOT_ALLOWED', 'ROAMING_NOT_ALLOWED', 'ILLEGAL_UE', 'UE_AUTH_KEY_REVOKED'],
  smf:  ['DNN_NOT_SUPPORTED', 'INSUFFICIENT_SLICE_RESOURCES', 'INVALID_PDU_SESSION_ID', 'CONTEXT_NOT_FOUND'],
  upf:  ['INSUFFICIENT_RESOURCES', 'NF_CONGESTION'],
  pcf:  ['DNN_NOT_SUPPORTED', 'OPERATION_NOT_ALLOWED', 'POLICY_NOT_FOUND'],
  nrf:  ['NF_CONGESTION', 'INTERNAL_ERROR'],
}

const NFs = ['amf', 'smf', 'ausf', 'udm', 'nrf', 'upf', 'pcf']

export default function Failures() {
  const [nf, setNf] = useState('amf')
  const [failureType, setFailureType] = useState('nf_slowdown')
  const [intensity, setIntensity] = useState(0.5)
  const [selectedCodes, setSelectedCodes] = useState([])
  const [state, setState] = useState(null)
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)

  const load = async () => {
    try { setState(await api.failuresState()) } catch (e) { setErr(e.message) }
  }
  useEffect(() => { load(); const id = setInterval(load, 3000); return () => clearInterval(id) }, [])

  const inject = async () => {
    setBusy(true); setErr(null)
    try { 
      const body = { nf, failure_type: failureType, intensity }
      if (failureType === 'coded_error') body.error_codes = selectedCodes
      await api.injectFailure(body) 
    }
    catch (e) { setErr(e.message) }
    setBusy(false); load()
  }

  const clearOne = async (target) => {
    setBusy(true); setErr(null)
    try { await api.clearFailures(target) } catch (e) { setErr(e.message) }
    setBusy(false); load()
  }

  const isFaulty = (cfg) => cfg && (cfg.error_rate > 0 || cfg.extra_latency_ms > 0 || cfg.blackhole || cfg.unhealthy || cfg.corruption_rate > 0)

  return (
    <div className="space-y-6">
      <div className="animate-slide-up">
        <Tag color="amber">CHAOS</Tag>
        <h1 className="text-4xl font-bold text-paper mt-2">Failure <span className="text-amber-signal">Injection</span></h1>
        <p className="text-ink-400 mt-2 max-w-2xl">Deliberately break NFs to test detection and remediation. Each NF exposes a <span className="mono text-phosphor-dim">/failure</span> endpoint that the orchestrator calls to set fault parameters.</p>
      </div>

      {err && <ErrorBox message={err} />}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <Panel title="INJECT FAULT" subtitle="Configure and apply">
          <div className="space-y-4">
            <Select label="TARGET NF" value={nf} onChange={setNf} options={NFs.map((n) => ({ value: n, label: n.toUpperCase() }))} />
            <Select label="FAILURE TYPE" value={failureType} onChange={setFailureType} options={FAILURE_TYPES} />
            <div className="text-[10px] text-ink-400 -mt-2">{FAILURE_TYPES.find((f) => f.value === failureType)?.desc}</div>
            <Slider label="INTENSITY" value={intensity} onChange={setIntensity} min={0.1} max={1.0} step={0.1} help="0.1=mild, 1.0=severe" />

            {failureType === 'coded_error' && (
              <div className="border border-amber-signal/30 bg-amber-signal/5 p-3">
                <div className="mono text-[10px] text-amber-signal tracking-widest mb-2">
                  5G ERROR CODES TO INJECT
                </div>
                <div className="text-[10px] text-ink-400 mb-3">
                  NF will return TS 29.500 application/problem+json with one of the
                  selected codes randomly chosen per request (rate = intensity).
                  Tap to toggle.
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {(ERROR_CODES_BY_NF[nf] || []).map((code) => {
                    const active = selectedCodes.includes(code)
                    return (
                      <button
                        key={code}
                        onClick={() => setSelectedCodes(
                          active ? selectedCodes.filter((c) => c !== code)
                                 : [...selectedCodes, code]
                        )}
                        className={`mono text-[10px] px-2 py-1 border transition ${
                          active
                            ? 'border-amber-signal bg-amber-signal/20 text-amber-signal'
                            : 'border-ink-600 text-ink-400 hover:border-ink-500'
                        }`}
                      >
                        {active ? '✓ ' : ''}{code}
                      </button>
                    )
                  })}
                </div>
                {selectedCodes.length === 0 && (
                  <div className="mono text-[10px] text-alert mt-2">
                    Select at least one code before injecting
                  </div>
                )}
              </div>
            )}
            <div className="flex gap-2 pt-2">
              <Button onClick={inject} disabled={busy || (failureType === 'coded_error' && selectedCodes.length === 0)} variant="alert">⚠ INJECT</Button>
              <Button onClick={() => clearOne(nf)} disabled={busy} variant="ghost">CLEAR {nf.toUpperCase()}</Button>
            </div>
          </div>
        </Panel>

        <Panel title="ACTIVE FAULTS" subtitle="Current state across all NFs" className="lg:col-span-2">
          {state ? (
            <div className="space-y-2">
              {state.nfs.map((entry) => {
                const broken = isFaulty(entry.config)
                return (
                  <div key={entry.nf} className={`border p-3 ${broken ? 'border-alert/50 bg-alert/5' : 'border-ink-700 bg-ink-900/40'}`}>
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-3">
                        <span className={`mono font-bold tracking-widest ${broken ? 'text-alert' : 'text-phosphor'}`}>{entry.nf.toUpperCase()}</span>
                        {broken && <Tag color="alert">FAULT INJECTED</Tag>}
                        {!entry.reachable && <Tag color="alert">UNREACHABLE</Tag>}
                        {!broken && entry.reachable && <Tag color="phosphor">HEALTHY</Tag>}
                      </div>
                      {broken && (
                        <Button onClick={() => clearOne(entry.nf)} variant="ghost" size="sm">CLEAR</Button>
                      )}
                    </div>
                    {entry.config && (
                      <div className="grid grid-cols-2 md:grid-cols-5 gap-2 text-[10px] mono">
                        <FaultBit label="ERR_RATE" value={entry.config.error_rate} format={(v) => `${(v * 100).toFixed(0)}%`} highlight={entry.config.error_rate > 0} />
                        <FaultBit label="LATENCY" value={entry.config.extra_latency_ms} format={(v) => `${v}ms`} highlight={entry.config.extra_latency_ms > 0} />
                        <FaultBit label="BLACKHOLE" value={entry.config.blackhole ? 'YES' : 'NO'} highlight={entry.config.blackhole} />
                        <FaultBit label="UNHEALTHY" value={entry.config.unhealthy ? 'YES' : 'NO'} highlight={entry.config.unhealthy} />
                        <FaultBit label="CORRUPTION" value={entry.config.corruption_rate} format={(v) => `${(v * 100).toFixed(0)}%`} highlight={entry.config.corruption_rate > 0} />
                      </div>
                    )}
                  </div>
                )
              })}
              <div className="pt-3">
                <Button onClick={() => clearOne(null)} variant="ghost">CLEAR ALL</Button>
              </div>
            </div>
          ) : <Loading message="LOADING STATE" />}
        </Panel>
      </div>

      <Panel title="FAILURE CATALOG">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {FAILURE_TYPES.map((f) => (
            <div key={f.value} className="border border-ink-700 p-3 bg-ink-900/40">
              <div className="mono text-xs text-phosphor mb-1">{f.value}</div>
              <div className="text-[11px] text-paper mb-1">{f.label}</div>
              <div className="text-[10px] text-ink-400">{f.desc}</div>
            </div>
          ))}
        </div>
      </Panel>
    </div>
  )
}

function FaultBit({ label, value, format, highlight }) {
  const display = format ? format(value) : value
  return (
    <div>
      <div className="text-[9px] text-ink-400 tracking-widest">{label}</div>
      <div className={highlight ? 'text-alert' : 'text-ink-400'}>{display}</div>
    </div>
  )
}
