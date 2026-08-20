/**
 * BOTUX formatting utilities — shared across all dashboard pages.
 */

export function fmt$(v: number | null | undefined): string {
  if (v == null) return '—'
  return `$${v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

export function fmtPct(v: number | null | undefined): string {
  if (v == null) return '—'
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
}

export function fmtNum(v: number | null | undefined): string {
  if (v == null) return '—'
  return v.toLocaleString('en-US')
}

export function timeAgo(minutes: number | null | undefined): string {
  if (minutes == null) return '—'
  if (minutes < 60) return `${minutes}m ago`
  if (minutes < 1440) return `${Math.floor(minutes / 60)}h ago`
  return `${Math.floor(minutes / 1440)}d ago`
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    return d.toLocaleDateString('en-AU', { day: '2-digit', month: 'short', year: 'numeric' })
  } catch { return '—' }
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    return d.toLocaleTimeString('en-AU', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
  } catch { return '—' }
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    return d.toLocaleDateString('en-AU', { day: '2-digit', month: 'short' }) + ' ' +
           d.toLocaleTimeString('en-AU', { hour: '2-digit', minute: '2-digit', hour12: false })
  } catch { return '—' }
}

export const pnlColor = (v: number) => v >= 0 ? 'text-green-500' : 'text-red-500'

export const lcBadgeVariant = (state: string): 'default' | 'destructive' | 'secondary' | 'outline' => {
  const m: Record<string, 'default' | 'destructive' | 'secondary' | 'outline'> = {
    LIVE: 'default', SCALED: 'default', PAPER: 'secondary',
    SHADOW: 'outline', DEMOTED: 'destructive', OFFLINE: 'outline', RETIRED: 'outline',
  }
  return m[state] || 'outline'
}
