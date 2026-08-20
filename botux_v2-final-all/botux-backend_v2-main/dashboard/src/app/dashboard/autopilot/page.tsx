'use client'

import { useEffect, useMemo, useState } from 'react'
import {
  getAutopilotDecisions,
  getAutopilotPolicy,
  getAutopilotRunDetail,
  getAutopilotRuns,
  getAutopilotStatus,
  updateAutopilotPolicy,
  type AutopilotDecision,
  type AutopilotPolicy,
  type AutopilotRun,
} from '@/lib/api'
import { fmtDateTime } from '@/lib/format'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ClipboardList, Plane, RefreshCw, Save, TimerReset, Waypoints } from 'lucide-react'

type StatusPayload = {
  policy: AutopilotPolicy
  latest_run: AutopilotRun | null
  latest_recommendation_counts: Record<string, number>
  top_reason_codes: Array<{ reason_code: string; count: number }>
  autopilot_job: Record<string, unknown> | null
  generated_at: string
}

type FormState = {
  enabled: boolean
  mode: string
  evaluation_window_days: string
  shadow_min_closed_trades: string
  shadow_max_win_rate: string
  shadow_max_pnl_pct: string
  reactivate_interval_seconds: string
  reactivate_min_closed_trades: string
  reactivate_min_win_rate: string
  reactivate_min_pnl_pct: string
}

const EMPTY_FORM: FormState = {
  enabled: true,
  mode: 'observe',
  evaluation_window_days: '7',
  shadow_min_closed_trades: '4',
  shadow_max_win_rate: '45',
  shadow_max_pnl_pct: '-2',
  reactivate_interval_seconds: '86400',
  reactivate_min_closed_trades: '4',
  reactivate_min_win_rate: '55',
  reactivate_min_pnl_pct: '1',
}

export default function AutopilotPage() {
  const [status, setStatus] = useState<StatusPayload | null>(null)
  const [runs, setRuns] = useState<AutopilotRun[]>([])
  const [decisions, setDecisions] = useState<AutopilotDecision[]>([])
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null)
  const [selectedRunDecisions, setSelectedRunDecisions] = useState<AutopilotDecision[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saveMessage, setSaveMessage] = useState<string | null>(null)
  const [filters, setFilters] = useState<{ bot_id: string; state: string }>({ bot_id: '', state: '' })
  const [form, setForm] = useState<FormState>(EMPTY_FORM)

  async function loadAll() {
    const statusRes = await getAutopilotStatus()
    const runsRes = await getAutopilotRuns(20)
    const decisionsRes = await getAutopilotDecisions({ limit: 80 })
    const policyRes = await getAutopilotPolicy()

    if (statusRes) setStatus(statusRes as StatusPayload)
    if (runsRes?.items) setRuns(runsRes.items)
    if (decisionsRes?.items) setDecisions(decisionsRes.items)

    const policy = (policyRes?.policy || statusRes?.policy || null) as AutopilotPolicy | null
    if (policy) {
      setForm({
        enabled: Boolean(policy.enabled ?? true),
        mode: String(policy.mode || 'observe'),
        evaluation_window_days: String(policy.evaluation_window_days ?? 7),
        shadow_min_closed_trades: String(policy.shadow_min_closed_trades ?? 4),
        shadow_max_win_rate: String(policy.shadow_max_win_rate ?? 45),
        shadow_max_pnl_pct: String(policy.shadow_max_pnl_pct ?? -2),
        reactivate_interval_seconds: String(policy.reactivate_interval_seconds ?? 86400),
        reactivate_min_closed_trades: String(policy.reactivate_min_closed_trades ?? 4),
        reactivate_min_win_rate: String(policy.reactivate_min_win_rate ?? 55),
        reactivate_min_pnl_pct: String(policy.reactivate_min_pnl_pct ?? 1),
      })
    }
  }

  useEffect(() => {
    let mounted = true
    async function initialLoad() {
      setLoading(true)
      await loadAll()
      if (mounted) setLoading(false)
    }
    initialLoad()
    const timer = setInterval(loadAll, 8000)
    return () => {
      mounted = false
      clearInterval(timer)
    }
  }, [])

  useEffect(() => {
    async function loadRunDetail() {
      if (selectedRunId == null) {
        setSelectedRunDecisions([])
        return
      }
      const payload = await getAutopilotRunDetail(selectedRunId)
      setSelectedRunDecisions(payload?.decisions || [])
    }
    loadRunDetail()
  }, [selectedRunId])

  async function savePolicy() {
    setSaving(true)
    setSaveMessage(null)
    const patch: Partial<AutopilotPolicy> = {
      enabled: form.enabled,
      mode: form.mode,
      evaluation_window_days: toNumber(form.evaluation_window_days, 7),
      shadow_min_closed_trades: toNumber(form.shadow_min_closed_trades, 4),
      shadow_max_win_rate: toNumber(form.shadow_max_win_rate, 45),
      shadow_max_pnl_pct: toNumber(form.shadow_max_pnl_pct, -2),
      reactivate_interval_seconds: toNumber(form.reactivate_interval_seconds, 86400),
      reactivate_min_closed_trades: toNumber(form.reactivate_min_closed_trades, 4),
      reactivate_min_win_rate: toNumber(form.reactivate_min_win_rate, 55),
      reactivate_min_pnl_pct: toNumber(form.reactivate_min_pnl_pct, 1),
    }
    const updated = await updateAutopilotPolicy(patch)
    if (!updated?.policy) {
      setSaveMessage('Failed to save policy.')
      setSaving(false)
      return
    }
    setSaveMessage(`Policy saved at ${fmtDateTime(updated.updated_at)}`)
    await loadAll()
    setSaving(false)
  }

  const filteredDecisions = useMemo(() => {
    return decisions.filter((row) => {
      if (filters.bot_id && row.bot_id !== filters.bot_id.toLowerCase()) return false
      if (filters.state && row.recommended_state !== filters.state.toLowerCase()) return false
      return true
    })
  }, [decisions, filters])

  const botOptions = useMemo(() => {
    return Array.from(new Set(decisions.map((row) => row.bot_id))).sort()
  }, [decisions])

  if (loading) {
    return (
      <div className="botux-page">
        <div className="h-8 bg-muted animate-pulse rounded w-72" />
        <div className="botux-kpi-grid">
          {[...Array(4)].map((_, i) => <div key={i} className="h-28 bg-muted animate-pulse rounded-lg" />)}
        </div>
        <div className="h-56 bg-muted animate-pulse rounded-lg" />
      </div>
    )
  }

  const latestRun = status?.latest_run || null
  const counts = status?.latest_recommendation_counts || {}

  return (
    <div className="botux-page">
      <section className="botux-page__header">
        <div className="botux-page__heading">
          <div className="botux-page__eyebrow">
            <Plane className="h-4 w-4" />
            Fleet automation
          </div>
          <h1 className="botux-page__title">Fleet Autopilot</h1>
          <p className="botux-page__description">Bot-level shadow gate and recovery policy based on realized P&amp;L and win rate.</p>
        </div>
        <Button variant="outline" size="sm" disabled={saving} onClick={loadAll}>
          <RefreshCw className="h-4 w-4 mr-2" />
          Refresh
        </Button>
      </section>

      <div className="botux-kpi-grid">
        <StatCard label="Mode" value={String(status?.policy?.mode || 'observe').toUpperCase()} />
        <StatCard label="Enabled" value={status?.policy?.enabled ? 'YES' : 'NO'} />
        <StatCard label="Latest Run" value={latestRun ? `#${latestRun.id}` : '—'} sub={latestRun?.status || 'no run'} />
        <StatCard
          label="Scheduler"
          value={status?.autopilot_job ? 'ACTIVE' : 'N/A'}
          sub={status?.autopilot_job ? `${String(status.autopilot_job.interval_seconds || '—')}s interval` : 'job not found'}
        />
      </div>

      <Card className="botux-panel-card">
        <CardHeader>
          <CardTitle>Policy Controls</CardTitle>
          <CardDescription>Shadow/recover thresholds at bot level</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-5">
            <SelectField
              label="Enabled"
              value={form.enabled ? 'true' : 'false'}
              onChange={(value) => setForm((prev) => ({ ...prev, enabled: value === 'true' }))}
              options={['true', 'false']}
            />
            <SelectField
              label="Mode"
              value={form.mode}
              onChange={(value) => setForm((prev) => ({ ...prev, mode: value }))}
              options={['observe', 'recommend', 'constrained_apply']}
            />
            <NumberField label="Eval Window (days)" value={form.evaluation_window_days} onChange={(value) => setForm((prev) => ({ ...prev, evaluation_window_days: value }))} />
            <NumberField label="Shadow Min Trades" value={form.shadow_min_closed_trades} onChange={(value) => setForm((prev) => ({ ...prev, shadow_min_closed_trades: value }))} />
            <NumberField label="Shadow Max Winrate" value={form.shadow_max_win_rate} onChange={(value) => setForm((prev) => ({ ...prev, shadow_max_win_rate: value }))} />
            <NumberField label="Shadow Max P&L %" value={form.shadow_max_pnl_pct} onChange={(value) => setForm((prev) => ({ ...prev, shadow_max_pnl_pct: value }))} />
            <NumberField label="Reactivate Interval (s)" value={form.reactivate_interval_seconds} onChange={(value) => setForm((prev) => ({ ...prev, reactivate_interval_seconds: value }))} />
            <NumberField label="Reactivate Min Trades" value={form.reactivate_min_closed_trades} onChange={(value) => setForm((prev) => ({ ...prev, reactivate_min_closed_trades: value }))} />
            <NumberField label="Reactivate Min Winrate" value={form.reactivate_min_win_rate} onChange={(value) => setForm((prev) => ({ ...prev, reactivate_min_win_rate: value }))} />
            <NumberField label="Reactivate Min P&L %" value={form.reactivate_min_pnl_pct} onChange={(value) => setForm((prev) => ({ ...prev, reactivate_min_pnl_pct: value }))} />
          </div>

          <div className="flex items-center gap-3">
            <Button disabled={saving} onClick={savePolicy}>
              <Save className="h-4 w-4 mr-2" />
              {saving ? 'Saving...' : 'Save Policy'}
            </Button>
            {saveMessage && <p className="text-xs text-muted-foreground">{saveMessage}</p>}
          </div>
        </CardContent>
      </Card>

      <Tabs defaultValue="overview" className="space-y-4">
        <TabsList className="grid w-full grid-cols-3 md:w-fit md:min-w-[28rem]">
          <TabsTrigger value="overview">
            <Waypoints className="h-4 w-4" />
            Overview
          </TabsTrigger>
          <TabsTrigger value="decisions">
            <ClipboardList className="h-4 w-4" />
            Decisions
          </TabsTrigger>
          <TabsTrigger value="runs">
            <TimerReset className="h-4 w-4" />
            Runs
          </TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="botux-tab-panel space-y-4">
          <Card className="botux-panel-card">
            <CardHeader>
              <CardTitle>Latest Recommendation Mix</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-3 md:grid-cols-2">
              <CountBadge label="active" value={counts.active || 0} />
              <CountBadge label="shadow" value={counts.shadow || 0} />
            </CardContent>
          </Card>

          <Card className="botux-panel-card">
            <CardHeader>
              <CardTitle>Top Reason Codes</CardTitle>
              <CardDescription>Most common reasons in latest cycle</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="flex flex-wrap gap-2">
                {(status?.top_reason_codes || []).length === 0 && (
                  <span className="text-sm text-muted-foreground">No reason codes yet.</span>
                )}
                {(status?.top_reason_codes || []).map((row) => (
                  <Badge key={row.reason_code} variant="outline" className="text-xs">
                    {row.reason_code} · {row.count}
                  </Badge>
                ))}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="decisions" className="botux-tab-panel space-y-4">
          <Card className="botux-panel-card">
            <CardHeader>
              <CardTitle>Decision Filters</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-3 md:grid-cols-3">
              <SelectField
                label="Bot"
                value={filters.bot_id}
                onChange={(value) => setFilters((prev) => ({ ...prev, bot_id: value }))}
                options={['', ...botOptions]}
              />
              <SelectField
                label="State"
                value={filters.state}
                onChange={(value) => setFilters((prev) => ({ ...prev, state: value }))}
                options={['', 'active', 'shadow']}
              />
              <div className="flex items-end">
                <Button variant="outline" onClick={() => setFilters({ bot_id: '', state: '' })}>Reset Filters</Button>
              </div>
            </CardContent>
          </Card>

          <Card className="botux-table-card">
            <CardHeader>
              <CardTitle>Decisions</CardTitle>
              <CardDescription>{filteredDecisions.length} records</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="botux-data-table">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-muted-foreground">
                      <th className="text-left py-2 pr-4">Bot</th>
                      <th className="text-left py-2 pr-4">Transition</th>
                      <th className="text-left py-2 pr-4">Closed</th>
                      <th className="text-left py-2 pr-4">Winrate</th>
                      <th className="text-left py-2 pr-4">P&amp;L %</th>
                      <th className="text-left py-2 pr-4">Reasons</th>
                      <th className="text-left py-2 pr-4">Applied</th>
                      <th className="text-left py-2">Created</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredDecisions.map((row) => {
                      const metrics = decisionMetrics(row)
                      return (
                        <tr key={row.id}>
                          <td className="py-2 pr-4 font-medium">{row.bot_id}</td>
                          <td className="py-2 pr-4">
                            <span className="text-muted-foreground">{row.previous_state}</span>
                            <span className="mx-2">→</span>
                            <span>{row.recommended_state}</span>
                          </td>
                          <td className="py-2 pr-4 tabular-nums">{valueOrDash(metrics.closed_trades)}</td>
                          <td className="py-2 pr-4 tabular-nums">{formatNumber(metrics.win_rate, 1)}</td>
                          <td className="py-2 pr-4 tabular-nums">{formatNumber(metrics.pnl_pct_total, 2)}</td>
                          <td className="py-2 pr-4">
                            <div className="flex flex-wrap gap-1">
                              {(row.reason_codes || []).map((code) => (
                                <Badge key={code} variant="outline" className="text-[10px]">{code}</Badge>
                              ))}
                            </div>
                          </td>
                          <td className="py-2 pr-4">{row.applied ? <Badge>yes</Badge> : <Badge variant="outline">no</Badge>}</td>
                          <td className="py-2 text-muted-foreground">{fmtDateTime(row.created_at)}</td>
                        </tr>
                      )
                    })}
                    {filteredDecisions.length === 0 && (
                      <tr>
                        <td colSpan={8} className="py-6 text-center text-muted-foreground">No decisions found.</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="runs" className="botux-tab-panel space-y-4">
          <Card className="botux-table-card">
            <CardHeader>
              <CardTitle>Runs</CardTitle>
              <CardDescription>Recent autopilot cycles persisted by scheduler</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="botux-data-table">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-muted-foreground">
                      <th className="text-left py-2 pr-4">Run</th>
                      <th className="text-left py-2 pr-4">Status</th>
                      <th className="text-left py-2 pr-4">Mode</th>
                      <th className="text-left py-2 pr-4">Bots</th>
                      <th className="text-left py-2 pr-4">Started</th>
                      <th className="text-left py-2 pr-4">Completed</th>
                      <th className="text-left py-2">Detail</th>
                    </tr>
                  </thead>
                  <tbody>
                    {runs.map((run) => (
                      <tr key={run.id}>
                        <td className="py-2 pr-4 font-medium">#{run.id}</td>
                        <td className="py-2 pr-4">
                          <Badge variant={run.status === 'completed' ? 'default' : run.status === 'failed' ? 'destructive' : 'outline'}>
                            {run.status}
                          </Badge>
                        </td>
                        <td className="py-2 pr-4">{run.mode}</td>
                        <td className="py-2 pr-4 tabular-nums">{run.bots_count}</td>
                        <td className="py-2 pr-4 text-muted-foreground">{fmtDateTime(run.started_at)}</td>
                        <td className="py-2 pr-4 text-muted-foreground">{fmtDateTime(run.completed_at)}</td>
                        <td className="py-2">
                          <Button size="sm" variant={selectedRunId === run.id ? 'default' : 'outline'} onClick={() => setSelectedRunId(run.id)}>
                            View
                          </Button>
                        </td>
                      </tr>
                    ))}
                    {runs.length === 0 && (
                      <tr>
                        <td colSpan={7} className="py-6 text-center text-muted-foreground">No runs yet.</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>

          {selectedRunId != null && (
            <Card className="botux-panel-card">
              <CardHeader>
                <CardTitle>Run #{selectedRunId} Decisions</CardTitle>
                <CardDescription>{selectedRunDecisions.length} records</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="grid gap-2 md:grid-cols-2 lg:grid-cols-4">
                  {selectedRunDecisions.map((row) => {
                    const metrics = decisionMetrics(row)
                    return (
                      <div key={row.id} className="rounded-lg border border-border bg-background p-3">
                        <p className="font-semibold">{row.bot_id}</p>
                        <p className="text-xs text-muted-foreground mt-1">{row.previous_state} → {row.recommended_state}</p>
                        <p className="text-xs mt-2 tabular-nums">closed: {valueOrDash(metrics.closed_trades)}</p>
                        <p className="text-xs tabular-nums">winrate: {formatNumber(metrics.win_rate, 1)}</p>
                        <p className="text-xs tabular-nums">p&amp;l: {formatNumber(metrics.pnl_pct_total, 2)}</p>
                      </div>
                    )
                  })}
                </div>
              </CardContent>
            </Card>
          )}
        </TabsContent>
      </Tabs>
    </div>
  )
}

function SelectField({
  label,
  value,
  onChange,
  options,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  options: string[]
}) {
  return (
    <div className="space-y-2">
      <label className="text-xs text-muted-foreground">{label}</label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-8 w-full rounded-lg border border-input bg-background px-2.5 text-sm"
      >
        {options.map((option) => (
          <option key={option || 'all'} value={option}>
            {option || 'all'}
          </option>
        ))}
      </select>
    </div>
  )
}

function NumberField({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (value: string) => void
}) {
  return (
    <div className="space-y-2">
      <label className="text-xs text-muted-foreground">{label}</label>
      <Input type="number" value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  )
}

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <Card className="botux-panel-card">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">{label}</CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-2xl font-bold tabular-nums">{value}</p>
        {sub && <p className="text-xs text-muted-foreground mt-1">{sub}</p>}
      </CardContent>
    </Card>
  )
}

function CountBadge({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border border-border bg-background p-3">
      <p className="text-xs text-muted-foreground uppercase tracking-wider">{label}</p>
      <p className="text-2xl font-bold tabular-nums mt-1">{value}</p>
    </div>
  )
}

function toNumber(raw: string, fallback: number): number {
  const parsed = Number(raw)
  return Number.isFinite(parsed) ? parsed : fallback
}

function decisionMetrics(row: AutopilotDecision): Record<string, number | null> {
  const evidence = row.evidence || {}
  const metrics =
    row.previous_state === 'shadow'
      ? evidence.shadow_metrics || {}
      : evidence.recent_metrics || {}
  return {
    closed_trades: typeof metrics.closed_trades === 'number' ? metrics.closed_trades : null,
    win_rate: typeof metrics.win_rate === 'number' ? metrics.win_rate : null,
    pnl_pct_total: typeof metrics.pnl_pct_total === 'number' ? metrics.pnl_pct_total : null,
  }
}

function formatNumber(value: number | null, digits: number): string {
  return value == null ? '—' : value.toFixed(digits)
}

function valueOrDash(value: number | null): string {
  return value == null ? '—' : String(value)
}
