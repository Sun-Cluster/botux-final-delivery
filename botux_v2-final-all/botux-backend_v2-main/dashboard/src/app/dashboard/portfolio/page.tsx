'use client'
import { useEffect, useState } from 'react'
import { getScorecards, getBotImprovement, getModelDrift, getSignalQuality } from '@/lib/api'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { TrendingUp, TrendingDown, Minus, Activity, Radio } from 'lucide-react'

const driftBadge = (flag: string) => {
  const m: Record<string, { class: string; label: string }> = {
    drift_down: { class: 'bg-red-500/10 text-red-500 border-red-500/30', label: 'DRIFT DOWN' },
    watch: { class: 'bg-amber-500/10 text-amber-400 border-amber-500/30', label: 'WATCH' },
    improving: { class: 'bg-green-500/10 text-green-400 border-green-500/30', label: 'IMPROVING' },
    stable: { class: 'bg-secondary text-secondary-foreground', label: 'STABLE' },
    insufficient_data: { class: 'bg-muted text-muted-foreground', label: 'LOW DATA' },
    open_only: { class: 'bg-blue-500/10 text-blue-400 border-blue-500/30', label: 'OPEN ONLY' },
    no_data: { class: 'bg-muted text-muted-foreground', label: 'NO DATA' },
  }
  return m[flag] || { class: '', label: flag }
}

export default function PortfolioPage() {
  const [data, setData] = useState<any>(null)

  useEffect(() => {
    async function load() {
      const [scorecards, improvement, drift, signals] = await Promise.allSettled([
        getScorecards(), getBotImprovement(), getModelDrift(), getSignalQuality(),
      ]).then(r => r.map(x => x.status === 'fulfilled' ? x.value : null))
      setData({ scorecards, improvement, drift, signals })
    }
    load()
    const i = setInterval(load, 5000)
    return () => clearInterval(i)
  }, [])

  if (!data) return (
    <div className="botux-page">
      <div className="h-9 bg-muted animate-pulse rounded-lg w-72" />
      <div className="h-[200px] bg-muted animate-pulse rounded-xl" />
      <div className="grid gap-4 md:grid-cols-2">
        <div className="h-[200px] bg-muted animate-pulse rounded-xl" />
        <div className="h-[200px] bg-muted animate-pulse rounded-xl" />
      </div>
      <div className="h-[200px] bg-muted animate-pulse rounded-xl" />
    </div>
  )

  const sc = data.scorecards?.scorecards || {}
  const ranking = data.scorecards?.ranking || []
  const improvement = data.improvement?.bots || []
  const drift = data.drift?.strategies || []
  const signals = data.signals?.sources || []

  return (
    <div className="botux-page">
      <section className="botux-page__header">
        <div className="botux-page__heading">
          <div className="botux-page__eyebrow">Scoreboard + drift</div>
          <h1 className="botux-page__title">Portfolio</h1>
          <p className="botux-page__description">
            Compare strategy quality, drift, signal freshness and execution confidence from the same evaluation surface.
          </p>
        </div>
        <div className="botux-page__meta">
          <Badge variant="outline" className="text-xs h-8 px-3">{ranking.length} ranked</Badge>
          <Badge variant="outline" className="text-xs h-8 px-3">{drift.filter((s: any) => ['drift_down', 'watch'].includes(s.drift_flag)).length} flagged</Badge>
          <Badge variant="outline" className="text-xs h-8 px-3">{signals.length} sources</Badge>
        </div>
      </section>

      {/* Scoreboard */}
      <Card className="botux-table-card">
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm">Strategy Scoreboard</CardTitle>
            <Badge variant="outline" className="text-xs">{ranking.length} ranked</Badge>
          </div>
        </CardHeader>
        <CardContent className="pt-0">
          <Table>
            <TableHeader>
              <TableRow>
                {['#', 'Bot', 'Trades', 'Win Rate', 'Sharpe', 'Drawdown', 'Quality', 'Confidence'].map(h => (
                  <TableHead key={h} className="text-xs">{h}</TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {ranking.map((r: any, i: number) => {
                const bid = typeof r === 'string' ? r : r.bot_id
                const c = sc[bid] || {}
                const wr = !c.suppressed && c.win_rate != null ? c.win_rate.toFixed(1) + '%' : c.suppressed ? '<10' : '—'
                return (
                  <TableRow key={bid}>
                    <TableCell className="font-mono text-xs font-bold text-muted-foreground">{i + 1}</TableCell>
                    <TableCell className="font-semibold">{bid}</TableCell>
                    <TableCell className="font-mono text-xs font-bold">{c.total_trades || 0}</TableCell>
                    <TableCell className={`font-mono text-xs ${c.win_rate != null && c.win_rate >= 50 ? 'text-green-500' : ''}`}>{wr}</TableCell>
                    <TableCell className="font-mono text-xs">{c.sharpe != null ? c.sharpe.toFixed(2) : '—'}</TableCell>
                    <TableCell className="font-mono text-xs">{c.max_drawdown_pct != null ? c.max_drawdown_pct.toFixed(2) + '%' : '—'}</TableCell>
                    <TableCell className="font-mono text-xs">{c.quality_score ?? '—'}</TableCell>
                    <TableCell className="font-mono text-xs">{typeof c.confidence === 'number' ? (c.confidence * 100).toFixed(0) + '%' : (c.confidence || '—').toString().replace(/_/g, ' ')}</TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* Bot Improvement + Drift */}
      <div className="botux-duo-grid">
        <Card className="botux-panel-card">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-2"><Activity className="w-4 h-4" /> Bot Improvement</CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            <div className="space-y-2">
              {improvement.map((b: any) => {
                const TrendIcon = b.trend === 'up' ? TrendingUp : b.trend === 'down' ? TrendingDown : Minus
                const trendColor = b.trend === 'up' ? 'text-green-500' : b.trend === 'down' ? 'text-red-500' : 'text-muted-foreground'
                return (
                  <div key={b.bot_id} className="flex items-center gap-3 rounded-lg border border-border bg-background px-3 py-2">
                    <span className={`w-2 h-2 rounded-full ${b.enabled ? 'bg-green-500' : 'bg-muted-foreground/40'}`} />
                    <span className="text-xs font-medium flex-1 truncate">{b.display_name || b.bot_id}</span>
                    <Badge variant="outline" className="text-[10px]">{b.lifecycle_state}</Badge>
                    <span className="font-mono text-[10px] text-muted-foreground w-8 text-right">{b.quality_score ?? '—'}</span>
                    <TrendIcon className={`w-3.5 h-3.5 ${trendColor}`} />
                  </div>
                )
              })}
            </div>
          </CardContent>
        </Card>

        <Card className="botux-panel-card">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-2"><Activity className="w-4 h-4" /> Model Drift</CardTitle>
            <CardDescription className="text-xs">{drift.filter((s: any) => ['drift_down', 'watch'].includes(s.drift_flag)).length} flagged</CardDescription>
          </CardHeader>
          <CardContent className="pt-0">
            <div className="space-y-2">
              {drift.map((s: any) => {
                const d = driftBadge(s.drift_flag)
                return (
                  <div key={s.strategy_id} className="flex items-center gap-3 rounded-lg border border-border bg-background px-3 py-2">
                    <span className="text-xs font-medium flex-1 truncate">{s.name || s.strategy_id}</span>
                    <span className="font-mono text-[10px] text-muted-foreground w-12 text-right">{s.trades}t{s.open_trades > 0 ? ` (${s.open_trades}o)` : ''}</span>
                    <Badge variant="outline" className={`text-[10px] ${d.class}`}>{d.label}</Badge>
                  </div>
                )
              })}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Signal Sources */}
      <Card className="botux-table-card">
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm flex items-center gap-2"><Radio className="w-4 h-4" /> Signal Sources</CardTitle>
            <Badge variant="outline" className="text-xs">{signals.length} sources</Badge>
          </div>
        </CardHeader>
        <CardContent className="pt-0">
          <Table>
            <TableHeader>
              <TableRow>
                {['Source', 'Today', 'Pending', 'Traded', 'Conversion', 'Freshness'].map(h => (
                  <TableHead key={h} className="text-xs">{h}</TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {signals.map((s: any) => {
                const fresh = s.freshness_min == null ? '—' : s.freshness_min < 60 ? s.freshness_min + 'm' : Math.floor(s.freshness_min / 60) + 'h'
                const freshColor = s.freshness_min == null ? '' : s.freshness_min < 15 ? 'text-green-500' : s.freshness_min < 120 ? 'text-amber-400' : 'text-red-500'
                return (
                  <TableRow key={s.source}>
                    <TableCell className="font-semibold text-sm">{s.source}</TableCell>
                    <TableCell className="font-mono text-xs font-bold">{s.today}</TableCell>
                    <TableCell className="font-mono text-xs">{s.pending}</TableCell>
                    <TableCell className="font-mono text-xs">{s.traded}</TableCell>
                    <TableCell className="font-mono text-xs">{((s.conversion_rate || 0) * 100).toFixed(1)}%</TableCell>
                    <TableCell className={`font-mono text-xs font-semibold ${freshColor}`}>{fresh}</TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}
