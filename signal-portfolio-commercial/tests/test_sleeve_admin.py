"""AD-03 "Research universe and sleeves" -- the sleeve-catalog admin
service's own tests. Runs through `tenant_session_factory` (the genuine
non-superuser `app_role` login) with `set_tenant_scope` explicitly
called, exactly like tests/test_product_admin.py, so cross-tenant
isolation is exercised against the real `sleeves` RLS policy, not just
this module's own `tenant_id` filter.
"""
import pytest

from app.db import set_tenant_scope
from app.models.tenancy import Tenant
from app.services.sleeve_admin import (
    InvalidSleeveDraftError,
    create_sleeve,
    get_sleeve,
    list_sleeves,
)

_VALID_FIELDS = dict(
    provider="north-star-research",
    analyst="m.chen",
    strategy_horizon="swing",
    asset_class="EQUITY",
    parser_version="v3",
    execution_policy_id="ep-standard",
    cost_model_id="cm-standard",
    capacity_policy_id="cap-standard",
    risk_unit_id="ru-1pct",
    history_origin="north-star-research",
)


def _seed_tenants(db_session):
    db_session.add_all(
        [
            Tenant(tenant_id="tenant-a", display_name="A", environment="LOCAL_SIM"),
            Tenant(tenant_id="tenant-b", display_name="B", environment="LOCAL_SIM"),
        ]
    )
    db_session.commit()


def test_list_sleeves_is_empty_for_a_fresh_tenant(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        assert list_sleeves(session, tenant_id="tenant-a") == []
    finally:
        session.rollback()
        session.close()


def test_create_sleeve_persists_and_reloads(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        sleeve = create_sleeve(session, tenant_id="tenant-a", **_VALID_FIELDS)
        session.commit()
        set_tenant_scope(session, "tenant-a")

        reloaded = get_sleeve(session, sleeve.sleeve_id, tenant_id="tenant-a")
        assert reloaded is not None
        assert reloaded.provider == "north-star-research"

        listed = list_sleeves(session, tenant_id="tenant-a")
        assert [s.sleeve_id for s in listed] == [sleeve.sleeve_id]
    finally:
        session.rollback()
        session.close()


def test_create_sleeve_rejects_a_missing_required_field(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        fields = dict(_VALID_FIELDS)
        fields["provider"] = "   "
        with pytest.raises(InvalidSleeveDraftError):
            create_sleeve(session, tenant_id="tenant-a", **fields)
    finally:
        session.rollback()
        session.close()


def test_create_sleeve_rejects_a_fully_omitted_required_field(db_session, tenant_session_factory):
    """Distinct from `..._rejects_a_missing_required_field` above, which
    only ever sends a BLANK (whitespace) value for the field -- never a
    field that is entirely absent from the kwargs. `missing` is built
    from `fields.get(key, "")`: if that default were anything non-blank
    (e.g. a mutant swapping `""` for some placeholder), a field omitted
    outright would slip past this validation, and `create_sleeve` would
    instead blow up with a raw `KeyError` out of the later dict
    comprehension (`fields[key]`) -- an unhandled, admin-facing crash
    instead of a clean `InvalidSleeveDraftError` the UI can render. This
    pins the real contract: a fully omitted field is rejected exactly
    the same way as a blank one."""
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        fields = dict(_VALID_FIELDS)
        del fields["provider"]
        with pytest.raises(InvalidSleeveDraftError):
            create_sleeve(session, tenant_id="tenant-a", **fields)
    finally:
        session.rollback()
        session.close()


def test_get_sleeve_cross_tenant_returns_none(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session_a = tenant_session_factory()
    try:
        set_tenant_scope(session_a, "tenant-a")
        sleeve = create_sleeve(session_a, tenant_id="tenant-a", **_VALID_FIELDS)
        session_a.commit()
        set_tenant_scope(session_a, "tenant-a")
        sleeve_id = sleeve.sleeve_id
    finally:
        session_a.close()

    session_b = tenant_session_factory()
    try:
        set_tenant_scope(session_b, "tenant-b")
        assert get_sleeve(session_b, sleeve_id, tenant_id="tenant-b") is None
        assert list_sleeves(session_b, tenant_id="tenant-b") == []
    finally:
        session_b.rollback()
        session_b.close()
