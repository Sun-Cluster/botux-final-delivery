from __future__ import annotations

import re


STOP_LOSS = "stop_loss"
TAKE_PROFIT = "take_profit"
DTE_EXIT = "dte_exit"
MAX_HOLD = "max_hold"
TRAILING_STOP = "trailing_stop"
BREAKEVEN_EXIT = "breakeven_exit"
MANUAL_EXIT = "manual_exit"
BROKER_RECONCILE = "broker_reconcile"

_SEPARATOR_PATTERN = re.compile(r"[\s\-]+")


def normalize_exit_reason(reason: str | None) -> str:
    raw = str(reason or "").strip()
    if not raw:
        return MANUAL_EXIT

    normalized = _SEPARATOR_PATTERN.sub("_", raw.lower())
    collapsed = normalized.strip("_")
    if not collapsed:
        return MANUAL_EXIT

    if collapsed in {"stop_loss", "hard_stop", "stop_hit", "sl_hit"}:
        return STOP_LOSS
    if "stop_loss" in collapsed or collapsed.startswith("stop_hit") or collapsed.startswith("sl_hit"):
        return STOP_LOSS
    if "stop_hit" in collapsed:
        return STOP_LOSS
    if "profit_target" in collapsed or "take_profit" in collapsed:
        return TAKE_PROFIT
    if collapsed in {"target_hit", "tp_hit", "tp"}:
        return TAKE_PROFIT
    if collapsed in {"partial_profit", "partial_profit_then_trail"}:
        return TAKE_PROFIT
    if collapsed == "dte_exit" or collapsed.startswith("dte_"):
        return DTE_EXIT
    if collapsed in {"max_hold", "time_stop"} or collapsed.startswith("max_hold_"):
        return MAX_HOLD
    if "trailing_stop" in collapsed or collapsed.startswith("trail"):
        return TRAILING_STOP
    if "breakeven" in collapsed:
        return BREAKEVEN_EXIT
    if collapsed in {"sell_execution", "manual_close", "manual_exit"}:
        return MANUAL_EXIT
    if collapsed in {"broker_position_absent", "broker_reconcile"}:
        return BROKER_RECONCILE
    return collapsed
