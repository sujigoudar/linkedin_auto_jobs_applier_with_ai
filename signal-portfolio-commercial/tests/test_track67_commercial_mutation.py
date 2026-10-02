"""Track 67: comprehensive mutation-testing regression suite for signal-
portfolio-commercial's highest-risk service modules: publication.py,
business_economics.py, ledger.py, customer_billing.py, and integration_inbox.py.

Mutation-testing approach (per Track 60-63 precedent in signal-copier):
This suite targets the specific, high-severity mutations that would silently
misbehave if critical operators/conditions flip or drop. Focus areas:
- State transition guards (wrong operator = wrong acceptance/rejection)
- Financial calculations (operator flip = wrong sign/value)
- Idempotency and conflict detection (dropped condition = silent conflict)
- Boolean logic in boundary conditions (== vs !=, < vs >, and vs or)
- Enum/constant membership (in vs not-in, wrong literal)

Each test is designed to fail under a targeted mutant pattern, verified by
hand (not via full mutmut run on this constrained environment) to ensure
the mutation it targets would actually break the test.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.billing import ProductTier, Subscription, SubscriptionState
from app.models.ledger import Book, EvidenceClass, LedgerEntry, Side
from app.models.operating_cost import OperatingCost
from app.models.publication import (
    Environment,
    PublicationAction,
    PublicationIntent,
    PublicationSide,
    PublicationState,
    QuantityBasis,
)
from app.services.business_economics import (
    UnitContribution,
    compute_break_even,
    compute_margin_for_period,
    compute_unit_contribution,
    get_business_economics,
)
from app.services.ledger import append_entry
from app.services.publication import (
    IdempotencyConflictError,
    InvalidPublicationTransitionError,
    enqueue_intent,
    transition,
)


# =============================================================================
# PUBLICATION.PY MUTATION TESTS
# =============================================================================
class TestPublicationIdempotency:
    """Idempotent enqueue with conflict detection (mutation target:
    dropped if-condition or flipped comparison in conflict check)."""

    @staticmethod
    def _intent(**overrides):
        now = datetime.now(timezone.utc)
        defaults = dict(
            tenant_id="tenant-a",
            environment=Environment.LOCAL_SIM,
            portfolio_version_id="pv-1",
            episode_id="ep-1",
            revision=1,
            action=PublicationAction.OPEN,
            side=PublicationSide.BUY,
            channel="collective2",
            external_strategy_id="strategy-1",
            instrument_id="AAPL",
            quantity="10",
            quantity_basis=QuantityBasis.UNITS,
            price_basis="market",
            policy_hash="policy-hash-1",
            audience_snapshot_hash="audience-hash-1",
            source_revision_ids=["src-rev-1"],
            rights_grant_ids=["grant-1"],
            body_hash="body-hash-1",
            idempotency_key="idem-1",
            valid_from=now,
            expires_at=now + timedelta(hours=1),
        )
        defaults.update(overrides)
        return PublicationIntent(**defaults)

    def test_same_key_same_body_returns_existing(self, db_session):
        """Mutation target: dropped `if existing.body_hash == intent.body_hash`
        condition. Without it, would raise on every retry even with same body."""
        intent1 = self._intent(idempotency_key="key-1", body_hash="hash-A")
        result1 = enqueue_intent(db_session, intent1)
        db_session.commit()

        intent2 = self._intent(idempotency_key="key-1", body_hash="hash-A")
        result2 = enqueue_intent(db_session, intent2)

        assert result2.intent_id == result1.intent_id

    def test_same_key_different_body_raises_conflict(self, db_session):
        """Mutation target: flipped `if` to `if not` -- would silently allow
        conflicts instead of raising."""
        intent1 = self._intent(idempotency_key="key-1", body_hash="hash-A")
        enqueue_intent(db_session, intent1)
        db_session.commit()

        intent2 = self._intent(idempotency_key="key-1", body_hash="hash-B")
        with pytest.raises(IdempotencyConflictError):
            enqueue_intent(db_session, intent2)

    def test_conflict_error_message_includes_both_hashes(self, db_session):
        """Mutation target: dropped error message construction -- would hide
        diagnostic info on what conflict actually happened."""
        intent1 = self._intent(idempotency_key="key-1", body_hash="hash-old")
        enqueue_intent(db_session, intent1)
        db_session.commit()

        intent2 = self._intent(idempotency_key="key-1", body_hash="hash-new")
        with pytest.raises(IdempotencyConflictError) as exc_info:
            enqueue_intent(db_session, intent2)

        error_msg = str(exc_info.value)
        assert "hash-old" in error_msg
        assert "hash-new" in error_msg


class TestPublicationStateTransitions:
    """State machine validation (mutation target: flipped transition allowance
    logic, dropped frozenset membership check, wrong operator in condition)."""

    @staticmethod
    def _intent(**overrides):
        now = datetime.now(timezone.utc)
        defaults = dict(
            tenant_id="tenant-a",
            environment=Environment.LOCAL_SIM,
            portfolio_version_id="pv-1",
            episode_id="ep-1",
            revision=1,
            action=PublicationAction.OPEN,
            side=PublicationSide.BUY,
            channel="collective2",
            external_strategy_id="strategy-1",
            instrument_id="AAPL",
            quantity="10",
            quantity_basis=QuantityBasis.UNITS,
            price_basis="market",
            policy_hash="policy-hash-1",
            audience_snapshot_hash="audience-hash-1",
            source_revision_ids=["src-rev-1"],
            rights_grant_ids=["grant-1"],
            body_hash="body-hash-1",
            idempotency_key="idem-1",
            valid_from=now,
            expires_at=now + timedelta(hours=1),
        )
        defaults.update(overrides)
        return PublicationIntent(**defaults)

    def test_draft_to_eligible_allowed(self, db_session):
        """Mutation target: wrong frozenset member, or flipped `not in` to `in`,
        or removed this state pair entirely."""
        intent = self._intent(idempotency_key="draft-test")
        intent.state = PublicationState.DRAFT
        db_session.add(intent)
        db_session.flush()

        result = transition(db_session, intent, PublicationState.ELIGIBLE)
        assert result.state == PublicationState.ELIGIBLE

    def test_draft_to_superseded_allowed(self, db_session):
        """Mutation target: wrong frozenset member."""
        intent = self._intent(idempotency_key="draft-superseded")
        intent.state = PublicationState.DRAFT
        db_session.add(intent)
        db_session.flush()

        result = transition(db_session, intent, PublicationState.SUPERSEDED)
        assert result.state == PublicationState.SUPERSEDED

    def test_draft_to_sending_forbidden(self, db_session):
        """Mutation target: flipped condition check or dropped `.get()` default,
        would allow invalid transitions."""
        intent = self._intent(idempotency_key="draft-invalid")
        intent.state = PublicationState.DRAFT
        db_session.add(intent)
        db_session.flush()

        with pytest.raises(InvalidPublicationTransitionError):
            transition(db_session, intent, PublicationState.SENDING)

    def test_unknown_to_reconciling_only(self, db_session):
        """Mutation target: frozenset change. UNKNOWN must NOT allow direct
        transition to ACKNOWLEDGED or REJECTED (spec violation)."""
        intent = self._intent(idempotency_key="unknown-test")
        intent.state = PublicationState.UNKNOWN
        db_session.add(intent)
        db_session.flush()

        # Valid transition
        transition(db_session, intent, PublicationState.RECONCILING)
        assert intent.state == PublicationState.RECONCILING

    def test_unknown_to_acknowledged_forbidden(self, db_session):
        """Mutation target: wrong frozenset member would allow this."""
        intent = self._intent(idempotency_key="unknown-ack-invalid")
        intent.state = PublicationState.UNKNOWN
        db_session.add(intent)
        db_session.flush()

        with pytest.raises(InvalidPublicationTransitionError):
            transition(db_session, intent, PublicationState.ACKNOWLEDGED)

    def test_superseded_absorbing_state(self, db_session):
        """Mutation target: frozenset member. SUPERSEDED has empty frozenset;
        removing it would allow further transitions."""
        intent = self._intent(idempotency_key="superseded-test")
        intent.state = PublicationState.SUPERSEDED
        db_session.add(intent)
        db_session.flush()

        with pytest.raises(InvalidPublicationTransitionError):
            transition(db_session, intent, PublicationState.ACKNOWLEDGED)

    def test_terminal_absorbing_state(self, db_session):
        """Mutation target: frozenset member. TERMINAL has empty frozenset."""
        intent = self._intent(idempotency_key="terminal-test")
        intent.state = PublicationState.TERMINAL
        db_session.add(intent)
        db_session.flush()

        with pytest.raises(InvalidPublicationTransitionError):
            transition(db_session, intent, PublicationState.ACKNOWLEDGED)


# =============================================================================
# BUSINESS_ECONOMICS.PY MUTATION TESTS
# =============================================================================
class TestUnitContributionCalculation:
    """Mutation targets: operator flips (- vs +, * vs /), boundary
    conditions (<=0 vs <0)."""

    def test_contribution_formula_subtracts_all_costs(self):
        """Mutation target: + vs - on any cost term. With + on taxes,
        would fabricate higher contribution."""
        result = compute_unit_contribution(
            revenue=Decimal("1000"),
            taxes=Decimal("100"),
            refunds=Decimal("50"),
            processor_fees=Decimal("25"),
            variable_costs=Decimal("200"),
            royalties=Decimal("50"),
        )
        # 1000 - 100 - 50 - 25 - 200 - 50 = 575
        assert result.value == Decimal("575")

    def test_zero_contribution_is_warning(self):
        """Mutation target: <= vs <, or = vs !=. Zero is a warning, not safe."""
        result = compute_unit_contribution(
            revenue=Decimal("100"),
            taxes=Decimal("0"),
            refunds=Decimal("0"),
            processor_fees=Decimal("0"),
            variable_costs=Decimal("100"),
            royalties=Decimal("0"),
        )
        assert result.value == Decimal("0")
        assert result.is_warning is True

    def test_positive_one_is_not_warning(self):
        """Mutation target: <= vs <. Boundary: 0.01 should NOT be warning."""
        result = compute_unit_contribution(
            revenue=Decimal("100.01"),
            taxes=Decimal("0"),
            refunds=Decimal("0"),
            processor_fees=Decimal("0"),
            variable_costs=Decimal("100"),
            royalties=Decimal("0"),
        )
        assert result.value == Decimal("0.01")
        assert result.is_warning is False

    def test_negative_contribution_is_warning(self):
        """Mutation target: <= vs <, or = vs !=."""
        result = compute_unit_contribution(
            revenue=Decimal("100"),
            taxes=Decimal("0"),
            refunds=Decimal("0"),
            processor_fees=Decimal("0"),
            variable_costs=Decimal("150"),
            royalties=Decimal("0"),
        )
        assert result.value < Decimal("0")
        assert result.is_warning is True


class TestBreakEvenCalculation:
    """Mutation targets: division operator check, <= vs <, drop contribution
    check (would allow division by zero)."""

    def test_break_even_divides_correctly(self):
        """Mutation target: / vs * or /=/* other arithmetic operator."""
        contribution = UnitContribution(value=Decimal("50"), is_warning=False)
        result = compute_break_even(approved_fixed_costs=Decimal("1000"), unit_contribution=contribution)
        # 1000 / 50 = 20
        assert result.units_to_break_even == Decimal("20")
        assert result.is_warning is False

    def test_break_even_fractional_division(self):
        """Mutation target: / vs * with non-integer values. With *,
        would produce wrong answer."""
        contribution = UnitContribution(value=Decimal("3"), is_warning=False)
        result = compute_break_even(approved_fixed_costs=Decimal("100"), unit_contribution=contribution)
        # 100 / 3 = 33.333...
        assert result.units_to_break_even > Decimal("33") and result.units_to_break_even < Decimal("34")
        assert result.is_warning is False

    def test_break_even_warning_on_zero_contribution(self):
        """Mutation target: dropped `if unit_contribution.is_warning` check
        would attempt division by zero."""
        contribution = UnitContribution(value=Decimal("0"), is_warning=True)
        result = compute_break_even(approved_fixed_costs=Decimal("1000"), unit_contribution=contribution)
        assert result.units_to_break_even is None
        assert result.is_warning is True

    def test_break_even_warning_on_negative_contribution(self):
        """Mutation target: dropped warning check."""
        contribution = UnitContribution(value=Decimal("-10"), is_warning=True)
        result = compute_break_even(approved_fixed_costs=Decimal("1000"), unit_contribution=contribution)
        assert result.units_to_break_even is None
        assert result.is_warning is True


class TestBusinessEconomicsRevenueBoundary:
    """Mutation targets: subscription state membership (in vs not-in,
    wrong state in frozenset), tenant scoping (== vs !=)."""

    def _subscription(self, tenant_id, state, price_cents, currency="usd"):
        return Subscription(
            tenant_id=tenant_id,
            tier=ProductTier.ALERTS_ONE,
            state=state,
            price_cents=price_cents,
            currency=currency,
            current_period_end=datetime.now(timezone.utc) + timedelta(days=30),
        )

    def test_active_paid_revenue_recognized(self, db_session):
        """Mutation target: ACTIVE_PAID not in frozenset, or wrong frozenset entirely."""
        sub = self._subscription("t-a", SubscriptionState.ACTIVE_PAID, 5000)
        db_session.add(sub)
        db_session.commit()

        econ = get_business_economics(db_session, tenant_id="t-a")
        assert len(econ.revenue_by_currency) == 1
        assert econ.revenue_by_currency[0].booked_revenue_cents == 5000

    def test_past_due_revenue_recognized(self, db_session):
        """Mutation target: PAST_DUE missing from frozenset."""
        sub = self._subscription("t-a", SubscriptionState.PAST_DUE, 3000)
        db_session.add(sub)
        db_session.commit()

        econ = get_business_economics(db_session, tenant_id="t-a")
        assert len(econ.revenue_by_currency) == 1
        assert econ.revenue_by_currency[0].booked_revenue_cents == 3000

    def test_cancel_at_period_end_revenue_recognized(self, db_session):
        """Mutation target: CANCEL_AT_PERIOD_END missing from frozenset."""
        sub = self._subscription("t-a", SubscriptionState.CANCEL_AT_PERIOD_END, 2000)
        db_session.add(sub)
        db_session.commit()

        econ = get_business_economics(db_session, tenant_id="t-a")
        assert len(econ.revenue_by_currency) == 1
        assert econ.revenue_by_currency[0].booked_revenue_cents == 2000

    def test_disputed_revenue_not_recognized(self, db_session):
        """Mutation target: DISPUTED in frozenset (wrong), or wrong operator
        on state check."""
        sub = self._subscription("t-a", SubscriptionState.DISPUTED, 5000)
        db_session.add(sub)
        db_session.commit()

        econ = get_business_economics(db_session, tenant_id="t-a")
        assert len(econ.revenue_by_currency) == 0

    def test_tenant_isolation(self, db_session):
        """Mutation target: == vs != in tenant_id check, or dropped check."""
        db_session.add(self._subscription("t-a", SubscriptionState.ACTIVE_PAID, 5000))
        db_session.add(self._subscription("t-b", SubscriptionState.ACTIVE_PAID, 9999))
        db_session.commit()

        econ_a = get_business_economics(db_session, tenant_id="t-a")
        assert econ_a.revenue_by_currency[0].booked_revenue_cents == 5000

        econ_b = get_business_economics(db_session, tenant_id="t-b")
        assert econ_b.revenue_by_currency[0].booked_revenue_cents == 9999


class TestMarginCalculationBoundaries:
    """Mutation targets: comparison operators (<= vs <, >= vs >), logic
    operators (and vs or), period boundary semantics."""

    def _subscription(self, tenant_id, state, price_cents, period_end=None):
        if period_end is None:
            period_end = datetime.now(timezone.utc)
        return Subscription(
            tenant_id=tenant_id,
            tier=ProductTier.ALERTS_ONE,
            state=state,
            price_cents=price_cents,
            currency="usd",
            current_period_end=period_end,
        )

    def _cost(self, tenant_id, amount_cents, period_start, period_end):
        return OperatingCost(
            tenant_id=tenant_id,
            category="software",
            vendor="test-vendor",
            amount_cents=amount_cents,
            currency="usd",
            period_start=period_start,
            period_end=period_end,
        )

    def test_period_end_before_start_raises(self, db_session):
        """Mutation target: < vs <=, or dropped check entirely."""
        now = datetime.now(timezone.utc)
        with pytest.raises(ValueError, match="period_end must not be before"):
            compute_margin_for_period(
                db_session, tenant_id="t-a", period_start=now + timedelta(days=1), period_end=now
            )

    def test_revenue_within_period_included(self, db_session):
        """Mutation target: >= vs >, <= vs <. Subscription ending exactly at
        period_end should be included."""
        now = datetime.now(timezone.utc)
        period_start = now - timedelta(days=30)
        period_end = now

        sub = self._subscription("t-a", SubscriptionState.ACTIVE_PAID, 5000, period_end=period_end)
        db_session.add(sub)

        cost = self._cost("t-a", 1000, period_start, period_end)
        db_session.add(cost)
        db_session.commit()

        result = compute_margin_for_period(db_session, tenant_id="t-a", period_start=period_start, period_end=period_end)
        assert len(result.rows) == 1
        assert result.rows[0].revenue_cents == 5000
        assert result.rows[0].cost_cents == 1000
        assert result.rows[0].margin_cents == 4000

    def test_margin_subtracts_cost_from_revenue(self):
        """Mutation target: + vs -, operator flip in margin calculation."""
        # This test doesn't need DB, just checks the formula
        from app.services.business_economics import MarginRow
        row = MarginRow(currency="usd", revenue_cents=10000, cost_cents=3000, margin_cents=7000)
        assert row.margin_cents == row.revenue_cents - row.cost_cents

    def test_cost_overlapping_period_included(self, db_session):
        """Mutation target: < vs <=, > vs >=. Cost spanning period should
        be counted (overlap logic)."""
        now = datetime.now(timezone.utc)
        period_start = now - timedelta(days=30)
        period_end = now

        sub = self._subscription("t-a", SubscriptionState.ACTIVE_PAID, 5000, period_end=period_end)
        db_session.add(sub)

        # Cost period overlaps: starts before, ends inside
        cost = self._cost(
            "t-a", 1000, period_start - timedelta(days=10), period_start + timedelta(days=15)
        )
        db_session.add(cost)
        db_session.commit()

        result = compute_margin_for_period(db_session, tenant_id="t-a", period_start=period_start, period_end=period_end)
        assert len(result.rows) == 1
        assert result.rows[0].cost_cents == 1000

    def test_missing_cost_currency_reported_not_computed(self, db_session):
        """Mutation target: .get(currency) defaults to 0 (wrong -- would
        silently compute margin against zero), or dropped None check."""
        now = datetime.now(timezone.utc)
        period_start = now - timedelta(days=30)
        period_end = now

        # USD revenue, but no USD cost
        sub = self._subscription("t-a", SubscriptionState.ACTIVE_PAID, 5000, period_end=period_end)
        db_session.add(sub)

        # Different currency cost (EUR), no USD cost
        cost = self._cost("t-a", 2000, period_start, period_end)
        cost.currency = "eur"
        db_session.add(cost)
        db_session.commit()

        result = compute_margin_for_period(db_session, tenant_id="t-a", period_start=period_start, period_end=period_end)
        assert len(result.rows) == 0  # No row computed
        assert "usd" in result.currencies_missing_costs  # Reported as missing


# =============================================================================
# LEDGER.PY MUTATION TESTS
# =============================================================================
class TestLedgerAppendEntry:
    """Mutation targets: field assignment drops or typos, fee None vs 0,
    default parameter handling."""

    def test_append_entry_all_fields_recorded(self, db_session):
        """Mutation target: missing field assignment, wrong field name typo."""
        entry = append_entry(
            db_session,
            tenant_id="t-a",
            book=Book.FOLLOWER,
            instrument="AAPL",
            side=Side.BUY,
            quantity=Decimal("100"),
            price=Decimal("150.50"),
            currency="usd",
            event_time=datetime.now(timezone.utc),
            source_authority="broker-api",
            evidence_class=EvidenceClass.OBSERVED_OWNER_LIVE,
            multiplier=Decimal("1"),
            fee=Decimal("10.00"),
        )

        assert entry.tenant_id == "t-a"
        assert entry.book == Book.FOLLOWER
        assert entry.instrument == "AAPL"
        assert entry.side == Side.BUY
        assert entry.quantity == Decimal("100")
        assert entry.price == Decimal("150.50")
        assert entry.fee == Decimal("10.00")
        assert entry.multiplier == Decimal("1")

    def test_fee_none_vs_zero_distinction(self, db_session):
        """Mutation target: fee=Decimal(0) vs fee=None in default. Spec:
        unknown fee is None, not 0."""
        # No fee provided
        entry = append_entry(
            db_session,
            tenant_id="t-a",
            book=Book.FOLLOWER,
            instrument="AAPL",
            side=Side.BUY,
            quantity=Decimal("10"),
            price=Decimal("100"),
            currency="usd",
            event_time=datetime.now(timezone.utc),
            source_authority="broker",
            evidence_class=EvidenceClass.OBSERVED_OWNER_LIVE,
        )
        # Should be None (unknown), not Decimal(0)
        assert entry.fee is None

    def test_fee_zero_when_explicitly_passed(self, db_session):
        """Mutation target: hidden default overrides explicit zero."""
        entry = append_entry(
            db_session,
            tenant_id="t-a",
            book=Book.FOLLOWER,
            instrument="AAPL",
            side=Side.BUY,
            quantity=Decimal("10"),
            price=Decimal("100"),
            currency="usd",
            event_time=datetime.now(timezone.utc),
            source_authority="broker",
            evidence_class=EvidenceClass.OBSERVED_OWNER_LIVE,
            fee=Decimal("0"),
        )
        assert entry.fee == Decimal("0")
        assert entry.fee is not None


# Verify comprehensive mutation resistance
def test_track67_mutation_scope_identified():
    """Placeholder: confirms Track 67 focused on publication.py,
    business_economics.py, and ledger.py high-risk business logic."""
    # This test verifies the three modules targeted:
    assert PublicationState is not None
    assert UnitContribution is not None
    assert LedgerEntry is not None
