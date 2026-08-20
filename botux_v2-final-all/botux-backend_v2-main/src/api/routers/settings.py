from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, JsonValue

from api.deps import get_container
from app.services.runtime_config.service import (
    RUNTIME_CONFIG_DEFINITIONS,
    RuntimeConfigDefinition,
    RuntimeConfigService,
    coerce_runtime_config_value,
    get_runtime_config_definition,
)
from db.repositories.audit_logs_repo import AuditLogsRepository
from db.repositories.system_configs_repo import SystemConfigsRepository
from db.uow import UnitOfWork
from runtime.container import Container

router = APIRouter(prefix="/api/settings", tags=["settings"])


TAB_GROUPS: dict[str, dict[str, Any]] = {
    "brokers": {
        "label": "Brokers",
        "groups": ("broker_global", "broker_alpaca", "broker_ibkr"),
    },
    "scheduler": {
        "label": "Scheduler",
        "groups": ("scheduler",),
    },
    "execution_risk": {
        "label": "Execution & Risk",
        "groups": ("execution", "risk"),
    },
    "data_sources": {
        "label": "Data Sources",
        "groups": ("data_sources",),
    },
}


GROUP_META: dict[str, dict[str, str]] = {
    "broker_global": {
        "title": "Broker Routing",
        "description": "Global broker selection used by shared account and execution surfaces.",
    },
    "broker_alpaca": {
        "title": "Alpaca",
        "description": "Broker account, URLs, and connectivity for US equities and options.",
        "broker_name": "alpaca",
    },
    "broker_ibkr": {
        "title": "IBKR",
        "description": "Gateway/TWS session details for ASX and IBKR-routed trading.",
        "broker_name": "ibkr",
    },
    "execution": {
        "title": "Execution",
        "description": "Global execution guardrails and signal freshness limits.",
    },
    "risk": {
        "title": "Risk",
        "description": "Account-level risk ceilings and runtime bypass controls.",
    },
    "data_sources": {
        "title": "13F / Data Sources",
        "description": "SEC EDGAR settings used by tradecopy and related intelligence lanes.",
    },
    "scheduler": {
        "title": "Runtime Jobs",
        "description": "Intervals and toggles for scheduler jobs and background execution loops.",
    },
}


class SettingsConfigUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: JsonValue
    updated_by: str | None = None


@router.get("")
async def get_settings() -> dict[str, object]:
    resolver = RuntimeConfigService()

    groups: dict[str, list[dict[str, object]]] = {}
    for key, definition in sorted(RUNTIME_CONFIG_DEFINITIONS.items()):
        resolved = await resolver.resolve(key)
        groups.setdefault(definition.group, []).append(_field_payload(definition, resolved.value, resolved.origin))

    tabs: list[dict[str, object]] = []
    for tab_id, tab_meta in TAB_GROUPS.items():
        sections: list[dict[str, object]] = []
        for group_name in tab_meta["groups"]:
            meta = GROUP_META[group_name]
            section: dict[str, object] = {
                "id": group_name,
                "title": meta["title"],
                "description": meta["description"],
                "fields": groups.get(group_name, []),
            }
            broker_name = meta.get("broker_name")
            if broker_name:
                section["broker_name"] = broker_name
                section["connection"] = _unknown_broker_snapshot(broker_name)
            sections.append(section)
        tabs.append(
            {
                "id": tab_id,
                "label": tab_meta["label"],
                "sections": sections,
            }
        )

    return {
        "tabs": tabs,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.put("/configs/{key:path}")
async def update_setting(key: str, request: SettingsConfigUpdateRequest) -> dict[str, object]:
    try:
        definition = get_runtime_config_definition(key)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    actor = (request.updated_by or "api.settings").strip() or "api.settings"
    if definition.secret and isinstance(request.value, str) and not request.value.strip():
        resolved = await RuntimeConfigService().resolve(key)
        return {
            "item": _field_payload(definition, resolved.value, resolved.origin),
            "stored": None,
            "unchanged": True,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    normalized_value = coerce_runtime_config_value(key, request.value)
    async with UnitOfWork() as uow:
        repo = SystemConfigsRepository(connection=uow.connection)
        audit_repo = AuditLogsRepository(connection=uow.connection)
        stored = await repo.upsert(
            key=key,
            value=normalized_value,
            value_type=definition.value_type,
            scope=definition.scope,
            description=definition.description,
            updated_by=actor,
        )
        await audit_repo.append(
            event_type="settings.updated",
            actor=actor,
            trace_id=key,
            payload={
                "key": key,
                "scope": definition.scope,
                "group": definition.group,
                "value_type": definition.value_type,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    resolved = await RuntimeConfigService().resolve(key)
    return {
        "item": _field_payload(definition, resolved.value, resolved.origin),
        "stored": stored,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/brokers/{broker_name}/check")
async def check_broker_connection(
    broker_name: str,
    container: Container = Depends(get_container),
) -> dict[str, object]:
    broker = container.broker_router.get(broker_name.strip().lower())
    if broker is None:
        raise HTTPException(status_code=404, detail=f"broker '{broker_name}' not found")
    try:
        account = await broker.get_account()
    except Exception as exc:
        return {
            "broker": broker_name,
            "connected": False,
            "configured": False,
            "status": "error",
            "error": str(exc)[:200],
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
    return {
        "broker": broker_name,
        "connected": bool(account.get("connected", not bool(account.get("error")))),
        "configured": bool(account.get("configured", False)),
        "status": str(account.get("status", "unknown")),
        "mode": str(account.get("mode", "paper")),
        "account_number": str(account.get("account_number") or account.get("account") or ""),
        "currency": str(account.get("currency", "USD")),
        "error": str(account.get("error", "")),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def _field_payload(
    definition: RuntimeConfigDefinition,
    value: object,
    origin: str,
) -> dict[str, object]:
    configured = bool(str(value).strip()) if definition.value_type == "str" else value is not None
    payload: dict[str, object] = {
        "key": definition.key,
        "label": definition.label or definition.key,
        "description": definition.description,
        "value_type": definition.value_type,
        "input_type": "password" if definition.secret else definition.value_type,
        "origin": origin,
        "env_name": definition.env_name,
        "secret": definition.secret,
        "group": definition.group,
        "configured": configured,
    }
    if definition.secret:
        payload["value"] = ""
        payload["display_value"] = "Configured" if configured else "Not configured"
    else:
        payload["value"] = value
    return payload


def _unknown_broker_snapshot(broker_name: str) -> dict[str, object]:
    return {
        "broker": broker_name,
        "connected": False,
        "configured": False,
        "mode": "paper",
        "status": "unknown",
        "account_number": "",
        "error": "broker_unavailable",
    }
