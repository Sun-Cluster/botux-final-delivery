from __future__ import annotations

from tortoise import fields
from tortoise.models import Model


class SignalRecord(Model):
    id = fields.BigIntField(pk=True)
    signal_id = fields.CharField(max_length=128, unique=True)
    symbol = fields.CharField(max_length=32)
    action = fields.CharField(max_length=16)
    status = fields.CharField(max_length=32, index=True)
    score = fields.FloatField(null=True)
    confidence = fields.FloatField(null=True)
    priority = fields.IntField(default=5, index=True)
    source = fields.CharField(max_length=64, null=True)
    headline = fields.CharField(max_length=200, null=True)
    lane_hint = fields.CharField(max_length=64, null=True)
    strategy_hint = fields.CharField(max_length=128, null=True)
    dedup_key = fields.CharField(max_length=200, null=True, index=True)
    scan_timestamp = fields.DatetimeField(null=True, index=True)
    blocked_reason = fields.TextField(null=True)
    metadata = fields.JSONField(default=dict)
    schema_version = fields.CharField(max_length=16, default="v1")
    created_at = fields.DatetimeField(auto_now_add=True, index=True)

    class Meta(Model.Meta):
        table = "signals"
        indexes = (("status", "created_at"), ("source", "scan_timestamp"))


class SignalEvent(Model):
    id = fields.BigIntField(pk=True)
    signal = fields.ForeignKeyField("models.SignalRecord", related_name="events", on_delete=fields.CASCADE)
    event_type = fields.CharField(max_length=64)
    payload = fields.JSONField(default=dict)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta(Model.Meta):
        table = "signal_events"


class CouncilDecisionRecord(Model):
    id = fields.BigIntField(pk=True)
    signal = fields.ForeignKeyField("models.SignalRecord", related_name="council_decisions", on_delete=fields.CASCADE)
    decision = fields.CharField(max_length=16)
    reason = fields.TextField(null=True)
    confidence = fields.FloatField(null=True)
    buy_votes = fields.FloatField(null=True)
    total_votes = fields.IntField(null=True)
    vetoed = fields.BooleanField(default=False)
    veto_reason = fields.TextField(null=True)
    approval_score = fields.FloatField(null=True)
    position_size_pct = fields.FloatField(null=True)
    stop_loss_pct = fields.FloatField(null=True)
    take_profit_pct = fields.FloatField(null=True)
    votes_count = fields.IntField(default=0)
    failures_count = fields.IntField(default=0)
    schema_version = fields.CharField(max_length=16, default="v1")
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta(Model.Meta):
        table = "council_decisions"


class OrderRecord(Model):
    id = fields.BigIntField(pk=True)
    signal = fields.ForeignKeyField("models.SignalRecord", related_name="orders", on_delete=fields.CASCADE)
    idempotency_key = fields.CharField(max_length=160, unique=True)
    symbol = fields.CharField(max_length=32)
    action = fields.CharField(max_length=16)
    quantity = fields.DecimalField(max_digits=20, decimal_places=8)
    broker_name = fields.CharField(max_length=64, null=True)
    market = fields.CharField(max_length=64, null=True)
    order_type = fields.CharField(max_length=32, default="market")
    bot_id = fields.CharField(max_length=128, null=True, index=True)
    signal_source = fields.CharField(max_length=64, null=True)
    signal_score = fields.FloatField(null=True)
    route_reason = fields.CharField(max_length=160, null=True)
    position_size_pct = fields.FloatField(null=True)
    stop_loss_pct = fields.FloatField(null=True)
    take_profit_pct = fields.FloatField(null=True)
    limit_price = fields.FloatField(null=True)
    reference_price = fields.FloatField(null=True)
    entry_price = fields.FloatField(null=True)
    last_price = fields.FloatField(null=True)
    take_profit_price = fields.FloatField(null=True)
    stop_loss_price = fields.FloatField(null=True)
    status = fields.CharField(max_length=32, index=True)
    created_at = fields.DatetimeField(auto_now_add=True, index=True)

    class Meta(Model.Meta):
        table = "orders"
        indexes = (("status", "created_at"),)


class ExecutionRecord(Model):
    id = fields.BigIntField(pk=True)
    order = fields.ForeignKeyField(
        "models.OrderRecord",
        related_name="executions",
        on_delete=fields.CASCADE,
    )
    broker_order_id = fields.CharField(max_length=128, null=True)
    status = fields.CharField(max_length=32)
    filled_qty = fields.DecimalField(max_digits=20, decimal_places=8, default=0)
    avg_price = fields.DecimalField(max_digits=20, decimal_places=8, null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta(Model.Meta):
        table = "executions"


class TradeOutcomeRecord(Model):
    id = fields.BigIntField(pk=True)
    signal = fields.ForeignKeyField("models.SignalRecord", related_name="outcomes", on_delete=fields.CASCADE)
    order = fields.ForeignKeyField(
        "models.OrderRecord",
        related_name="outcomes",
        on_delete=fields.SET_NULL,
        null=True,
    )
    symbol = fields.CharField(max_length=32)
    outcome = fields.CharField(max_length=32)
    pnl_pct = fields.FloatField(null=True)
    trade_id = fields.CharField(max_length=160, null=True, index=True)
    action = fields.CharField(max_length=16, null=True)
    quantity = fields.FloatField(null=True)
    entry_price = fields.FloatField(null=True)
    exit_price = fields.FloatField(null=True)
    close_reason = fields.TextField(null=True)
    bot_id = fields.CharField(max_length=128, null=True, index=True)
    source = fields.CharField(max_length=64, null=True)
    broker_order_id = fields.CharField(max_length=128, null=True)
    broker_name = fields.CharField(max_length=64, null=True)
    market = fields.CharField(max_length=64, null=True)
    order_type = fields.CharField(max_length=32, null=True)
    features = fields.JSONField(default=dict)
    created_at = fields.DatetimeField(auto_now_add=True, index=True)
    closed_at = fields.DatetimeField(null=True)

    class Meta(Model.Meta):
        table = "trade_outcomes"
        indexes = (("symbol", "created_at"),)


class PositionSnapshot(Model):
    id = fields.BigIntField(pk=True)
    snapshot_key = fields.CharField(max_length=128, unique=True)
    payload = fields.JSONField(default=dict)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta(Model.Meta):
        table = "positions_snapshots"


class BotProfile(Model):
    id = fields.BigIntField(pk=True)
    bot_id = fields.CharField(max_length=128, unique=True)
    display_name = fields.CharField(max_length=160, null=True)
    mission = fields.TextField(null=True)
    strategy_type = fields.CharField(max_length=160, null=True)
    horizon = fields.CharField(max_length=64, null=True)
    market = fields.CharField(max_length=64, null=True)
    broker = fields.CharField(max_length=64, null=True)
    mode = fields.CharField(max_length=64, null=True)
    lifecycle_state = fields.CharField(max_length=64, null=True, index=True)
    status = fields.CharField(max_length=64, null=True)
    icon = fields.CharField(max_length=64, null=True)
    intel_source = fields.CharField(max_length=128, null=True)
    notes = fields.TextField(null=True)
    enabled = fields.BooleanField(default=True)
    autopilot_state = fields.CharField(max_length=32, default="active", index=True)
    autopilot_changed_at = fields.DatetimeField(null=True)
    metadata = fields.JSONField(default=dict)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta(Model.Meta):
        table = "bot_profiles"


class StrategyRegistry(Model):
    id = fields.BigIntField(pk=True)
    strategy_id = fields.CharField(max_length=128, unique=True)
    metadata = fields.JSONField(default=dict)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta(Model.Meta):
        table = "strategy_registry"


class SystemConfig(Model):
    id = fields.BigIntField(pk=True)
    key = fields.CharField(max_length=160, unique=True)
    value = fields.JSONField()
    value_type = fields.CharField(max_length=32)
    scope = fields.CharField(max_length=32, default="global", index=True)
    description = fields.TextField(null=True)
    updated_by = fields.CharField(max_length=64, null=True)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta(Model.Meta):
        table = "system_configs"


class GateFailure(Model):
    id = fields.BigIntField(pk=True)
    signal_id = fields.CharField(max_length=128, null=True, index=True)
    gate_name = fields.CharField(max_length=64)
    reason = fields.TextField(null=True)
    decision = fields.CharField(max_length=16, null=True)
    veto = fields.BooleanField(default=False)
    confidence = fields.FloatField(null=True)
    buy_votes = fields.FloatField(null=True)
    blocked_reason = fields.CharField(max_length=128, null=True)
    dedup_key = fields.CharField(max_length=200, null=True)
    trading_halted = fields.BooleanField(default=False)
    trading_halt_reason = fields.CharField(max_length=200, null=True)
    consecutive_losses = fields.IntField(null=True)
    correlation_blocked = fields.BooleanField(default=False)
    correlation_reason = fields.CharField(max_length=200, null=True)
    correlated_with_csv = fields.TextField(null=True)
    sector_overlap = fields.BooleanField(default=False)
    pdt_allowed = fields.BooleanField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta(Model.Meta):
        table = "gate_failures"


class AuditLog(Model):
    id = fields.BigIntField(pk=True)
    trace_id = fields.CharField(max_length=128, null=True, index=True)
    actor = fields.CharField(max_length=64, null=True)
    event_type = fields.CharField(max_length=64)
    payload = fields.JSONField(default=dict)
    created_at = fields.DatetimeField(auto_now_add=True, index=True)

    class Meta(Model.Meta):
        table = "audit_logs"


class OutboxEvent(Model):
    id = fields.BigIntField(pk=True)
    event_key = fields.CharField(max_length=160, unique=True)
    event_type = fields.CharField(max_length=64)
    payload = fields.JSONField(default=dict)
    status = fields.CharField(max_length=32, default="pending", index=True)
    created_at = fields.DatetimeField(auto_now_add=True, index=True)

    class Meta(Model.Meta):
        table = "outbox_events"
        indexes = (("status", "created_at"),)
