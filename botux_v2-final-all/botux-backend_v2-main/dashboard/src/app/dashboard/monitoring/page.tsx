'use client'
import { useEffect, useState } from 'react'
import { getMonitorSummary, getBotImprovement, getModelDrift, getActions } from '@/lib/api'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Activity, TrendingUp, TrendingDown, Minus, AlertTriangle, CheckCircle } from 'lucide-react'

export default function MonitoringPage() {
  const [data, setData] = useState<any>(null)

  useEffect(() => {
    async function load() {
      const [monitor, improvement, drift, actions] = await Promise.allSettled([
        getMonitorSummary(), getBotImprovement(), getModelDrift(), getActions(),
      ]).then(r => r.map(x => x.status === 'fulfilled' ? x.value : null))
      setData({ monitor, improvement, drift, actions })
    }
    load()
    const i = setInterval(load, 5000)
    return () => clearInterval(i)
  }, [])

  if (!data) return (
    <div className="botux-page">
      <div className="h-8 bg-muted animate-pulse rounded w-64" />
      <div className="botux-kpi-grid">
        {[...Array(4)].map((_, i) => <div key={i} className="h-32 bg-muted animate-pulse rounded-lg" />)}
      </div>
    </div>
  )

  const mon = data.monitor
  const improvement = data.improvement?.bots || []
  const drift = data.drift?.strategies || []
  const actions = data.actions?.actions || []

  const gradeColor = mon?.grade === 'A' ? 'text-green-500' : mon?.grade === 'B' ? '' : mon?.grade === 'C' ? 'text-amber-500' : 'text-red-500'

  const driftBadge = (flag: string) => {
    const m: Record<string, { variant: 'default' | 'destructive' | 'secondary' | 'outline'; label: string }> = {
      drift_down: { variant: 'destructive', label: 'DRIFT DOWN' },
      watch: { variant: 'secondary', label: 'WATCH' },
      improving: { variant: 'default', label: 'IMPROVING' },
      stable: { variant: 'outline', label: 'STABLE' },
      insufficient_data: { variant: 'outline', label: 'LOW DATA' },
      open_only: { variant: 'secondary', label: 'OPEN ONLY' },
      no_data: { variant: 'outline', label: 'NO DATA' },
    }
    return m[flag] || { variant: 'outline' as const, label: flag }
  }

  return (
    <div className="botux-page">
      <section className="botux-page__header">
        <div className="botux-page__heading">
          <div className="botux-page__eyebrow">Health watch</div>
          <h1 className="botux-page__title">Monitoring</h1>
          <p className="botux-page__description">System health, action queue, bot improvement and model drift in one view.</p>
        </div>
      </section>

      {/* System Grade + Metrics */}
      <div className="botux-kpi-grid">
        <Card className="botux-panel-card">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">System Grade</CardTitle>
            <Activity className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className={`text-4xl font-bold ${gradeColor}`}>{mon?.grade || '—'}</div>
            <p className="text-xs text-muted-foreground mt-2">{mon?.issues || 0} issues detected</p>
          </CardContent>
        </Card>
        <Card className="botux-panel-card">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Active Bots</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold tabular-nums">{mon?.active_bots}/{mon?.total_bots}</div>
            <p className="text-xs text-muted-foreground mt-2">{mon?.enabled_bots} enabled</p>
          </CardContent>
        </Card>
        <Card className="botux-panel-card">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Signals Today</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold tabular-nums">{mon?.signals_today}</div>
            <p className="text-xs text-muted-foreground mt-2">{mon?.trades_today} trades</p>
          </CardContent>
        </Card>
        <Card className="botux-panel-card">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Alerts</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold tabular-nums">{(mon?.drift_alerts || 0) + (mon?.stale_feeds || 0)}</div>
            <p className="text-xs text-muted-foreground mt-2">{mon?.drift_alerts} drift · {mon?.stale_feeds} stale</p>
          </CardContent>
        </Card>
      </div>

      {/* Action Queue */}
      <Card className="botux-table-card">
        <CardHeader>
          <CardTitle className="flex items-center justify-between">
            <div className="flex items-center gap-2"><AlertTriangle className="h-5 w-5" /> Action Queue</div>
            <Badge variant={actions.length > 0 ? 'destructive' : 'secondary'}>
              {actions.length > 0 ? `${actions.length} actions` : 'All Clear'}
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {actions.length === 0 ? (
            <div className="botux-empty">
              <CheckCircle className="h-12 w-12 text-green-500 mx-auto mb-4" />
              <p className="text-lg font-semibold">All Clear</p>
              <p className="text-muted-foreground">No actions needed.</p>
            </div>
          ) : (
            <div className="botux-list">
              {actions.map((a: any, i: number) => (
                <div key={i} className="botux-list__item">
                  <div className="flex items-center space-x-4">
                    <div className={`p-2 rounded-full ${a.priority === 'high' ? 'bg-red-500/10 text-red-500' : 'bg-amber-500/10 text-amber-400'}`}>
                      <AlertTriangle className="h-4 w-4" />
                    </div>
                    <div>
                      <p className="font-medium">{a.issue.replace(/_/g, ' ')}</p>
                      <p className="text-sm text-muted-foreground">{a.detail}</p>
                    </div>
                  </div>
                  <Badge variant={a.priority === 'high' ? 'destructive' : 'secondary'}>{a.priority}</Badge>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Bot Improvement + Model Drift */}
      <div className="botux-duo-grid">
        <Card className="botux-panel-card">
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><Activity className="h-5 w-5" /> Bot Improvement</CardTitle>
            <CardDescription>Performance trends per bot</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="botux-list">
              {improvement.map((b: any) => {
                const TrendIcon = b.trend === 'up' ? TrendingUp : b.trend === 'down' ? TrendingDown : Minus
                const trendColor = b.trend === 'up' ? 'text-green-500' : b.trend === 'down' ? 'text-red-500' : 'text-muted-foreground'
                return (
                  <div key={b.bot_id} className="botux-list__item">
                    <div className="flex items-center space-x-3">
                      <div className={`w-2.5 h-2.5 rounded-full ${b.enabled ? 'bg-green-600' : 'bg-muted-foreground/40'}`} />
                      <div>
                        <p className="text-sm font-medium">{b.display_name || b.bot_id}</p>
                        <p className="text-xs text-muted-foreground">
                          {b.lifecycle_state}
                          {b.autopilot_state ? ` · AP:${String(b.autopilot_state).toUpperCase()}` : ''}
                          {b.broker_submit_allowed === false ? ' · submit off' : ''}
                          {` · ${b.total_trades}t`}
                          {b.open_trades > 0 ? ` (${b.open_trades} open)` : ''}
                        </p>
                      </div>
                    </div>
                    <div className="flex items-center gap-3">
                      <span className="text-sm text-muted-foreground">{b.quality_score ?? '—'}</span>
                      <TrendIcon className={`h-4 w-4 ${trendColor}`} />
                    </div>
                  </div>
                )
              })}
            </div>
          </CardContent>
        </Card>

        <Card className="botux-panel-card">
          <CardHeader>
            <CardTitle className="flex items-center gap-2"><Activity className="h-5 w-5" /> Model Drift</CardTitle>
            <CardDescription>Strategy performance monitoring</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="botux-list">
              {drift.map((s: any) => {
                const d = driftBadge(s.drift_flag)
                return (
                  <div key={s.strategy_id} className="botux-list__item">
                    <div>
                      <p className="text-sm font-medium">{s.name || s.strategy_id}</p>
                      <p className="text-xs text-muted-foreground">{s.lifecycle_state} · {s.trades}t{s.open_trades > 0 ? ` (${s.open_trades} open)` : ''}</p>
                    </div>
                    <Badge variant={d.variant}>{d.label}</Badge>
                  </div>
                )
              })}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
