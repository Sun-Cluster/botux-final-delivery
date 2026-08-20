from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from config import AppConfig, load_configs
from infra.brokers.alpaca_adapter import AlpacaAdapter
from infra.brokers.base import BrokerPort
from infra.brokers.ibkr_adapter import IbkrAdapter
from infra.brokers.router import BrokerRouter
from infra.queue.bus import InProcessQueueBus
from runtime.logging import configure_logging

if TYPE_CHECKING:
    from infra.scheduler.runner import SchedulerRunner
    from runtime.process_manager import ProcessManager


@dataclass
class Container:
    config: AppConfig
    broker: BrokerPort
    brokers: dict[str, BrokerPort]
    broker_router: BrokerRouter
    queue_bus: InProcessQueueBus
    scheduler: SchedulerRunner | None = None
    last_reconcile_report: dict[str, object] | None = None
    last_reconcile_run_at: str | None = None
    trading_halted: bool = False
    trading_halt_reason: str | None = None
    trading_halted_at: str | None = None
    process_manager: ProcessManager | None = None
    db_context_ready: bool = False
    db_context_owned: bool = False


def build_container() -> Container:
    from runtime.process_manager import ProcessManager

    config = load_configs()
    configure_logging(config.log_level)
    brokers = _build_brokers()
    broker = brokers["alpaca"]
    broker_router = BrokerRouter(default_broker=broker, brokers=brokers)
    queue_bus = InProcessQueueBus()
    container = Container(
        config=config,
        broker=broker,
        brokers=brokers,
        broker_router=broker_router,
        queue_bus=queue_bus,
        scheduler=None,
        last_reconcile_report=None,
        last_reconcile_run_at=None,
        trading_halted=False,
        trading_halt_reason=None,
        trading_halted_at=None,
        process_manager=None,
        db_context_ready=False,
        db_context_owned=False,
    )
    container.process_manager = ProcessManager(container)
    return container


def _build_brokers() -> dict[str, BrokerPort]:
    return {
        "alpaca": AlpacaAdapter(),
        "ibkr": IbkrAdapter(),
    }
