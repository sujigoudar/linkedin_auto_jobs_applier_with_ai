"""app/services/candidate_comparison.py::create_portfolio_version_draft_from_candidate
-- the version_number race fix: a database-level unique constraint on
(tenant_id, portfolio_id, version_number) (see
alembic/versions/a2c7e4f91b35_portfolio_versions_version_number_unique.py
and app/models/portfolio_version.py's own `__table_args__`), plus the
function's own catch-and-retry-once logic around it.

Three tests:
  1. A real, genuine two-DB-session race (tenant_session_factory gives
     two independent connections against the SAME database), using a
     monkeypatched pause point so the interleaving is deterministic
     instead of a flaky sleep-based race.
  2. A simpler single-session simulation of the same collision (a stale
     `existing_max` read, forced via monkeypatch, while a real
     conflicting row already exists), confirming the one retry recovers.
  3. A persistent conflict (every attempt stays stale) raises
     `PortfolioVersionNumberConflictError` rather than looping forever
     or leaking a raw IntegrityError.
"""
from __future__ import annotations

import threading
from datetime import datetime as real_datetime
from datetime import timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import set_tenant_scope
from app.models.portfolio_version import PortfolioVersion
from app.models.research_run import ResearchRun
from app.models.sleeve import Sleeve
from app.services.candidate_comparison import (
    PortfolioVersionNumberConflictError,
    create_portfolio_version_draft_from_candidate,
)

_TENANT_ID = "tenant-a"


def _seed_sleeve(session: Session, sleeve_id: str = "sleeve-x") -> None:
    """A real Sleeve row -- `PortfolioVersionSleeve.sleeve_id` is a
    foreign key to `sleeves.sleeve_id` (app/models/portfolio_version.py),
    so the function under test's own post-version sleeve-membership
    insert needs one real row to reference, independent of this test's
    own version_number race."""
    session.add(
        Sleeve(
            sleeve_id=sleeve_id,
            tenant_id=_TENANT_ID,
            provider="test-provider",
            analyst="test-analyst",
            strategy_horizon="swing",
            asset_class="equity",
            parser_version="v1",
            execution_policy_id="policy-1",
            cost_model_id="cost-1",
            capacity_policy_id="capacity-1",
            risk_unit_id="risk-1",
            history_origin="test-fixture",
        )
    )
    session.commit()


def _run(sleeve_id: str = "sleeve-x") -> ResearchRun:
    """A transient (never persisted) ResearchRun -- the function under
    test only ever reads `run.sleeve_ids`/`run.recipes` in memory
    (app/services/candidate_comparison.py's own `_candidates_for_run`),
    never queries ResearchRun back from the database, so a real row is
    unnecessary for these tests."""
    return ResearchRun(
        tenant_id=_TENANT_ID,
        sleeve_ids=[sleeve_id],
        recipes=["equal_capital"],
        subset_min=1,
        subset_max=1,
    )


def test_true_two_session_race_is_recovered_by_the_retry(tenant_session_factory, monkeypatch):
    """Two REAL, independent sessions (two separate Postgres connections
    against the same disposable cluster) racing to create the first
    version of the same (tenant_id, portfolio_id): session_other commits
    version_number=1 for real, in between session_under_test's own
    `existing_max` read (which therefore sees nothing) and its INSERT --
    exactly the interleaving the database-level unique constraint exists
    to catch, and the function's own retry exists to recover from."""
    portfolio_id = "race-portfolio-real-concurrency"
    session_other = tenant_session_factory()
    session_under_test = tenant_session_factory()
    try:
        set_tenant_scope(session_other, _TENANT_ID)
        set_tenant_scope(session_under_test, _TENANT_ID)
        _seed_sleeve(session_other)
        #: `_seed_sleeve`'s own `commit()` ends the transaction
        #: `set_tenant_scope`'s `set_config(..., true)` (transaction-LOCAL)
        #: was scoped to -- re-set it before session_other is used again
        #: below, same as the real app/api/dashboard_routes.py route
        #: handlers do after their own rollback/retry paths.
        set_tenant_scope(session_other, _TENANT_ID)

        reached_pause_point = threading.Event()
        other_session_committed = threading.Event()

        class _PausingDatetime:
            """Patches the one `datetime.now(...)` call the function
            makes between its `existing_max` SELECT and its INSERT
            (building `research_cutoff`) -- a real, deterministic pause
            point for the race, not a flaky `time.sleep`."""

            @staticmethod
            def now(tz=None):
                reached_pause_point.set()
                other_session_committed.wait(timeout=10)
                return real_datetime.now(tz)

        monkeypatch.setattr("app.services.candidate_comparison.datetime", _PausingDatetime)

        result: dict[str, int | str] = {}
        error: dict[str, BaseException] = {}

        def _call_under_test() -> None:
            try:
                version = create_portfolio_version_draft_from_candidate(
                    session_under_test,
                    tenant_id=_TENANT_ID,
                    research_run=_run(),
                    candidate_index=0,
                    portfolio_id=portfolio_id,
                    consent_disclosure_version="v1",
                    max_subscriber_capacity=10,
                )
                # Captured as plain values BEFORE commit: `commit()` expires
                # the ORM instance (`expire_on_commit=True`), and any later
                # attribute access would re-SELECT through
                # `session_under_test` in a brand-new transaction where
                # `set_tenant_scope`'s transaction-LOCAL GUC is no longer
                # set -- RLS would then (correctly) hide the very row this
                # call just committed.
                result["version_number"] = version.version_number
                result["portfolio_id"] = version.portfolio_id
                session_under_test.commit()
            except BaseException as exc:  # noqa: BLE001 - surfaced to the main thread below
                error["exc"] = exc

        thread = threading.Thread(target=_call_under_test)
        thread.start()
        assert reached_pause_point.wait(timeout=10), "function under test never reached its pause point"

        # session_other now genuinely wins the race for version_number=1,
        # independently of session_under_test, which is paused mid-call.
        session_other.add(
            PortfolioVersion(
                tenant_id=_TENANT_ID,
                portfolio_id=portfolio_id,
                version_number=1,
                cash_weight=Decimal("0"),
                research_cutoff=real_datetime.now(timezone.utc),
                max_subscriber_capacity=1,
                consent_disclosure_version="v1",
            )
        )
        session_other.commit()
        other_session_committed.set()

        thread.join(timeout=15)
        assert not thread.is_alive(), "function under test never returned"
        if "exc" in error:
            raise error["exc"]

        # session_under_test's first attempt collided with session_other's
        # already-committed version_number=1; its one retry re-read the
        # real max() and landed on 2.
        assert result["version_number"] == 2
        assert result["portfolio_id"] == portfolio_id
    finally:
        session_other.close()
        session_under_test.close()


def test_a_stale_read_colliding_with_an_existing_row_is_recovered_by_one_retry(db_session, monkeypatch):
    """A simpler, single-session simulation of the same race: forces the
    function's FIRST `existing_max` read to come back stale (empty) via
    monkeypatch, while a real, already-committed version_number=1 row
    sits in the table -- the first attempt's INSERT therefore collides
    with that real row, and the retry (a fresh, non-stale read) must
    recover with version_number=2."""
    portfolio_id = "race-portfolio-single-session"
    _seed_sleeve(db_session)
    db_session.add(
        PortfolioVersion(
            tenant_id=_TENANT_ID,
            portfolio_id=portfolio_id,
            version_number=1,
            cash_weight=Decimal("0"),
            research_cutoff=real_datetime.now(timezone.utc),
            max_subscriber_capacity=1,
            consent_disclosure_version="v1",
        )
    )
    db_session.commit()

    class _StaleOnceResult:
        @staticmethod
        def all():
            return []

    call_count = {"n": 0}
    real_scalars = Session.scalars

    def _scalars_stale_first_call(self, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _StaleOnceResult()
        return real_scalars(self, *args, **kwargs)

    monkeypatch.setattr(Session, "scalars", _scalars_stale_first_call)

    version = create_portfolio_version_draft_from_candidate(
        db_session,
        tenant_id=_TENANT_ID,
        research_run=_run(),
        candidate_index=0,
        portfolio_id=portfolio_id,
        consent_disclosure_version="v1",
        max_subscriber_capacity=10,
    )
    db_session.commit()

    assert call_count["n"] == 2  # the stale first read, then one real retry read
    assert version.version_number == 2

    stored = (
        db_session.scalars(
            select(PortfolioVersion.version_number)
            .where(PortfolioVersion.tenant_id == _TENANT_ID, PortfolioVersion.portfolio_id == portfolio_id)
            .order_by(PortfolioVersion.version_number)
        ).all()
    )
    assert stored == [1, 2]


def test_a_persistent_collision_raises_a_clear_specific_error_not_a_raw_db_error(db_session, monkeypatch):
    """When EVERY attempt's read stays stale (a genuinely persistent
    conflict -- not just one unlucky interleaving), the function must
    not silently swallow it or let a raw IntegrityError leak out: it
    raises `PortfolioVersionNumberConflictError` naming the portfolio
    and tenant."""
    portfolio_id = "race-portfolio-persistent-conflict"
    _seed_sleeve(db_session)
    for already_taken in (1, 2):
        db_session.add(
            PortfolioVersion(
                tenant_id=_TENANT_ID,
                portfolio_id=portfolio_id,
                version_number=already_taken,
                cash_weight=Decimal("0"),
                research_cutoff=real_datetime.now(timezone.utc),
                max_subscriber_capacity=1,
                consent_disclosure_version="v1",
            )
        )
    db_session.commit()

    class _AlwaysStaleResult:
        @staticmethod
        def all():
            return []  # every attempt "believes" version_number 1 is free

    monkeypatch.setattr(Session, "scalars", lambda self, *a, **k: _AlwaysStaleResult())

    with pytest.raises(PortfolioVersionNumberConflictError) as exc_info:
        create_portfolio_version_draft_from_candidate(
            db_session,
            tenant_id=_TENANT_ID,
            research_run=_run(),
            candidate_index=0,
            portfolio_id=portfolio_id,
            consent_disclosure_version="v1",
            max_subscriber_capacity=10,
        )
    assert portfolio_id in str(exc_info.value)
    assert _TENANT_ID in str(exc_info.value)

    # Undo the `Session.scalars` monkeypatch before verifying -- otherwise
    # this test's own verification query below would see the same fake
    # always-empty result the function under test did, rather than the
    # real database state.
    monkeypatch.undo()

    # Confirm nothing from the failed attempts was left half-committed.
    db_session.rollback()
    stored = (
        db_session.scalars(
            select(PortfolioVersion.version_number)
            .where(PortfolioVersion.tenant_id == _TENANT_ID, PortfolioVersion.portfolio_id == portfolio_id)
            .order_by(PortfolioVersion.version_number)
        ).all()
    )
    assert stored == [1, 2]
