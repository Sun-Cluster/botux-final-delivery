from __future__ import annotations

from tortoise import fields
from tortoise.models import Model


class AutopilotPolicy(Model):
    id = fields.BigIntField(pk=True)
    name = fields.CharField(max_length=128, unique=True)
    enabled = fields.BooleanField(default=True, index=True)
    mode = fields.CharField(max_length=32, default="observe", index=True)
    evaluation_window_days = fields.IntField(default=7)
    shadow_min_closed_trades = fields.IntField(default=4)
    shadow_max_win_rate = fields.FloatField(default=45.0)
    shadow_max_pnl_pct = fields.FloatField(default=-2.0)
    reactivate_interval_seconds = fields.IntField(default=86400)
    reactivate_min_closed_trades = fields.IntField(default=4)
    reactivate_min_win_rate = fields.FloatField(default=55.0)
    reactivate_min_pnl_pct = fields.FloatField(default=1.0)
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta(Model.Meta):
        table = "autopilot_policies"


class AutopilotRun(Model):
    id = fields.BigIntField(pk=True)
    policy = fields.ForeignKeyField(
        "models.AutopilotPolicy",
        related_name="runs",
        on_delete=fields.SET_NULL,
        null=True,
    )
    mode = fields.CharField(max_length=32, default="observe")
    snapshot = fields.JSONField(default=dict)
    bots_count = fields.IntField(default=0)
    started_at = fields.DatetimeField(auto_now_add=True, index=True)
    completed_at = fields.DatetimeField(null=True)
    status = fields.CharField(max_length=32, default="running", index=True)
    error = fields.TextField(null=True)

    class Meta(Model.Meta):
        table = "autopilot_runs"


class AutopilotDecision(Model):
    id = fields.BigIntField(pk=True)
    run = fields.ForeignKeyField(
        "models.AutopilotRun",
        related_name="decisions",
        on_delete=fields.CASCADE,
    )
    policy = fields.ForeignKeyField(
        "models.AutopilotPolicy",
        related_name="decisions",
        on_delete=fields.SET_NULL,
        null=True,
    )
    bot_id = fields.CharField(max_length=128, index=True)
    previous_state = fields.CharField(max_length=32)
    recommended_state = fields.CharField(max_length=32, index=True)
    reason_codes = fields.JSONField(default=list)
    evidence = fields.JSONField(default=dict)
    applied = fields.BooleanField(default=False)
    applied_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True, index=True)

    class Meta(Model.Meta):
        table = "autopilot_decisions"
        indexes = (("bot_id", "created_at"), ("recommended_state", "created_at"))
