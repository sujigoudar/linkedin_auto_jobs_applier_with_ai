"""Track 11 -- OperatingCost CRUD + tenant scoping, CSV import, and the
per-category/per-cost-center summary AD-12's Cost attribution panel
reads.
"""
from datetime import datetime, timezone

import pytest

from app.db import set_tenant_scope
from app.models.operating_cost import OperatingCostCategory, OperatingCostSource
from app.models.tenancy import Tenant
from app.services.operating_cost import (
    CsvImportError,
    InvalidOperatingCostError,
    create_operating_cost,
    delete_operating_cost,
    get_operating_cost,
    import_operating_costs_csv,
    list_operating_costs,
    summarize_operating_costs,
)

_JAN_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
_JAN_END = datetime(2026, 1, 31, 23, 59, 59, tzinfo=timezone.utc)


def _seed_tenants(db_session):
    db_session.add_all(
        [
            Tenant(tenant_id="tenant-a", display_name="A", environment="LOCAL_SIM"),
            Tenant(tenant_id="tenant-b", display_name="B", environment="LOCAL_SIM"),
        ]
    )
    db_session.commit()


def test_create_and_get_operating_cost(db_session):
    _seed_tenants(db_session)
    set_tenant_scope(db_session, "tenant-a")
    row = create_operating_cost(
        db_session,
        tenant_id="tenant-a",
        category=OperatingCostCategory.SUBSCRIPTION,
        vendor="Telegram Premium",
        amount_cents=500,
        currency="usd",
        period_start=_JAN_START,
        period_end=_JAN_END,
    )
    db_session.commit()

    fetched = get_operating_cost(db_session, row.cost_id, tenant_id="tenant-a")
    assert fetched is not None
    assert fetched.vendor == "Telegram Premium"
    assert fetched.entry_source == OperatingCostSource.MANUAL
    assert fetched.is_usage_estimate is False


def test_operating_cost_is_tenant_scoped(db_session):
    _seed_tenants(db_session)
    row = create_operating_cost(
        db_session,
        tenant_id="tenant-a",
        category=OperatingCostCategory.INFRASTRUCTURE,
        vendor="AWS",
        amount_cents=10000,
        period_start=_JAN_START,
        period_end=_JAN_END,
    )
    other = create_operating_cost(
        db_session,
        tenant_id="tenant-b",
        category=OperatingCostCategory.INFRASTRUCTURE,
        vendor="OCI",
        amount_cents=7000,
        period_start=_JAN_START,
        period_end=_JAN_END,
    )
    db_session.commit()

    # Service-layer tenant check: reading/deleting a real row under the
    # WRONG tenant_id is refused, independent of the database role.
    assert get_operating_cost(db_session, row.cost_id, tenant_id="tenant-a") is not None
    assert get_operating_cost(db_session, row.cost_id, tenant_id="tenant-b") is None
    assert list_operating_costs(db_session, tenant_id="tenant-b") == [other]
    assert delete_operating_cost(db_session, row.cost_id, tenant_id="tenant-b") is False


def test_operating_cost_row_level_security(db_session, tenant_session_factory):
    """The database's own enforcement, not just the service layer: a
    plain `app_role` connection scoped to tenant-a via `set_tenant_scope`
    can see only tenant-a's own `operating_costs` rows, even querying the
    table directly."""
    _seed_tenants(db_session)
    create_operating_cost(
        db_session, tenant_id="tenant-a", category=OperatingCostCategory.OTHER, vendor="X",
        amount_cents=100, period_start=_JAN_START, period_end=_JAN_END,
    )
    create_operating_cost(
        db_session, tenant_id="tenant-b", category=OperatingCostCategory.OTHER, vendor="Y",
        amount_cents=200, period_start=_JAN_START, period_end=_JAN_END,
    )
    db_session.commit()

    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        rows = list_operating_costs(session, tenant_id="tenant-a")
        assert [r.vendor for r in rows] == ["X"]
    finally:
        session.rollback()
        session.close()


def test_negative_amount_rejected(db_session):
    _seed_tenants(db_session)
    set_tenant_scope(db_session, "tenant-a")
    with pytest.raises(InvalidOperatingCostError):
        create_operating_cost(
            db_session,
            tenant_id="tenant-a",
            category=OperatingCostCategory.OTHER,
            vendor="X",
            amount_cents=-1,
            period_start=_JAN_START,
            period_end=_JAN_END,
        )


def test_period_end_before_start_rejected(db_session):
    _seed_tenants(db_session)
    set_tenant_scope(db_session, "tenant-a")
    with pytest.raises(InvalidOperatingCostError):
        create_operating_cost(
            db_session,
            tenant_id="tenant-a",
            category=OperatingCostCategory.OTHER,
            vendor="X",
            amount_cents=100,
            period_start=_JAN_END,
            period_end=_JAN_START,
        )


def test_delete_operating_cost(db_session):
    _seed_tenants(db_session)
    set_tenant_scope(db_session, "tenant-a")
    row = create_operating_cost(
        db_session,
        tenant_id="tenant-a",
        category=OperatingCostCategory.OTHER,
        vendor="X",
        amount_cents=100,
        period_start=_JAN_START,
        period_end=_JAN_END,
    )
    db_session.commit()
    assert delete_operating_cost(db_session, row.cost_id, tenant_id="tenant-a") is True
    assert get_operating_cost(db_session, row.cost_id, tenant_id="tenant-a") is None


def test_summarize_operating_costs_by_category_and_cost_center(db_session):
    _seed_tenants(db_session)
    set_tenant_scope(db_session, "tenant-a")
    create_operating_cost(
        db_session, tenant_id="tenant-a", category=OperatingCostCategory.SUBSCRIPTION, vendor="Slack",
        amount_cents=800, period_start=_JAN_START, period_end=_JAN_END, cost_center="sleeve-momentum",
    )
    create_operating_cost(
        db_session, tenant_id="tenant-a", category=OperatingCostCategory.SUBSCRIPTION, vendor="Twitter API",
        amount_cents=10000, period_start=_JAN_START, period_end=_JAN_END,  # no cost_center -- base/unattributed
    )
    create_operating_cost(
        db_session, tenant_id="tenant-a", category=OperatingCostCategory.INFRASTRUCTURE, vendor="AWS",
        amount_cents=5000, period_start=_JAN_START, period_end=_JAN_END,
    )
    db_session.commit()

    summary = summarize_operating_costs(db_session, tenant_id="tenant-a")
    assert len(summary.rows) == 3
    by_cat = {row.category: row.total_cents for row in summary.by_category}
    assert by_cat[OperatingCostCategory.SUBSCRIPTION.value] == 10800
    assert by_cat[OperatingCostCategory.INFRASTRUCTURE.value] == 5000
    by_center = {row.cost_center: row.total_cents for row in summary.by_cost_center}
    assert by_center["sleeve-momentum"] == 800
    assert by_center[None] == 15000  # unattributed base costs
    assert summary.total_cents_by_currency["usd"] == 15800


def test_import_operating_costs_csv_happy_path(db_session):
    _seed_tenants(db_session)
    set_tenant_scope(db_session, "tenant-a")
    csv_text = (
        "category,vendor,amount_cents,currency,cadence,period_start,period_end,description,cost_center\n"
        "subscription,Telegram,500,usd,monthly,2026-01-01,2026-01-31,,\n"
        "infrastructure,AWS,20000,usd,monthly,2026-01-01,2026-01-31,Base hosting,\n"
    )
    result = import_operating_costs_csv(db_session, tenant_id="tenant-a", csv_text=csv_text)
    db_session.commit()
    assert len(result.created) == 2
    rows = list_operating_costs(db_session, tenant_id="tenant-a")
    assert len(rows) == 2
    assert all(row.entry_source == OperatingCostSource.CSV_IMPORT for row in rows)


def test_import_operating_costs_csv_is_all_or_nothing_on_bad_row(db_session):
    _seed_tenants(db_session)
    set_tenant_scope(db_session, "tenant-a")
    csv_text = (
        "category,vendor,amount_cents,currency,cadence,period_start,period_end\n"
        "subscription,Telegram,500,usd,monthly,2026-01-01,2026-01-31\n"
        "not_a_real_category,Broken,100,usd,monthly,2026-01-01,2026-01-31\n"
    )
    with pytest.raises(CsvImportError):
        import_operating_costs_csv(db_session, tenant_id="tenant-a", csv_text=csv_text)
    db_session.commit()
    assert list_operating_costs(db_session, tenant_id="tenant-a") == []


def test_import_operating_costs_csv_missing_required_column(db_session):
    _seed_tenants(db_session)
    set_tenant_scope(db_session, "tenant-a")
    csv_text = "vendor,amount_cents\nTelegram,500\n"
    with pytest.raises(CsvImportError):
        import_operating_costs_csv(db_session, tenant_id="tenant-a", csv_text=csv_text)
