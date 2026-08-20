from enum import Enum


class SignalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"


class CouncilDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    VETO = "veto"


class OrderAction(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(str, Enum):
    REQUESTED = "requested"
    SUBMITTED = "submitted"
    EXECUTED = "executed"
    FAILED = "failed"
    CANCELED = "canceled"


class ExecutionStatus(str, Enum):
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    FILLED = "filled"
    EXECUTED = "executed"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELED = "canceled"
    EXPIRED = "expired"


class TradeOutcomeStatus(str, Enum):
    OPEN = "open"
    WIN = "win"
    LOSS = "loss"
    BREAKEVEN = "breakeven"


class OutboxStatus(str, Enum):
    PENDING = "pending"
    PROCESSED = "processed"
    FAILED = "failed"


class RuntimeHealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    BLOCKED = "blocked"


class DutySeverity(str, Enum):
    INFO = "info"
    MEDIUM = "medium"
    HIGH = "high"


class ReconcileStatus(str, Enum):
    OK = "ok"
    WARN = "warn"
    MISSING = "missing"


class LaneRuntimeStatus(str, Enum):
    ACTIVE = "active"
    IDLE = "idle"


class LaneScanState(str, Enum):
    IDLE = "idle"
    COMPLETED = "completed"
    SKIPPED = "skipped"
