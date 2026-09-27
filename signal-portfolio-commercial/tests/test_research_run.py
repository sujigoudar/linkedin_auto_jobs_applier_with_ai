"""AD-04 "Portfolio Lab builder" -- the research-run service's own
tests. Runs through `tenant_session_factory` + `set_tenant_scope` like
tests/test_sleeve_admin.py, since this reads real Sleeve rows those RLS
policies protect.
"""
from decimal import Decimal

import pytest

from app.db import set_tenant_scope
from app.models.sleeve import Sleeve
from app.models.tenancy import Tenant
from app.services.research_run import (
    InvalidResearchRunError,
    compute_research_run_preview,
    create_research_run,
    get_research_run,
    list_research_runs,
)

_VALID_KWARGS = dict(
    recipes=["equal_capital"],
    subset_min=2,
    subset_max=5,
    cash_bps=1500,
    max_sleeve_bps=3500,
    max_cluster_bps=5000,
    train_sessions=252,
    test_sessions=63,
    holdout_fraction=Decimal("0.20"),
    cost_scenario_ids=["scenario-1"],
    resource_profile_id="profile-1",
)


def _seed_tenants(db_session):
    db_session.add_all(
        [
            Tenant(tenant_id="tenant-a", display_name="A", environment="LOCAL_SIM"),
            Tenant(tenant_id="tenant-b", display_name="B", environment="LOCAL_SIM"),
        ]
    )
    db_session.commit()


def _sleeve(session, *, tenant_id, suffix):
    sleeve = Sleeve(
        tenant_id=tenant_id,
        provider=f"provider-{suffix}",
        analyst="jane",
        strategy_horizon="swing",
        asset_class="equity",
        parser_version="v1",
        execution_policy_id="ep-1",
        cost_model_id="cm-1",
        capacity_policy_id="cap-1",
        risk_unit_id="ru-1",
        history_origin=f"provider-{suffix}",
    )
    session.add(sleeve)
    session.flush()
    return sleeve


def test_list_research_runs_is_empty_for_a_fresh_tenant(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        assert list_research_runs(session, tenant_id="tenant-a") == []
    finally:
        session.rollback()
        session.close()


def test_create_research_run_persists_and_reloads(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        sleeve = _sleeve(session, tenant_id="tenant-a", suffix="1")
        run = create_research_run(session, tenant_id="tenant-a", sleeve_ids=[sleeve.sleeve_id], **_VALID_KWARGS)
        session.commit()
        set_tenant_scope(session, "tenant-a")

        reloaded = get_research_run(session, run.research_run_id, tenant_id="tenant-a")
        assert reloaded is not None
        assert reloaded.sleeve_ids == [sleeve.sleeve_id]

        listed = list_research_runs(session, tenant_id="tenant-a")
        assert [r.research_run_id for r in listed] == [run.research_run_id]
    finally:
        session.rollback()
        session.close()


def test_create_research_run_rejects_invalid_subset_bounds(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        kwargs = dict(_VALID_KWARGS)
        kwargs["subset_min"] = 5
        kwargs["subset_max"] = 2
        with pytest.raises(InvalidResearchRunError):
            create_research_run(session, tenant_id="tenant-a", sleeve_ids=[], **kwargs)
    finally:
        session.rollback()
        session.close()


def test_create_research_run_rejects_an_unknown_recipe_name(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        kwargs = dict(_VALID_KWARGS)
        kwargs["recipes"] = ["not_a_real_recipe"]
        with pytest.raises(InvalidResearchRunError):
            create_research_run(session, tenant_id="tenant-a", sleeve_ids=[], **kwargs)
    finally:
        session.rollback()
        session.close()


def test_get_research_run_cross_tenant_returns_none(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session_a = tenant_session_factory()
    try:
        set_tenant_scope(session_a, "tenant-a")
        run = create_research_run(session_a, tenant_id="tenant-a", sleeve_ids=[], **_VALID_KWARGS)
        session_a.commit()
        set_tenant_scope(session_a, "tenant-a")
        run_id = run.research_run_id
    finally:
        session_a.close()

    session_b = tenant_session_factory()
    try:
        set_tenant_scope(session_b, "tenant-b")
        assert get_research_run(session_b, run_id, tenant_id="tenant-b") is None
        assert list_research_runs(session_b, tenant_id="tenant-b") == []
    finally:
        session_b.rollback()
        session_b.close()


def test_preview_on_a_valid_run_computes_the_real_candidate_denominator(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        sleeves = [_sleeve(session, tenant_id="tenant-a", suffix=str(i)) for i in range(5)]
        run = create_research_run(
            session, tenant_id="tenant-a", sleeve_ids=[s.sleeve_id for s in sleeves], **_VALID_KWARGS
        )
        session.commit()
        set_tenant_scope(session, "tenant-a")

        preview = compute_research_run_preview(session, run, tenant_id="tenant-a")
        assert preview.blockers == []
        assert preview.declared_candidates_count > 0
        assert preview.benchmark_count == 5
        assert preview.expected_resource_budget_estimated == preview.declared_candidates_count + preview.benchmark_count
    finally:
        session.rollback()
        session.close()


def test_preview_flags_an_unimplemented_recipe(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        sleeve = _sleeve(session, tenant_id="tenant-a", suffix="1")
        kwargs = dict(_VALID_KWARGS)
        kwargs["recipes"] = ["hrp"]
        run = create_research_run(session, tenant_id="tenant-a", sleeve_ids=[sleeve.sleeve_id], **kwargs)
        session.commit()
        set_tenant_scope(session, "tenant-a")

        preview = compute_research_run_preview(session, run, tenant_id="tenant-a")
        assert "RECIPE_NOT_IMPLEMENTED:hrp" in preview.blockers
    finally:
        session.rollback()
        session.close()


def test_preview_flags_an_unknown_sleeve_and_a_cross_tenant_sleeve(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session_b = tenant_session_factory()
    try:
        set_tenant_scope(session_b, "tenant-b")
        other_tenant_sleeve = _sleeve(session_b, tenant_id="tenant-b", suffix="b")
        session_b.commit()
        set_tenant_scope(session_b, "tenant-b")
        other_sleeve_id = other_tenant_sleeve.sleeve_id
    finally:
        session_b.close()

    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        run = create_research_run(
            session,
            tenant_id="tenant-a",
            sleeve_ids=["nonexistent-sleeve", other_sleeve_id],
            **_VALID_KWARGS,
        )
        session.commit()
        set_tenant_scope(session, "tenant-a")

        preview = compute_research_run_preview(session, run, tenant_id="tenant-a")
        assert "UNKNOWN_SLEEVE:nonexistent-sleeve" in preview.blockers
        assert f"UNKNOWN_SLEEVE:{other_sleeve_id}" in preview.blockers
    finally:
        session.rollback()
        session.close()


def test_preview_flags_a_universe_larger_than_the_exhaustive_run_limit(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        sleeves = [_sleeve(session, tenant_id="tenant-a", suffix=str(i)) for i in range(13)]
        run = create_research_run(
            session, tenant_id="tenant-a", sleeve_ids=[s.sleeve_id for s in sleeves], **_VALID_KWARGS
        )
        session.commit()
        set_tenant_scope(session, "tenant-a")

        preview = compute_research_run_preview(session, run, tenant_id="tenant-a")
        assert any(b.startswith("TOO_MANY_ELIGIBLE_SLEEVES") for b in preview.blockers)
    finally:
        session.rollback()
        session.close()


def test_preview_flags_no_recipe_selected(db_session, tenant_session_factory):
    _seed_tenants(db_session)
    session = tenant_session_factory()
    try:
        set_tenant_scope(session, "tenant-a")
        kwargs = dict(_VALID_KWARGS)
        kwargs["recipes"] = []
        run = create_research_run(session, tenant_id="tenant-a", sleeve_ids=[], **kwargs)
        session.commit()
        set_tenant_scope(session, "tenant-a")

        preview = compute_research_run_preview(session, run, tenant_id="tenant-a")
        assert "NO_RECIPE_SELECTED" in preview.blockers
    finally:
        session.rollback()
        session.close()
