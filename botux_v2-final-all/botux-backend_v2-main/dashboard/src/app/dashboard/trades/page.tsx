'use client'
import { fmt$, fmtDateTime } from '@/lib/format'
import { useEffect, useState } from 'react'
import { getTrades } from '@/lib/api'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { ArrowLeftRight } from 'lucide-react'
import { DashboardLoading } from '@/components/dashboard-loading'


export default function TradesPage() {
  const [trades, setTrades] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(0)
  const [filter, setFilter] = useState('all')
  const [sortBy, setSortBy] = useState<string>('created_at')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')
  const PAGE_SIZE = 20

  useEffect(() => {
    async function load() {
      try { const data = await getTrades(); setTrades(Array.isArray(data) ? data : []) } catch {}
      setLoading(false)
    }
    load()
    const i = setInterval(load, 5000)
    return () => clearInterval(i)
  }, [])

  if (loading) return <DashboardLoading title="Loading trades" description="Pulling the latest executions and trade outcomes." />

  // Filter
  const filtered = filter === 'all' ? trades : trades.filter(t => t.outcome === filter)
  // Sort
  const sorted = [...filtered].sort((a, b) => {
    const av = a[sortBy] || a.features?.[sortBy] || ''
    const bv = b[sortBy] || b.features?.[sortBy] || ''
    const cmp = typeof av === 'number' ? av - bv : String(av).localeCompare(String(bv))
    return sortDir === 'asc' ? cmp : -cmp
  })

  const closed = trades.filter(t => t.outcome !== 'OPEN')
  const open = trades.filter(t => t.outcome === 'OPEN')
  const wins = closed.filter(t => t.outcome === 'WIN').length
  const losses = closed.filter(t => t.outcome === 'LOSS').length
  const winRate = (wins + losses) > 0 ? ((wins / (wins + losses)) * 100).toFixed(1) : '—'
  const brokerBreakdown = trades.reduce((acc: Record<string, number>, trade: any) => {
    const brokerName = String(trade.broker_name || 'unknown').trim().toUpperCase()
    acc[brokerName] = (acc[brokerName] || 0) + 1
    return acc
  }, {})
  const brokerSummary = Object.entries(brokerBreakdown)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3)
    .map(([name, count]) => `${name} ${count}`)
    .join(' · ')

  return (
    <div className="botux-page">
      <section className="botux-page__header">
        <div className="botux-page__heading">
          <div className="botux-page__eyebrow">Execution ledger</div>
          <h1 className="botux-page__title">Trades</h1>
          <p className="botux-page__description">Complete trading history, win rate and per-trade performance details.</p>
        </div>
      </section>

      <div className="botux-kpi-grid">
        <Card className="botux-panel-card">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Total Trades</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{trades.length}</div>
            <p className="text-xs text-muted-foreground mt-1">{open.length} open &middot; {closed.length} closed</p>
          </CardContent>
        </Card>
        <Card className="botux-panel-card">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Win Rate</CardTitle>
          </CardHeader>
          <CardContent>
            <div className={`text-2xl font-bold ${winRate !== '—' && parseFloat(winRate) >= 50 ? 'text-green-500' : ''}`}>{winRate}{winRate !== '—' ? '%' : ''}</div>
            <p className="text-xs text-muted-foreground mt-1">{wins}W / {losses}L</p>
          </CardContent>
        </Card>
        <Card className="botux-panel-card">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Open</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{open.length}</div>
          </CardContent>
        </Card>
        <Card className="botux-panel-card">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Broker Mix</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{Object.keys(brokerBreakdown).length}</div>
            <p className="text-xs text-muted-foreground mt-1 truncate">{brokerSummary || 'No broker routing yet'}</p>
          </CardContent>
        </Card>
      </div>

      {/* Table — v1 exact: rounded-lg border wrapper */}
      <Card className="botux-table-card">
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>Trading History</CardTitle>
            <div className="flex items-center gap-2">
              {['all', 'OPEN', 'WIN', 'LOSS', 'CLOSED_STALE'].map(f => (
                <button key={f} onClick={() => { setFilter(f); setPage(0) }}
                  className={`px-2.5 py-1 text-[10px] font-medium rounded-md border transition-colors ${filter === f ? 'bg-foreground text-background border-foreground' : 'border-border text-muted-foreground hover:text-foreground'}`}>
                  {f === 'all' ? 'All' : f}
                </button>
              ))}
            </div>
          </div>
          <CardDescription>{sorted.length} trades {filter !== 'all' ? `(filtered: ${filter})` : 'from all bots and brokers'}</CardDescription>
        </CardHeader>
        <CardContent>
          {trades.length === 0 ? (
            <div className="flex flex-col items-center py-16 border border-dashed rounded-lg">
              <div className="botux-empty w-full">
                <ArrowLeftRight className="h-12 w-12 text-muted-foreground mb-4" />
                <p className="text-lg font-semibold mb-2">No Trades Yet</p>
                <p className="text-muted-foreground text-center max-w-sm">Trades will appear here when your bots execute during market hours.</p>
              </div>
            </div>
          ) : (
            <>
              <div className="rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow>
                        {[
                          { label: 'Symbol', key: 'symbol' },
                          { label: 'Action', key: 'action' },
                          { label: 'Qty', key: '' },
                          { label: 'Entry', key: 'entry_price' },
                        { label: 'Exit', key: 'exit_price' },
                        { label: 'P&L %', key: 'pnl_pct' },
                          { label: 'MFE %', key: '' },
                          { label: 'MAE %', key: '' },
                          { label: 'Bot', key: '' },
                          { label: 'Broker', key: '' },
                          { label: 'Outcome', key: 'outcome' },
                          { label: 'Opened', key: 'created_at' },
                          { label: 'Closed', key: 'closed_at' },
                      ].map(h => (
                        <TableHead key={h.label} className={h.key ? 'cursor-pointer hover:text-foreground select-none' : ''} onClick={() => {
                          if (!h.key) return
                          if (sortBy === h.key) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
                          else { setSortBy(h.key); setSortDir('desc') }
                          setPage(0)
                        }}>
                          {h.label}{sortBy === h.key ? (sortDir === 'asc' ? ' ↑' : ' ↓') : ''}
                        </TableHead>
                      ))}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {sorted.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE).map((t, i) => {
                      const feat = t.features || {}
                      const pnlPctRaw = t.pnl_pct ?? feat.pnl_pct ?? feat.bot_pnl_pct
                      const pnlPct = pnlPctRaw == null ? null : Number(pnlPctRaw)
                      const qty = t.quantity ?? feat.quantity ?? feat.qty
                      const botId = t.bot_id ?? feat.bot_id
                      const brokerName = (t.broker_name || feat.broker_name || '—').toString().toUpperCase()
                      const mfeRaw = (t as any).mfe_pct ?? feat.mfe_pct ?? feat.max_favorable_excursion_pct
                      const maeRaw = (t as any).mae_pct ?? feat.mae_pct ?? feat.max_adverse_excursion_pct
                      const mfe = mfeRaw == null ? null : Number(mfeRaw)
                      const mae = maeRaw == null ? null : Number(maeRaw)
                      return (
                        <TableRow key={i}>
                          <TableCell className="font-bold">{t.symbol}</TableCell>
                          <TableCell><Badge variant={t.action === 'BUY' ? 'default' : 'destructive'}>{t.action}</Badge></TableCell>
                          <TableCell>{qty ?? '—'}</TableCell>
                          <TableCell>{fmt$(t.entry_price)}</TableCell>
                          <TableCell>{t.exit_price > 0 ? fmt$(t.exit_price) : '—'}</TableCell>
                          <TableCell className={`font-medium ${pnlPct != null && pnlPct > 0 ? 'text-green-500' : pnlPct != null && pnlPct < 0 ? 'text-red-500' : ''}`}>
                            {pnlPct != null && Number.isFinite(pnlPct) ? (pnlPct > 0 ? '+' : '') + pnlPct.toFixed(2) + '%' : '—'}
                          </TableCell>
                          <TableCell className="text-green-500/80 tabular-nums">
                            {mfe != null && Number.isFinite(mfe) ? `${mfe > 0 ? '+' : ''}${mfe.toFixed(2)}%` : '—'}
                          </TableCell>
                          <TableCell className="text-red-500/80 tabular-nums">
                            {mae != null && Number.isFinite(mae) ? `${mae.toFixed(2)}%` : '—'}
                          </TableCell>
                          <TableCell className="text-muted-foreground">{botId || '—'}</TableCell>
                          <TableCell className="text-muted-foreground">{brokerName}</TableCell>
                          <TableCell>
                            <Badge variant={t.outcome === 'WIN' ? 'default' : t.outcome === 'LOSS' ? 'destructive' : 'secondary'}>
                              {t.outcome}
                            </Badge>
                          </TableCell>
                          <TableCell className="text-xs text-muted-foreground tabular-nums">{fmtDateTime(t.created_at)}</TableCell>
                          <TableCell className="text-xs text-muted-foreground tabular-nums">{t.closed_at && t.outcome !== 'OPEN' ? fmtDateTime(t.closed_at) : '—'}</TableCell>
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
              </div>
              {sorted.length > PAGE_SIZE && (
                <div className="flex items-center justify-between pt-4 px-1">
                  <p className="text-xs text-muted-foreground">
                    Showing {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, sorted.length)} of {sorted.length}
                  </p>
                  <div className="flex items-center gap-2">
                    <button onClick={() => setPage(p => Math.max(0, p - 1))} disabled={page === 0}
                      className="px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-background hover:bg-muted disabled:opacity-30 disabled:cursor-not-allowed transition-colors">
                      Previous
                    </button>
                    <span className="text-xs text-muted-foreground tabular-nums">
                      Page {page + 1} of {Math.ceil(sorted.length / PAGE_SIZE)}
                    </span>
                    <button onClick={() => setPage(p => Math.min(Math.ceil(sorted.length / PAGE_SIZE) - 1, p + 1))} disabled={(page + 1) * PAGE_SIZE >= sorted.length}
                      className="px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-background hover:bg-muted disabled:opacity-30 disabled:cursor-not-allowed transition-colors">
                      Next
                    </button>
                  </div>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
