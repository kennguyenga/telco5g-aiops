export function Panel({ children, className = '', title, subtitle, accent = 'phosphor' }) {
  const accentColor = accent === 'amber' ? 'text-amber-signal' : accent === 'alert' ? 'text-alert' : 'text-phosphor'
  return (
    <div className={`bg-ink-800/70 border border-ink-700 rounded-card ${className}`}>
      {(title || subtitle) && (
        <div className="px-5 py-3.5 border-b border-ink-700">
          {title && <div className={`text-[12px] font-semibold tracking-wide ${accentColor}`}>{title}</div>}
          {subtitle && <div className="text-xs text-ink-400 mt-0.5">{subtitle}</div>}
        </div>
      )}
      <div className="p-5">{children}</div>
    </div>
  )
}

export function Stat({ label, value, unit, accent = 'paper' }) {
  const colors = { paper: 'text-paper', phosphor: 'text-phosphor', amber: 'text-amber-signal', alert: 'text-alert', ok: 'text-ok' }
  return (
    <div>
      <div className="text-[11px] text-ink-400 tracking-wide mb-1.5">{label}</div>
      <div className="flex items-baseline gap-1">
        <span className={`mono text-3xl font-bold ${colors[accent]}`}>{value}</span>
        {unit && <span className="mono text-xs text-ink-400">{unit}</span>}
      </div>
    </div>
  )
}

export function Button({ children, onClick, disabled, variant = 'primary', size = 'md', className = '' }) {
  const variants = {
    primary: 'bg-phosphor text-ink-950 font-semibold border border-phosphor hover:bg-phosphor/90 shadow-[0_1px_0_rgba(255,255,255,0.12)_inset]',
    ghost:   'bg-ink-700/40 border border-ink-600 text-ink-400 hover:text-paper hover:border-ink-500',
    amber:   'bg-amber-signal/15 border border-amber-signal/70 text-amber-signal hover:bg-amber-signal/25',
    alert:   'bg-alert/15 border border-alert/70 text-alert hover:bg-alert/25',
  }
  const sizes = { sm: 'px-2.5 py-1 text-[11px]', md: 'px-4 py-2 text-[12px]', lg: 'px-6 py-2.5 text-[13px]' }
  return (
    <button onClick={onClick} disabled={disabled}
      className={`rounded-lg tracking-wide transition-all disabled:opacity-30 disabled:cursor-not-allowed ${variants[variant]} ${sizes[size]} ${className}`}>
      {children}
    </button>
  )
}

export function Slider({ label, value, onChange, min, max, step, unit, help }) {
  return (
    <div>
      <div className="flex justify-between items-baseline mb-2">
        <label className="text-[11px] text-ink-400 tracking-wide">{label}</label>
        <span className="mono text-sm text-phosphor">{value}{unit && <span className="text-ink-400 ml-1">{unit}</span>}</span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="w-full accent-phosphor" />
      {help && <div className="text-[11px] text-ink-400 mt-1">{help}</div>}
    </div>
  )
}

export function Tag({ children, color = 'phosphor' }) {
  const colors = {
    phosphor: 'border-phosphor-dim/50 text-phosphor bg-phosphor/10',
    amber: 'border-amber-dim/50 text-amber-signal bg-amber-signal/10',
    alert: 'border-alert/50 text-alert bg-alert/10',
    ok: 'border-ok/40 text-ok bg-ok/10',
    ink: 'border-ink-600 text-ink-400 bg-ink-700/40',
  }
  return <span className={`mono text-[10px] tracking-wide border rounded-full px-2 py-0.5 ${colors[color]}`}>{children}</span>
}

export function Loading({ message = 'Loading' }) {
  return (
    <div className="flex flex-col items-center py-12">
      <div className="w-48 h-0.5 bg-ink-700 overflow-hidden relative mb-3 rounded-full">
        <div className="absolute inset-0 data-stream" />
      </div>
      <div className="text-[12px] text-phosphor tracking-wide">{message}</div>
    </div>
  )
}

export function ErrorBox({ message }) {
  return (
    <div className="border border-alert/40 bg-alert/5 p-4 rounded-card">
      <div className="text-[12px] text-alert font-semibold tracking-wide mb-1.5">Couldn't reach the backend</div>
      <div className="text-xs text-paper font-mono">{message}</div>
      <div className="text-[11px] text-ink-400 mt-2">Make sure the stack is running (docker compose up). The dashboard polls the collector, orchestrator, ML, and agent services.</div>
    </div>
  )
}

export function Select({ label, value, onChange, options }) {
  return (
    <div>
      {label && <div className="text-[11px] text-ink-400 tracking-wide mb-1.5">{label}</div>}
      <select value={value} onChange={(e) => onChange(e.target.value)}
        className="text-xs bg-ink-900 border border-ink-600 text-paper rounded-lg px-3 py-2 w-full focus:border-phosphor outline-none">
        {options.map((o) => <option key={o.value || o} value={o.value || o}>{o.label || o}</option>)}
      </select>
    </div>
  )
}
