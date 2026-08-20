'use client'
import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import { getBots, getStrategies, getRewardStatus, getFleetEdge } from '@/lib/api'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Bot, Zap, BarChart3, Target, Pickaxe, Copy, Activity } from 'lucide-react'

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

const lcBadge = (state: string) => {
  const m: Record<string, { bg: string; text: string }> = {
    SCALED: { bg: 'bg-green-500/15', text: 'text-green-400' },
    LIVE: { bg: 'bg-green-500/15', text: 'text-green-400' },
    STRONG_PAPER: { bg: 'bg-emerald-500/15', text: 'text-emerald-400' },
    PAPER: { bg: 'bg-blue-500/15', text: 'text-blue-400' },
    TESTING: { bg: 'bg-blue-500/15', text: 'text-blue-400' },
    TWEAK: { bg: 'bg-amber-500/15', text: 'text-amber-400' },
    SHADOW: { bg: 'bg-amber-500/15', text: 'text-amber-400' },
    SEEDED: { bg: 'bg-purple-500/15', text: 'text-purple-400' },
    ARENA: { bg: 'bg-purple-500/15', text: 'text-purple-400' },
    DEMOTED: { bg: 'bg-red-500/15', text: 'text-red-400' },
    ARCHIVED: { bg: 'bg-zinc-500/15', text: 'text-zinc-400' },
  }
  return m[state] || { bg: 'bg-muted', text: 'text-muted-foreground' }
}

const autopilotBadge = (state?: string) => {
  const normalized = (state || 'active').toUpperCase()
  if (normalized === 'SHADOW') {
    return { label: 'AUTOPILOT SHADOW', className: 'bg-amber-500/15 text-amber-400' }
  }
  return { label: 'AUTOPILOT ACTIVE', className: 'bg-green-500/15 text-green-400' }
}

export default function BotsPage() {
  const router = useRouter()
  const [bots, setBots] = useState<any>(null)
  const [strategies, setStrategies] = useState<any>(null)
  const [reward, setReward] = useState<any>(null)
  const [edge, setEdge] = useState<any>(null)

  useEffect(() => {
    let mounted = true
    async function loadCore() {
      const [b, s] = await Promise.allSettled([getBots(), getStrategies()])
        .then(r => r.map(x => x.status === 'fulfilled' ? x.value : null))
      if (!mounted) return
      if (b && (b as any).profiles && Object.keys((b as any).profiles).length > 0) setBots(b)
      if (s) setStrategies(s)
    }
    async function loadIntel() {
      const [r, e] = await Promise.allSettled([getRewardStatus(), getFleetEdge()])
        .then(r => r.map(x => x.status === 'fulfilled' ? x.value : null))
      if (!mounted) return
      if (r) setReward(r)
      if (e) setEdge(e)
    }
    loadCore()
    loadIntel()
    const i1 = setInterval(loadCore, 8000)
    const i2 = setInterval(loadIntel, 30000)
    return () => { mounted = false; clearInterval(i1); clearInterval(i2) }
  }, [])

  if (!bots) return (
    <div className="botux-page">
      <div className="h-10 w-56 bg-muted animate-pulse rounded-lg" />
      <div className="grid gap-4 md:grid-cols-2">
        {[1,2,3,4].map(i => <div key={i} className="h-48 bg-muted animate-pulse rounded-xl" />)}
      </div>
    </div>
  )

  const profiles = Object.values(bots.profiles || {}) as any[]
  const strats = strategies?.strategies || {}
  const enabled = profiles.filter((p: any) => p.enabled).length
  const active = profiles.filter((p: any) => ['PAPER','LIVE','SCALED'].includes(p.lifecycle_state)).length
  const alpacaBots = profiles.filter((p: any) => p.broker === 'alpaca')
  const ibkrBots = profiles.filter((p: any) => p.broker === 'ibkr')
  const totalWins = profiles.reduce((a: number, p: any) => a + (p.performance?.wins || 0), 0)
  const totalLosses = profiles.reduce((a: number, p: any) => a + (p.performance?.losses || 0), 0)
  const fleetWr = (totalWins + totalLosses) > 0 ? (totalWins / (totalWins + totalLosses) * 100) : null
  const avgR = reward?.avg_r_per_trade

  return (
    <div className="botux-page">
      <section className="botux-page__header">
        <div className="botux-page__heading">
          <div className="botux-page__eyebrow">Fleet orchestration</div>
          <h1 className="botux-page__title">Bot Fleet</h1>
          <p className="botux-page__description">
            {profiles.length} bots deployed across {alpacaBots.length > 0 && ibkrBots.length > 0 ? '2 brokers' : '1 broker'} with lifecycle, broker and learning signals aligned in one view.
          </p>
        </div>
        <div className="botux-page__meta">
          <Card className="botux-hero-card min-w-[10rem]">
            <CardContent className="p-0 text-right">
              <div className={`text-xl font-bold tabular-nums ${fleetWr && fleetWr >= 55 ? 'text-green-500' : ''}`}>
                {fleetWr != null ? `${fleetWr.toFixed(1)}%` : '—'}
              </div>
              <p className="text-[11px] text-muted-foreground">Fleet Win Rate</p>
            </CardContent>
          </Card>
          <Card className="botux-hero-card min-w-[10rem]">
            <CardContent className="p-0 text-right">
              <div className={`text-xl font-bold tabular-nums ${avgR > 0 ? 'text-green-500' : avgR < 0 ? 'text-red-500' : ''}`}>
                {avgR != null ? `${avgR > 0 ? '+' : ''}${avgR.toFixed(1)}R` : '—'}
              </div>
              <p className="text-[11px] text-muted-foreground">Avg R/Trade</p>
            </CardContent>
          </Card>
          <Card className="botux-hero-card min-w-[10rem]">
            <CardContent className="p-0 text-right">
              <div className="text-xl font-bold tabular-nums">{active}<span className="text-muted-foreground text-base">/{profiles.length}</span></div>
              <p className="text-[11px] text-muted-foreground" title="Bots in PAPER, LIVE, or SCALED lifecycle (TESTING and DEMOTED excluded)">Active (Live/Paper)</p>
            </CardContent>
          </Card>
        </div>
      </section>

      {/* Intelligence Strip */}
      {reward && (
        <div className="botux-strip">
          <Activity className="h-4 w-4 text-[#0ABAB5]" />
          <span className="botux-strip__label">Intelligence</span>
          <span className="text-xs">Trades: <strong>{reward.total_trades_processed}</strong></span>
          <span className="text-xs">Lessons: <strong>{reward.lessons_stored}</strong></span>
          <span className="text-xs">Patterns: <strong>{reward.patterns_tracked}</strong></span>
          <span className="text-xs">Cumulative: <strong className={reward.cumulative_r > 0 ? 'text-green-500' : 'text-red-500'}>{reward.cumulative_r > 0 ? '+' : ''}{reward.cumulative_r?.toFixed(0)}R</strong></span>
        </div>
      )}

      <div className="botux-kpi-grid">
        <Card className="botux-panel-card">
          <CardContent className="space-y-1 py-1">
            <p className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Enabled bots</p>
            <p className="text-3xl font-bold tracking-tight tabular-nums">{enabled}</p>
            <p className="text-sm text-muted-foreground">Profiles ready to receive signals</p>
          </CardContent>
        </Card>
        <Card className="botux-panel-card">
          <CardContent className="space-y-1 py-1">
            <p className="text-xs uppercase tracking-[0.16em] text-muted-foreground">US Equities</p>
            <p className="text-3xl font-bold tracking-tight tabular-nums">{alpacaBots.length}</p>
            <p className="text-sm text-muted-foreground">Alpaca fleet members</p>
          </CardContent>
        </Card>
        <Card className="botux-panel-card">
          <CardContent className="space-y-1 py-1">
            <p className="text-xs uppercase tracking-[0.16em] text-muted-foreground">ASX Equities</p>
            <p className="text-3xl font-bold tracking-tight tabular-nums">{ibkrBots.length}</p>
            <p className="text-sm text-muted-foreground">IBKR fleet members</p>
          </CardContent>
        </Card>
        <Card className="botux-panel-card">
          <CardContent className="space-y-1 py-1">
            <p className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Reward feed</p>
            <p className="text-3xl font-bold tracking-tight tabular-nums">{reward?.total_trades_processed ?? 0}</p>
            <p className="text-sm text-muted-foreground">Trades processed by the learning layer</p>
          </CardContent>
        </Card>
      </div>

      {/* Core Fleet — original bots */}
      {(() => {
        const coreBots = alpacaBots.filter((p: any) => !p.bot_id?.startsWith('auto_'))
        return coreBots.length > 0 && (
          <div>
            <div className="flex items-center gap-3 mb-4">
              <div className="h-px flex-1 bg-border" />
              <span className="text-xs font-bold text-muted-foreground uppercase tracking-[0.2em]">Core Fleet — US Equities</span>
              <div className="h-px flex-1 bg-border" />
            </div>
            <div className="grid gap-5 md:grid-cols-2 lg:grid-cols-3">
              {coreBots.map((p: any) => <BotProfileCard key={p.bot_id} p={p} strats={strats} router={router} reward={reward} edge={edge} />)}
            </div>
          </div>
        )
      })()}

      {/* IBKR Fleet */}
      {(() => {
        const coreIbkr = ibkrBots.filter((p: any) => !p.bot_id?.startsWith('auto_'))
        return coreIbkr.length > 0 && (
          <div>
            <div className="flex items-center gap-3 mb-4">
              <div className="h-px flex-1 bg-border" />
              <span className="text-xs font-bold text-muted-foreground uppercase tracking-[0.2em]">Core Fleet — ASX Equities</span>
              <div className="h-px flex-1 bg-border" />
            </div>
            <div className="grid gap-5 md:grid-cols-2 lg:grid-cols-3">
              {coreIbkr.map((p: any) => <BotProfileCard key={p.bot_id} p={p} strats={strats} router={router} reward={reward} edge={edge} />)}
            </div>
          </div>
        )
      })()}

      {/* Test Bots — auto-spawned from Arena/Factory */}
      {(() => {
        const testBots = profiles.filter((p: any) => p.bot_id?.startsWith('auto_'))
        if (testBots.length === 0) return null

        // Compute fleet stats
        // PATCH-BOTFLEET-TRUTH-01: totals derived from CLOSED trades only.
        // total_trades historically included opens, which double-counted
        // anything still in flight. Wins/losses/win_rate are closed-only by
        // definition, so the "Trades" count must match the same scope.
        const totalClosed = testBots.reduce((a: number, p: any) => a + (p.performance?.closed_trades ?? ((p.performance?.wins || 0) + (p.performance?.losses || 0))), 0)
        const totalOpen   = testBots.reduce((a: number, p: any) => a + (p.performance?.open_trades || 0), 0)
        const totalWins = testBots.reduce((a: number, p: any) => a + (p.performance?.wins || 0), 0)
        const totalLosses = testBots.reduce((a: number, p: any) => a + (p.performance?.losses || 0), 0)
        const totalPnl = testBots.reduce((a: number, p: any) => a + (p.performance?.total_pnl || 0), 0)
        const fleetWr = (totalWins + totalLosses) > 0 ? (totalWins / (totalWins + totalLosses) * 100) : null
        // "Trading" = at least one CLOSED trade. Bots with only open positions
        // haven't produced a result yet and shouldn't inflate this count.
        const tradingBots = testBots.filter((p: any) => {
          const c = p.performance?.closed_trades ?? ((p.performance?.wins || 0) + (p.performance?.losses || 0))
          return c > 0
        }).length
        const demoted = testBots.filter((p: any) => p.lifecycle_state === 'DEMOTED').length
        const strong = testBots.filter((p: any) => p.lifecycle_state === 'STRONG_PAPER').length
        // Top/Bottom 3 by P&L. Dedupe by bot_id (defence in depth — should
        // already be unique because the parent profiles dict is keyed by
        // bot_id) and exclude bots with zero closed trades from the rank
        // since their pnl is meaningless without a realised result.
        const ranked = [...testBots].filter((p: any) => {
          const c = p.performance?.closed_trades ?? ((p.performance?.wins || 0) + (p.performance?.losses || 0))
          return c > 0
        })
        const seenIds = new Set<string>()
        const sorted = ranked
          .filter((p: any) => { if (seenIds.has(p.bot_id)) return false; seenIds.add(p.bot_id); return true })
          .sort((a, b) => (b.performance?.total_pnl || 0) - (a.performance?.total_pnl || 0))
        const top3 = sorted.slice(0, 3)
        const bottom3 = sorted.length > 3 ? sorted.slice(-3).reverse() : []

        return (
          <div>
            <div className="flex items-center gap-3 mb-4">
              <div className="h-px flex-1 bg-border" />
              <span className="text-xs font-bold text-muted-foreground uppercase tracking-[0.2em]">Test Bots — Auto-Spawned Paper</span>
              <span className="text-[10px] text-muted-foreground">({testBots.length} bots · $10k each)</span>
              <div className="h-px flex-1 bg-border" />
            </div>

            {/* Fleet Stats Leaderboard */}
            <div className="mb-5 rounded-xl border border-border bg-background p-4">
              <div className="grid grid-cols-2 md:grid-cols-7 gap-4 text-center">
                <div>
                  <div className="text-xl font-bold tabular-nums">{testBots.length}</div>
                  <p className="text-[10px] text-muted-foreground mt-0.5">Total Bots</p>
                </div>
                <div>
                  <div className="text-xl font-bold tabular-nums">{tradingBots}</div>
                  <p className="text-[10px] text-muted-foreground mt-0.5" title="Bots with at least one closed trade">With Results</p>
                </div>
                <div>
                  <div className="text-xl font-bold tabular-nums">{totalClosed}<span className="text-muted-foreground text-sm">{totalOpen > 0 ? ` · ${totalOpen}` : ''}</span></div>
                  <p className="text-[10px] text-muted-foreground mt-0.5">Closed{totalOpen > 0 ? ' · Open' : ''}</p>
                </div>
                <div>
                  <div className={`text-xl font-bold tabular-nums ${fleetWr != null ? (fleetWr >= 50 ? 'text-green-500' : 'text-red-500') : ''}`}>
                    {fleetWr != null ? `${fleetWr.toFixed(1)}%` : '—'}
                  </div>
                  <p className="text-[10px] text-muted-foreground mt-0.5">Fleet WR</p>
                </div>
                <div>
                  <div className={`text-xl font-bold tabular-nums ${totalPnl > 0 ? 'text-green-500' : totalPnl < 0 ? 'text-red-500' : ''}`}>
                    {totalPnl !== 0 ? `${totalPnl > 0 ? '+' : '-'}$${Math.abs(totalPnl).toFixed(0)}` : '—'}
                  </div>
                  <p className="text-[10px] text-muted-foreground mt-0.5">Fleet P&L</p>
                </div>
                <div>
                  <div className="text-xl font-bold tabular-nums text-emerald-400">{strong}</div>
                  <p className="text-[10px] text-muted-foreground mt-0.5">Strong</p>
                </div>
                <div>
                  <div className="text-xl font-bold tabular-nums text-red-400">{demoted}</div>
                  <p className="text-[10px] text-muted-foreground mt-0.5">Demoted</p>
                </div>
              </div>

              {/* Top/Bottom 3 mini-leaderboard */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4 pt-4 border-t border-border/40">
                <div>
                  <p className="text-[10px] text-muted-foreground uppercase tracking-wider mb-2">🏆 Top 3 by P&L</p>
                  <div className="space-y-1">
                    {top3.map((b: any) => {
                      const pnl = b.performance?.total_pnl || 0
                      const wr = b.performance?.win_rate
                      return (
                        <div key={b.bot_id} className="flex items-center justify-between text-xs">
                          <span className="font-medium">{b.display_name}</span>
                          <span className="text-muted-foreground">
                            <span className={`${wr != null && wr >= 50 ? 'text-green-500' : ''}`}>{wr != null ? `${wr.toFixed(0)}%` : '—'}</span>
                            <span className="mx-2">·</span>
                            <span className={`${pnl > 0 ? 'text-green-500' : pnl < 0 ? 'text-red-500' : ''}`}>
                              {pnl !== 0 ? `${pnl > 0 ? '+' : '-'}$${Math.abs(pnl).toFixed(2)}` : '—'}
                            </span>
                          </span>
                        </div>
                      )
                    })}
                  </div>
                </div>
                <div>
                  <p className="text-[10px] text-muted-foreground uppercase tracking-wider mb-2">⚠ Bottom 3 by P&L</p>
                  <div className="space-y-1">
                    {bottom3.map((b: any) => {
                      const pnl = b.performance?.total_pnl || 0
                      const wr = b.performance?.win_rate
                      return (
                        <div key={b.bot_id} className="flex items-center justify-between text-xs">
                          <span className="font-medium">{b.display_name}</span>
                          <span className="text-muted-foreground">
                            <span className={`${wr != null && wr >= 50 ? 'text-green-500' : 'text-red-500'}`}>{wr != null ? `${wr.toFixed(0)}%` : '—'}</span>
                            <span className="mx-2">·</span>
                            <span className={`${pnl > 0 ? 'text-green-500' : pnl < 0 ? 'text-red-500' : ''}`}>
                              {pnl !== 0 ? `${pnl > 0 ? '+' : '-'}$${Math.abs(pnl).toFixed(2)}` : '—'}
                            </span>
                          </span>
                        </div>
                      )
                    })}
                  </div>
                </div>
              </div>
            </div>

            <div className="grid gap-5 md:grid-cols-2 lg:grid-cols-3">
              {testBots.map((p: any) => <BotProfileCard key={p.bot_id} p={p} strats={strats} router={router} reward={reward} edge={edge} />)}
            </div>
          </div>
        )
      })()}
    </div>
  )
}

function BotProfileCard({ p, strats, router, reward, edge }: { p: any; strats: any; router: any; reward: any; edge: any }) {
  const alloc = p.allocation || {}
  const amt = alloc.usd || alloc.aud || 0
  const perf = p.performance || {}
  // PATCH-BOTFLEET-TRUTH-01: separate closed vs open. The API's
  // perf.total_trades historically included opens, which made "X trades"
  // misleading (e.g. "20 trades / 0/0 W/L" when all 20 were still open).
  // closed_trades is the source of truth for win rate, W/L, and the trade
  // count label. open_trades is shown separately when present.
  const closed = perf.closed_trades ?? ((perf.wins || 0) + (perf.losses || 0))
  const openT = perf.open_trades || 0
  const wins = perf.wins || 0
  const losses = perf.losses || 0
  const isEnabled = p.enabled
  const Icon = botIcons[p.bot_id] || Bot
  const color = botColors[p.bot_id] || '#0ABAB5'
  const lc = lcBadge(p.lifecycle_state)
  const ap = autopilotBadge(p.autopilot_state)
  const brokerSubmitAllowed = p.broker_submit_allowed !== false

  // Reward data — avg_r only. Win rate and pnl come from canonical
  // scorecard so they reconcile with rendered W/L. Previously displayWr
  // fell back to reward.bot_performance.win_rate which uses a different
  // sample/window than the canonical scorecard, producing rendered
  // contradictions like "67% win rate, 0/3 W/L".
  const botReward = reward?.bot_performance?.[p.bot_id]
  const avgR = botReward?.avg_r ?? perf.avg_r
  // Compute win rate locally from W/L so it can never disagree with the
  // rendered W/L. Returns null when there are no closed trades.
  const displayWr = (wins + losses) > 0 ? (wins / (wins + losses)) * 100 : null
  const pnl = perf.total_pnl

  // Edge health
  const botEdge = (edge as any)?.fleet?.[p.bot_id]

  return (
    <Card className={`botux-panel-card overflow-hidden cursor-pointer transition-colors hover:border-foreground/20 group ${!isEnabled ? 'opacity-50' : ''}`}
      onClick={() => router.push(`/dashboard/bots/${p.bot_id}`)}>

      {/* Color accent bar */}
      <div className="h-1" style={{ backgroundColor: color }} />

      <CardContent className="pt-5 pb-4">
        {/* Identity row */}
        <div className="flex items-start justify-between mb-4">
            <div className="flex items-center gap-3">
              <div className="rounded-xl border border-border bg-muted p-2.5">
                <Icon className="h-6 w-6" style={{ color }} />
              </div>
              <div>
                <h3 className="text-lg font-bold tracking-tight">{p.display_name || p.bot_id}</h3>
                <p className="font-mono text-[11px] text-muted-foreground leading-tight">{p.bot_id}</p>
                <p className="text-xs text-muted-foreground leading-tight">{p.mission?.slice(0, 50) || p.strategy_type}</p>
              </div>
            </div>
          <div className="flex flex-col items-end gap-1.5">
            <Badge variant="outline" className={`text-[10px] px-2 py-0.5 ${lc.bg} ${lc.text} border-0`}>
              {p.lifecycle_state}
            </Badge>
            <Badge variant="outline" className={`text-[9px] px-1.5 py-0 border-0 ${ap.className}`}>
              {ap.label}
            </Badge>
            <Badge variant="outline" className={`text-[9px] px-1.5 py-0 border-0 ${brokerSubmitAllowed ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400'}`}>
              {brokerSubmitAllowed ? 'BROKER SUBMIT ON' : 'BROKER SUBMIT OFF'}
            </Badge>
            {p.source_status && p.source_status !== 'merged' && (
              <Badge variant="outline" className={`text-[9px] px-1.5 py-0 border-0 ${
                p.source_status === 'json_only' ? 'bg-yellow-500/10 text-yellow-500' :
                p.source_status === 'db_only' ? 'bg-blue-500/10 text-blue-400' :
                p.source_status === 'conflict' ? 'bg-orange-500/10 text-orange-400' : ''
              }`}>
                {p.source_status === 'json_only' ? 'LOCAL' :
                 p.source_status === 'db_only' ? 'DB' :
                 p.source_status === 'conflict' ? 'CONFLICT' : p.source_status}
              </Badge>
            )}
            {isEnabled && (
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute h-full w-full rounded-full opacity-75 bg-green-500" />
                <span className="relative rounded-full h-2 w-2" style={{ backgroundColor: color }} />
              </span>
            )}
          </div>
        </div>

        {/* Strategy + Allocation strip */}
        <div className="flex items-center justify-between text-xs mb-3 pb-2 border-b border-border/40">
          <div className="flex items-center gap-2">
            <Badge variant="secondary" className="text-[10px] px-1.5 py-0">{p.horizon || 'Swing'}</Badge>
            <Badge variant="secondary" className="text-[10px] px-1.5 py-0">{p.broker?.toUpperCase()}</Badge>
            {p.runtime_status && (
              <Badge variant="secondary" className="text-[10px] px-1.5 py-0">{String(p.runtime_status).toUpperCase()}</Badge>
            )}
            {botEdge?.severity === 'HEALTHY' && (
              <Badge variant="secondary" className="text-[10px] px-1.5 py-0 bg-green-500/10 text-green-400">Edge OK</Badge>
            )}
          </div>
          <span className="font-medium tabular-nums">
            {amt > 0 ? `${alloc.currency === 'AUD' ? 'A' : ''}$${amt.toLocaleString()}` : '—'}
          </span>
        </div>

        {/* Test Bot Identity strip — only for auto-spawned bots */}
        {p.bot_id?.startsWith('auto_') && (() => {
          const fp = p.arena_params || {}
          const tpPct = fp.take_profit_pct ? (fp.take_profit_pct * 100).toFixed(1) : '?'
          const slPct = fp.stop_loss_pct ? (fp.stop_loss_pct * 100).toFixed(1) : '?'
          const rr = fp.take_profit_pct && fp.stop_loss_pct ? (fp.take_profit_pct / fp.stop_loss_pct).toFixed(1) : '?'
          const family = p.family || p.strategy_type || 'unknown'
          const parent = p.lineage?.parent_strategy || p.arena_source || 'unknown'
          return (
            <div className="mb-3 pb-3 border-b border-border/40 space-y-1.5">
              <div className="flex items-center justify-between text-[10px]">
                <div className="flex items-center gap-1.5 flex-wrap">
                  <Badge variant="outline" className="text-[9px] px-1 py-0 capitalize">{family}</Badge>
                  <span className="text-muted-foreground">TP +{tpPct}% / SL -{slPct}% (R:R {rr}:1)</span>
                </div>
              </div>
              <div className="text-[9px] text-muted-foreground/70 truncate">
                Parent: {parent.replace(/^(ext_|elite_|brain_)/, '').slice(0, 40)}
              </div>
            </div>
          )
        })()}

        {/* Performance metrics */}
        <div className="grid grid-cols-4 gap-3 text-center">
          <div>
            <div className={`text-lg font-bold tabular-nums ${displayWr != null ? (displayWr >= 55 ? 'text-green-500' : displayWr < 45 ? 'text-red-500' : '') : 'text-muted-foreground'}`}>
              {displayWr != null ? `${displayWr.toFixed(0)}%` : '—'}
            </div>
            <p className="text-[10px] text-muted-foreground mt-0.5">Win Rate</p>
          </div>
          <div>
            <div className="text-lg font-bold tabular-nums">
              <span className="text-green-500">{wins}</span>
              <span className="text-muted-foreground/50 mx-0.5">/</span>
              <span className="text-red-500">{losses}</span>
            </div>
            <p className="text-[10px] text-muted-foreground mt-0.5">W / L</p>
          </div>
          <div>
            <div className={`text-lg font-bold tabular-nums ${avgR != null ? (avgR > 0 ? 'text-green-500' : 'text-red-500') : 'text-muted-foreground'}`}>
              {avgR != null ? `${avgR > 0 ? '+' : ''}${avgR.toFixed(1)}` : '—'}
            </div>
            <p className="text-[10px] text-muted-foreground mt-0.5">Avg R</p>
          </div>
          <div>
            <div className={`text-lg font-bold tabular-nums ${pnl > 0 ? 'text-green-500' : pnl < 0 ? 'text-red-500' : 'text-muted-foreground'}`}>
              {pnl != null && pnl !== 0 ? `${pnl > 0 ? '+' : '-'}$${Math.abs(pnl).toFixed(2)}` : '—'}
            </div>
            <p className="text-[10px] text-muted-foreground mt-0.5">P&L $</p>
          </div>
        </div>

        {/* Trade progress bar — closed and open are accounted separately so
            "X closed" never silently absorbs open positions. */}
        {(closed + openT) > 0 && (
          <div className="mt-3">
            <div className="flex h-1.5 rounded-full overflow-hidden bg-muted">
              {wins > 0 && <div className="bg-green-500 transition-all" style={{ width: `${(wins / (closed + openT)) * 100}%` }} />}
              {losses > 0 && <div className="bg-red-500 transition-all" style={{ width: `${(losses / (closed + openT)) * 100}%` }} />}
              {openT > 0 && <div className="bg-blue-400 transition-all" style={{ width: `${(openT / (closed + openT)) * 100}%` }} />}
            </div>
            <div className="flex justify-between text-[10px] text-muted-foreground mt-1">
              <span>{closed} closed{openT > 0 ? ` · ${openT} open` : ''}</span>
              <span>{closed === 0 && openT > 0 ? 'no closed trades yet' : ''}</span>
            </div>
          </div>
        )}

        {/* Hermes review + lifecycle readiness — for auto bots only */}
        {p.bot_id?.startsWith('auto_') && p.hermes_review && (
          <div className="mt-3 pt-3 border-t border-border/40">
            <div className="flex items-center gap-1.5 mb-1">
              <span className="text-[9px] font-bold text-muted-foreground uppercase tracking-wider">Hermes</span>
              {(() => {
                const dec = p.hermes_review.decision || 'KEEP'
                const colors: Record<string, string> = {
                  PROMOTE: 'bg-emerald-500/15 text-emerald-400',
                  KEEP: 'bg-blue-500/15 text-blue-400',
                  TWEAK: 'bg-amber-500/15 text-amber-400',
                  DEMOTE: 'bg-red-500/15 text-red-400',
                }
                return <Badge variant="outline" className={`text-[9px] px-1.5 py-0 border-0 ${colors[dec] || 'bg-muted'}`}>{dec}</Badge>
              })()}
              {p.hermes_review.confidence != null && (
                <span className="text-[9px] text-muted-foreground">conf: {(p.hermes_review.confidence * 100).toFixed(0)}%</span>
              )}
            </div>
            <p className="text-[9px] text-muted-foreground/80 line-clamp-2">{p.hermes_review.reasoning}</p>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
