'use client'
import { fmt$, fmtDateTime } from '@/lib/format'
import { getBots, getTrades, getStrategies, getRewardStatus, getFleetEdge, getRegime, getBotPaperTrades, getBotDebates, getBotTuningHistory, patchBotProfile } from '@/lib/api'
import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { ArrowLeft, TrendingUp, TrendingDown, Shield, BarChart3, Activity, Zap, Target, Pickaxe, Copy, Bot, Brain, Gauge, Clock, Scale, Sliders } from 'lucide-react'
import Link from 'next/link'

const botIcons: Record<string, any> = {
  turbo: Zap, drifter: BarChart3, swingtrade: BarChart3,
  gambler: Target, options: Target,
  copycat: Copy, tradecopy: Copy,
  nugget_bot: Pickaxe, ausmining: Pickaxe,
  evo_catalyst: Zap,
}

const botColors: Record<string, string> = {
  turbo: '#0ABAB5', drifter: '#6366F1', swingtrade: '#6366F1',
  gambler: '#F59E0B', options: '#F59E0B',
  copycat: '#8B5CF6', tradecopy: '#8B5CF6',
  nugget_bot: '#EF4444', ausmining: '#EF4444', evo_catalyst: '#10B981',
}

const lcColor = (s: string) => {
  const m: Record<string, string> = {
    LIVE: 'bg-green-500/15 text-green-400',
    SCALED: 'bg-green-500/15 text-green-400',
    PAPER: 'bg-blue-500/15 text-blue-400',
    SHADOW: 'bg-amber-500/15 text-amber-400',
    DEMOTED: 'bg-red-500/15 text-red-400',
  }
  return m[s] || ''
}

export default function BotProfilePage() {
  const params = useParams()
  const botId = params.id as string
  const [profile, setProfile] = useState<any>(null)
  const [trades, setTrades] = useState<any[]>([])
  const [strategy, setStrategy] = useState<any>(null)
  const [reward, setReward] = useState<any>(null)
  const [edgeData, setEdgeData] = useState<any>(null)
  const [regime, setRegime] = useState<any>(null)
  const [paperTrades, setPaperTrades] = useState<any[]>([])
  const [debates, setDebates] = useState<any[]>([])
  const [tuningHistory, setTuningHistory] = useState<any[]>([])
  const [savingEnabled, setSavingEnabled] = useState(false)
  const [savingBrokerSubmit, setSavingBrokerSubmit] = useState(false)
  const [controlError, setControlError] = useState<string | null>(null)

  useEffect(() => {
    let mounted = true
    async function loadProfile() {
      const botsData = await getBots().catch(() => null)
      if (!mounted) return
      const botProfile = (botsData as any)?.profiles?.[botId] || null
      if (botProfile) setProfile(botProfile)
    }
    async function loadCore() {
      const [tradeRes, stratRes] = await Promise.allSettled([
        getTrades(), getStrategies(),
      ]).then(r => r.map(x => x.status === 'fulfilled' ? x.value : null))
      if (!mounted) return
      const allTrades = Array.isArray(tradeRes) ? tradeRes : []
      if (allTrades.length > 0) setTrades(allTrades.filter((t: any) => t.features?.bot_id === botId || t.bot_id === botId))
      const strats = (stratRes as any)?.strategies || {}
      const matchedStrat = Object.values(strats).find((s: any) => (s as any).bot_ids?.includes(botId))
      if (matchedStrat) setStrategy(matchedStrat)
    }
    async function loadIntel() {
      const [rewardRes, edgeRes, regimeRes] = await Promise.allSettled([
        getRewardStatus(), getFleetEdge(), getRegime(),
      ]).then(r => r.map(x => x.status === 'fulfilled' ? x.value : null))
      if (!mounted) return
      if (rewardRes) setReward(rewardRes)
      if (edgeRes) setEdgeData(edgeRes)
      if (regimeRes) setRegime(regimeRes)
    }
    async function loadGovernance() {
      const [paperRes, debateRes, tuneRes] = await Promise.allSettled([
        getBotPaperTrades(botId), getBotDebates(botId), getBotTuningHistory(botId),
      ]).then(r => r.map(x => x.status === 'fulfilled' ? x.value : null))
      if (!mounted) return
      setPaperTrades(((paperRes as any)?.trades) || [])
      setDebates(((debateRes as any)?.debates) || [])
      setTuningHistory(((tuneRes as any)?.adjustments) || [])
    }
    loadProfile()
    loadCore()
    loadIntel()
    loadGovernance()
    const i0 = setInterval(loadProfile, 15000)
    const i1 = setInterval(loadCore, 8000)
    const i2 = setInterval(loadIntel, 30000)
    const i3 = setInterval(loadGovernance, 15000)
    return () => { mounted = false; clearInterval(i0); clearInterval(i1); clearInterval(i2); clearInterval(i3) }
  }, [botId])

  if (!profile) return (
    <div className="botux-page">
      <div className="h-8 bg-muted animate-pulse rounded w-64" />
      <div className="h-64 bg-muted animate-pulse rounded-xl" />
    </div>
  )

  const p = profile
  const perf = p.performance || {}
  const alloc = p.allocation || {}
  const amt = alloc.usd || alloc.aud || 0
  const Icon = botIcons[botId] || Bot
  const color = botColors[botId] || '#0ABAB5'
  const wins = trades.filter((t: any) => t.outcome === 'WIN').length
  const losses = trades.filter((t: any) => t.outcome === 'LOSS').length
  const openTrades = trades.filter((t: any) => t.outcome === 'OPEN')
  const closedTrades = trades.filter((t: any) => t.outcome !== 'OPEN')
  const winRate = (wins + losses) > 0 ? ((wins / (wins + losses)) * 100) : null

  // Reward + edge data for this bot
  const botReward = reward?.bot_performance?.[botId]
  const botEdge = (edgeData as any)?.fleet?.[botId]

  // Calculate avg P&L from trades
  const avgPnl = closedTrades.length > 0
    ? closedTrades.reduce((a: number, t: any) => a + (t.pnl_pct || 0), 0) / closedTrades.length
    : null

  async function updateBotControls(patch: Record<string, any>, mode: 'enabled' | 'broker') {
    const previous = p
    const next = {
      ...previous,
      ...patch,
    }
    next.autopilot_state = String(next.autopilot_state || 'active').toLowerCase()
    next.broker_submit_allowed = Boolean(next.enabled) && ['PAPER', 'LIVE', 'SCALED'].includes(String(next.lifecycle_state || '').toUpperCase()) && next.autopilot_state !== 'shadow'
    setControlError(null)
    setProfile(next)
    if (mode === 'enabled') setSavingEnabled(true)
    if (mode === 'broker') setSavingBrokerSubmit(true)
    const result = await patchBotProfile(botId, patch)
    if (!result?.updated) {
      setProfile(previous)
      setControlError(result?.error || 'Could not update bot controls.')
    } else {
      const botsData = await getBots().catch(() => null)
      const refreshed = (botsData as any)?.profiles?.[botId] || null
      if (refreshed) setProfile(refreshed)
    }
    if (mode === 'enabled') setSavingEnabled(false)
    if (mode === 'broker') setSavingBrokerSubmit(false)
  }

  return (
    <div className="botux-page">
      {/* Back */}
      <Link href="/dashboard/bots">
        <Button variant="ghost" size="sm"><ArrowLeft className="h-4 w-4 mr-2" /> Back to Fleet</Button>
      </Link>

      {/* Hero Section */}
      <Card className="botux-hero-card">
        <CardContent className="p-0">
        <div className="p-6 pb-5">
          <div className="flex items-start justify-between">
            <div className="flex items-center gap-4">
              <div className="rounded-2xl border border-border bg-muted p-4">
                <Icon className="h-10 w-10" style={{ color }} />
              </div>
              <div>
                <div className="flex items-center gap-3">
                  <h1 className="text-3xl font-bold tracking-tight">{p.display_name || botId}</h1>
                  <Badge variant="outline" className={`${lcColor(p.lifecycle_state)} border-0 text-xs`}>{p.lifecycle_state}</Badge>
                  <Badge variant={p.enabled ? 'default' : 'secondary'} className={p.enabled ? 'bg-green-500/15 text-green-400 border-0 text-xs' : 'text-xs'}>
                    {p.enabled ? 'Active' : 'Inactive'}
                  </Badge>
                  <Badge variant="outline" className={`${(p.autopilot_state || 'active') === 'shadow' ? 'bg-amber-500/15 text-amber-400' : 'bg-green-500/15 text-green-400'} border-0 text-xs`}>
                    {(p.autopilot_state || 'active') === 'shadow' ? 'Autopilot Shadow' : 'Autopilot Active'}
                  </Badge>
                  <Badge variant="outline" className={`${p.broker_submit_allowed === false ? 'bg-red-500/10 text-red-400' : 'bg-green-500/10 text-green-400'} border-0 text-xs`}>
                    {p.broker_submit_allowed === false ? 'Broker Submit Off' : 'Broker Submit On'}
                  </Badge>
                </div>
                <p className="text-muted-foreground mt-1">{p.mission}</p>
                <div className="flex items-center gap-2 mt-2">
                  <Badge variant="secondary" className="text-[10px]">{p.strategy_type?.replace(/_/g, ' ')}</Badge>
                  <Badge variant="secondary" className="text-[10px]">{p.horizon}</Badge>
                  <Badge variant="secondary" className="text-[10px]">{p.broker?.toUpperCase()}</Badge>
                  <Badge variant="secondary" className="text-[10px]">{(p.market || '').replace(/_/g, ' ')}</Badge>
                </div>
                <div className="mt-4 grid gap-3 sm:grid-cols-2">
                  <div className="flex items-center justify-between rounded-lg border border-border bg-background/70 px-3 py-2">
                    <div>
                      <p className="text-sm font-medium">Bot Enabled</p>
                      <p className="text-xs text-muted-foreground">Allow this bot to stay active in the fleet.</p>
                    </div>
                    <Switch
                      checked={Boolean(p.enabled)}
                      disabled={savingEnabled}
                      aria-label="Toggle bot enabled"
                      onCheckedChange={(checked) => updateBotControls({ enabled: checked }, 'enabled')}
                    />
                  </div>
                  <div className="flex items-center justify-between rounded-lg border border-border bg-background/70 px-3 py-2">
                    <div>
                      <p className="text-sm font-medium">Submit To Broker</p>
                      <p className="text-xs text-muted-foreground">This maps to autopilot shadow mode for broker submission.</p>
                    </div>
                    <Switch
                      checked={p.broker_submit_allowed !== false}
                      disabled={savingBrokerSubmit || !Boolean(p.enabled)}
                      aria-label="Toggle broker submit"
                      onCheckedChange={(checked) => updateBotControls({ autopilot_state: checked ? 'active' : 'shadow' }, 'broker')}
                    />
                  </div>
                </div>
                {controlError && (
                  <p className="mt-3 text-xs text-red-400">{controlError}</p>
                )}
              </div>
            </div>
            <div className="text-right">
              <div className="text-2xl font-bold tabular-nums">
                {amt > 0 ? `${alloc.currency === 'AUD' ? 'A' : ''}$${amt.toLocaleString()}` : '—'}
              </div>
              <p className="text-xs text-muted-foreground">{alloc.pct || 0}% allocation</p>
            </div>
          </div>
        </div>
        </CardContent>
      </Card>

      {/* Performance Scorecard */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-5">
        <Card className="botux-panel-card">
          <CardContent className="pt-4 pb-3">
            <p className="text-[11px] text-muted-foreground font-medium uppercase tracking-wider">Win Rate</p>
            <div className={`text-2xl font-bold tabular-nums mt-1 ${winRate != null ? (winRate >= 55 ? 'text-green-500' : winRate < 45 ? 'text-red-500' : '') : ''}`}>
              {winRate != null ? `${winRate.toFixed(1)}%` : '—'}
            </div>
            <p className="text-xs text-muted-foreground mt-1">{wins}W / {losses}L</p>
          </CardContent>
        </Card>
        <Card className="botux-panel-card">
          <CardContent className="pt-4 pb-3">
            <p className="text-[11px] text-muted-foreground font-medium uppercase tracking-wider">Trades</p>
            <div className="text-2xl font-bold tabular-nums mt-1">{trades.length}</div>
            <p className="text-xs text-muted-foreground mt-1">{openTrades.length} open / {closedTrades.length} closed</p>
          </CardContent>
        </Card>
        <Card className="botux-panel-card">
          <CardContent className="pt-4 pb-3">
            <p className="text-[11px] text-muted-foreground font-medium uppercase tracking-wider">Avg R</p>
            <div className={`text-2xl font-bold tabular-nums mt-1 ${(botReward?.avg_r ?? perf.avg_r) > 0 ? 'text-green-500' : (botReward?.avg_r ?? perf.avg_r) < 0 ? 'text-red-500' : ''}`}>
              {(botReward?.avg_r ?? perf.avg_r) != null ? `${(botReward?.avg_r ?? perf.avg_r) > 0 ? '+' : ''}${(botReward?.avg_r ?? perf.avg_r).toFixed(1)}R` : '—'}
            </div>
            <p className="text-xs text-muted-foreground mt-1">Total: {botReward?.total_r != null ? `${botReward.total_r.toFixed(0)}R` : '—'}</p>
          </CardContent>
        </Card>
        <Card className="botux-panel-card">
          <CardContent className="pt-4 pb-3">
            <p className="text-[11px] text-muted-foreground font-medium uppercase tracking-wider">Avg P&L</p>
            <div className={`text-2xl font-bold tabular-nums mt-1 ${avgPnl != null ? (avgPnl > 0 ? 'text-green-500' : 'text-red-500') : ''}`}>
              {avgPnl != null ? `${avgPnl > 0 ? '+' : ''}${avgPnl.toFixed(2)}%` : '—'}
            </div>
            <p className="text-xs text-muted-foreground mt-1">per closed trade</p>
          </CardContent>
        </Card>
        <Card className="botux-panel-card">
          <CardContent className="pt-4 pb-3">
            <p className="text-[11px] text-muted-foreground font-medium uppercase tracking-wider">Edge Health</p>
            <div className={`text-2xl font-bold mt-1 ${botEdge?.severity === 'HEALTHY' ? 'text-green-500' : 'text-amber-500'}`}>
              {botEdge?.severity === 'HEALTHY' ? 'Strong' : botEdge?.severity ? 'Low Data' : '—'}
            </div>
            <p className="text-xs text-muted-foreground mt-1">Regime: {(regime as any)?.regime || '—'}</p>
          </CardContent>
        </Card>
      </div>

      {/* Test Bot Lineage Card — only for auto-spawned bots */}
      {botId.startsWith('auto_') && (() => {
        const fp = (p as any).arena_params || {}
        const lineage = (p as any).lineage || {}
        const family = (p as any).family || (p as any).strategy_type || 'unknown'
        const fpId = (p as any).fingerprint_id || 'none'
        const tpPct = fp.take_profit_pct ? (fp.take_profit_pct * 100).toFixed(2) : '?'
        const slPct = fp.stop_loss_pct ? (fp.stop_loss_pct * 100).toFixed(2) : '?'
        const rr = fp.take_profit_pct && fp.stop_loss_pct ? (fp.take_profit_pct / fp.stop_loss_pct).toFixed(2) : '?'
        const minScore = fp.min_combined_score ? (fp.min_combined_score * 100).toFixed(0) : '?'
        const lifecycleHistory = (p as any).lifecycle_history || []
        const hermesReview = (p as any).hermes_review

        return (
          <Card className="botux-panel-card">
            <CardHeader className="pb-3">
              <CardTitle className="text-sm flex items-center gap-2">
                <Activity className="h-4 w-4" /> Test Bot Lineage & Hypothesis
              </CardTitle>
              <CardDescription>Strategy fingerprint, parent source, and lifecycle journey</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div>
                  <p className="text-[11px] text-muted-foreground">Family</p>
                  <p className="text-sm font-bold capitalize">{family}</p>
                </div>
                <div>
                  <p className="text-[11px] text-muted-foreground">Fingerprint ID</p>
                  <p className="text-xs font-mono break-all">{fpId}</p>
                </div>
                <div>
                  <p className="text-[11px] text-muted-foreground">Take Profit</p>
                  <p className="text-sm font-bold text-green-500">+{tpPct}%</p>
                </div>
                <div>
                  <p className="text-[11px] text-muted-foreground">Stop Loss</p>
                  <p className="text-sm font-bold text-red-500">-{slPct}%</p>
                </div>
                <div>
                  <p className="text-[11px] text-muted-foreground">Risk:Reward</p>
                  <p className="text-sm font-bold">{rr}:1</p>
                </div>
                <div>
                  <p className="text-[11px] text-muted-foreground">Min Score</p>
                  <p className="text-sm font-bold">{minScore}</p>
                </div>
                <div>
                  <p className="text-[11px] text-muted-foreground">Preferred Regime</p>
                  <p className="text-sm font-bold">{fp.preferred_regime || '—'}</p>
                </div>
                <div>
                  <p className="text-[11px] text-muted-foreground">Hold Period</p>
                  <p className="text-sm font-bold">{p.horizon || '—'}</p>
                </div>
              </div>

              <div className="border-t border-border/40 pt-3">
                <p className="text-[11px] text-muted-foreground mb-1">Parent Strategy</p>
                <p className="text-sm font-mono break-all">{lineage.parent_strategy || (p as any).arena_source || 'unknown'}</p>
                {lineage.parent_source && (
                  <p className="text-[10px] text-muted-foreground mt-1">Source: {lineage.parent_source}</p>
                )}
                {lineage.parent_win_rate != null && (
                  <p className="text-[10px] text-muted-foreground">Parent WR: {lineage.parent_win_rate.toFixed(0)}%</p>
                )}
              </div>

              {hermesReview && (
                <div className="border-t border-border/40 pt-3">
                  <p className="text-[11px] text-muted-foreground mb-2">Hermes Review</p>
                  <div className="flex items-center gap-2 mb-2">
                    <Badge variant="outline" className={`text-xs ${
                      hermesReview.decision === 'PROMOTE' ? 'bg-emerald-500/15 text-emerald-400 border-0' :
                      hermesReview.decision === 'TWEAK' ? 'bg-amber-500/15 text-amber-400 border-0' :
                      hermesReview.decision === 'DEMOTE' ? 'bg-red-500/15 text-red-400 border-0' :
                      'bg-blue-500/15 text-blue-400 border-0'
                    }`}>
                      {hermesReview.decision}
                    </Badge>
                    <span className="text-xs text-muted-foreground">
                      confidence: {((hermesReview.confidence || 0) * 100).toFixed(0)}%
                    </span>
                  </div>
                  <p className="text-xs text-muted-foreground">{hermesReview.reasoning}</p>
                </div>
              )}

              {lifecycleHistory.length > 0 && (
                <div className="border-t border-border/40 pt-3">
                  <p className="text-[11px] text-muted-foreground mb-2">Lifecycle History</p>
                  <div className="space-y-1">
                    {lifecycleHistory.map((h: any, i: number) => (
                      <div key={i} className="flex items-center justify-between text-xs">
                        <span>
                          <span className="text-muted-foreground">{h.from}</span>
                          <span className="mx-1">→</span>
                          <span className="font-medium">{h.to}</span>
                        </span>
                        <span className="text-[10px] text-muted-foreground">{h.reason?.slice(0, 50)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>
        )
      })()}

      {/* Intelligence + Configuration side by side */}
      <div className="grid gap-4 md:grid-cols-2">
        {/* Intelligence */}
        <Card className="botux-panel-card">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm flex items-center gap-2"><Brain className="h-4 w-4" /> Intelligence</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              {botEdge?.short && (
                <>
                  <div>
                    <p className="text-[11px] text-muted-foreground">Recent WR (20t)</p>
                    <p className={`text-sm font-bold ${botEdge.short.win_rate >= 55 ? 'text-green-500' : botEdge.short.win_rate < 45 ? 'text-red-500' : ''}`}>
                      {botEdge.short.win_rate?.toFixed(1)}%
                    </p>
                  </div>
                  <div>
                    <p className="text-[11px] text-muted-foreground">Baseline WR</p>
                    <p className="text-sm font-bold">{botEdge.baseline?.win_rate?.toFixed(1)}%</p>
                  </div>
                  <div>
                    <p className="text-[11px] text-muted-foreground">Expectancy</p>
                    <p className="text-sm font-bold">${botEdge.short.expectancy_usd?.toFixed(2)}</p>
                  </div>
                  <div>
                    <p className="text-[11px] text-muted-foreground">Max Drawdown</p>
                    <p className="text-sm font-bold text-red-500">{botEdge.baseline?.max_drawdown?.toFixed(1)}%</p>
                  </div>
                </>
              )}
              {!botEdge?.short && (
                <div className="col-span-2 text-sm text-muted-foreground py-4 text-center">
                  Insufficient trade data for edge analysis
                </div>
              )}
            </div>
          </CardContent>
        </Card>

        {/* Configuration */}
        <Card className="botux-panel-card">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm flex items-center gap-2"><Gauge className="h-4 w-4" /> Configuration</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <p className="text-[11px] text-muted-foreground">Mode</p>
                <p className="text-sm font-bold">{p.mode?.toUpperCase()}</p>
              </div>
              <div>
                <p className="text-[11px] text-muted-foreground">Max Position</p>
                <p className="text-sm font-bold">{p.risk?.max_position_pct || '—'}%</p>
              </div>
              <div>
                <p className="text-[11px] text-muted-foreground">Max Daily Loss</p>
                <p className="text-sm font-bold">{p.risk?.max_daily_loss_pct || '—'}%</p>
              </div>
              <div>
                <p className="text-[11px] text-muted-foreground">Risk Profile</p>
                <p className="text-sm font-bold capitalize">{p.risk?.profile || '—'}</p>
              </div>
              {strategy && (
                <div className="col-span-2">
                  <p className="text-[11px] text-muted-foreground">Strategy</p>
                  <p className="text-sm font-bold">{strategy.name} v{strategy.version}</p>
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Recent Trades */}
      <Card className="botux-table-card">
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm flex items-center gap-2"><Clock className="h-4 w-4" /> Recent Trades</CardTitle>
            <span className="text-xs text-muted-foreground">{trades.length} total</span>
          </div>
        </CardHeader>
        <CardContent>
          {trades.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">No trades yet</div>
          ) : (
            <div className="botux-list">
              {trades.slice(0, 10).map((t: any, i: number) => {
                const feat = t.features || {}
                const pnlPct = t.pnl_pct || 0
                const qty = feat.qty || feat.filled_qty || 0
                const entryP = t.entry_price || feat.fill_price || 0
                const exitP = t.exit_price || 0
                const pnlDollar = qty > 0 && exitP > 0 ? (exitP - entryP) * qty : null
                const isBuy = t.action === 'BUY' || t.action === 'STRONG_BUY'
                const outcomeColor = t.outcome === 'WIN' ? 'text-green-500' : t.outcome === 'LOSS' ? 'text-red-500' : 'text-blue-400'
                return (
                  <div key={i} className="botux-list__item">
                    <div className="flex items-center gap-3">
                      <div className="rounded-lg border border-border bg-muted p-1.5">
                        {isBuy ? <TrendingUp className="h-3.5 w-3.5 text-green-500" /> : <TrendingDown className="h-3.5 w-3.5 text-red-500" />}
                      </div>
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-sm">{t.symbol}</span>
                          <span className={`text-[10px] font-bold ${outcomeColor}`}>{t.outcome}</span>
                        </div>
                        <p className="text-[11px] text-muted-foreground">
                          {qty || '—'} @ {fmt$(entryP)}
                          {feat.council_decision && (
                            <span className="ml-1.5 text-muted-foreground/60">
                              Council {feat.council_votes}/5 ({feat.council_confidence ? `${(feat.council_confidence*100).toFixed(0)}%` : '—'})
                            </span>
                          )}
                        </p>
                      </div>
                    </div>
                    <div className="flex items-center gap-5 text-right">
                      {exitP > 0 && (
                        <div>
                          <p className="text-[11px] text-muted-foreground">Exit</p>
                          <p className="text-sm tabular-nums">{fmt$(exitP)}</p>
                        </div>
                      )}
                      <div>
                        <p className="text-[11px] text-muted-foreground">P&L %</p>
                        <p className={`text-sm font-medium tabular-nums ${pnlPct > 0 ? 'text-green-500' : pnlPct < 0 ? 'text-red-500' : ''}`}>
                          {pnlPct !== 0 ? `${pnlPct > 0 ? '+' : ''}${pnlPct.toFixed(2)}%` : '—'}
                        </p>
                      </div>
                      <div>
                        <p className="text-[11px] text-muted-foreground">P&L $</p>
                        <p className={`text-sm font-bold tabular-nums ${pnlDollar != null ? (pnlDollar > 0 ? 'text-green-500' : 'text-red-500') : ''}`}>
                          {pnlDollar != null ? `${pnlDollar > 0 ? '+' : ''}$${Math.abs(pnlDollar).toFixed(2)}` : '—'}
                        </p>
                      </div>
                      <div className="w-20">
                        <p className="text-[11px] text-muted-foreground">Time</p>
                        <p className="text-[11px] text-muted-foreground tabular-nums">{fmtDateTime(t.created_at)}</p>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Paper Trades w/ MFE/MAE — only for test bots that have paper trade data */}
      {paperTrades.length > 0 && (
        <Card className="botux-table-card">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm flex items-center gap-2"><BarChart3 className="h-4 w-4" /> Paper Trades & Excursion (MFE / MAE)</CardTitle>
              <span className="text-xs text-muted-foreground">{paperTrades.length} paper trades</span>
            </div>
            <CardDescription>How far price moved in our favor (MFE) and against us (MAE) before exit — drives the adaptive tuner</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="botux-data-table">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-left text-[11px] text-muted-foreground">
                    <th className="px-3 py-2">Symbol</th>
                    <th className="px-3 py-2">Outcome</th>
                    <th className="px-3 py-2">P&L %</th>
                    <th className="px-3 py-2">MFE %</th>
                    <th className="px-3 py-2">MAE %</th>
                    <th className="px-3 py-2">Exit Reason</th>
                    <th className="px-3 py-2">Hold (h)</th>
                    <th className="px-3 py-2">Time</th>
                  </tr>
                </thead>
                <tbody>
                  {paperTrades.slice(0, 25).map((t: any, i: number) => {
                    const mfe = t.mfe_pct
                    const mae = t.mae_pct
                    const pnl = t.bot_pnl_pct || 0
                    const hasExcursion = mfe != null && mae != null
                    const cutEarly = hasExcursion && t.bot_outcome === 'WIN' && mfe > pnl * 1.5 && mfe > pnl + 1.0
                    const heldLong = hasExcursion && t.bot_outcome === 'LOSS' && Math.abs(mae) > Math.abs(pnl) * 1.3
                    return (
                      <tr key={i}>
                        <td className="px-3 py-2 font-bold">{t.symbol}</td>
                        <td className="px-3 py-2">
                          <Badge variant={t.bot_outcome === 'WIN' ? 'default' : 'destructive'} className="text-[10px]">{t.bot_outcome}</Badge>
                        </td>
                        <td className={`px-3 py-2 tabular-nums font-medium ${pnl > 0 ? 'text-green-500' : pnl < 0 ? 'text-red-500' : ''}`}>
                          {pnl > 0 ? '+' : ''}{pnl.toFixed(2)}%
                        </td>
                        <td className={`px-3 py-2 tabular-nums ${cutEarly ? 'text-amber-400 font-bold' : 'text-green-500/80'}`}>
                          {mfe != null ? `+${mfe.toFixed(2)}%` : '—'}
                          {cutEarly && <span className="ml-1 text-[9px]">cut early</span>}
                        </td>
                        <td className={`px-3 py-2 tabular-nums ${heldLong ? 'text-amber-400 font-bold' : 'text-red-500/80'}`}>
                          {mae != null ? `${mae.toFixed(2)}%` : '—'}
                          {heldLong && <span className="ml-1 text-[9px]">held long</span>}
                        </td>
                        <td className="px-3 py-2 text-muted-foreground">{t.exit_reason || '—'}</td>
                        <td className="px-3 py-2 tabular-nums">{(t.hold_hours || 0).toFixed(1)}</td>
                        <td className="px-3 py-2 text-[10px] text-muted-foreground tabular-nums">{fmtDateTime(t.ts)}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Hermes Bull/Bear Debates */}
      {debates.length > 0 && (
        <Card className="botux-table-card">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm flex items-center gap-2"><Scale className="h-4 w-4" /> Hermes Bull/Bear Debates</CardTitle>
              <span className="text-xs text-muted-foreground">{debates.length} debates</span>
            </div>
            <CardDescription>Structured debates Hermes ran before high-impact decisions on this bot</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {debates.map((d: any, i: number) => {
              const decisionColor = d.final_decision === 'PROMOTE' ? 'bg-emerald-500/15 text-emerald-400'
                : d.final_decision === 'DEMOTE' ? 'bg-red-500/15 text-red-400'
                : 'bg-blue-500/15 text-blue-400'
              return (
                <div key={i} className="rounded-lg border border-border bg-background p-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Badge variant="outline" className="text-[10px]">Proposed: {d.proposed_action}</Badge>
                      <Badge variant="outline" className={`text-[10px] border-0 ${decisionColor}`}>Final: {d.final_decision}</Badge>
                      <span className="text-[11px] text-muted-foreground">confidence: {((d.confidence || 0) * 100).toFixed(0)}%</span>
                    </div>
                    <span className="text-[10px] text-muted-foreground tabular-nums">{fmtDateTime(d.ts)}</span>
                  </div>
                  <div className="grid gap-3 md:grid-cols-2">
                    <div className="rounded-md border border-border bg-muted p-3">
                      <p className="text-[10px] uppercase tracking-wider text-muted-foreground font-bold mb-1.5">Bull (FOR)</p>
                      <p className="text-xs text-muted-foreground leading-relaxed">{d.bull_argument}</p>
                    </div>
                    <div className="rounded-md border border-border bg-muted p-3">
                      <p className="text-[10px] uppercase tracking-wider text-muted-foreground font-bold mb-1.5">Bear (AGAINST)</p>
                      <p className="text-xs text-muted-foreground leading-relaxed">{d.bear_argument}</p>
                    </div>
                  </div>
                  <div className="rounded-md border border-border bg-background p-3">
                    <p className="text-[10px] uppercase tracking-wider text-muted-foreground font-bold mb-1.5">Synthesis</p>
                    <p className="text-xs leading-relaxed">{d.synthesis_reasoning}</p>
                  </div>
                </div>
              )
            })}
          </CardContent>
        </Card>
      )}

      {/* Adaptive Tuning History */}
      {tuningHistory.length > 0 && (
        <Card className="botux-table-card">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm flex items-center gap-2"><Sliders className="h-4 w-4" /> Adaptive TP/SL Tuning</CardTitle>
              <span className="text-xs text-muted-foreground">{tuningHistory.length} adjustments</span>
            </div>
            <CardDescription>Evidence-driven TP/SL tweaks within hard guardrails</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="botux-list">
              {tuningHistory.map((a: any, i: number) => (
                <div key={i} className="botux-list__item items-start">
                  <div className="flex-1">
                    <div className="flex items-center gap-3 mb-1">
                      <span className="text-xs font-medium">
                        TP <span className="text-muted-foreground">{(a.old_tp * 100).toFixed(2)}%</span>
                        <span className="mx-1">→</span>
                        <span className={a.tp_change > 0 ? 'text-green-500' : a.tp_change < 0 ? 'text-amber-500' : ''}>{(a.new_tp * 100).toFixed(2)}%</span>
                      </span>
                      <span className="text-xs font-medium">
                        SL <span className="text-muted-foreground">{(a.old_sl * 100).toFixed(2)}%</span>
                        <span className="mx-1">→</span>
                        <span className={a.sl_change < 0 ? 'text-green-500' : a.sl_change > 0 ? 'text-amber-500' : ''}>{(a.new_sl * 100).toFixed(2)}%</span>
                      </span>
                    </div>
                    <p className="text-[11px] text-muted-foreground leading-relaxed">{a.reason}</p>
                  </div>
                  <span className="text-[10px] text-muted-foreground tabular-nums whitespace-nowrap ml-3">{fmtDateTime(a.ts)}</span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
