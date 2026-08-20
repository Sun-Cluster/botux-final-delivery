export function Card({ children, className = '', ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={`bg-[var(--surface-1)] border border-[var(--border)] rounded-xl overflow-hidden transition-colors hover:border-[var(--border-2)] ${className}`} {...props}>
      {children}
    </div>
  )
}

export function CardHead({ title, meta, children }: { title: string; meta?: string; children?: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between px-4 py-2.5 border-b border-[var(--border)]">
      <h3 className="text-[10px] font-bold text-[var(--text-2)] uppercase tracking-wider">{title}</h3>
      <div className="flex items-center gap-2">
        {meta && <span className="font-mono text-[9px] text-[var(--text-3)]">{meta}</span>}
        {children}
      </div>
    </div>
  )
}

export function StatCard({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div className="p-3 border-r border-[var(--border)] last:border-r-0">
      <div className={`text-lg font-extrabold tracking-tight leading-none ${color || 'text-[var(--text-1)]'}`}>{value}</div>
      <div className="font-mono text-[8px] text-[var(--text-3)] uppercase tracking-wider mt-1">{label}</div>
      {sub && <div className="font-mono text-[10px] text-[var(--text-2)] mt-0.5">{sub}</div>}
    </div>
  )
}

export function Badge({ children, variant = 'default' }: { children: React.ReactNode; variant?: 'default' | 'green' | 'red' | 'amber' | 'blue' | 'dim' }) {
  const colors = {
    default: 'text-[var(--text-2)] border-[var(--border)] bg-[var(--surface-2)]',
    green: 'text-[var(--green)] border-[var(--green)]/30 bg-[var(--green)]/5',
    red: 'text-[var(--red)] border-[var(--red)]/30 bg-[var(--red)]/5',
    amber: 'text-[var(--amber)] border-[var(--amber)]/30 bg-[var(--amber)]/5',
    blue: 'text-[#5588cc] border-[#5588cc]/30 bg-[#5588cc]/5',
    dim: 'text-[var(--text-3)] border-[var(--border)] bg-[var(--surface-2)]',
  }
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full font-mono text-[8px] font-semibold uppercase tracking-wide border ${colors[variant]}`}>
      {children}
    </span>
  )
}

export function Dot({ on, className = '' }: { on: boolean; className?: string }) {
  return (
    <span className={`inline-block w-1.5 h-1.5 rounded-full flex-shrink-0 ${on ? 'bg-[var(--green)] shadow-[0_0_6px_rgba(24,201,106,0.5)]' : 'bg-[var(--text-3)]'} ${className}`} />
  )
}
