# SPDX-License-Identifier: AGPL-3.0-only
"""Reports: client-scoped report entities, lifecycle, AI narrative and PDF storage.

A report is a `pages` row with type = 'report' (see
supabase/migrations/20260926_001_reports_module.sql). Every route lives under
/py/clients/{client_id}/reports so the existing `require_client_role`
dependency resolves the caller's role on THAT client before anything runs, and
every query below additionally filters by client_id + type='report' + id, so a
report id from client B is simply "not found" when used under client A.

This module talks to Supabase with the service role (bypassing RLS), exactly
like the other backend modules, so authorization is enforced here - the
reports.* permission matrix in supabase_admin.ROLE_PERMISSION_MATRIX is the
single source of truth (view/download = viewer, create/edit/generate =
editor, archive = admin, delete = owner).

Reports are a presentation layer: this module never fetches or recalculates
marketing metrics. The browser loads them from the existing dashboard
endpoints and saves that snapshot with the report; the AI narrative is
generated from that saved snapshot only.
"""
import asyncio
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field

from .auth import verify_bearer_token
from .chat_service import get_ai_provider_config
from .client_access import get_client_team, require_client_role
from .core import logger
from .email_service import email_configured, send_email_with_attachment
from .report_ai import ReportAIError, generate_report_text
from .schemas import AuthUser
from .supabase_admin import (
    create_client_notification,
    create_user_notification,
    get_client_workspace,
    role_has_permission,
    supabase_admin_configured,
    supabase_rest_url,
    supabase_service_headers,
)

router = APIRouter(prefix="/py/clients/{client_id}/reports", tags=["reports"])

REPORT_BUCKET = "report-files"
REPORT_TYPE = "report"
REPORT_STATUSES = ("draft", "final", "sent", "archived")
REPORT_TEMPLATES = ("monthly", "weekly", "executive")
REPORT_SOURCES = ("google_analytics_4", "google_ads", "meta_ads", "hubspot_crm", "klaviyo", "shopify")
# The dashboard endpoints only accept these rolling windows (Google Ads and
# Meta map `days` onto fixed 7/14/30/90 presets), so reports offer the same.
PERIOD_DAYS = (7, 14, 30, 90)
SOURCE_PATTERN = "google_analytics_4|google_ads|meta_ads|hubspot_crm|klaviyo|shopify"
# chart:<source>:<metric> is one chart per source metric; the bare "chart" id is
# the legacy single "Performance" section kept so existing reports still load.
SECTION_RE = re.compile("^(summary|kpis|chart|tables|recommendations|commentary:(" + SOURCE_PATTERN + ")|chart:(" + SOURCE_PATTERN + "):[A-Za-z0-9_]{1,32})$")
CHART_SECTION_RE = re.compile("^chart:(" + SOURCE_PATTERN + "):[A-Za-z0-9_]{1,32}$")
CHART_TYPES = ("line", "area", "bar", "hbar")
# Same custom-range rules as the dashboards (py/app/ga4_dashboard.py).
MAX_CUSTOM_RANGE_DAYS = 365
EARLIEST_CUSTOM_DATE = date(2015, 8, 14)
SEND_DEDUPE_SECONDS = 60
_recent_sends: dict[str, float] = {}
NARRATIVE_SECTION_RE = re.compile(r"^(summary|recommendations|commentary:(google_analytics_4|google_ads|meta_ads|hubspot_crm|klaviyo|shopify))$")
MAX_CONTENT_BYTES = 1_500_000
MAX_PDF_BYTES = 15 * 1024 * 1024
MAX_NARRATIVE_CHARS = 6000

LIST_COLUMNS = (
    "id,client_id,organization_id,name,template,status,period_start,period_end,page_count,config,"
    "created_by,created_at,updated_at,pdf_path,pdf_version,pdf_generated_at,pdf_size_bytes"
)
FULL_COLUMNS = LIST_COLUMNS + ",content"
SORTS = {
    "updated": "updated_at.desc",
    "oldest": "updated_at.asc",
    "name": "name.asc",
    "period": "period_end.desc.nullslast",
    "status": "status.asc,updated_at.desc",
}

# from -> allowed targets, and the permission needed to make the move.
STATUS_TRANSITIONS: dict[tuple[str, str], str] = {
    ("draft", "final"): "reports.edit",
    ("final", "draft"): "reports.edit",
    ("draft", "archived"): "reports.manage",
    ("final", "archived"): "reports.manage",
    ("sent", "archived"): "reports.manage",
    ("archived", "draft"): "reports.manage",
    ("sent", "draft"): "reports.edit",
}


# ── Schemas ─────────────────────────────────────────────────────────────────

class SectionConfig(BaseModel):
    id: str
    enabled: bool = True


class ReportConfig(BaseModel):
    sources: list[str] = Field(default_factory=list)
    sections: list[SectionConfig] = Field(default_factory=list)
    periodDays: int = 30
    periodMode: Literal["preset", "custom"] = "preset"
    customStart: date | None = None
    customEnd: date | None = None
    chartTypes: dict[str, str] = Field(default_factory=dict)


class ReportCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    # Templates are no longer chosen when creating a report. Accepted (and
    # ignored) so older clients keep working; existing reports keep theirs.
    template: Literal["monthly", "weekly", "executive"] | None = None
    periodDays: int = 30
    periodMode: Literal["preset", "custom"] = "preset"
    customStart: date | None = None
    customEnd: date | None = None
    sources: list[str] = Field(default_factory=list)
    sections: list[SectionConfig] | None = None


class ReportUpdate(BaseModel):
    expectedUpdatedAt: str = Field(min_length=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    config: ReportConfig | None = None
    content: dict[str, Any] | None = None
    periodStart: date | None = None
    periodEnd: date | None = None


class StatusChange(BaseModel):
    status: Literal["draft", "final", "archived"]


class NarrativeRequest(BaseModel):
    section: str
    provider: str | None = None


class SendRequest(BaseModel):
    recipientIds: list[str] = Field(min_length=1, max_length=50)
    sendId: str | None = Field(default=None, max_length=64)


# ── Small helpers ───────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_permission(role: str | None, permission: str) -> None:
    if not role_has_permission(role, permission):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You don't have permission to do this on this client")


def _storage_url() -> str:
    base = os.getenv("SUPABASE_URL") or os.getenv("VITE_SUPABASE_URL")
    if not base:
        raise RuntimeError("SUPABASE_URL is not configured")
    return f"{base.rstrip('/')}/storage/v1"


def _service_available() -> None:
    if not supabase_admin_configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Reports are unavailable right now")


def _schema_missing(exc: Exception) -> bool:
    """True when Supabase says the Reports migration hasn't been applied:
    an unknown pages column (Postgres 42703) or the missing report-files bucket."""
    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    body = exc.response.text or ""
    return "42703" in body or "NoSuchBucket" in body or "Bucket not found" in body


def _upstream_error(exc: Exception) -> HTTPException:
    if _schema_missing(exc):
        logger.error("Reports database migration is not applied: %s", exc.response.text[:300])
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "migration_missing",
                "message": "Reports isn't set up on this database yet. An administrator needs to apply the 20260926_001_reports_module migration.",
            },
        )
    logger.exception("reports upstream failure: %s", exc)
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Reports service is temporarily unavailable. Try again.")


def _period_for(days: int, today: date | None = None) -> tuple[date, date]:
    end = today or datetime.now(timezone.utc).date()
    return end - timedelta(days=days - 1), end


def default_sections(template: str, sources: list[str]) -> list[dict[str, Any]]:
    """Ordered section list for a template, restricted to the report's sources.
    Commentary sections only exist for sources that are actually selected."""
    commentary = [{"id": f"commentary:{source}", "enabled": True} for source in sources]
    if template == "executive":
        return [
            {"id": "summary", "enabled": True},
            {"id": "kpis", "enabled": True},
            {"id": "recommendations", "enabled": True},
            {"id": "chart", "enabled": False},
            {"id": "tables", "enabled": False},
            *({**item, "enabled": False} for item in commentary),
        ]
    if template == "weekly":
        return [
            {"id": "summary", "enabled": True},
            {"id": "kpis", "enabled": True},
            {"id": "chart", "enabled": True},
            {"id": "tables", "enabled": False},
            *({**item, "enabled": False} for item in commentary),
            {"id": "recommendations", "enabled": False},
        ]
    return [
        {"id": "summary", "enabled": True},
        {"id": "kpis", "enabled": True},
        {"id": "chart", "enabled": True},
        {"id": "tables", "enabled": True},
        *commentary,
        {"id": "recommendations", "enabled": True},
    ]


def normalize_sections(sections: list[SectionConfig] | list[dict[str, Any]], sources: list[str]) -> list[dict[str, Any]]:
    """Validate ids, drop duplicates and commentary for unselected sources."""
    seen: set[str] = set()
    cleaned: list[dict[str, Any]] = []
    for raw in sections:
        item = raw if isinstance(raw, dict) else raw.model_dump()
        section_id = str(item.get("id", ""))
        if not SECTION_RE.match(section_id):
            raise HTTPException(status_code=422, detail=f"Unknown report section: {section_id[:40]}")
        if section_id in seen:
            continue
        if section_id.startswith("commentary:") and section_id.split(":", 1)[1] not in sources:
            continue
        if CHART_SECTION_RE.match(section_id) and section_id.split(":")[1] not in sources:
            continue
        seen.add(section_id)
        cleaned.append({"id": section_id, "enabled": bool(item.get("enabled", True))})
    return cleaned


def validate_period_days(days: int) -> int:
    if days not in PERIOD_DAYS:
        raise HTTPException(status_code=422, detail=f"Period must be one of {', '.join(str(d) for d in PERIOD_DAYS)} days")
    return days


def resolve_period(
    mode: str,
    days: int,
    custom_start: date | None,
    custom_end: date | None,
    today: date | None = None,
) -> tuple[str, int, date, date, str | None, str | None]:
    """Validates the report period and returns
    (mode, period_days, start, end, custom_start_iso, custom_end_iso).
    Presets are rolling windows ending today; a custom range is inclusive and
    follows the dashboards' rules (both dates, start <= end, not in the future,
    at most 365 days, nothing before the earliest supported date)."""
    if mode != "custom":
        validate_period_days(days)
        start, end = _period_for(days, today)
        return "preset", days, start, end, None, None
    if custom_start is None or custom_end is None:
        raise HTTPException(status_code=422, detail="Choose both a start and an end date for a custom range.")
    if custom_start > custom_end:
        raise HTTPException(status_code=422, detail="The start date must be on or before the end date.")
    now = today or datetime.now(timezone.utc).date()
    # One day of slack past UTC "today", like the dashboards, for users east of UTC.
    if custom_end > now + timedelta(days=1):
        raise HTTPException(status_code=422, detail="The end date can't be in the future.")
    if custom_start < EARLIEST_CUSTOM_DATE:
        raise HTTPException(status_code=422, detail=f"There is no data before {EARLIEST_CUSTOM_DATE.isoformat()}.")
    span = (custom_end - custom_start).days + 1
    if span > MAX_CUSTOM_RANGE_DAYS:
        raise HTTPException(status_code=422, detail=f"Custom ranges can be at most {MAX_CUSTOM_RANGE_DAYS} days.")
    return "custom", span, custom_start, custom_end, custom_start.isoformat(), custom_end.isoformat()


def normalize_chart_types(chart_types: dict[str, str], sections: list[dict[str, Any]]) -> dict[str, str]:
    """Chart type is per chart section; unknown types are rejected and entries
    for sections that no longer exist are dropped."""
    chart_ids = {item["id"] for item in sections if CHART_SECTION_RE.match(item["id"])}
    cleaned: dict[str, str] = {}
    for section_id, chart_type in (chart_types or {}).items():
        if chart_type not in CHART_TYPES:
            raise HTTPException(status_code=422, detail="Unknown chart type")
        if section_id in chart_ids:
            cleaned[section_id] = chart_type
    return cleaned


def build_config(sources: list[str], sections: list[dict[str, Any]], period: tuple, chart_types: dict[str, str]) -> dict[str, Any]:
    mode, days, _start, _end, custom_start, custom_end = period
    config: dict[str, Any] = {
        "sources": sources,
        "sections": sections,
        "periodDays": days,
        "periodMode": mode,
        "chartTypes": normalize_chart_types(chart_types, sections),
    }
    if mode == "custom":
        config["customStart"] = custom_start
        config["customEnd"] = custom_end
    return config


def validate_sources_shape(sources: list[str]) -> list[str]:
    unique = list(dict.fromkeys(sources))
    bad = [source for source in unique if source not in REPORT_SOURCES]
    if bad:
        raise HTTPException(status_code=422, detail=f"Unsupported report source: {bad[0][:40]}")
    return unique


def clean_content(content: dict[str, Any]) -> dict[str, Any]:
    """Only the keys the report uses, with bounded, control-character-free
    narrative text. Narratives are stored and rendered as PLAIN TEXT."""
    allowed_keys = {"narratives", "snapshot", "snapshotAt"}
    cleaned = {key: value for key, value in content.items() if key in allowed_keys}
    narratives = cleaned.get("narratives")
    if narratives is not None:
        if not isinstance(narratives, dict):
            raise HTTPException(status_code=422, detail="narratives must be an object")
        safe: dict[str, str] = {}
        for section_id, text in narratives.items():
            if not NARRATIVE_SECTION_RE.match(str(section_id)):
                raise HTTPException(status_code=422, detail="Unknown narrative section")
            if not isinstance(text, str):
                raise HTTPException(status_code=422, detail="Narrative text must be a string")
            stripped = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
            if len(stripped) > MAX_NARRATIVE_CHARS:
                raise HTTPException(status_code=422, detail="A narrative section is too long")
            safe[str(section_id)] = stripped
        cleaned["narratives"] = safe
    snapshot = cleaned.get("snapshot")
    if snapshot is not None and not isinstance(snapshot, dict):
        raise HTTPException(status_code=422, detail="snapshot must be an object")
    if len(repr(cleaned)) > MAX_CONTENT_BYTES:
        raise HTTPException(status_code=413, detail="This report has too much data to save")
    return cleaned


def serialize_report(row: dict[str, Any], *, include_content: bool = False) -> dict[str, Any]:
    payload = {
        "id": row["id"],
        "clientId": row["client_id"],
        "organizationId": row.get("organization_id"),
        "name": row.get("name"),
        "template": row.get("template"),
        "status": row.get("status") or "draft",
        "periodStart": row.get("period_start"),
        "periodEnd": row.get("period_end"),
        "pageCount": row.get("page_count"),
        "config": row.get("config") or {},
        "createdBy": row.get("created_by"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
        "pdf": {
            "available": bool(row.get("pdf_path")),
            "version": row.get("pdf_version") or 0,
            "generatedAt": row.get("pdf_generated_at"),
            "sizeBytes": row.get("pdf_size_bytes"),
        },
    }
    if include_content:
        payload["content"] = row.get("content") or {}
    return payload


def download_filename(client_name: str | None, report_name: str, period_end: str | None) -> str:
    def slug(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")[:60] or "Report"

    client_slug, name_slug = slug(client_name or "Client"), slug(report_name)
    # Default report names already start with the client's name.
    parts = [name_slug] if name_slug.lower().startswith(client_slug.lower()) else [client_slug, name_slug]
    if period_end:
        parts.append(period_end)
    return "_".join(parts) + ".pdf"


def _escape_like(value: str) -> str:
    return re.sub(r"[*,()\\]", " ", value).strip()


# ── Data access (service role, always scoped by client_id + type) ───────────

def _scope(client_id: str, report_id: str | None = None) -> dict[str, str]:
    params = {"client_id": f"eq.{client_id}", "type": f"eq.{REPORT_TYPE}"}
    if report_id:
        params["id"] = f"eq.{report_id}"
    return params


async def _get_report(client_id: str, report_id: str, *, columns: str = FULL_COLUMNS) -> dict[str, Any]:
    _service_available()
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            response = await http.get(
                f"{supabase_rest_url()}/pages",
                params={**_scope(client_id, report_id), "select": columns, "limit": "1"},
                headers=supabase_service_headers(),
            )
            response.raise_for_status()
            rows = response.json()
    except httpx.HTTPError as exc:
        raise _upstream_error(exc) from exc
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    return rows[0]


async def _patch_report(
    client_id: str,
    report_id: str,
    changes: dict[str, Any],
    *,
    expected_updated_at: str | None = None,
) -> dict[str, Any] | None:
    """Returns the updated row, or None when the optimistic-concurrency filter
    (or the scope) matched nothing."""
    params = _scope(client_id, report_id)
    if expected_updated_at:
        params["updated_at"] = f"eq.{expected_updated_at}"
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            response = await http.patch(
                f"{supabase_rest_url()}/pages",
                params={**params, "select": FULL_COLUMNS},
                headers=supabase_service_headers({"Prefer": "return=representation"}),
                json=changes,
            )
            response.raise_for_status()
            rows = response.json()
    except httpx.HTTPError as exc:
        raise _upstream_error(exc) from exc
    return rows[0] if rows else None


async def _connected_source_types(client_id: str) -> set[str]:
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            response = await http.get(
                f"{supabase_rest_url()}/data_sources",
                params={"select": "type", "client_id": f"eq.{client_id}", "status": "in.(connected,syncing)"},
                headers=supabase_service_headers(),
            )
            response.raise_for_status()
            return {row["type"] for row in response.json() if row.get("type")}
    except httpx.HTTPError as exc:
        raise _upstream_error(exc) from exc


async def _insert_report(row: dict[str, Any]) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            response = await http.post(
                f"{supabase_rest_url()}/pages",
                params={"select": FULL_COLUMNS},
                headers=supabase_service_headers({"Prefer": "return=representation"}),
                json=row,
            )
            response.raise_for_status()
            return response.json()[0]
    except httpx.HTTPError as exc:
        raise _upstream_error(exc) from exc


async def _storage_delete(paths: list[str]) -> None:
    if not paths:
        return
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            await http.request(
                "DELETE",
                f"{_storage_url()}/object/{REPORT_BUCKET}",
                headers=supabase_service_headers(),
                json={"prefixes": paths},
            )
    except Exception:  # noqa: BLE001 - orphan cleanup must never fail the action
        logger.exception("report file cleanup failed for %s", paths)


def _conflict(current_updated_at: str | None) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "report_conflict",
            "message": "This report was changed somewhere else since you opened it.",
            "currentUpdatedAt": current_updated_at,
        },
    )


# ── Routes ──────────────────────────────────────────────────────────────────

@router.get("")
async def list_reports(
    response: Response,
    client_id: str,
    status_filter: str | None = Query(default=None, alias="status"),
    q: str | None = Query(default=None, max_length=120),
    sort: str = Query(default="updated"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    role: str = Depends(require_client_role("viewer")),
):
    _require_permission(role, "reports.view")
    _service_available()
    if sort not in SORTS:
        raise HTTPException(status_code=422, detail="Unknown sort")
    params: dict[str, str] = {
        **_scope(client_id),
        "select": LIST_COLUMNS,
        "order": SORTS[sort],
        "limit": str(limit),
        "offset": str(offset),
    }
    if status_filter and status_filter != "all":
        if status_filter not in REPORT_STATUSES:
            raise HTTPException(status_code=422, detail="Unknown status")
        params["status"] = f"eq.{status_filter}"
    else:
        # The default Library hides archived reports; they stay retrievable.
        params["status"] = "neq.archived"
    search = _escape_like(q or "")
    if search:
        params["name"] = f"ilike.*{search}*"
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            upstream = await http.get(
                f"{supabase_rest_url()}/pages",
                params=params,
                headers=supabase_service_headers({"Prefer": "count=exact"}),
            )
            upstream.raise_for_status()
            rows = upstream.json()
            content_range = upstream.headers.get("content-range", "")
    except httpx.HTTPError as exc:
        raise _upstream_error(exc) from exc
    total = int(content_range.split("/")[-1]) if "/" in content_range and content_range.split("/")[-1].isdigit() else len(rows)
    response.headers["Cache-Control"] = "private, no-store"
    return {"reports": [serialize_report(row) for row in rows], "total": total, "limit": limit, "offset": offset}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_report(
    client_id: str,
    body: ReportCreate,
    user: AuthUser = Depends(verify_bearer_token),
    role: str = Depends(require_client_role("viewer")),
):
    _require_permission(role, "reports.edit")
    _service_available()
    period = resolve_period(body.periodMode, body.periodDays, body.customStart, body.customEnd)
    sources = validate_sources_shape(body.sources)
    if not sources:
        raise HTTPException(status_code=422, detail="Select at least one connected data source")
    connected = await _connected_source_types(client_id)
    missing = [source for source in sources if source not in connected]
    if missing:
        raise HTTPException(status_code=422, detail="One of the selected sources is not connected to this client")
    workspace = await get_client_workspace(client_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Client not found")
    sections = normalize_sections(body.sections, sources) if body.sections else default_sections(body.template or "monthly", sources)
    start, end = period[2], period[3]
    now = _now_iso()
    row = await _insert_report({
        "client_id": client_id,
        "organization_id": workspace["organization_id"],
        "type": REPORT_TYPE,
        "name": body.name.strip(),
        "template": body.template,
        "status": "draft",
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "config": build_config(sources, sections, period, {}),
        "content": {},
        "created_by": user.sub,
        "updated_by": user.sub,
        "updated_at": now,
    })
    return serialize_report(row, include_content=True)


@router.get("/{report_id}")
async def get_report(
    client_id: str,
    report_id: str,
    response: Response,
    role: str = Depends(require_client_role("viewer")),
):
    _require_permission(role, "reports.view")
    row = await _get_report(client_id, report_id)
    response.headers["Cache-Control"] = "private, no-store"
    return serialize_report(row, include_content=True)


@router.patch("/{report_id}")
async def update_report(
    client_id: str,
    report_id: str,
    body: ReportUpdate,
    user: AuthUser = Depends(verify_bearer_token),
    role: str = Depends(require_client_role("viewer")),
):
    _require_permission(role, "reports.edit")
    current = await _get_report(client_id, report_id, columns="id,status,updated_at,config")
    if current.get("status") != "draft":
        raise HTTPException(status_code=409, detail={"code": "report_locked", "message": "Only draft reports can be edited. Reopen it as a draft first."})
    changes: dict[str, Any] = {"updated_by": user.sub, "updated_at": _now_iso()}
    if body.name is not None:
        changes["name"] = body.name.strip()
    if body.config is not None:
        sources = validate_sources_shape(body.config.sources)
        if not sources:
            raise HTTPException(status_code=422, detail="Select at least one data source")
        connected = await _connected_source_types(client_id)
        previously = set((current.get("config") or {}).get("sources") or [])
        # Sources already in the report may stay (e.g. temporarily disconnected);
        # newly added ones must be connected right now.
        if any(source not in connected and source not in previously for source in sources):
            raise HTTPException(status_code=422, detail="One of the selected sources is not connected to this client")
        period = resolve_period(body.config.periodMode, body.config.periodDays, body.config.customStart, body.config.customEnd)
        sections = normalize_sections(body.config.sections, sources)
        changes["config"] = build_config(sources, sections, period, body.config.chartTypes)
    if body.content is not None:
        changes["content"] = clean_content(body.content)
    if body.periodStart is not None or body.periodEnd is not None:
        if body.periodStart is None or body.periodEnd is None or body.periodStart > body.periodEnd:
            raise HTTPException(status_code=422, detail="Invalid report period")
        changes["period_start"] = body.periodStart.isoformat()
        changes["period_end"] = body.periodEnd.isoformat()
    updated = await _patch_report(client_id, report_id, changes, expected_updated_at=body.expectedUpdatedAt)
    if updated is None:
        latest = await _get_report(client_id, report_id, columns="id,updated_at")
        raise _conflict(latest.get("updated_at"))
    return serialize_report(updated, include_content=True)


@router.post("/{report_id}/status")
async def change_report_status(
    client_id: str,
    report_id: str,
    body: StatusChange,
    user: AuthUser = Depends(verify_bearer_token),
    role: str = Depends(require_client_role("viewer")),
):
    current = await _get_report(client_id, report_id, columns="id,status,updated_at,content,config")
    source = current.get("status") or "draft"
    permission = STATUS_TRANSITIONS.get((source, body.status))
    if permission is None:
        raise HTTPException(status_code=409, detail=f"A {source} report cannot become {body.status}")
    _require_permission(role, permission)
    if body.status == "final" and not (current.get("content") or {}).get("snapshot"):
        raise HTTPException(status_code=409, detail="Load the report data before marking it final")
    updated = await _patch_report(
        client_id, report_id,
        {"status": body.status, "updated_by": user.sub, "updated_at": _now_iso()},
        expected_updated_at=current.get("updated_at"),
    )
    if updated is None:
        raise _conflict(None)
    return serialize_report(updated, include_content=True)


@router.post("/{report_id}/duplicate", status_code=status.HTTP_201_CREATED)
async def duplicate_report(
    client_id: str,
    report_id: str,
    user: AuthUser = Depends(verify_bearer_token),
    role: str = Depends(require_client_role("viewer")),
):
    _require_permission(role, "reports.edit")
    source = await _get_report(client_id, report_id, columns=LIST_COLUMNS)
    config = source.get("config") or {}
    connected = await _connected_source_types(client_id)
    sources = [item for item in (config.get("sources") or []) if item in connected]
    if not sources:
        raise HTTPException(status_code=409, detail="None of this report's data sources are connected any more")
    custom = config.get("periodMode") == "custom"
    try:
        period = resolve_period(
            "custom" if custom else "preset",
            config.get("periodDays") if config.get("periodDays") in PERIOD_DAYS else 30,
            date.fromisoformat(config["customStart"]) if custom and config.get("customStart") else None,
            date.fromisoformat(config["customEnd"]) if custom and config.get("customEnd") else None,
        )
    except HTTPException:
        period = resolve_period("preset", 30, None, None)
    start, end = period[2], period[3]
    workspace = await get_client_workspace(client_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Client not found")
    base = (source.get("name") or "Report")[:100]
    row = await _insert_report({
        "client_id": client_id,
        "organization_id": workspace["organization_id"],
        "type": REPORT_TYPE,
        "name": f"{base} (copy)",
        "template": source.get("template"),
        "status": "draft",
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        # Structure is copied; data snapshot, narratives and the PDF are NOT -
        # the copy must load fresh numbers and get fresh commentary.
        "config": build_config(
            sources,
            normalize_sections(config.get("sections") or default_sections(source.get("template") or "monthly", sources), sources),
            period,
            config.get("chartTypes") or {},
        ),
        "content": {},
        "created_by": user.sub,
        "updated_by": user.sub,
        "updated_at": _now_iso(),
    })
    return serialize_report(row, include_content=True)


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_report(
    client_id: str,
    report_id: str,
    role: str = Depends(require_client_role("viewer")),
):
    _require_permission(role, "reports.delete")
    current = await _get_report(client_id, report_id, columns="id,pdf_path")
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            response = await http.delete(
                f"{supabase_rest_url()}/pages",
                params=_scope(client_id, report_id),
                headers=supabase_service_headers(),
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise _upstream_error(exc) from exc
    await _storage_delete([current["pdf_path"]] if current.get("pdf_path") else [])
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{report_id}/narrative")
async def generate_narrative(
    client_id: str,
    report_id: str,
    body: NarrativeRequest,
    user: AuthUser = Depends(verify_bearer_token),
    role: str = Depends(require_client_role("viewer")),
):
    _require_permission(role, "reports.edit")
    if not NARRATIVE_SECTION_RE.match(body.section):
        raise HTTPException(status_code=422, detail="This section does not have AI commentary")
    report = await _get_report(client_id, report_id, columns="id,name,status,config,content,period_start,period_end")
    if report.get("status") != "draft":
        raise HTTPException(status_code=409, detail="Only draft reports can be edited")
    content = report.get("content") or {}
    if not (content.get("snapshot") or {}).get("sources"):
        raise HTTPException(status_code=409, detail={"code": "no_data", "message": "Load the report data before generating commentary."})
    workspace = await get_client_workspace(client_id)
    ai_config = await get_ai_provider_config(client_id, preferred_provider=body.provider, user_id=user.sub)
    if not ai_config:
        raise HTTPException(status_code=409, detail={"code": "no_ai_provider", "message": "Connect an AI provider to generate report narratives."})
    try:
        result = await asyncio.to_thread(
            generate_report_text,
            ai_config,
            section=body.section,
            client_name=(workspace or {}).get("name") or "the client",
            report_name=report.get("name") or "Report",
            period_start=report.get("period_start"),
            period_end=report.get("period_end"),
            snapshot=content["snapshot"],
        )
    except ReportAIError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail={"code": "ai_failed", "message": str(exc)}) from exc
    return {"section": body.section, **result}


@router.put("/{report_id}/pdf")
async def upload_report_pdf(
    client_id: str,
    report_id: str,
    request: Request,
    user: AuthUser = Depends(verify_bearer_token),
    role: str = Depends(require_client_role("viewer")),
):
    _require_permission(role, "reports.edit")
    current = await _get_report(client_id, report_id, columns="id,name,status,updated_at,pdf_path,pdf_version")
    if current.get("status") == "archived":
        raise HTTPException(status_code=409, detail="Archived reports cannot be exported. Restore it first.")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="The PDF is too large")
    data = await request.body()
    if not data.startswith(b"%PDF-"):
        raise HTTPException(status_code=422, detail="That file is not a PDF")
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="The PDF is too large")
    try:
        page_count = max(1, min(500, int(request.headers.get("x-page-count", "1"))))
    except ValueError:
        page_count = 1
    version = int(current.get("pdf_version") or 0) + 1
    path = f"{client_id}/{report_id}/v{version}.pdf"
    try:
        async with httpx.AsyncClient(timeout=60) as http:
            upload = await http.post(
                f"{_storage_url()}/object/{REPORT_BUCKET}/{path}",
                headers=supabase_service_headers({"Content-Type": "application/pdf", "x-upsert": "true"}),
                content=data,
            )
            upload.raise_for_status()
    except httpx.HTTPError as exc:
        raise _upstream_error(exc) from exc
    updated = await _patch_report(
        client_id, report_id,
        {
            "pdf_path": path,
            "pdf_version": version,
            "pdf_generated_at": _now_iso(),
            "pdf_size_bytes": len(data),
            "page_count": page_count,
            "updated_by": user.sub,
            "updated_at": _now_iso(),
        },
    )
    if updated is None:
        await _storage_delete([path])
        raise HTTPException(status_code=404, detail="Report not found")
    if current.get("pdf_path"):
        await _storage_delete([current["pdf_path"]])
    if request.headers.get("x-silent") == "1":
        # Regenerated only so an email carries the CURRENT report; not news.
        return serialize_report(updated, include_content=True)
    try:
        # Fired only here - a confirmed, stored PDF - and deduped per version,
        # so retries or repeated library refreshes can never repeat it.
        await create_client_notification(
            client_id,
            f"{current.get('name') or 'Report'} generated",
            f"A PDF of \"{current.get('name') or 'Report'}\" is ready to download.",
            event_type="report_generated",
            category="reports",
            action_url=f"/reports?report={report_id}",
            action_label="Open report",
            dedupe_key=f"report_generated:{report_id}:v{version}",
            metadata={"report_id": report_id, "version": version},
        )
    except Exception:  # noqa: BLE001 - a notification problem must never fail the export
        logger.exception("report_generated notification failed for report %s", report_id)
    return serialize_report(updated, include_content=True)


@router.get("/{report_id}/pdf")
async def download_report_pdf(
    client_id: str,
    report_id: str,
    role: str = Depends(require_client_role("viewer")),
):
    _require_permission(role, "reports.view")
    report = await _get_report(client_id, report_id, columns="id,name,period_end,pdf_path")
    path = report.get("pdf_path")
    if not path or not path.startswith(f"{client_id}/{report_id}/"):
        raise HTTPException(status_code=404, detail="This report has no PDF yet. Export it from the builder first.")
    try:
        async with httpx.AsyncClient(timeout=60) as http:
            upstream = await http.get(f"{_storage_url()}/object/{REPORT_BUCKET}/{path}", headers=supabase_service_headers())
    except httpx.HTTPError as exc:
        raise _upstream_error(exc) from exc
    if upstream.status_code in (400, 404):
        raise HTTPException(status_code=404, detail="The PDF file is missing. Export the report again.")
    if upstream.status_code >= 400:
        raise _upstream_error(RuntimeError(f"storage returned {upstream.status_code}"))
    workspace = await get_client_workspace(client_id)
    filename = download_filename((workspace or {}).get("name"), report.get("name") or "Report", report.get("period_end"))
    return Response(
        content=upstream.content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _member_display(member: dict[str, Any]) -> str:
    return (member.get("display_name") or (member.get("email") or "").split("@")[0] or "A teammate").strip()


@router.post("/{report_id}/send")
async def send_report_to_teammates(
    client_id: str,
    report_id: str,
    body: SendRequest,
    user: AuthUser = Depends(verify_bearer_token),
    role: str = Depends(require_client_role("viewer")),
):
    """Emails the report's stored PDF to selected teammates of THIS client.

    Recipients are re-validated here against the client's real team (the same
    client_team_members source Settings uses); ids from another client or
    organisation are rejected, never trusted from the browser. Each teammate
    gets their own email so nobody learns who else received it."""
    _require_permission(role, "reports.manage")
    if not email_configured():
        raise HTTPException(status_code=503, detail={"code": "email_unavailable", "message": "Email delivery isn't set up on this server yet."})
    report = await _get_report(client_id, report_id, columns="id,name,status,updated_at,period_end,pdf_path,organization_id")
    if report.get("status") == "archived":
        raise HTTPException(status_code=409, detail="Archived reports can't be sent. Restore it first.")
    path = report.get("pdf_path")
    if not path or not path.startswith(f"{client_id}/{report_id}/"):
        raise HTTPException(status_code=409, detail={"code": "no_pdf", "message": "This report has no PDF yet. Export it first."})

    team = await get_client_team(client_id, user.sub)
    by_id = {str(member.get("user_id")): member for member in team if member.get("user_id") and member.get("email")}
    wanted = list(dict.fromkeys(body.recipientIds))
    unknown = [item for item in wanted if item not in by_id]
    if unknown:
        raise HTTPException(status_code=403, detail={"code": "recipient_not_allowed", "message": "One or more recipients aren't members of this client."})

    # A double-click / retry storm with the same recipients must not email twice.
    key = f"{report_id}:{user.sub}:{body.sendId or ','.join(sorted(wanted))}"
    now = time.monotonic()
    for stale in [k for k, at in _recent_sends.items() if now - at > SEND_DEDUPE_SECONDS]:
        _recent_sends.pop(stale, None)
    if key in _recent_sends:
        return {"sent": len(wanted), "failed": [], "duplicate": True}
    _recent_sends[key] = now

    try:
        async with httpx.AsyncClient(timeout=60) as http:
            upstream = await http.get(f"{_storage_url()}/object/{REPORT_BUCKET}/{path}", headers=supabase_service_headers())
    except httpx.HTTPError as exc:
        _recent_sends.pop(key, None)
        raise _upstream_error(exc) from exc
    if upstream.status_code >= 400:
        _recent_sends.pop(key, None)
        raise HTTPException(status_code=404, detail={"code": "pdf_missing", "message": "The PDF file is missing. Export the report again."})
    pdf_bytes = upstream.content

    workspace = await get_client_workspace(client_id)
    client_name = (workspace or {}).get("name") or "your client"
    filename = download_filename(client_name, report.get("name") or "Report", report.get("period_end"))
    sender = (user.email or "A teammate").strip()
    sent_ids: list[str] = []
    failed: list[str] = []
    for recipient_id in wanted:
        member = by_id[recipient_id]
        ok = await send_email_with_attachment(
            member["email"],
            f"{report.get('name') or 'Report'} - {client_name}",
            f"{sender} shared the report \"{report.get('name') or 'Report'}\" for {client_name} with you.\n\nThe PDF is attached.",
            filename,
            pdf_bytes,
        )
        (sent_ids if ok else failed).append(recipient_id)

    if not sent_ids:
        _recent_sends.pop(key, None)
        raise HTTPException(status_code=502, detail={"code": "send_failed", "message": "Could not send the report. Try again."})

    if report.get("status") == "final":
        await _patch_report(client_id, report_id, {"status": "sent", "updated_by": user.sub, "updated_at": _now_iso()})
    try:
        await create_user_notification(
            sent_ids,
            report.get("organization_id"),
            f"{report.get('name') or 'Report'} was shared with you",
            f"{sender} sent you a report for {client_name}.",
            event_type="report_delivered",
            category="reports",
            client_id=client_id,
            action_url=f"/reports?report={report_id}&mode=view&client={client_id}",
            action_label="Open report",
            dedupe_key=f"report_delivered:{report_id}:{body.sendId or int(time.time())}",
            metadata={"report_id": report_id},
        )
    except Exception:  # noqa: BLE001 - the emails already went out
        logger.exception("report_delivered notification failed for report %s", report_id)
    return {"sent": len(sent_ids), "failed": [{"userId": item, "email": None} for item in failed], "duplicate": False}
