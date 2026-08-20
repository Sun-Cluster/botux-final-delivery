'use client'
import { useEffect, useState, useRef } from 'react'
import { getEcosystem, getSystemSubstrate, getSystemPipeline, getSystemLearning, getExecutorStatusFull } from '@/lib/api'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Zap, BarChart3, Target, Pickaxe, Copy, Bot, Shield, Eye, Radio, Newspaper, Telescope, Crown, Wrench, Brain, ArrowRightLeft, Scale, Activity, Users, ChevronRight, AlertTriangle, Database, GitBranch, Sliders, BookOpen, Gavel } from 'lucide-react'

const AVATARS: Record<string, any> = {
  zap: Zap, chart: BarChart3, target: Target, pickaxe: Pickaxe, copy: Copy,
  bolt: Zap, bot: Bot, shield: Shield, eye: Eye, radar: Radio,
  newspaper: Newspaper, telescope: Telescope, crown: Crown, wrench: Wrench,
  brain: Brain, bridge: ArrowRightLeft, scale: Scale, sword: Activity,
}

const MOOD_EMOJI: Record<string, string> = {
  commanding: '👑', analyzing: '🧠', building: '🔨', guarding: '🛡️', deliberating: '⚖️',
  focused: '🎯', vigilant: '👁️', alert: '⚡', scanning: '📡', reading: '📰',
  exploring: '🔭', converting: '🔄', watching: '👀', classifying: '📊',
  trading: '💰', practicing: '📋', observing: '👀', preparing: '🔧',
  sleeping: '😴', resting: '☕', reporting: '📊', working: '⚙️',
  waiting: '⏳', idle: '💤', retired: '🏖️',
}

const RANK_CONFIG: Record<string, { label: string; color: string; bg: string; border: string }> = {
  commander: { label: 'Commander', color: 'text-foreground', bg: 'bg-muted', border: 'border-border' },
  supervisor: { label: 'Supervisor', color: 'text-foreground', bg: 'bg-muted', border: 'border-border' },
  worker: { label: 'Worker', color: 'text-muted-foreground', bg: 'bg-muted', border: 'border-border' },
}

export default function AgentsPage() {
  const [data, setData] = useState<any>(null)
  const [substrate, setSubstrate] = useState<any>(null)
  const [pipeline, setPipeline] = useState<any>(null)
  const [learning, setLearning] = useState<any>(null)
  const [executor, setExecutor] = useState<any>(null)
  const [tick, setTick] = useState(0)
  const [prevFeed, setPrevFeed] = useState<string[]>([])
  const [newEvents, setNewEvents] = useState<Set<number>>(new Set())
  const feedRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const t = setInterval(() => setTick(v => v + 1), 1000)
    return () => clearInterval(t)
  }, [])

  useEffect(() => {
    async function load() {
      try {
        const d = await getEcosystem()
        if (!d) return
        const feed = (d.activity_feed || []).map((e: any) => e.ts + e.detail)
        const fresh = new Set<number>()
        feed.forEach((key: string, i: number) => { if (!prevFeed.includes(key)) fresh.add(i) })
        setNewEvents(fresh)
        setPrevFeed(feed)
        setData(d)
      } catch {}
    }
    async function loadSystem() {
      const [sub, pipe, learn, exec] = await Promise.allSettled([
        getSystemSubstrate(), getSystemPipeline(), getSystemLearning(), getExecutorStatusFull(),
      ]).then(r => r.map(x => x.status === 'fulfilled' ? x.value : null))
      if (sub) setSubstrate(sub)
      if (pipe) setPipeline(pipe)
      if (learn) setLearning(learn)
      if (exec) setExecutor(exec)
    }
    load(); loadSystem()
    const i = setInterval(load, 3000)
    const j = setInterval(loadSystem, 8000)
    return () => { clearInterval(i); clearInterval(j) }
  }, [])

  if (!data) return (
    <div className="h-full flex items-center justify-center">
      <div className="text-center">
        <Crown className="h-16 w-16 text-muted-foreground mx-auto mb-4 animate-pulse" />
        <p className="text-xl font-bold">Initializing Agent Command...</p>
        <p className="text-sm text-muted-foreground mt-2">Establishing hierarchy links</p>
      </div>
    </div>
  )

  const agents = data.agents || []
  const activity = data.activity_feed || []
  const hierarchy = data.hierarchy || {}
  const commanders = agents.filter((a: any) => a.rank === 'commander')
  const supervisors = agents.filter((a: any) => a.rank === 'supervisor')
  const workers = agents.filter((a: any) => a.rank === 'worker')

  return (
    <div className="botux-page max-h-screen overflow-y-auto">
      {/* Header */}
      <section className="botux-page__header">
        <div className="botux-page__heading">
          <div className="botux-page__eyebrow">System orchestration</div>
          <h1 className="botux-page__title">Agent Command Center</h1>
          <p className="botux-page__description">{data.agent_count} agents · {data.active_count} active · live system hierarchy and activity.</p>
        </div>
        <div className="flex items-center gap-4 rounded-[calc(var(--radius)*1.05)] border border-border bg-background px-4 py-3">
          <div className="text-center"><div className="text-lg font-bold tabular-nums">{hierarchy.commander}</div><div className="text-[9px] text-muted-foreground uppercase">Commander</div></div>
          <div className="text-center"><div className="text-lg font-bold tabular-nums">{hierarchy.supervisor}</div><div className="text-[9px] text-muted-foreground uppercase">Supervisors</div></div>
          <div className="text-center"><div className="text-lg font-bold tabular-nums">{hierarchy.worker}</div><div className="text-[9px] text-muted-foreground uppercase">Workers</div></div>
          <div className="h-8 w-px bg-border" />
          <div className="relative flex h-3 w-3">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
            <span className="relative inline-flex rounded-full h-3 w-3 bg-green-500" />
          </div>
        </div>
      </section>

      {/* ── EXECUTOR HEALTH BANNER ── */}
      {(() => {
        const lastRun = executor?.last_run
        const tradesToday = executor?.trades_today ?? 0
        const ageMin = lastRun ? Math.floor((Date.now() - new Date(lastRun).getTime()) / 60000) : 999
        const isHung = ageMin < 30 && tradesToday === 0 && (pipeline?.signals?.by_status?.pending || 0) > 50
        const recentAlerts = pipeline?.recent_alerts || []
        const hasFailureAlert = recentAlerts.some((a: string) => a.includes('PREDICTED FAILURE: executor'))

        if (!isHung && !hasFailureAlert) return null
        return (
          <div className="botux-callout botux-callout--danger">
            <div className="flex items-start gap-3">
              <AlertTriangle className="h-5 w-5 text-red-400 flex-shrink-0 mt-0.5" />
              <div className="flex-1">
                <p className="text-sm font-bold text-red-400">EXECUTOR HUNG — TRADE PIPELINE BLOCKED</p>
                <p className="text-xs text-muted-foreground mt-1 leading-relaxed">
                  Executor last ran {ageMin}m ago, trades today: {tradesToday}, pending signals: {pipeline?.signals?.by_status?.pending || 0}.
                  Guardian is reporting PREDICTED FAILURE on the executor. The cycle processes the first signal, deliberates with council, then stops silently before executing.
                  <br /><span className="text-red-500">No new trades will accumulate until this is fixed. Learning loop is starved.</span>
                </p>
              </div>
            </div>
          </div>
        )
      })()}

      {/* ── SYSTEM SUBSTRATE TRUTH (4 panels) ── */}
      <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4">
        {/* SUBSTRATE PANEL */}
        <Card className="botux-panel-card">
          <CardContent className="pt-4 pb-3">
            <div className="flex items-center justify-between mb-2">
              <span className="text-[11px] uppercase tracking-wider text-muted-foreground font-bold">Trade Substrate</span>
              <Database className="h-3.5 w-3.5 text-muted-foreground" />
            </div>
            <div className="text-2xl font-bold tabular-nums">{substrate?.trade_substrate?.clean_trades ?? '—'}</div>
            <p className="text-[10px] text-muted-foreground mt-1">
              clean trades · {substrate?.trade_substrate?.contaminated_quarantined ?? 0} quarantined
            </p>
            <div className="mt-2 pt-2 border-t border-border/30">
              <div className="flex items-center justify-between text-[10px]">
                <span className="text-muted-foreground">MFE quality</span>
                <span className={`font-bold tabular-nums ${(substrate?.trade_substrate?.excursion_quality_pct ?? 0) >= 30 ? 'text-green-500' : 'text-amber-400'}`}>
                  {substrate?.trade_substrate?.excursion_quality_pct ?? 0}%
                </span>
              </div>
              <p className="text-[9px] text-muted-foreground/70 mt-0.5">
                {substrate?.trade_substrate?.real_excursion_count ?? 0} of {substrate?.trade_substrate?.clean_trades ?? 0} trades w/ real excursion
              </p>
            </div>
          </CardContent>
        </Card>

        {/* PIPELINE PANEL */}
        <Card className="botux-panel-card">
          <CardContent className="pt-4 pb-3">
            <div className="flex items-center justify-between mb-2">
              <span className="text-[11px] uppercase tracking-wider text-muted-foreground font-bold">Signal Pipeline</span>
              <GitBranch className="h-3.5 w-3.5 text-muted-foreground" />
            </div>
            <div className="text-2xl font-bold tabular-nums">{pipeline?.signals?.by_status?.pending ?? '—'}</div>
            <p className="text-[10px] text-muted-foreground mt-1">pending signals</p>
            <div className="mt-2 pt-2 border-t border-border/30 space-y-1">
              {pipeline?.signals?.by_status && Object.entries(pipeline.signals.by_status).map(([k, v]: any) => (
                <div key={k} className="flex items-center justify-between text-[10px]">
                  <span className="text-muted-foreground">{k}</span>
                  <span className="tabular-nums font-medium">{v}</span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        {/* COUNCIL PANEL */}
        <Card className="botux-panel-card">
          <CardContent className="pt-4 pb-3">
            <div className="flex items-center justify-between mb-2">
              <span className="text-[11px] uppercase tracking-wider text-muted-foreground font-bold">Council</span>
              <Gavel className="h-3.5 w-3.5 text-muted-foreground" />
            </div>
            <div className="text-2xl font-bold tabular-nums">
              {pipeline?.council?.stats?.total ?? '—'}
            </div>
            <p className="text-[10px] text-muted-foreground mt-1">lifetime decisions</p>
            <div className="mt-2 pt-2 border-t border-border/30 space-y-1">
              <div className="flex items-center justify-between text-[10px]">
                <span className="text-green-500">approved</span>
                <span className="tabular-nums font-medium text-green-500">{pipeline?.council?.stats?.approved ?? '—'}</span>
              </div>
              <div className="flex items-center justify-between text-[10px]">
                <span className="text-red-400">rejected</span>
                <span className="tabular-nums font-medium text-red-400">{pipeline?.council?.stats?.rejected ?? '—'}</span>
              </div>
              <div className="flex items-center justify-between text-[10px]">
                <span className="text-amber-400">vetoed</span>
                <span className="tabular-nums font-medium text-amber-400">{pipeline?.council?.stats?.vetoed ?? '—'}</span>
              </div>
              {pipeline?.council?.last_decision_ts && (
                <p className="text-[9px] text-muted-foreground/70 pt-1">
                  last: {pipeline.council.last_decision_ts.slice(0, 10)}
                </p>
              )}
            </div>
          </CardContent>
        </Card>

        {/* LEARNING PANEL */}
        <Card className="botux-panel-card">
          <CardContent className="pt-4 pb-3">
            <div className="flex items-center justify-between mb-2">
              <span className="text-[11px] uppercase tracking-wider text-muted-foreground font-bold">Learning</span>
              <Brain className="h-3.5 w-3.5 text-muted-foreground" />
            </div>
            <div className="text-2xl font-bold tabular-nums">
              {learning?.reward?.cumulative_r != null ? `${learning.reward.cumulative_r > 0 ? '+' : ''}${learning.reward.cumulative_r}R` : '—'}
            </div>
            <p className="text-[10px] text-muted-foreground mt-1">
              {learning?.reward?.total_trades_processed ?? 0} reward signals
            </p>
            <div className="mt-2 pt-2 border-t border-border/30 grid grid-cols-2 gap-x-2 gap-y-1">
              <div className="flex items-center justify-between text-[10px]">
                <span className="text-muted-foreground">lessons</span>
                <span className="tabular-nums font-medium">{learning?.lessons?.count ?? 0}</span>
              </div>
              <div className="flex items-center justify-between text-[10px]">
                <span className="text-muted-foreground">debates</span>
                <span className="tabular-nums font-medium">{learning?.debates?.count ?? 0}</span>
              </div>
              <div className="flex items-center justify-between text-[10px]">
                <span className="text-muted-foreground">validations</span>
                <span className="tabular-nums font-medium">{learning?.validations?.count ?? 0}</span>
              </div>
              <div className="flex items-center justify-between text-[10px]">
                <span className="text-muted-foreground">tunings</span>
                <span className="tabular-nums font-medium">{learning?.tuning?.count ?? 0}</span>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* ── SOURCE TAXONOMY ── */}
      {substrate?.taxonomy?.sources && (
        <Card className="botux-panel-card">
          <CardContent className="pt-4 pb-3">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <Radio className="h-4 w-4 text-muted-foreground" />
                <span className="text-sm font-bold">Source Taxonomy</span>
              </div>
              <span className="text-[10px] text-muted-foreground">Trade-driving vs context-only</span>
            </div>
            <div className="grid gap-2 md:grid-cols-2 lg:grid-cols-3">
              {Object.entries(substrate.taxonomy.sources).map(([name, info]: any) => {
                const isDriver = info.class === 'TRADE_DRIVER'
                const liveCount = pipeline?.signals?.by_source?.[name] ?? 0
                return (
                  <div key={name} className="rounded-lg border border-border bg-background p-2.5">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs font-bold">{name}</span>
                      <Badge variant="outline" className="text-[8px]">
                        {isDriver ? 'TRADE DRIVER' : 'CONTEXT'}
                      </Badge>
                    </div>
                    <p className="text-[10px] text-muted-foreground">{liveCount} live signals · scored: {info.has_score ? 'yes' : 'no'}</p>
                  </div>
                )
              })}
            </div>
          </CardContent>
        </Card>
      )}

      {/* ── RECENT LEARNING ARTIFACTS ── */}
      <div className="grid gap-3 md:grid-cols-2">
        {/* LATEST LESSON */}
        <Card className="botux-panel-card">
          <CardContent className="pt-4 pb-3">
            <div className="flex items-center gap-2 mb-2">
              <BookOpen className="h-4 w-4 text-muted-foreground" />
              <span className="text-sm font-bold">Latest Hermes Lesson</span>
            </div>
            {learning?.lessons?.latest ? (
              <>
                <p className="text-[11px] text-muted-foreground mb-1.5">
                  {learning.lessons.latest.bot_id} · decision: <span className={`font-bold ${learning.lessons.latest.was_correct ? 'text-green-500' : 'text-red-400'}`}>
                    {learning.lessons.latest.decision} {learning.lessons.latest.was_correct ? '✓' : '✗'}
                  </span>
                </p>
                <p className="text-xs leading-relaxed">{learning.lessons.latest.lesson_text}</p>
              </>
            ) : (
              <p className="text-xs text-muted-foreground italic">No lessons judged yet</p>
            )}
          </CardContent>
        </Card>

        {/* LATEST DEBATE */}
        <Card className="botux-panel-card">
          <CardContent className="pt-4 pb-3">
            <div className="flex items-center gap-2 mb-2">
              <Scale className="h-4 w-4 text-muted-foreground" />
              <span className="text-sm font-bold">Latest Bull/Bear Debate</span>
            </div>
            {learning?.debates?.latest ? (
              <>
                <p className="text-[11px] text-muted-foreground mb-1.5">
                  {learning.debates.latest.display_name} · {learning.debates.latest.proposed_action} → <span className="font-bold text-foreground">{learning.debates.latest.final_decision}</span> ({Math.round((learning.debates.latest.confidence || 0) * 100)}%)
                </p>
                <p className="text-xs leading-relaxed line-clamp-3">{learning.debates.latest.synthesis_reasoning}</p>
              </>
            ) : (
              <p className="text-xs text-muted-foreground italic">No debates yet</p>
            )}
          </CardContent>
        </Card>

        {/* LATEST VALIDATION */}
        <Card className="botux-panel-card">
          <CardContent className="pt-4 pb-3">
            <div className="flex items-center gap-2 mb-2">
              <Shield className="h-4 w-4 text-muted-foreground" />
              <span className="text-sm font-bold">Latest Strategy Validation</span>
            </div>
            {learning?.validations?.latest ? (
              <>
                <p className="text-[11px] text-muted-foreground mb-1.5">
                  {learning.validations.latest.strategy_name} · <span className={`font-bold ${learning.validations.latest.passed ? 'text-green-500' : 'text-red-400'}`}>
                    {learning.validations.latest.passed ? 'PASSED' : 'REJECTED'}
                  </span> (score {learning.validations.latest.score})
                </p>
                <p className="text-xs leading-relaxed line-clamp-3">{learning.validations.latest.reasoning}</p>
              </>
            ) : (
              <p className="text-xs text-muted-foreground italic">No validations yet</p>
            )}
          </CardContent>
        </Card>

        {/* LATEST TUNING */}
        <Card className="botux-panel-card">
          <CardContent className="pt-4 pb-3">
            <div className="flex items-center gap-2 mb-2">
              <Sliders className="h-4 w-4 text-muted-foreground" />
              <span className="text-sm font-bold">Latest Adaptive Tuning</span>
            </div>
            {learning?.tuning?.latest ? (
              <>
                <p className="text-[11px] text-muted-foreground mb-1.5">
                  {learning.tuning.latest.bot_id} · TP {(learning.tuning.latest.old_tp * 100).toFixed(2)}% → <span className="font-bold text-foreground">{(learning.tuning.latest.new_tp * 100).toFixed(2)}%</span>
                </p>
                <p className="text-xs leading-relaxed line-clamp-3">{learning.tuning.latest.reason}</p>
              </>
            ) : (
              <p className="text-xs text-muted-foreground italic">No tuning yet</p>
            )}
          </CardContent>
        </Card>
      </div>

      {/* COMMANDER TIER */}
      <div>
        <div className="botux-tier-heading">
          <Crown className="h-4 w-4 text-muted-foreground" />
          <h2 className="botux-tier-heading__label">Command</h2>
          <div className="botux-tier-heading__line" />
        </div>
        <div className="grid gap-3 grid-cols-1">
          {commanders.map((a: any) => <AgentTile key={a.id} agent={a} tick={tick} />)}
        </div>
      </div>

      {/* SUPERVISOR TIER */}
      <div>
        <div className="botux-tier-heading">
          <Shield className="h-4 w-4 text-muted-foreground" />
          <h2 className="botux-tier-heading__label">Supervisors</h2>
          <div className="botux-tier-heading__line" />
        </div>
        <div className="grid gap-3 grid-cols-2 lg:grid-cols-4">
          {supervisors.map((a: any) => <AgentTile key={a.id} agent={a} tick={tick} />)}
        </div>
      </div>

      {/* WORKER TIER */}
      <div>
        <div className="botux-tier-heading">
          <Users className="h-4 w-4 text-muted-foreground" />
          <h2 className="botux-tier-heading__label">Workers</h2>
          <div className="botux-tier-heading__line" />
        </div>
        <div className="grid gap-2 grid-cols-2 md:grid-cols-3 lg:grid-cols-5">
          {workers.map((a: any) => <AgentTile key={a.id} agent={a} tick={tick} compact />)}
        </div>
      </div>

      {/* LIVE FEED */}
      <div className="botux-feed">
        <div className="botux-feed__header">
          <div className="flex items-center gap-2">
            <Activity className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm font-semibold">Live Activity Feed</span>
          </div>
          <div className="flex items-center gap-3">
            <span className="relative flex h-2.5 w-2.5"><span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" /><span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-green-500" /></span>
            <span className="text-[10px] text-muted-foreground tabular-nums">{activity.length} events</span>
          </div>
        </div>
        <div ref={feedRef} className="botux-feed__body">
          {activity.length === 0 ? (
            <div className="py-12 text-center text-muted-foreground">
              <Activity className="h-10 w-10 mx-auto mb-3 animate-spin text-muted-foreground/20" />
              <p className="text-sm">Waiting for agent activity...</p>
            </div>
          ) : activity.map((e: any, i: number) => {
            const isNew = newEvents.has(i)
            const isWarn = e.level === 'WARN'
            const isError = e.level === 'ERROR'
            const time = e.ts ? new Date(e.ts).toLocaleTimeString('en-AU', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }) : '??:??:??'
            const agentColors: Record<string, string> = {
              executor: 'text-foreground', risk_engine: 'text-red-500', signal_engine: 'text-foreground',
              position_monitor: 'text-muted-foreground', newsfeed_intel: 'text-muted-foreground', scout: 'text-muted-foreground',
              system: 'text-foreground',
            }
            const rowState = isNew ? 'is-new' : isWarn ? 'is-warn' : isError ? 'is-error' : ''

            return (
              <div key={i} className={`botux-feed__row ${rowState}`}>
                <div className="botux-feed__cell"><span className="font-mono text-[10px] text-muted-foreground/70 tabular-nums">{time}</span></div>
                <div className="botux-feed__cell"><span className={`font-mono text-[10px] font-bold ${agentColors[e.agent] || 'text-muted-foreground'}`}>{e.agent}</span></div>
                <div className="botux-feed__cell"><span className="botux-feed__badge">{e.type}</span></div>
                <div className="botux-feed__cell min-w-0"><span className="text-[10px] text-foreground/85">{isWarn ? 'WARN: ' : isError ? 'ERROR: ' : ''}{e.detail}</span></div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

function AgentTile({ agent, tick, compact }: { agent: any; tick: number; compact?: boolean }) {
  const Icon = AVATARS[agent.avatar] || Bot
  const rank = RANK_CONFIG[agent.rank] || RANK_CONFIG.worker
  const isActive = agent.status === 'active'
  const emoji = MOOD_EMOJI[agent.mood] || '🤖'
  const bobY = isActive ? Math.sin((tick + agent.id.charCodeAt(0)) * 0.7) * 1.5 : 0

  if (compact) {
    return (
      <div className={`relative rounded-lg border ${rank.border} bg-background p-2.5 transition-all duration-500 ${isActive ? '' : 'opacity-50'}`}
        style={{ transform: `translateY(${bobY}px)` }}>
        {isActive && <span className="absolute top-1.5 right-1.5 flex h-2 w-2"><span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" /><span className="relative inline-flex rounded-full h-2 w-2 bg-green-500" /></span>}
        <div className="flex items-center gap-2 mb-1.5">
          <Icon className={`h-4 w-4 flex-shrink-0 ${isActive ? 'text-foreground' : 'text-muted-foreground'}`} />
          <p className="text-[10px] font-semibold truncate">{agent.name}</p>
        </div>
        <div className="text-[9px] text-muted-foreground truncate">{emoji} {agent.mood}</div>
        {isActive && <div className="mt-1.5 h-0.5 rounded-full bg-muted overflow-hidden"><div className="h-full bg-foreground rounded-full animate-progress" /></div>}
      </div>
    )
  }

  return (
    <div className={`relative rounded-xl border ${rank.border} bg-background overflow-hidden transition-all duration-500 ${isActive ? '' : 'opacity-60'}`}
      style={{ transform: `translateY(${bobY}px)` }}>
      {isActive && <span className="absolute top-3 right-3 flex h-2.5 w-2.5"><span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" /><span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-green-500" /></span>}

      <div className="p-4">
        <div className="flex items-center gap-3 mb-3">
          <div className={`w-11 h-11 rounded-lg flex items-center justify-center ${rank.bg}`}>
            <Icon className={`h-5 w-5 ${rank.color}`} />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-bold truncate">{agent.name}</p>
            <p className="text-[10px] text-muted-foreground">{agent.department} · {agent.schedule}</p>
          </div>
        </div>

        {/* Rank badge */}
        <div className="mb-2">
          <span className={`text-[9px] font-bold uppercase tracking-wider ${rank.color}`}>{rank.label}</span>
        </div>

        {/* Mood */}
        <div className="bg-muted rounded-md px-3 py-1.5 mb-2">
          <p className="text-[11px]">{emoji} {agent.mood}</p>
        </div>

        {/* Current task */}
        <p className="text-[10px] text-muted-foreground leading-relaxed mb-2">{agent.current_task}</p>

        {/* Status bar */}
        <div className="flex items-center justify-between">
          <Badge variant={isActive ? 'default' : 'secondary'} className="text-[9px]">{agent.status}</Badge>
          {agent.last_active && <span className="text-[9px] text-muted-foreground">{agent.last_active}</span>}
          {agent.lifecycle && <Badge variant="outline" className="text-[8px]">{agent.lifecycle}</Badge>}
        </div>

        {/* Activity bar */}
        {isActive && <div className="mt-3 h-1 rounded-full bg-muted overflow-hidden"><div className="h-full rounded-full animate-progress bg-foreground" /></div>}
      </div>
    </div>
  )
}
