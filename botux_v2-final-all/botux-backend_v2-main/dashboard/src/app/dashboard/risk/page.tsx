'use client'
import { fmt$ } from '@/lib/format'
import { useEffect, useState } from 'react'
import { getRisk, getCouncil, getPDT, getRegime, getFleetRisk } from '@/lib/api'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Shield, CheckCircle, AlertCircle, Users, ShieldAlert } from 'lucide-react'

export default function RiskPage() {
  const [data, setData] = useState<any>(null)

  useEffect(() => {
    async function load() {
      const [risk, council, pdt, regime, fleetRisk] = await Promise.allSettled([
        getRisk(), getCouncil(), getPDT(), getRegime(), getFleetRisk(),
      ]).then(r => r.map(x => x.status === 'fulfilled' ? x.value : null))
      setData({ risk, council, pdt, regime, fleetRisk })
    }
    load()
    const i = setInterval(load, 5000)
    return () => clearInterval(i)
  }, [])

  if (!data) return (
    <div className="botux-page">
      <div className="h-8 bg-muted animate-pulse rounded w-64" />
      <div className="botux-kpi-grid mt-4">
        {[...Array(5)].map((_, i) => <div key={i} className="h-32 bg-muted animate-pulse rounded-lg" />)}
      </div>
    </div>
  )

  const risk = data.risk
  const council = data.council
  const pdt = data.pdt
  const regime = data.regime
  const fleetRisk = data.fleetRisk
  const stats = council?.stats || {}
  const lim = fleetRisk?.limits || {}

  const approvalRate = stats.total > 0 ? Math.round((stats.approved / stats.total) * 100) : 0
  const fmtP = (v?: number | null) => v == null ? '—' : `${v > 0 ? '+' : ''}${Number(v).toFixed(1)}%`

  return (
    <div className="botux-page">
      <section className="botux-page__header">
        <div className="botux-page__heading">
          <div className="botux-page__eyebrow">Protection layer</div>
          <h1 className="botux-page__title">Risk & Council</h1>
          <p className="botux-page__description">Risk controls, regime context, PDT limits, council decisions, and fleet safety limits.</p>
        </div>
      </section>

      {/* Top Stats Grid */}
      <div className="grid gap-4 grid-cols-2 md:grid-cols-3 lg:grid-cols-5 mb-6">
        <Card className="botux-panel-card">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">System Status</CardTitle>
            {risk?.halted ? <AlertCircle className="h-4 w-4 text-red-500 animate-pulse" /> : <CheckCircle className="h-4 w-4 text-green-500" />}
          </CardHeader>
          <CardContent>
            <div className="text-xl font-bold tabular-nums">{risk?.halted ? 'HALTED' : 'Clear'}</div>
            <p className="text-[10px] text-muted-foreground mt-1 truncate" title={risk?.halt_reason}>{risk?.halt_reason || 'No halt conditions'}</p>
          </CardContent>
        </Card>

        <Card className="botux-panel-card">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Market Regime</CardTitle>
            <Shield className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-xl font-bold capitalize">{regime?.regime || '—'}</div>
            <p className="text-[10px] text-muted-foreground mt-1">Classification: {regime?.trend || 'sideways'}</p>
          </CardContent>
        </Card>

        <Card className="botux-panel-card">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">PDT Status</CardTitle>
            <Shield className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-xl font-bold tabular-nums">{pdt?.day_trades_remaining ?? '—'} / {pdt?.day_trades_max ?? 3}</div>
            <p className="text-[10px] text-muted-foreground mt-1">Day trades remaining</p>
          </CardContent>
        </Card>

        <Card className="botux-panel-card">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Council Rate</CardTitle>
            <Users className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-xl font-bold tabular-nums">{approvalRate}%</div>
            <p className="text-[10px] text-muted-foreground mt-1">{stats.approved || 0} of {stats.total || 0} decisions</p>
          </CardContent>
        </Card>

        <Card className="botux-panel-card">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Fleet Status</CardTitle>
            {fleetRisk?.fleet_halted ? <ShieldAlert className="h-4 w-4 text-red-500 animate-pulse" /> : <Shield className="h-4 w-4 text-green-500" />}
          </CardHeader>
          <CardContent>
            <div className="text-xl font-bold tabular-nums">{fleetRisk?.fleet_halted ? 'HALTED' : 'Clear'}</div>
            <p className="text-[10px] text-muted-foreground mt-1 truncate" title={fleetRisk?.halt_reason}>{fleetRisk?.halt_reason || 'No fleet halts'}</p>
          </CardContent>
        </Card>
      </div>

      {/* Limits and Fleet Details Grid */}
      <div className="grid gap-6 md:grid-cols-2 mb-6">
        <Card className="botux-panel-card">
          <CardHeader>
            <CardTitle className="text-sm font-medium">Protection Limits</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div className="pb-2 border-b flex justify-between">
                <span className="text-muted-foreground">Daily Loss Cap</span>
                <span className="font-semibold">{lim.daily_loss_cap || 5}%</span>
              </div>
              <div className="pb-2 border-b flex justify-between">
                <span className="text-muted-foreground">Weekly Loss Cap</span>
                <span className="font-semibold">{lim.weekly_loss_cap || 8}%</span>
              </div>
              <div className="pb-2 border-b flex justify-between">
                <span className="text-muted-foreground">Monthly Loss Cap</span>
                <span className="font-semibold">{lim.monthly_loss_cap || 12}%</span>
              </div>
              <div className="pb-2 border-b flex justify-between">
                <span className="text-muted-foreground">Max Drawdown</span>
                <span className="font-semibold">{lim.max_drawdown || 15}%</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Max Positions</span>
                <span className="font-semibold">{lim.max_positions || 15}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Max Per Bot</span>
                <span className="font-semibold">{lim.max_per_bot || 25}%</span>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="botux-panel-card">
          <CardHeader>
            <CardTitle className="text-sm font-medium">Fleet Risk Telemetry</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div className="pb-2 border-b flex justify-between">
                <span className="text-muted-foreground">Peak Equity</span>
                <span className="font-semibold">{fmt$(fleetRisk?.peak_equity)}</span>
              </div>
              <div className="pb-2 border-b flex justify-between">
                <span className="text-muted-foreground">Drawdown</span>
                <span className={`font-semibold ${(fleetRisk?.current_drawdown_pct || 0) > 5 ? 'text-red-500' : ''}`}>{fmtP(fleetRisk?.current_drawdown_pct)}</span>
              </div>
              <div className="pb-2 border-b flex justify-between">
                <span className="text-muted-foreground">Daily P&L</span>
                <span className={`font-semibold ${(fleetRisk?.daily_pnl || 0) >= 0 ? 'text-green-500' : 'text-red-500'}`}>{fmt$(fleetRisk?.daily_pnl)}</span>
              </div>
              <div className="pb-2 border-b flex justify-between">
                <span className="text-muted-foreground">Weekly P&L</span>
                <span className={`font-semibold ${(fleetRisk?.weekly_pnl || 0) >= 0 ? 'text-green-500' : 'text-red-500'}`}>{fmt$(fleetRisk?.weekly_pnl)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Monthly P&L</span>
                <span className={`font-semibold ${(fleetRisk?.monthly_pnl || 0) >= 0 ? 'text-green-500' : 'text-red-500'}`}>{fmt$(fleetRisk?.monthly_pnl)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Positions</span>
                <span className="font-semibold">{fleetRisk?.total_positions || 0} / {lim.max_positions || 15}</span>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Council Governance Detail Card */}
      <Card className="botux-panel-card mb-6">
        <CardHeader>
          <CardTitle className="text-sm font-medium">Council Governance</CardTitle>
          <CardDescription>Multi-agent deliberation and veto controls</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-6 text-sm">
            <div>
              <span className="text-muted-foreground mr-2">Min Required Votes:</span>
              <span className="font-semibold">{council?.min_votes || 3}</span>
            </div>
            <div>
              <span className="text-muted-foreground mr-2">Min Confidence:</span>
              <span className="font-semibold">{(council?.min_confidence || 0.55).toFixed(2)}</span>
            </div>
            <div>
              <span className="text-muted-foreground mr-2">Risk Veto:</span>
              <Badge variant={council?.risk_veto ? 'default' : 'secondary'} className={council?.risk_veto ? 'bg-green-500/10 text-green-500 hover:bg-green-500/20' : ''}>
                {council?.risk_veto ? 'Enabled' : 'Disabled'}
              </Badge>
            </div>
            <div>
              <span className="text-muted-foreground mr-2">Vetoed Count:</span>
              <span className="font-semibold text-amber-500">{stats.vetoed || 0}</span>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Recent Council Decisions Table */}
      <Card className="botux-table-card">
        <CardHeader>
          <CardTitle>Recent Council Decisions</CardTitle>
          <CardDescription>Decisions and voting counts from the last 10 council runs.</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="font-semibold">Symbol</TableHead>
                  <TableHead className="font-semibold">Decision</TableHead>
                  <TableHead className="font-semibold">Votes</TableHead>
                  <TableHead className="font-semibold">Confidence</TableHead>
                  <TableHead className="font-semibold">Time</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {council?.recent && council.recent.length > 0 ? (
                  council.recent.map((d: any, idx: number) => {
                    const isApprove = String(d.decision).toUpperCase() === 'APPROVE'
                    const isReject = String(d.decision).toUpperCase() === 'REJECT'
                    const badgeVariant = isApprove ? 'default' : isReject ? 'destructive' : 'outline'
                    const badgeClass = isApprove
                      ? 'bg-green-500/10 text-green-500 border border-green-500/20 hover:bg-green-500/20'
                      : isReject
                      ? 'bg-red-500/10 text-red-500 border border-red-500/20 hover:bg-red-500/20'
                      : 'bg-amber-500/10 text-amber-500 border border-amber-500/20 hover:bg-amber-500/20'
                    const displayTime = d.created_at
                      ? new Date(d.created_at).toLocaleString('en-US', {
                          day: '2-digit',
                          month: 'short',
                          hour: '2-digit',
                          minute: '2-digit',
                        })
                      : '—'
                    return (
                      <TableRow key={idx}>
                        <TableCell className="font-bold">{d.symbol || '—'}</TableCell>
                        <TableCell>
                          <Badge variant={badgeVariant} className={badgeClass}>
                            {String(d.decision).toUpperCase()}
                          </Badge>
                        </TableCell>
                        <TableCell>{d.buy_votes || 0} / 5</TableCell>
                        <TableCell className="tabular-nums">{(d.confidence || 0).toFixed(2)}</TableCell>
                        <TableCell className="text-muted-foreground">{displayTime}</TableCell>
                      </TableRow>
                    )
                  })
                ) : (
                  <TableRow>
                    <TableCell colSpan={5} className="text-center text-muted-foreground py-4">
                      No recent council decisions found.
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
