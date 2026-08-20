/**
 * BOTUX v2 API client — fast, cached, same-origin through Next.js proxy.
 */

const API = '/api/v2'

// Client-side cache — prevents redundant fetches across components
const cache: Record<string, { data: any; ts: number }> = {}
const CACHE_TTL = 4000 // 4 seconds — balance speed vs rate limits

async function get<T = any>(path: string): Promise<T | null> {
  const key = path
  const now = Date.now()

  // Return cached if fresh
  if (cache[key] && (now - cache[key].ts) < CACHE_TTL) {
    return cache[key].data as T
  }

  try {
    const res = await fetch(`${API}${path}`, {
      signal: AbortSignal.timeout(10000),
    })
    if (!res.ok) return cache[key]?.data ?? null
    const data = await res.json()
    cache[key] = { data, ts: now }
    return data
  } catch {
    // Return stale cache on error — never flash empty
    return cache[key]?.data ?? null
  }
}

async function put<T = any>(path: string, body: unknown): Promise<T | null> {
  try {
    const res = await fetch(`${API}${path}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(12000),
    })
    if (!res.ok) return null
    const data = await res.json()
    return data as T
  } catch {
    return null
  }
}

async function patch<T = any>(path: string, body: unknown): Promise<T | null> {
  try {
    const res = await fetch(`${API}${path}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(12000),
    })
    if (!res.ok) return null
    const data = await res.json()
    delete cache['/api/bots/canonical']
    return data as T
  } catch {
    return null
  }
}

function invalidateCache(prefixes: string[]) {
  Object.keys(cache).forEach((key) => {
    if (prefixes.some((prefix) => key.startsWith(prefix))) delete cache[key]
  })
}

// ─── Proven v2 endpoints ────────────────────────────────────────────────

export async function getSummary() {
  return get<{
    equity: number; cash: number; buying_power: number;
    daily_pl: number; daily_pl_pct: number; position_market_value: number;
    unrealized_pl: number; mode: string; position_count: number;
    account: {
      equity: number; cash: number; buying_power: number;
      pnl_today: number; pnl_today_pct: number; last_equity: number;
      portfolio_value: number; mode: string;
    };
  }>('/summary')
}

export async function getSnapshot() {
  return get<{
    equity: number; cash: number; daily_pnl: number; daily_pnl_pct: number;
  }>('/api/snapshot')
}

export async function getPositions() {
  // PATCH-DASH-POSITIONS-API-ROUTE-01: call the merged /api/positions
  // endpoint (Alpaca 5 + IBKR 30) instead of the raw Alpaca-only /positions
  // passthrough. The merged endpoint wraps rows in { positions: [...] }.
  const res = await get<{
    positions?: Array<{
      symbol: string; side: string; qty: number; avg_entry_price: number;
      current_price: number; market_value: number; unrealized_pl: number;
      unrealized_plpc: number;
      broker?: string; market?: string; currency?: string;
    }>;
  }>('/api/positions')
  return Array.isArray(res?.positions) ? res.positions : []
}

export async function getBrokers() {
  return get<{
    brokers: Array<{
      name: string; connected: boolean; mode: string; equity: number;
      cash: number; buying_power?: number; last_equity?: number;
      portfolio_value?: number; currency: string; operating_capital?: number;
      status?: string; account_number?: string; error?: string;
    }>;
  }>('/api/brokers/status')
}

export async function getBots() {
  return get<{
    profiles: Record<string, {
      bot_id: string; display_name: string; mission: string;
      strategy_type: string; horizon: string; market: string;
      broker: string; mode: string; lifecycle_state: string;
      lifecycle_state_raw?: string;
      icon: string; allocation: { pct: number; usd?: number; aud?: number; currency: string };
      risk: { max_position_pct: number; max_daily_loss_pct: number };
      runtime_status?: string;
      stored_status?: string | null;
      autopilot_state?: string;
      autopilot_changed_at?: string | null;
      broker_submit_allowed?: boolean;
      scheduler_active?: boolean;
      enabled: boolean; performance: {
        total_trades: number; closed_trades?: number; open_trades?: number;
        wins: number; losses: number;
        avg_r?: number | null; total_pnl?: number | null;
        gross_profit?: number | null; gross_loss?: number | null;
        win_rate: number | null; sharpe: number | null;
        quality_score: number | null; confidence: string | number | null;
        suppressed: boolean;
      };
    }>;
    count: number; source: string;
  }>('/api/bots/canonical')
}

export async function patchBotProfile(
  botId: string,
  body: {
    enabled?: boolean
    autopilot_state?: string
  }
) {
  return patch<{
    updated: boolean
    bot_id: string
    patch?: Record<string, any>
    error?: string
  }>(`/api/bots/canonical/${botId}`, body)
}

export async function getStrategies() {
  return get<{
    strategies: Record<string, {
      strategy_id: string; name: string; version: string;
      lifecycle_state: string; bot_ids: string[]; market: string;
      signal_sources: string[]; intel_engine: string | null;
    }>;
    count: number; source: string;
  }>('/api/strategies')
}

export async function getTrades() {
  return get<Array<{
    symbol: string; action: string; entry_price: number; exit_price: number;
    pnl_pct: number | null; outcome: string;
    quantity?: number | null; qty?: number | null;
    bot_id?: string | null; broker_name?: string | null;
    market?: string | null; order_type?: string | null;
    mfe_pct?: number | null; mae_pct?: number | null;
    opened_at?: string | null; closed_at?: string | null; created_at?: string | null;
    features: {
      bot_id?: string; qty?: number; quantity?: number; signal_id?: string;
      pnl_pct?: number; bot_pnl_pct?: number;
      mfe_pct?: number; mae_pct?: number;
      max_favorable_excursion_pct?: number; max_adverse_excursion_pct?: number;
    };
  }>>('/api/trades')
}

export async function getScorecards() {
  return get<{
    scorecards: Record<string, {
      total_trades: number; wins: number; losses: number;
      win_rate: number | null; sharpe: number | null;
      max_drawdown_pct: number | null; confidence: string;
      quality_score: number; suppressed: boolean;
    }>;
    ranking: Array<{ bot_id: string; quality_score: number }>;
  }>('/api/bots/scorecards')
}

export async function getRisk() {
  return get<{
    halted: boolean; trades_today: number; wins_today: number;
    losses_today: number; daily_pnl: number; max_drawdown: number;
  }>('/api/risk/status')
}

export async function getFleetRisk() {
  return get<{
    fleet_halted: boolean;
    halt_reason: string | null;
    peak_equity: number;
    current_drawdown_pct: number;
    daily_pnl: number;
    weekly_pnl: number;
    monthly_pnl: number;
    total_positions: number;
    limits: {
      daily_loss_cap: number;
      weekly_loss_cap: number;
      monthly_loss_cap: number;
      max_drawdown: number;
      max_positions: number;
      max_per_bot: number;
      reserve_min: number;
    };
  }>('/api/fleet/risk')
}

export async function getExecutorStatus() {
  return get<{
    trades_today: number; market_open: boolean;
    thresholds: { min_combined_score: number; take_profit_pct: number; stop_loss_pct: number; cooldown_minutes: number };
  }>('/api/executor/status')
}

export async function getCouncil() {
  return get<{
    stats: { total: number; approved: number; rejected: number; vetoed: number };
  }>('/api/council/status')
}

export async function getPDT() {
  return get<{
    day_trades_used: number; day_trades_max: number; day_trades_remaining: number;
  }>('/api/pdt/status')
}

export async function getRegime() {
  return get<{ regime: string }>('/api/regime')
}

export async function getMonitorSummary() {
  return get<{
    grade: string; total_bots: number; active_bots: number;
    enabled_bots: number; connected_brokers: number; total_brokers: number;
    signals_today: number; trades_today: number; drift_alerts: number;
    stale_feeds: number; issues: number;
  }>('/api/monitor/summary')
}

export async function getActions() {
  return get<{
    actions: Array<{
      type: string; issue: string; detail: string; priority: string;
    }>;
    count: number;
  }>('/api/monitor/actions')
}

export async function getSignalQuality() {
  return get<{
    sources: Array<{
      source: string; total: number; today: number; pending: number;
      rejected: number; traded: number; conversion_rate: number;
      freshness_min: number | null;
    }>;
  }>('/api/monitor/signal_quality')
}

export async function getBotImprovement() {
  return get<{
    bots: Array<{
      bot_id: string; display_name: string; lifecycle_state: string;
      enabled: boolean; quality_score: number | null;
      confidence: string | number | null; win_rate: number | null;
      total_trades: number; trend: string; suppressed: boolean;
    }>;
  }>('/api/monitor/bot_improvement')
}

export async function getModelDrift() {
  return get<{
    strategies: Array<{
      strategy_id: string; name: string; lifecycle_state: string;
      quality_score: number | null; recent_win_rate: number | null;
      historical_win_rate: number | null; drift_flag: string;
      trades: number;
    }>;
  }>('/api/monitor/model_drift')
}

export async function getEcosystem() {
  return get<any>('/api/ecosystem')
}

export async function getSignals() {
  return get<{
    signals: Array<{
      signal_id: string
      ticker?: string
      symbol?: string
      headline?: string | null
      action?: string
      status?: string
      score?: number | null
      conf?: number | null
      confidence?: number | null
      source?: string | null
      lane_hint?: string | null
      strategy_hint?: string | null
      schema_version?: string | number
      blocked_reason?: string | null
      reason_code?: string | null
      reason?: string | null
      created_at?: string | null
    }>
  }>('/api/signals')
}

// ─── Intelligence + Self-Improvement endpoints ────────────────────────

export async function getRewardStatus() {
  return get<{
    total_trades_processed: number; cumulative_r: number; avg_r_per_trade: number;
    bot_performance: Record<string, { trades: number; wins: number; total_r: number; avg_r: number; win_rate: number }>;
    regime_performance: Record<string, any>; source_performance: Record<string, any>;
    patterns_tracked: number; lessons_stored: number;
  }>('/api/reward/status')
}

export async function getEdgeStatus() {
  return get<{
    total_strategies: number; by_source: Record<string, number>; by_type: Record<string, number>;
    by_grade: Record<string, number>; avg_quality: number; last_research: string;
  }>('/api/edge/status')
}

export async function getFleetEdge() {
  return get<{ fleet: Record<string, any> }>('/api/fleet/edge-status')
}

export async function getPortfolioHistory() {
  return get<{
    timestamp: number[];
    equity: number[];
    profit_loss: number[];
    profit_loss_pct: number[];
    base_value: number;
  }>('/portfolio/history')
}

export async function postChat(message: string, scope: string = 'summary') {
  const res = await fetch(`${API}/api/chat`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({message, scope}),
    signal: AbortSignal.timeout(60000),
  })
  return res.json()
}

export async function postChatInject(proposal: any, approved: boolean, operatorNote: string = '') {
  const res = await fetch(`${API}/api/chat/inject`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({proposal, approved, operator_note: operatorNote}),
    signal: AbortSignal.timeout(15000),
  })
  return res.json()
}

export async function getChatHistory(limit: number = 20) {
  return get<{history: any[]; count: number}>(`/api/chat/history?limit=${limit}`)
}

export async function getSystemSubstrate() {
  return get<any>('/api/system/substrate')
}

export async function getSystemPipeline() {
  return get<any>('/api/system/pipeline')
}

export async function getSystemLearning() {
  return get<any>('/api/system/learning')
}

export async function getExecutorStatusFull() {
  return get<any>('/api/executor/status')
}

// ─── Fleet Autopilot endpoints ─────────────────────────────────────────

export type AutopilotPolicy = {
  id?: number
  name?: string
  enabled?: boolean
  mode?: 'observe' | 'recommend' | 'constrained_apply' | string
  evaluation_window_days?: number
  shadow_min_closed_trades?: number
  shadow_max_win_rate?: number
  shadow_max_pnl_pct?: number
  reactivate_interval_seconds?: number
  reactivate_min_closed_trades?: number
  reactivate_min_win_rate?: number
  reactivate_min_pnl_pct?: number
  created_at?: string
  updated_at?: string
}

export type AutopilotDecision = {
  id: number
  run_id: number
  policy_id: number | null
  bot_id: string
  previous_state: string
  recommended_state: string
  reason_codes: string[]
  evidence: Record<string, any>
  applied: boolean
  applied_at: string | null
  created_at: string
}

export type AutopilotRun = {
  id: number
  policy_id: number | null
  mode: string
  snapshot: Record<string, any>
  bots_count: number
  started_at: string
  completed_at: string | null
  status: string
  error: string | null
}

export async function getAutopilotStatus() {
  return get<{
    policy: AutopilotPolicy
    latest_run: AutopilotRun | null
    latest_recommendation_counts: Record<string, number>
    top_reason_codes: Array<{ reason_code: string; count: number }>
    autopilot_job: Record<string, any> | null
    generated_at: string
  }>('/api/autopilot/status')
}

export async function getAutopilotPolicy() {
  return get<{ policy: AutopilotPolicy; generated_at: string }>('/api/autopilot/policy')
}

export async function updateAutopilotPolicy(patch: Partial<AutopilotPolicy>) {
  const payload = await put<{ policy: AutopilotPolicy; updated_at: string }>('/api/autopilot/policy', patch)
  invalidateCache(['/api/autopilot/'])
  return payload
}

export async function getAutopilotRuns(limit: number = 20) {
  return get<{ items: AutopilotRun[]; count: number; generated_at: string }>(`/api/autopilot/runs?limit=${limit}`)
}

export async function getAutopilotRunDetail(runId: number) {
  return get<{ run?: AutopilotRun; decisions: AutopilotDecision[]; decision_count: number; generated_at: string; error?: string }>(
    `/api/autopilot/runs/${runId}`
  )
}

export async function getAutopilotDecisions(filters?: {
  limit?: number
  run_id?: number
  bot_id?: string
  state?: string
}) {
  const params = new URLSearchParams()
  if (filters?.limit != null) params.set('limit', String(filters.limit))
  if (filters?.run_id != null) params.set('run_id', String(filters.run_id))
  if (filters?.bot_id) params.set('bot_id', filters.bot_id)
  if (filters?.state) params.set('state', filters.state)
  const query = params.toString()
  return get<{ items: AutopilotDecision[]; count: number; generated_at: string }>(
    `/api/autopilot/decisions${query ? `?${query}` : ''}`
  )
}

export async function getBotPaperTrades(botId: string) {
  return get<{
    bot_id: string;
    trades: Array<{
      ts: string; bot_id: string; bot_name: string; symbol: string; action: string;
      entry_price: number; exit_price: number; qty: number;
      bot_outcome: string; bot_pnl_pct: number; bot_pnl_dollar: number;
      exit_reason: string; signal_score: number; signal_source: string;
      regime_at_entry: string; hold_hours: number;
      mfe_pct?: number; mae_pct?: number;
      max_favorable_price?: number; max_adverse_price?: number;
    }>;
    count: number;
  }>(`/api/bots/${botId}/paper_trades`)
}

export async function getBotDebates(botId: string) {
  return get<{
    bot_id: string;
    debates: Array<{
      ts: string; bot_id: string; display_name: string;
      proposed_action: string; bull_argument: string; bear_argument: string;
      synthesis_reasoning: string; final_decision: string; confidence: number;
    }>;
    count: number;
  }>(`/api/bots/${botId}/debates`)
}

export async function getBotTuningHistory(botId: string) {
  return get<{
    bot_id: string;
    adjustments: Array<{
      ts: string; bot_id: string; action: string;
      old_tp: number; new_tp: number; old_sl: number; new_sl: number;
      tp_change: number; sl_change: number; reason: string;
    }>;
    count: number;
  }>(`/api/bots/${botId}/tuning_history`)
}

export type SettingsField = {
  key: string
  label: string
  description: string
  value_type: 'str' | 'int' | 'float' | 'bool'
  input_type: 'password' | 'str' | 'int' | 'float' | 'bool'
  origin: string
  env_name: string
  secret: boolean
  group: string
  configured: boolean
  value: string | number | boolean
  display_value?: string
}

export type SettingsSection = {
  id: string
  title: string
  description: string
  broker_name?: string
  connection?: {
    broker: string
    connected: boolean
    configured: boolean
    mode: string
    status: string
    account_number: string
    error: string
  }
  fields: SettingsField[]
}

export type SettingsTab = {
  id: string
  label: string
  sections: SettingsSection[]
}

export async function getSettings() {
  return get<{ tabs: SettingsTab[]; generated_at: string }>('/api/settings')
}

export async function updateSettingConfig(
  key: string,
  body: { value: string | number | boolean; updated_by?: string }
) {
  const payload = await put<{
    item: SettingsField
    unchanged?: boolean
    updated_at: string
  }>(`/api/settings/configs/${key}`, body)
  invalidateCache(['/api/settings', '/api/brokers/status'])
  return payload
}

export async function checkBrokerConnection(brokerName: string) {
  try {
    const res = await fetch(`${API}/api/settings/brokers/${brokerName}/check`, {
      method: 'POST',
      signal: AbortSignal.timeout(12000),
    })
    if (!res.ok) return null
    return await res.json() as {
      broker: string
      connected: boolean
      configured: boolean
      status: string
      mode: string
      account_number: string
      currency: string
      error: string
      checked_at: string
    }
  } catch {
    return null
  }
}
