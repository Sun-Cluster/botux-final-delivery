from __future__ import annotations

import asyncio
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.services.runtime_config.service import RuntimeConfigService


class Sec13FService:
    _cache: dict[str, tuple[datetime, Any]] = {}
    _MISSING = object()

    async def fetch_tracked_fund_rows(
        self,
        tracked_funds: tuple[dict[str, object], ...],
    ) -> list[dict[str, object]]:
        if not await self._network_enabled_runtime():
            return []
        settings = await self._settings()
        headers = {
            "User-Agent": settings["user_agent"],
            "Accept": "application/json, text/xml, application/xml;q=0.9, */*;q=0.8",
        }
        timeout = settings["timeout_seconds"]
        concurrency = settings["concurrency"]
        semaphore = asyncio.Semaphore(concurrency)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
            tasks = [self._fetch_fund_with_limit(client, semaphore, fund, settings) for fund in tracked_funds]
            rows = await asyncio.gather(*tasks, return_exceptions=True)
        payloads: list[dict[str, object]] = []
        for fund, row in zip(tracked_funds, rows, strict=False):
            if isinstance(row, dict):
                payloads.append(row)
                continue
            payloads.append(
                {
                    "fund": str(fund.get("fund", "")),
                    "cik": str(fund.get("cik", "")),
                    "weight": _as_float(fund.get("weight")),
                    "latest_date": None,
                    "new_filing": False,
                    "updated_at": _iso_now(),
                    "holdings": [],
                    "holdings_count": 0,
                    "error": type(row).__name__ if isinstance(row, Exception) else "fetch_failed",
                }
            )
        return payloads

    async def _settings(self) -> dict[str, object]:
        runtime = RuntimeConfigService()
        user_agent = await runtime.resolve("intel.sec_13f_user_agent")
        timeout = await runtime.resolve_float("intel.sec_13f_timeout_seconds")
        concurrency = await runtime.resolve("intel.sec_13f_concurrency")
        lookback_days = await runtime.resolve("intel.sec_13f_new_filing_lookback_days")
        return {
            "user_agent": str(user_agent.value or "BOTUX tradecopy support@example.com"),
            "timeout_seconds": max(1.0, float(timeout.value)),
            "concurrency": max(1, _int_value(concurrency.value, default=3)),
            "lookback_days": max(1, _int_value(lookback_days.value, default=7)),
        }

    async def _fetch_fund_with_limit(
        self,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        fund: dict[str, object],
        settings: dict[str, object],
    ) -> dict[str, object]:
        async with semaphore:
            return await self._fetch_fund_snapshot(client, fund, settings)

    async def _fetch_fund_snapshot(
        self,
        client: httpx.AsyncClient,
        fund: dict[str, object],
        settings: dict[str, object],
    ) -> dict[str, object]:
        fund_name = str(fund.get("fund", ""))
        cik = str(fund.get("cik", ""))
        weight = _as_float(fund.get("weight"))
        now = _iso_now()
        submissions = await self._submissions_payload(client, cik)
        filing = _latest_13f_filing(submissions)
        if filing is None:
            return {
                "fund": fund_name,
                "cik": cik,
                "weight": weight,
                "latest_date": None,
                "new_filing": False,
                "updated_at": now,
                "holdings": [],
                "holdings_count": 0,
            }
        accession_number = str(filing.get("accessionNumber", ""))
        filing_date = str(filing.get("filingDate", "")) or None
        primary_document = str(filing.get("primaryDocument", ""))
        archive_path = f"{int(cik)}/{accession_number.replace('-', '')}"
        index_payload = await self._archive_index_payload(client, archive_path)
        holdings = await self._archive_holdings(client, archive_path, index_payload, primary_document)
        return {
            "fund": fund_name,
            "cik": cik,
            "weight": weight,
            "latest_date": filing_date,
            "new_filing": _is_recent_filing(filing_date, lookback_days=_int_value(settings["lookback_days"], default=7)),
            "updated_at": now,
            "accession_number": accession_number,
            "form": str(filing.get("form", "")),
            "primary_document": primary_document,
            "holdings": holdings,
            "holdings_count": len(holdings),
        }

    async def _submissions_payload(self, client: httpx.AsyncClient, cik: str) -> dict[str, object]:
        cache_key = f"sec13f:submissions:{cik}"
        cached = self._get_cached(cache_key, ttl_seconds=21600)
        if isinstance(cached, dict):
            return cached
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        payload = await self._get_json(client, url)
        self._set_cache(cache_key, payload)
        return payload

    async def _archive_index_payload(self, client: httpx.AsyncClient, archive_path: str) -> dict[str, object]:
        cache_key = f"sec13f:index:{archive_path}"
        cached = self._get_cached(cache_key, ttl_seconds=21600)
        if isinstance(cached, dict):
            return cached
        url = f"https://www.sec.gov/Archives/edgar/data/{archive_path}/index.json"
        payload = await self._get_json(client, url)
        self._set_cache(cache_key, payload)
        return payload

    async def _archive_holdings(
        self,
        client: httpx.AsyncClient,
        archive_path: str,
        index_payload: dict[str, object],
        primary_document: str,
    ) -> list[dict[str, object]]:
        cache_key = f"sec13f:holdings:{archive_path}"
        cached = self._get_cached(cache_key, ttl_seconds=21600)
        if isinstance(cached, list):
            return cached
        filenames = _candidate_xml_names(index_payload=index_payload, primary_document=primary_document)
        for name in filenames:
            url = f"https://www.sec.gov/Archives/edgar/data/{archive_path}/{name}"
            xml_payload = await self._get_text(client, url)
            holdings = _parse_information_table(xml_payload)
            if holdings:
                self._set_cache(cache_key, holdings)
                return holdings
        self._set_cache(cache_key, [])
        return []

    async def _get_json(self, client: httpx.AsyncClient, url: str) -> dict[str, object]:
        response = await client.get(url)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            return {str(key): value for key, value in payload.items()}
        return {}

    async def _get_text(self, client: httpx.AsyncClient, url: str) -> str:
        response = await client.get(url)
        response.raise_for_status()
        return response.text

    def _network_enabled(self) -> bool:
        if os.getenv("PYTEST_CURRENT_TEST"):
            return False
        return True

    async def _network_enabled_runtime(self) -> bool:
        runtime = RuntimeConfigService()
        disable_live_fetch = await runtime.resolve_bool("intel.disable_live_fetch")
        if bool(disable_live_fetch.value):
            return False
        return self._network_enabled()

    @classmethod
    def _get_cached(cls, key: str, *, ttl_seconds: int) -> Any:
        cached = cls._cache.get(key)
        if cached is None:
            return cls._MISSING
        fetched_at, payload = cached
        if (datetime.now(timezone.utc) - fetched_at).total_seconds() > ttl_seconds:
            return cls._MISSING
        return payload

    @classmethod
    def _set_cache(cls, key: str, payload: Any) -> None:
        cls._cache[key] = (datetime.now(timezone.utc), payload)


def _candidate_xml_names(*, index_payload: dict[str, object], primary_document: str) -> list[str]:
    directory = index_payload.get("directory")
    if not isinstance(directory, dict):
        return []
    items = directory.get("item")
    if not isinstance(items, list):
        return []
    xml_names = [
        str(item.get("name", ""))
        for item in items
        if isinstance(item, dict) and str(item.get("name", "")).lower().endswith(".xml")
    ]
    xml_names = [name for name in xml_names if name]
    non_primary = [name for name in xml_names if name != primary_document]
    priority = sorted(non_primary, key=_xml_priority)
    if primary_document and primary_document in xml_names:
        priority.append(primary_document)
    for name in xml_names:
        if name not in priority:
            priority.append(name)
    return priority


def _xml_priority(name: str) -> tuple[int, str]:
    lowered = name.lower()
    if "information" in lowered or "infotable" in lowered:
        return (0, lowered)
    if re.fullmatch(r"\d+\.xml", lowered):
        return (1, lowered)
    if "primary" in lowered:
        return (3, lowered)
    return (2, lowered)


def _latest_13f_filing(submissions: dict[str, object]) -> dict[str, object] | None:
    filings = submissions.get("filings")
    if not isinstance(filings, dict):
        return None
    recent = filings.get("recent")
    if not isinstance(recent, dict):
        return None
    forms = recent.get("form")
    filing_dates = recent.get("filingDate")
    accession_numbers = recent.get("accessionNumber")
    primary_documents = recent.get("primaryDocument")
    if not all(isinstance(value, list) for value in (forms, filing_dates, accession_numbers, primary_documents)):
        return None
    for index, form in enumerate(forms):
        if str(form).upper() not in {"13F-HR", "13F-HR/A"}:
            continue
        return {
            "form": str(form),
            "filingDate": _list_value(filing_dates, index),
            "accessionNumber": _list_value(accession_numbers, index),
            "primaryDocument": _list_value(primary_documents, index),
        }
    return None


def _list_value(values: list[object], index: int) -> str:
    if index >= len(values):
        return ""
    return str(values[index] or "")


def _parse_information_table(payload: str) -> list[dict[str, object]]:
    if not payload.strip():
        return []
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return []
    namespace = _root_namespace(root)
    tables = root.findall(".//ns:infoTable", namespace) if namespace else root.findall(".//infoTable")
    if not tables:
        return []
    holdings: list[dict[str, object]] = []
    for table in tables:
        put_call = _child_text(table, "putCall", namespace)
        if put_call:
            continue
        issuer = _child_text(table, "nameOfIssuer", namespace)
        if not issuer:
            continue
        holdings.append(
            {
                "issuer": issuer,
                "title_of_class": _child_text(table, "titleOfClass", namespace),
                "cusip": _child_text(table, "cusip", namespace),
                "reported_value": _text_float(_child_text(table, "value", namespace)),
                "reported_shares": _text_float(_nested_child_text(table, ("shrsOrPrnAmt", "sshPrnamt"), namespace)),
                "share_type": _nested_child_text(table, ("shrsOrPrnAmt", "sshPrnamtType"), namespace),
            }
        )
    return holdings


def _root_namespace(root: ET.Element) -> dict[str, str]:
    if root.tag.startswith("{") and "}" in root.tag:
        return {"ns": root.tag[1 : root.tag.index("}")]}
    return {}


def _child_text(element: ET.Element, name: str, namespace: dict[str, str]) -> str:
    path = f"ns:{name}" if namespace else name
    child = element.find(path, namespace)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _nested_child_text(element: ET.Element, names: tuple[str, str], namespace: dict[str, str]) -> str:
    parent_name, child_name = names
    parent_path = f"ns:{parent_name}" if namespace else parent_name
    child_path = f"ns:{child_name}" if namespace else child_name
    parent = element.find(parent_path, namespace)
    if parent is None:
        return ""
    child = parent.find(child_path, namespace)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _text_float(value: str) -> float:
    if not value:
        return 0.0
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return 0.0


def _is_recent_filing(filing_date: str | None, *, lookback_days: int) -> bool:
    if not filing_date:
        return False
    try:
        parsed = datetime.strptime(filing_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return parsed >= datetime.now(timezone.utc) - timedelta(days=lookback_days)


def _int_value(value: object, *, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_float(value: object) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()
