'use client'
import { useEffect, useState } from 'react'
import { getSignalQuality, getSignals } from '@/lib/api'
import { fmtDateTime } from '@/lib/format'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Radio, CheckCircle, AlertCircle, TrendingUp, BarChart3 } from 'lucide-react'
import { DashboardLoading } from '@/components/dashboard-loading'
import { ReasonIndicator } from '@/components/reason-indicator'

export default function SignalsPage() {
  const [data, setData] = useState<any>(null)
  const [signals, setSignals] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [sigPage, setSigPage] = useState(0)
  const SIG_PAGE = 20

  useEffect(() => {
    async function load() {
      try {
        const [sq, sigRaw] = await Promise.allSettled([
          getSignalQuality(),
          getSignals(),
        ]).then(r => r.map(x => x.status === 'fulfilled' ? x.value : null))
        setData(sq)
        setSignals((sigRaw as any)?.signals || [])
      } catch { setData({}); setSignals([]) }
      finally { setLoading(false) }
    }
    load()
    const i = setInterval(load, 5000)
    return () => clearInterval(i)
  }, [])

  if (loading && !data) {
    return <DashboardLoading title="Loading signals" description="Collecting signal flow, source health, and recent decisions." />
  }

  const sources = data?.sources || []
  const totalToday = sources.reduce((a: number, s: any) => a + (s.today || 0), 0)
  const totalTraded = sources.reduce((a: number, s: any) => a + (s.traded || 0), 0)
  const totalPending = sources.reduce((a: number, s: any) => a + (s.pending || 0), 0)
  const signalReason = (signal: any) => String(signal?.reason || signal?.blocked_reason || '').trim()
  const showReason = (signal: any) => {
    const status = String(signal?.status || '').toLowerCase()
    return ['rejected', 'failed'].includes(status) && signalReason(signal).length > 0
  }

  return (
    <div className="botux-page">
      <section className="botux-page__header">
        <div className="botux-page__heading">
          <div className="botux-page__eyebrow">Signal flow</div>
          <h1 className="botux-page__title">Signal Intelligence</h1>
          <p className="botux-page__description">Signal sources, freshness, conversion and recent signal flow across the pipeline.</p>
        </div>
      </section>

      {/* Summary Stats */}
      <div className="botux-kpi-grid">
        <Card className="botux-panel-card">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Signals Today</CardTitle>
            <Radio className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold tabular-nums">{totalToday}</div>
            <p className="text-xs text-muted-foreground mt-1">{sources.length} sources active</p>
          </CardContent>
        </Card>
        <Card className="botux-panel-card">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Pending</CardTitle>
            <AlertCircle className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold tabular-nums">{totalPending}</div>
            <p className="text-xs text-muted-foreground mt-1">Awaiting council</p>
          </CardContent>
        </Card>
        <Card className="botux-panel-card">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Traded</CardTitle>
            <TrendingUp className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold tabular-nums text-green-500">{totalTraded}</div>
            <p className="text-xs text-muted-foreground mt-1">Converted to trades</p>
          </CardContent>
        </Card>
        <Card className="botux-panel-card">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Conversion</CardTitle>
            <BarChart3 className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold tabular-nums">{totalToday > 0 ? ((totalTraded / totalToday) * 100).toFixed(1) : '0.0'}%</div>
            <p className="text-xs text-muted-foreground mt-1">Signal to trade rate</p>
          </CardContent>
        </Card>
      </div>

      {/* Source Cards */}
      <Card className="botux-table-card">
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2"><Radio className="h-5 w-5" /> Signal Sources</CardTitle>
            <Badge variant="secondary">{sources.length} sources</Badge>
          </div>
        </CardHeader>
        <CardContent>
          <div className="botux-list">
            {sources.length === 0 ? (
              <div className="botux-empty"><p className="text-muted-foreground">No signal sources active</p></div>
            ) : sources.map((s: any) => {
              const fresh = s.freshness_min == null ? '—' : s.freshness_min < 60 ? `${s.freshness_min}m ago` : `${Math.floor(s.freshness_min / 60)}h ago`
              const freshOk = s.freshness_min != null && s.freshness_min < 120
              return (
                <div key={s.source} className="botux-list__item">
                  <div className="flex items-center space-x-4">
                    <div className={`p-2 rounded-full ${freshOk ? 'bg-green-500/10 text-green-500' : 'bg-amber-500/10 text-amber-500'}`}>
                      {freshOk ? <CheckCircle className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
                    </div>
                    <div>
                      <p className="font-semibold">{s.source}</p>
                      <p className="text-sm text-muted-foreground">{s.total} total · {s.today} today</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-6 text-right">
                    <div><p className="text-xs text-muted-foreground">Pending</p><p className="text-sm font-medium tabular-nums">{s.pending}</p></div>
                    <div><p className="text-xs text-muted-foreground">Traded</p><p className="text-sm font-medium tabular-nums text-green-500">{s.traded}</p></div>
                    <div><p className="text-xs text-muted-foreground">Conv.</p><p className="text-sm font-medium tabular-nums">{((s.conversion_rate || 0) * 100).toFixed(1)}%</p></div>
                    <div><p className="text-xs text-muted-foreground">Fresh</p><p className={`text-sm font-medium ${freshOk ? 'text-green-500' : 'text-amber-500'}`}>{fresh}</p></div>
                  </div>
                </div>
              )
            })}
          </div>
        </CardContent>
      </Card>

      {/* Recent Signals Table */}
      <Card className="botux-table-card">
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>Recent Signals</CardTitle>
            <Badge variant="secondary">{signals.length} signals</Badge>
          </div>
          <CardDescription>Latest signals from all sources</CardDescription>
        </CardHeader>
        <CardContent>
          {signals.length === 0 ? (
            <div className="text-center py-8"><p className="text-muted-foreground">No signals in current window</p></div>
          ) : (
            <>
              <div className="rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      {['Ticker', 'Source', 'Action', 'Score', 'Conf', 'Status', 'Time', 'Headline'].map(h => (
                        <TableHead key={h}>{h}</TableHead>
                      ))}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {signals.slice(sigPage * SIG_PAGE, (sigPage + 1) * SIG_PAGE).map((s: any, i: number) => (
                      <TableRow key={i}>
                        <TableCell className="font-bold">{s.ticker || '—'}</TableCell>
                        <TableCell className="text-xs">{s.source}</TableCell>
                        <TableCell>
                          <Badge variant={s.action === 'BUY' || s.action === 'STRONG_BUY' ? 'default' : s.action === 'SELL' ? 'destructive' : 'secondary'} className="text-[10px]">
                            {s.action}
                          </Badge>
                        </TableCell>
                        <TableCell className="tabular-nums">{s.sentiment != null ? Math.round(s.sentiment) : s.score || '—'}</TableCell>
                        <TableCell className="tabular-nums">{s.confidence ? (s.confidence * 100).toFixed(0) + '%' : '—'}</TableCell>
                        <TableCell>
                          <div className="flex items-center gap-2">
                            <Badge variant={s.status === 'pending' ? 'secondary' : s.status === 'executed' ? 'default' : 'outline'} className="text-[10px]">
                              {s.status}
                            </Badge>
                            {showReason(s) ? <ReasonIndicator reason={signalReason(s)} /> : null}
                          </div>
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground tabular-nums">{fmtDateTime(s.created_at)}</TableCell>
                        <TableCell className="text-xs text-muted-foreground max-w-[200px] truncate" title={s.headline}>{s.headline || '—'}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              {signals.length > SIG_PAGE && (
                <div className="flex items-center justify-between pt-4 px-1">
                  <p className="text-xs text-muted-foreground">Showing {sigPage * SIG_PAGE + 1}–{Math.min((sigPage + 1) * SIG_PAGE, signals.length)} of {signals.length}</p>
                  <div className="flex items-center gap-2">
                    <button onClick={() => setSigPage(p => Math.max(0, p - 1))} disabled={sigPage === 0}
                      className="px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-background hover:bg-muted disabled:opacity-30 disabled:cursor-not-allowed transition-colors">Previous</button>
                    <span className="text-xs text-muted-foreground tabular-nums">Page {sigPage + 1} of {Math.ceil(signals.length / SIG_PAGE)}</span>
                    <button onClick={() => setSigPage(p => Math.min(Math.ceil(signals.length / SIG_PAGE) - 1, p + 1))} disabled={(sigPage + 1) * SIG_PAGE >= signals.length}
                      className="px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-background hover:bg-muted disabled:opacity-30 disabled:cursor-not-allowed transition-colors">Next</button>
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
