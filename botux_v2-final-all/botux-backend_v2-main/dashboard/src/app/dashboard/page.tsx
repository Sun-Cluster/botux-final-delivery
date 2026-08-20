'use client'
import { useEffect, useState } from 'react'
import { getSummary, getSnapshot, getPositions, getBrokers, getRisk, getPDT, getRegime, getMonitorSummary, getActions, getPortfolioHistory, getExecutorStatus } from '@/lib/api'
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import { fmt$, fmtPct, pnlColor } from '@/lib/format'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { TrendingUp, TrendingDown, DollarSign, BarChart3, Shield, Activity, CheckCircle, AlertCircle, AlertTriangle, Radio, Zap } from 'lucide-react'
import { DashboardLoading } from '@/components/dashboard-loading'

export default function Page() {
  const [d, setD] = useState<any>({})
  const [ready, setReady] = useState(false)

  useEffect(() => {
    let mounted = true
    async function loadPrimary() {
      const [summary, snapshot, positions] = await Promise.allSettled([
        getSummary(), getSnapshot(), getPositions(),
      ]).then(results => results.map(r => r.status === 'fulfilled' ? r.value : null))
      if (!mounted) return
      const acct = (summary as any)?.account || {}
      if (!ready || acct.equity > 0 || (summary as any)?.data_quality?.warmup === false) {
        setD((prev: any) => ({ ...prev, summary, snapshot, positions }))
      }
      if (summary || snapshot || positions) setReady(true)
    }
    async function loadSecondary() {
      const [brokers, risk, pdt, regime, monitor, actions, history, executor] = await Promise.allSettled([
        getBrokers(), getRisk(), getPDT(), getRegime(), getMonitorSummary(), getActions(), getPortfolioHistory(), getExecutorStatus(),
      ]).then(results => results.map(r => r.status === 'fulfilled' ? r.value : null))
      if (!mounted) return
      setD((prev: any) => ({ ...prev, brokers, risk, pdt, regime, monitor, actions, history, executor }))
    }
    loadPrimary()
    loadSecondary()
    const i1 = setInterval(loadPrimary, 5000)
    const i2 = setInterval(loadSecondary, 10000)
    return () => { mounted = false; clearInterval(i1); clearInterval(i2) }
  }, [ready])

  if (!ready) return <DashboardLoading title="Loading trading dashboard" description="Syncing broker accounts, positions, and control-plane metrics." />

  const s = d.summary
  const snap = d.snapshot
  const pos = Array.isArray(d.positions) ? d.positions : []
  const brokers = d.brokers?.brokers || []
  const risk = d.risk
  const pdt = d.pdt
  const regime = d.regime
  const mon = d.monitor
  const actions = d.actions?.actions || []
  // Use account-level values for truth (top-level summary fields can be stale)
  const acct = (s as any)?.account || {}
  const mode = acct.mode || s?.mode || 'Paper'
  // P&L: prefer account.pnl_today (snapshot is often stale/zero)
  const pnl = acct.pnl_today || snap?.daily_pnl || s?.daily_pl || 0
  const pnlPct = acct.pnl_today_pct || snap?.daily_pnl_pct || s?.daily_pl_pct || 0
  const totalUnrealised = pos.reduce((a: number, p: any) => a + (parseFloat(p.unrealized_pl) || 0), 0)
  const isConnected = brokers.some((b: any) => b.connected)
  const connectedBrokers = brokers.filter((b: any) => b.connected)
  const brokerAccountCards = brokers.map((broker: any) => ({
    ...broker,
    label: String(broker.name || 'unknown').toUpperCase(),
    accountNumber: String(broker.account_number || '').trim(),
    statusLabel: broker.connected ? 'Connected' : (broker.error ? 'Error' : 'Offline'),
  }))
  const chartTickColor = 'var(--text-3)'

  return (
    <div className="botux-page">
      <section className="botux-page__header">
        <div className="botux-page__heading">
          <div className="botux-page__eyebrow">
            <span className="h-2 w-2 rounded-full bg-[var(--accent)] dot-pulse" />
            Live command center
          </div>
          <h1 className="botux-page__title">Trading Dashboard</h1>
          <p className="botux-page__description">
            Real-time trading state, capital exposure, broker connectivity and operator actions in one control surface.
          </p>
        </div>

        <div className="botux-page__meta">
          <Badge variant="outline" className="text-xs h-7 px-3">{mode} Mode</Badge>
          <Badge variant="outline" className="text-xs h-7 px-3">{regime?.regime || '—'}</Badge>
          <Card className="botux-hero-card min-w-[11rem]">
            <CardContent className="flex items-center justify-between gap-4 p-0">
              <div>
                <p className="text-xs uppercase tracking-[0.16em] text-muted-foreground">System grade</p>
                <p className={`text-4xl font-black tracking-tighter leading-none ${
                  mon?.grade === 'A' ? 'text-green-500' : mon?.grade === 'B' ? 'text-foreground' : mon?.grade === 'C' ? 'text-amber-400' : 'text-red-500'
                }`}>{mon?.grade || '—'}</p>
              </div>
              <div className="text-right">
                <p className="text-xs text-muted-foreground">Daily P&amp;L</p>
                <p className={`text-lg font-semibold tabular-nums ${pnlColor(pnl)}`}>{fmt$(pnl)}</p>
              </div>
            </CardContent>
          </Card>
        </div>
      </section>

      <div className="botux-kpi-grid">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Connection Status</CardTitle>
            {isConnected ? <CheckCircle className="h-4 w-4 text-green-500" /> : <AlertCircle className="h-4 w-4 text-red-500" />}
          </CardHeader>
          <CardContent>
            <Badge variant={isConnected ? 'default' : 'destructive'} className="text-sm">
              {isConnected ? 'Connected' : 'Disconnected'}
            </Badge>
            <p className="text-xs text-muted-foreground mt-3">
              {brokers.filter((b: any) => b.connected).map((b: any) => b.name.toUpperCase()).join(', ') || 'No brokers'}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Brokers Online</CardTitle>
            <DollarSign className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold tabular-nums">{connectedBrokers.length}/{brokers.length}</div>
            <p className="text-xs text-muted-foreground mt-1">{connectedBrokers.map((b: any) => String(b.name).toUpperCase()).join(', ') || 'No live broker connection'}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Open Positions</CardTitle>
            <TrendingUp className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold tabular-nums">{pos.length}</div>
            <p className={`text-xs mt-1 flex items-center gap-1 ${pnlColor(pnl)}`}>
              {pnl >= 0 ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
              {totalUnrealised >= 0 ? '+' : ''}{fmt$(Math.abs(totalUnrealised)).replace('$', '')} unrealized
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Risk Status</CardTitle>
            <Shield className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{risk?.halted ? 'HALTED' : 'Clear'}</div>
            <p className="text-xs text-muted-foreground mt-1">
              PDT {pdt?.day_trades_remaining ?? '—'}/{pdt?.day_trades_max ?? 3} · {d.executor?.trades_today || risk?.trades_today || 0} trades today
            </p>
          </CardContent>
        </Card>
      </div>

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold tracking-wide">Broker Accounts</h2>
            <p className="text-xs text-muted-foreground">Account truth is separated per broker instead of merged into one wallet view.</p>
          </div>
          <Badge variant="outline" className="text-xs h-7 px-3">{brokerAccountCards.length} accounts</Badge>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          {brokerAccountCards.map((broker: any) => (
            <Card key={broker.name} className="botux-panel-card">
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-2">
                      <CardTitle className="text-base">{broker.label}</CardTitle>
                      <Badge variant={broker.connected ? 'default' : 'secondary'} className="text-[10px]">{broker.statusLabel}</Badge>
                      <Badge variant="outline" className="text-[10px]">{String(broker.mode || 'paper').toUpperCase()}</Badge>
                    </div>
                    <CardDescription className="mt-1">
                      {broker.accountNumber ? `Acct ${broker.accountNumber}` : (broker.error || 'Account metadata unavailable')}
                    </CardDescription>
                  </div>
                  <div className="text-right">
                    <p className="text-xs text-muted-foreground">Currency</p>
                    <p className="text-sm font-semibold">{broker.currency || 'USD'}</p>
                  </div>
                </div>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
                  <div>
                    <p className="text-xs text-muted-foreground">Equity</p>
                    <p className="text-sm font-semibold tabular-nums">{fmt$(broker.equity)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground">Cash</p>
                    <p className="text-sm font-semibold tabular-nums">{fmt$(broker.cash)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground">Buying Power</p>
                    <p className="text-sm font-semibold tabular-nums">{fmt$(broker.buying_power)}</p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground">Last Equity</p>
                    <p className="text-sm font-semibold tabular-nums">{fmt$(broker.last_equity || broker.portfolio_value)}</p>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>

      <Card className="botux-command-card">
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm flex items-center gap-2"><Zap className="h-4 w-4 text-[#0ABAB5]" /> System Command</CardTitle>
          </div>
        </CardHeader>
        <CardContent>
          <div className="botux-stat-row">
            {[
              { label: 'Active Bots', value: `${mon?.active_bots ?? 0}/${mon?.total_bots ?? 0}` },
              { label: 'Enabled', value: `${mon?.enabled_bots ?? 0}`, ok: (mon?.enabled_bots || 0) > 0 },
              { label: 'Brokers', value: `${mon?.connected_brokers ?? 0}/${mon?.total_brokers ?? 0}`, ok: mon?.connected_brokers >= mon?.total_brokers },
              { label: 'Signals', value: `${mon?.signals_today ?? 0}` },
              { label: 'Trades', value: `${d.executor?.trades_today || mon?.trades_today || 0}` },
              { label: 'Drift', value: `${mon?.drift_alerts ?? 0}`, bad: (mon?.drift_alerts || 0) > 0 },
              { label: 'Stale', value: `${mon?.stale_feeds ?? 0}`, warn: (mon?.stale_feeds || 0) > 0 },
            ].map(m => (
              <div key={m.label} className="botux-stat">
                <div className={`botux-stat__value ${m.ok ? 'text-green-500' : m.bad ? 'text-red-500' : m.warn ? 'text-amber-400' : ''}`}>{m.value}</div>
                <p className="botux-stat__label">{m.label}</p>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Tabbed Content */}
      <Tabs defaultValue="positions" className="space-y-4">
        <TabsList className="grid w-full grid-cols-2 md:grid-cols-4">
          <TabsTrigger value="positions"><DollarSign className="h-4 w-4" /> Positions</TabsTrigger>
          <TabsTrigger value="actions"><AlertTriangle className="h-4 w-4" /> Actions</TabsTrigger>
          <TabsTrigger value="brokers"><Radio className="h-4 w-4" /> Brokers</TabsTrigger>
          <TabsTrigger value="overview"><Activity className="h-4 w-4" /> Overview</TabsTrigger>
        </TabsList>

        {/* Positions */}
        <TabsContent value="positions" className="botux-tab-panel space-y-4">
          {/* Summary */}
          <Card className="botux-panel-card">
            <CardHeader>
              <CardTitle className="flex items-center gap-2"><DollarSign className="h-5 w-5" /> Positions Summary</CardTitle>
              <CardDescription>Current open positions and unrealized P&L</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="grid gap-4 md:grid-cols-3">
                <div><p className="text-sm font-medium">Total Positions</p><p className="text-2xl font-bold tabular-nums">{pos.length}</p></div>
                <div><p className="text-sm font-medium">Unrealized P&L</p><p className={`text-2xl font-bold tabular-nums ${pnlColor(totalUnrealised)}`}>{totalUnrealised >= 0 ? '+' : ''}{totalUnrealised.toFixed(2)}</p></div>
                <div><p className="text-sm font-medium">Total Value</p><p className="text-2xl font-bold tabular-nums">{fmt$(pos.reduce((a: number, p: any) => a + Math.abs(parseFloat(p.market_value) || 0), 0))}</p></div>
              </div>
            </CardContent>
          </Card>

          {/* Position List */}
          <Card className="botux-table-card">
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle>Open Positions</CardTitle>
                <Badge variant="secondary">{pos.length} positions</Badge>
              </div>
            </CardHeader>
            <CardContent>
              {pos.length === 0 ? (
                <div className="flex flex-col items-center py-16 border border-dashed rounded-lg">
                  <BarChart3 className="h-12 w-12 text-muted-foreground mb-4" />
                  <p className="text-lg font-semibold mb-2">No Open Positions</p>
                  <p className="text-muted-foreground text-center max-w-sm text-sm">Positions will appear here when your bots execute trades during market hours.</p>
                </div>
              ) : (
                <div className="botux-list">
                  {pos.map((p: any) => {
                    const pl = parseFloat(p.unrealized_pl) || 0
                    const isLong = p.side === 'long'
                    return (
                      <div key={p.symbol} className="botux-list__item">
                        <div className="flex items-center space-x-4">
                          <div className={`p-2 rounded-full ${isLong ? 'bg-green-500/10 text-green-500' : 'bg-red-500/10 text-red-500'}`}>
                            {isLong ? <TrendingUp className="h-4 w-4" /> : <TrendingDown className="h-4 w-4" />}
                          </div>
                          <div>
                            <div className="flex items-center gap-2">
                              <p className="font-semibold">{p.symbol}</p>
                              <Badge variant={isLong ? 'default' : 'destructive'} className="text-[10px]">{p.side}</Badge>
                            </div>
                            <p className="text-sm text-muted-foreground">{Math.abs(p.qty)} shares @ {fmt$(p.avg_entry_price)}</p>
                          </div>
                        </div>
                        <div className="flex items-center gap-6 text-right">
                          <div>
                            <p className="text-xs text-muted-foreground">Current</p>
                            <p className="text-sm font-medium tabular-nums">{fmt$(p.current_price)}</p>
                          </div>
                          <div>
                            <p className="text-xs text-muted-foreground">P&L</p>
                            <p className={`text-sm font-semibold tabular-nums ${pnlColor(pl)}`}>{pl >= 0 ? '+' : ''}{pl.toFixed(2)}</p>
                          </div>
                          <div className="w-16">
                            <p className="text-xs text-muted-foreground">Return</p>
                            <p className={`text-sm font-semibold tabular-nums ${pnlColor(pl)}`}>{p.unrealized_plpc != null ? fmtPct(parseFloat(p.unrealized_plpc) * 100) : '—'}</p>
                          </div>
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        {/* Actions */}
        <TabsContent value="actions" className="botux-tab-panel space-y-4">
          <Card className="botux-table-card">
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle className="flex items-center gap-2"><AlertTriangle className="h-5 w-5" /> Action Queue</CardTitle>
                <Badge variant={actions.length > 0 ? 'destructive' : 'secondary'}>{actions.length > 0 ? `${actions.length} actions` : 'All Clear'}</Badge>
              </div>
              <CardDescription>System alerts and recommended actions</CardDescription>
            </CardHeader>
            <CardContent>
              {actions.length === 0 ? (
                <div className="flex flex-col items-center py-16">
                  <CheckCircle className="h-12 w-12 text-green-500 mb-4" />
                  <p className="text-lg font-semibold mb-2">All Clear</p>
                  <p className="text-muted-foreground text-sm">No actions needed — system operating normally.</p>
                </div>
              ) : (
                <div className="botux-list">
                  {actions.map((a: any, i: number) => (
                    <div key={i} className="botux-list__item">
                      <div className="flex items-center space-x-4">
                        <div className={`p-2 rounded-full ${a.priority === 'high' ? 'bg-red-500/10 text-red-500' : 'bg-amber-500/10 text-amber-500'}`}>
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
        </TabsContent>

        {/* Brokers */}
        <TabsContent value="brokers" className="botux-tab-panel space-y-4">
          <Card className="botux-table-card">
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle className="flex items-center gap-2"><Radio className="h-5 w-5" /> Broker Status</CardTitle>
                <Badge variant="secondary">{brokers.filter((b: any) => b.connected).length}/{brokers.length} connected</Badge>
              </div>
            </CardHeader>
            <CardContent>
              <div className="botux-list">
                {brokers.map((b: any) => (
                  <div key={b.name} className="botux-list__item">
                    <div className="flex items-center space-x-4">
                      <div className={`p-2 rounded-full ${b.connected ? 'bg-green-500/10 text-green-500' : 'bg-red-500/10 text-red-500'}`}>
                        {b.connected ? <CheckCircle className="h-4 w-4" /> : <AlertCircle className="h-4 w-4" />}
                      </div>
                      <div>
                        <div className="flex items-center gap-2">
                          <p className="font-semibold uppercase">{b.name}</p>
                          <Badge variant={b.connected ? 'default' : 'secondary'} className="text-[10px]">{b.connected ? 'Connected' : 'Offline'}</Badge>
                          <Badge variant="outline" className="text-[10px]">{b.mode?.toUpperCase()}</Badge>
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center gap-8 text-right">
                      <div><p className="text-xs text-muted-foreground">Equity</p><p className="text-sm font-semibold tabular-nums">{b.equity ? fmt$(b.equity) : '—'}</p></div>
                      <div><p className="text-xs text-muted-foreground">Cash</p><p className="text-sm font-semibold tabular-nums">{b.cash ? fmt$(b.cash) : '—'}</p></div>
                      <div><p className="text-xs text-muted-foreground">Buying Power</p><p className="text-sm font-semibold tabular-nums">{b.buying_power ? fmt$(b.buying_power) : '—'}</p></div>
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* Overview */}
        <TabsContent value="overview" className="botux-tab-panel space-y-4">
          {/* Equity Curve — real broker history */}
          {(() => {
            const hist = d.history
            if (!hist || !hist.equity || hist.equity.length < 2) return null
            const chartData = hist.timestamp.map((ts: number, i: number) => ({
              date: new Date(ts * 1000).toLocaleDateString('en-AU', { day: '2-digit', month: 'short' }),
              equity: hist.equity[i],
              pnl: hist.profit_loss[i],
            })).filter((p: any) => p.equity > 0) // filter out zero-equity startup points
            if (chartData.length < 2) return null
            return (
              <Card className="botux-panel-card">
                <CardHeader className="pb-2">
                  <div className="flex items-center justify-between">
                    <CardTitle className="text-sm flex items-center gap-2"><TrendingUp className="h-4 w-4 text-[#0ABAB5]" /> Equity Curve</CardTitle>
                    <Badge variant="outline" className="text-xs">{chartData.length} days · Alpaca Paper</Badge>
                  </div>
                </CardHeader>
                <CardContent>
                  <ResponsiveContainer width="100%" height={220}>
                    <AreaChart data={chartData}>
                      <defs>
                        <linearGradient id="eqGradient" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#0ABAB5" stopOpacity={0.3} />
                          <stop offset="95%" stopColor="#0ABAB5" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <XAxis dataKey="date" tick={{ fontSize: 10, fill: chartTickColor }} axisLine={false} tickLine={false} />
                      <YAxis tick={{ fontSize: 10, fill: chartTickColor }} axisLine={false} tickLine={false} domain={['auto', 'auto']} tickFormatter={(v) => `$${(v/1000).toFixed(1)}k`} />
                      <Tooltip
                        contentStyle={{ background: 'var(--surface-panel-strong)', border: '1px solid var(--border)', borderRadius: '12px', fontSize: '12px' }}
                        labelStyle={{ color: 'var(--text-2)' }}
                        formatter={(value: any) => [`$${Number(value).toLocaleString('en-US', {minimumFractionDigits:2})}`, 'Equity']}
                      />
                      <Area type="monotone" dataKey="equity" stroke="#0ABAB5" strokeWidth={2} fill="url(#eqGradient)" />
                    </AreaChart>
                  </ResponsiveContainer>
                </CardContent>
              </Card>
            )
          })()}
          <div className="botux-duo-grid">
            <Card className="botux-panel-card">
              <CardHeader><CardTitle>Quick Stats</CardTitle><CardDescription>Key metrics at a glance</CardDescription></CardHeader>
              <CardContent>
                <div className="space-y-3">
                  {[
                    ['Connection', isConnected ? 'Connected' : 'Disconnected', isConnected ? 'text-green-500' : 'text-red-500'],
                    ['System Grade', mon?.grade || '—', mon?.grade === 'A' ? 'text-green-500' : ''],
                    ['Market Regime', regime?.regime || '—', ''],
                    ['Signals Today', `${mon?.signals_today || 0}`, ''],
                    ['Data Source', 'BOTUX v2 API', 'text-[#0ABAB5]'],
                  ].map(([label, value, color]) => (
                    <div key={label as string} className="flex justify-between items-center">
                      <span className="text-sm text-muted-foreground">{label}</span>
                      <span className={`text-sm font-medium ${color}`}>{value}</span>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
            <Card className="botux-panel-card">
              <CardHeader><CardTitle>Recent Activity</CardTitle><CardDescription>Latest trading activity</CardDescription></CardHeader>
              <CardContent>
                <div className="space-y-3">
                  {[
                    ['Active Positions', `${pos.length}`, ''],
                    ['Trades Today', `${mon?.trades_today || 0}`, ''],
                    ['Total P&L', `${totalUnrealised >= 0 ? '+' : ''}${totalUnrealised.toFixed(2)}`, pnlColor(totalUnrealised)],
                    ['Brokers Online', `${connectedBrokers.length}/${brokers.length}`, ''],
                    ['Council Decisions', `${mon?.signals_today ? 'Active' : 'Idle'}`, ''],
                  ].map(([label, value, color]) => (
                    <div key={label as string} className="flex justify-between items-center">
                      <span className="text-sm text-muted-foreground">{label}</span>
                      <span className={`text-sm font-medium tabular-nums ${color}`}>{value}</span>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  )
}
