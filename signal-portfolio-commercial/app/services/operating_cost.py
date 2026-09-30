"""Operating-cost CRUD + CSV import -- Track 11. See
app/models/operating_cost.py for what this table is and deliberately is
not (no live billing-API integration; manual/CSV entry only).

Live-billing-API cost pulling (AWS Cost Explorer, OCI usage API, the
Stripe processor-fee API, Telegram/Twitter/OpenAI/Anthropic usage
endpoints) is real, valuable future work this pass explicitly does NOT
build: it needs real vendor credentials this environment does not have,
and a fabricated integration would be worse than an honest manual-entry
form (the same "no live Stripe SDK call exists" discipline
app/services/stripe_webhook.py's own docstring already sets for
billing). Track it as a documented follow-up, not a half-built stub.

LLM usage: grepping this codebase (app/services/model_gateway.py, the
only model/LLM-shaped module here) finds a permission BOUNDARY only --
`ModelGatewayConfig`/`require_model_permitted_action`/
`requires_human_review` -- and no actual provider API call, no request/
token counter, and no structured usage log anywhere in this build (see
that module's own docstring: "No model provider API key exists in this
environment"). There is therefore nothing real to derive a usage-based
LLM cost estimate FROM yet. `record_llm_usage_estimate` below exists so
that a future real integration that DOES log token/request counts has
a single, honest place to wire an estimate in (flagged with
`is_usage_estimate=True`, never silently indistinguishable from a real
manual invoice entry) -- it is not called anywhere in this build today.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.operating_cost import (
    OperatingCost,
    OperatingCostCadence,
    OperatingCostCategory,
    OperatingCostSource,
)

_MAX_VENDOR_LENGTH = 200
_MAX_DESCRIPTION_LENGTH = 2000
_MAX_CSV_ROWS = 500


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InvalidOperatingCostError(Exception):
    pass


def _validate_common(
    *, vendor: str, amount_cents: int, currency: str, period_start: datetime, period_end: datetime
) -> None:
    if not vendor or not (1 <= len(vendor) <= _MAX_VENDOR_LENGTH):
        raise InvalidOperatingCostError(f"vendor must be 1..{_MAX_VENDOR_LENGTH} characters")
    if amount_cents < 0:
        raise InvalidOperatingCostError("amount_cents must be >= 0 -- a cost is never negative")
    if not currency:
        raise InvalidOperatingCostError("currency is required")
    if period_end < period_start:
        raise InvalidOperatingCostError("period_end must not be before period_start")


def create_operating_cost(
    session: Session,
    *,
    tenant_id: str,
    category: OperatingCostCategory | str,
    vendor: str,
    amount_cents: int,
    currency: str = "usd",
    cadence: OperatingCostCadence | str = OperatingCostCadence.MONTHLY,
    period_start: datetime,
    period_end: datetime,
    description: str | None = None,
    cost_center: str | None = None,
    entry_source: OperatingCostSource | str = OperatingCostSource.MANUAL,
    is_usage_estimate: bool = False,
    created_by_user_id: str | None = None,
) -> OperatingCost:
    category = OperatingCostCategory(category)
    cadence = OperatingCostCadence(cadence)
    entry_source = OperatingCostSource(entry_source)
    if description is not None and len(description) > _MAX_DESCRIPTION_LENGTH:
        raise InvalidOperatingCostError(f"description must be <= {_MAX_DESCRIPTION_LENGTH} characters")
    _validate_common(
        vendor=vendor, amount_cents=amount_cents, currency=currency, period_start=period_start, period_end=period_end
    )
    row = OperatingCost(
        tenant_id=tenant_id,
        category=category,
        vendor=vendor,
        description=description,
        amount_cents=amount_cents,
        currency=currency.lower(),
        cadence=cadence,
        period_start=period_start,
        period_end=period_end,
        cost_center=cost_center or None,
        entry_source=entry_source,
        is_usage_estimate=is_usage_estimate,
        created_by_user_id=created_by_user_id,
    )
    session.add(row)
    session.flush()
    return row


def list_operating_costs(
    session: Session,
    *,
    tenant_id: str,
    category: str | None = None,
    cost_center: str | None = None,
) -> list[OperatingCost]:
    query = select(OperatingCost).where(OperatingCost.tenant_id == tenant_id)
    if category:
        query = query.where(OperatingCost.category == category)
    if cost_center:
        query = query.where(OperatingCost.cost_center == cost_center)
    query = query.order_by(OperatingCost.period_start.desc(), OperatingCost.cost_id.desc())
    return list(session.scalars(query).all())


def get_operating_cost(session: Session, cost_id: str, *, tenant_id: str) -> OperatingCost | None:
    row = session.get(OperatingCost, cost_id)
    if row is None or row.tenant_id != tenant_id:
        return None
    return row


def delete_operating_cost(session: Session, cost_id: str, *, tenant_id: str) -> bool:
    row = get_operating_cost(session, cost_id, tenant_id=tenant_id)
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True


#: The exact CSV header this pass accepts -- deliberately small and
#: explicit rather than sniffing arbitrary vendor-export formats; a
#: vendor's own raw export is expected to be reshaped into this shape
#: first (a real "map vendor CSV -> this shape" importer is itself
#: follow-up work, not fabricated here).
_CSV_REQUIRED_COLUMNS = (
    "category", "vendor", "amount_cents", "currency", "cadence", "period_start", "period_end",
)
_CSV_OPTIONAL_COLUMNS = ("description", "cost_center")


class CsvImportError(Exception):
    pass


@dataclass(frozen=True)
class CsvImportResult:
    created: list[OperatingCost]
    row_errors: list[str]


def _parse_period(value: str, *, field: str, row_num: int) -> datetime:
    value = value.strip()
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CsvImportError(f"row {row_num}: {field} {value!r} is not a valid ISO-8601 date/datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def import_operating_costs_csv(
    session: Session,
    *,
    tenant_id: str,
    csv_text: str,
    created_by_user_id: str | None = None,
) -> CsvImportResult:
    """A bounded, all-or-nothing CSV import: every row must be valid or
    NOTHING is committed (no `.flush()` calls the caller's `.commit()`
    for a partial batch) -- a half-imported cost ledger is worse than
    refusing the whole file and telling the human exactly which row is
    wrong."""
    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames is None:
        raise CsvImportError("CSV file has no header row")
    missing = [c for c in _CSV_REQUIRED_COLUMNS if c not in reader.fieldnames]
    if missing:
        raise CsvImportError(f"CSV is missing required column(s): {', '.join(missing)}")

    rows = list(reader)
    if not rows:
        raise CsvImportError("CSV file has no data rows")
    if len(rows) > _MAX_CSV_ROWS:
        raise CsvImportError(f"CSV has {len(rows)} rows -- the limit is {_MAX_CSV_ROWS} per import")

    created: list[OperatingCost] = []
    for row_num, raw_row in enumerate(rows, start=2):  # header is row 1
        try:
            amount_raw = (raw_row.get("amount_cents") or "").strip()
            if not amount_raw:
                raise CsvImportError(f"row {row_num}: amount_cents is required")
            try:
                amount_cents = int(Decimal(amount_raw))
            except Exception as exc:  # noqa: BLE001
                raise CsvImportError(f"row {row_num}: amount_cents {amount_raw!r} is not a whole number") from exc

            category_raw = (raw_row.get("category") or "").strip()
            try:
                category = OperatingCostCategory(category_raw)
            except ValueError as exc:
                raise CsvImportError(f"row {row_num}: category {category_raw!r} is not recognized") from exc

            cadence_raw = (raw_row.get("cadence") or OperatingCostCadence.MONTHLY.value).strip()
            try:
                cadence = OperatingCostCadence(cadence_raw)
            except ValueError as exc:
                raise CsvImportError(f"row {row_num}: cadence {cadence_raw!r} is not recognized") from exc

            period_start = _parse_period(raw_row.get("period_start") or "", field="period_start", row_num=row_num)
            period_end = _parse_period(raw_row.get("period_end") or "", field="period_end", row_num=row_num)

            vendor = (raw_row.get("vendor") or "").strip()
            currency = (raw_row.get("currency") or "usd").strip() or "usd"

            created.append(
                create_operating_cost(
                    session,
                    tenant_id=tenant_id,
                    category=category,
                    vendor=vendor,
                    amount_cents=amount_cents,
                    currency=currency,
                    cadence=cadence,
                    period_start=period_start,
                    period_end=period_end,
                    description=(raw_row.get("description") or "").strip() or None,
                    cost_center=(raw_row.get("cost_center") or "").strip() or None,
                    entry_source=OperatingCostSource.CSV_IMPORT,
                    created_by_user_id=created_by_user_id,
                )
            )
        except (CsvImportError, InvalidOperatingCostError) as exc:
            session.rollback()
            raise CsvImportError(str(exc)) from exc

    return CsvImportResult(created=created, row_errors=[])


def record_llm_usage_estimate(
    session: Session,
    *,
    tenant_id: str,
    vendor: str,
    amount_cents: int,
    currency: str,
    period_start: datetime,
    period_end: datetime,
    cost_center: str | None,
    description: str,
) -> OperatingCost:
    """Not called anywhere in this build (see this module's own
    docstring: no real LLM call/usage log exists here to derive a
    number from). Exists as the one honest place a FUTURE integration
    that does log real token/request counts would wire an estimate in
    -- always `entry_source=CSV_IMPORT`-or-`MANUAL`-adjacent but flagged
    `is_usage_estimate=True`, so the UI can render "estimated from
    logged usage, not billing-reconciled" rather than presenting it as
    an actual vendor invoice."""
    return create_operating_cost(
        session,
        tenant_id=tenant_id,
        category=OperatingCostCategory.LLM_USAGE,
        vendor=vendor,
        amount_cents=amount_cents,
        currency=currency,
        cadence=OperatingCostCadence.ONE_TIME,
        period_start=period_start,
        period_end=period_end,
        description=description,
        cost_center=cost_center,
        entry_source=OperatingCostSource.MANUAL,
        is_usage_estimate=True,
    )


@dataclass(frozen=True)
class CostBreakdownRow:
    category: str
    total_cents: int
    currency: str


@dataclass(frozen=True)
class CostCenterBreakdownRow:
    cost_center: str | None
    total_cents: int
    currency: str


@dataclass(frozen=True)
class OperatingCostSummary:
    rows: list[OperatingCost]
    by_category: list[CostBreakdownRow]
    by_cost_center: list[CostCenterBreakdownRow]
    total_cents_by_currency: dict[str, int]


def summarize_operating_costs(session: Session, *, tenant_id: str) -> OperatingCostSummary:
    """AD-12 "Cost attribution" -- a real breakdown by category and (only
    where set) by cost center, computed entirely from rows a human has
    actually entered. Never mixes currencies in one total (the same
    per-currency discipline app/services/business_economics.py's own
    revenue query already applies)."""
    rows = list_operating_costs(session, tenant_id=tenant_id)

    by_category_totals: dict[tuple[str, str], int] = {}
    by_center_totals: dict[tuple[str | None, str], int] = {}
    total_by_currency: dict[str, int] = {}
    for row in rows:
        cat_key = (row.category.value, row.currency)
        by_category_totals[cat_key] = by_category_totals.get(cat_key, 0) + row.amount_cents
        center_key = (row.cost_center, row.currency)
        by_center_totals[center_key] = by_center_totals.get(center_key, 0) + row.amount_cents
        total_by_currency[row.currency] = total_by_currency.get(row.currency, 0) + row.amount_cents

    by_category = [
        CostBreakdownRow(category=cat, total_cents=total, currency=cur)
        for (cat, cur), total in sorted(by_category_totals.items())
    ]
    by_cost_center = [
        CostCenterBreakdownRow(cost_center=center, total_cents=total, currency=cur)
        for (center, cur), total in sorted(by_center_totals.items(), key=lambda kv: (kv[0][0] or "", kv[0][1]))
    ]
    return OperatingCostSummary(
        rows=rows, by_category=by_category, by_cost_center=by_cost_center, total_cents_by_currency=total_by_currency
    )
