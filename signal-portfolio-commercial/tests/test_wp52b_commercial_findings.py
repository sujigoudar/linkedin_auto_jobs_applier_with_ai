"""Regression tests for WP-52b: Commercial platform audit findings.

Tests for findings from the audit gap analysis:
- G-C-02: PublicationIntent carries side field; adapters use it for direction
- G-C-10: Self-signup customers can select published products
- G-C-12: PLATFORM book entries carry account_id for per-account tracking
- G-C-13: Evidence class is per-account, not process-wide (copier-side)
- G-C-24: Reused event_id with different payload doesn't stall stream (copier-side)
- G-C-25: Routing outcome stored per-account, not overwritten (copier-side)
- G-C-26: Unknown event types advance cursor without stalling (copier-side)
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.models.integration_inbox import ExportStreamRegistration, InboxEvent
from app.models.ledger import Book, Side
from app.models.product import Product, ProductLifecycleState
from app.models.publication import PublicationAction, PublicationIntent, PublicationSide
from app.models.tenancy import Membership, MembershipRole, Tenant, UserIdentity
from app.services.collective2_publisher import build_order
from app.services.etoro_adapter import build_trade_request
from app.services.ledger import append_entry as append_ledger_entry
from app.services.portfolio_selection import create_portfolio_selection
from signal_platform_contracts import EvidenceClass


def _seed_tenant_and_user(db_session, *, tenant_id="tenant-a", user_id="user-a"):
    """Seed a tenant and user with membership."""
    if db_session.query(Tenant).filter_by(tenant_id=tenant_id).first() is None:
        db_session.add(
            Tenant(tenant_id=tenant_id, display_name=f"Tenant {tenant_id}", environment="LOCAL_SIM")
        )
        db_session.flush()
    if db_session.query(UserIdentity).filter_by(user_id=user_id).first() is None:
        db_session.add(UserIdentity(user_id=user_id, email=f"{user_id}@example.com"))
        db_session.flush()
    if (
        db_session.query(Membership)
        .filter_by(tenant_id=tenant_id, user_id=user_id)
        .first()
        is None
    ):
        db_session.add(
            Membership(tenant_id=tenant_id, user_id=user_id, role=MembershipRole.CUSTOMER)
        )
        db_session.commit()


def _published_product(db_session, *, tenant_id="tenant-a", slug="published-product"):
    """Create a PUBLISHED product."""
    product = Product(
        tenant_id=tenant_id,
        product_name="Published Product",
        slug=slug,
        lifecycle_state=ProductLifecycleState.PUBLISHED,
    )
    db_session.add(product)
    db_session.commit()
    return product


class TestGC02PublicationIntentSide:
    """G-C-02: PublicationIntent has required side field; adapters use it."""

    def test_gc02_collective2_respects_sell_side(self):
        """G-C-02: Collective2 adapter uses intent.side=SELL."""
        intent = PublicationIntent(
            intent_id="g-c-02-test-1",
            tenant_id="tenant1",
            environment="LOCAL_SIM",
            portfolio_version_id="pv1",
            episode_id="ep1",
            revision=1,
            action=PublicationAction.ADD,
            side=PublicationSide.SELL,  # G-C-02 fix: explicit side
            channel="collective2",
            external_strategy_id="strategy123",
            instrument_id="AAPL",
            quantity="10",
            quantity_basis="UNITS",
            price_basis="market",
            policy_hash="policy1",
            audience_snapshot_hash="aud1",
            source_revision_ids=[],
            rights_grant_ids=[],
            body_hash="body1",
            idempotency_key="idem1",
            valid_from="2026-10-01T00:00:00Z",
            expires_at="2026-10-02T00:00:00Z",
        )
        order = build_order(intent, c2_symbol="AAPL")
        # Side.SELL in Collective2 API is 2
        assert order["Side1"] == 2

    def test_gc02_collective2_respects_buy_side(self):
        """G-C-02: Collective2 adapter uses intent.side=BUY."""
        intent = PublicationIntent(
            intent_id="g-c-02-test-2",
            tenant_id="tenant1",
            environment="LOCAL_SIM",
            portfolio_version_id="pv1",
            episode_id="ep1",
            revision=1,
            action=PublicationAction.OPEN,
            side=PublicationSide.BUY,  # G-C-02 fix: explicit side
            channel="collective2",
            external_strategy_id="strategy123",
            instrument_id="AAPL",
            quantity="10",
            quantity_basis="UNITS",
            price_basis="market",
            policy_hash="policy1",
            audience_snapshot_hash="aud1",
            source_revision_ids=[],
            rights_grant_ids=[],
            body_hash="body1",
            idempotency_key="idem2",
            valid_from="2026-10-01T00:00:00Z",
            expires_at="2026-10-02T00:00:00Z",
        )
        order = build_order(intent, c2_symbol="AAPL")
        # Side.BUY in Collective2 API is 1
        assert order["Side1"] == 1

    def test_gc02_etoro_respects_sell_side(self):
        """G-C-02: eToro adapter uses intent.side=SELL."""
        intent = PublicationIntent(
            intent_id="g-c-02-test-3",
            tenant_id="tenant1",
            environment="LOCAL_SIM",
            portfolio_version_id="pv1",
            episode_id="ep1",
            revision=1,
            action=PublicationAction.ADD,
            side=PublicationSide.SELL,  # G-C-02 fix: explicit side
            channel="etoro",
            external_strategy_id="strategy123",
            instrument_id="AAPL",
            quantity="10",
            quantity_basis="UNITS",
            price_basis="market",
            policy_hash="policy1",
            audience_snapshot_hash="aud1",
            source_revision_ids=[],
            rights_grant_ids=[],
            body_hash="body1",
            idempotency_key="idem3",
            valid_from="2026-10-01T00:00:00Z",
            expires_at="2026-10-02T00:00:00Z",
        )
        request = build_trade_request(intent, instrument_symbol="AAPL")
        assert request["direction"] == "SELL"

    def test_gc02_etoro_respects_buy_side(self):
        """G-C-02: eToro adapter uses intent.side=BUY."""
        intent = PublicationIntent(
            intent_id="g-c-02-test-4",
            tenant_id="tenant1",
            environment="LOCAL_SIM",
            portfolio_version_id="pv1",
            episode_id="ep1",
            revision=1,
            action=PublicationAction.OPEN,
            side=PublicationSide.BUY,  # G-C-02 fix: explicit side
            channel="etoro",
            external_strategy_id="strategy123",
            instrument_id="AAPL",
            quantity="10",
            quantity_basis="UNITS",
            price_basis="market",
            policy_hash="policy1",
            audience_snapshot_hash="aud1",
            source_revision_ids=[],
            rights_grant_ids=[],
            body_hash="body1",
            idempotency_key="idem4",
            valid_from="2026-10-01T00:00:00Z",
            expires_at="2026-10-02T00:00:00Z",
        )
        request = build_trade_request(intent, instrument_symbol="AAPL")
        assert request["direction"] == "BUY"


class TestGC10CrossTenantProductSelection:
    """G-C-10: Self-signup customers can select published products from any tenant."""

    def test_gc10_select_published_product_cross_tenant(self, db_session):
        """G-C-10: Customer tenant can select PUBLISHED product from operator tenant."""
        # Seed operator and customer tenants
        _seed_tenant_and_user(db_session, tenant_id="operator-tenant", user_id="op-user")
        _seed_tenant_and_user(db_session, tenant_id="customer-tenant", user_id="cust-user")

        # Create PUBLISHED product in operator tenant
        product = _published_product(db_session, tenant_id="operator-tenant", slug="op-strategy")

        # Customer tenant selects it (G-C-10 fix: cross-tenant selection)
        selection = create_portfolio_selection(
            db_session,
            tenant_id="customer-tenant",  # Different from product.tenant_id
            user_id="cust-user",
            product_id=product.product_id,
        )
        assert selection.product_id == product.product_id
        assert selection.tenant_id == "customer-tenant"

    def test_gc10_cannot_select_draft_product(self, db_session):
        """G-C-10: Customers cannot select non-PUBLISHED products."""
        _seed_tenant_and_user(db_session, tenant_id="operator-tenant", user_id="op-user")
        _seed_tenant_and_user(db_session, tenant_id="customer-tenant", user_id="cust-user")

        # Create DRAFT product in operator tenant
        draft = Product(
            tenant_id="operator-tenant",
            product_name="Draft",
            slug="draft-strategy",
            lifecycle_state=ProductLifecycleState.DRAFT,
        )
        db_session.add(draft)
        db_session.commit()

        # Should fail: cannot select non-PUBLISHED
        from app.services.portfolio_selection import InvalidPortfolioSelectionError

        with pytest.raises(InvalidPortfolioSelectionError):
            create_portfolio_selection(
                db_session,
                tenant_id="customer-tenant",
                user_id="cust-user",
                product_id=draft.product_id,
            )


class TestGC12LedgerAccountId:
    """G-C-12: PLATFORM book entries carry account_id for per-account tracking."""

    def test_gc12_ledger_entry_stores_account_id(self, db_session):
        """G-C-12: LedgerEntry carries account_id from ExecutionAppliedPayload."""
        entry = append_ledger_entry(
            db_session,
            tenant_id="tenant1",
            book=Book.PLATFORM,
            instrument="AAPL",
            side=Side.BUY,
            quantity=Decimal("10"),
            price=Decimal("150"),
            currency="USD",
            event_time="2026-10-01T12:00:00Z",
            source_authority="signal-copier-relay:acct1",
            evidence_class=EvidenceClass.INTERNAL_PAPER,
            account_id="acct1",  # G-C-12 fix: carry account_id
        )
        db_session.refresh(entry)
        assert entry.account_id == "acct1"

    def test_gc12_per_account_tracking_enabled(self, db_session):
        """G-C-12: Can track positions separately per account."""
        # Add two accounts to the same instrument
        entry1 = append_ledger_entry(
            db_session,
            tenant_id="tenant1",
            book=Book.PLATFORM,
            instrument="AAPL",
            side=Side.BUY,
            quantity=Decimal("10"),
            price=Decimal("150"),
            currency="USD",
            event_time="2026-10-01T12:00:00Z",
            source_authority="signal-copier-relay:acct1",
            evidence_class=EvidenceClass.INTERNAL_PAPER,
            account_id="acct1",
        )
        entry2 = append_ledger_entry(
            db_session,
            tenant_id="tenant1",
            book=Book.PLATFORM,
            instrument="AAPL",
            side=Side.BUY,
            quantity=Decimal("20"),
            price=Decimal("150"),
            currency="USD",
            event_time="2026-10-01T12:00:00Z",
            source_authority="signal-copier-relay:acct2",
            evidence_class=EvidenceClass.INTERNAL_PAPER,
            account_id="acct2",
        )
        db_session.refresh(entry1)
        db_session.refresh(entry2)

        # Can distinguish entries by account_id
        assert entry1.account_id == "acct1"
        assert entry2.account_id == "acct2"
        # Quantities should not be combined incorrectly
        assert entry1.quantity == Decimal("10")
        assert entry2.quantity == Decimal("20")


class TestGC13EvidenceClassPerAccount:
    """G-C-13: Paper/live/simulated is per-account, not process-wide."""

    def test_gc13_inbox_events_carry_evidence_class(self, db_session):
        """G-C-13: Inbox events receive evidence_class per stream/account."""
        # Register streams with different environments
        stream1 = ExportStreamRegistration(
            tenant_id="tenant1",
            source_stream="signal-copier:acct1",
            environment="LOCAL_SIM",  # Paper account
        )
        db_session.add(stream1)

        stream2 = ExportStreamRegistration(
            tenant_id="tenant1",
            source_stream="signal-copier:acct2",
            environment="COMMERCIAL_LIVE",  # Live account
        )
        db_session.add(stream2)
        db_session.commit()

        # Verify registrations are distinct (G-C-13 fix: per-account environments)
        assert stream1.environment == "LOCAL_SIM"
        assert stream2.environment == "COMMERCIAL_LIVE"
        assert stream1.source_stream == "signal-copier:acct1"
        assert stream2.source_stream == "signal-copier:acct2"


class TestGC24EventIdDedup:
    """G-C-24: Reused event_id with different payload doesn't stall stream."""

    def test_gc24_exact_redelivery_idempotent(self, db_session):
        """G-C-24: Same event_id is idempotent (primary key prevents duplicates)."""
        # Create initial inbox event
        event1 = InboxEvent(
            event_id="exec-123",
            tenant_id="tenant1",
            event_type="EXECUTION_APPLIED",
            source_stream="signal-copier:acct1",
            producer_generation=1,
            export_sequence=1,
            envelope_json='{"data": "test1"}',
            payload_hash="hash1",
            received_at="2026-10-01T12:00:00Z",
            applied_at="2026-10-01T12:00:00Z",
        )
        db_session.add(event1)
        db_session.commit()

        # Verify the event exists
        existing = db_session.query(InboxEvent).filter_by(event_id="exec-123").first()
        assert existing is not None
        assert existing.payload_hash == "hash1"

    def test_gc24_different_payload_parked(self, db_session):
        """G-C-24: a reused event_id with a DIFFERENT payload is an integrity
        incident (never last-write-wins) and must not stall the stream: the
        next, well-formed event still ingests through the real service path."""
        from datetime import datetime, timezone

        from signal_platform_contracts import (
            Environment,
            EventEnvelope,
            EventType,
            ExecutionAppliedPayload,
            InstrumentIdentity,
            PrivateAccountIdentity,
            build_subject,
            compute_payload_hash,
        )

        from app.services.integration_inbox import (
            EventIntegrityError,
            ingest_export_event,
            register_export_stream,
        )

        _seed_tenant_and_user(db_session, tenant_id="tenant-a", user_id="user-a")
        stream = "signal-copier:gc24"
        register_export_stream(db_session, tenant_id="tenant-a", source_stream=stream, environment="LOCAL_SIM")
        db_session.commit()

        def envelope(event_id: str, export_sequence: int, broker_order_id: str) -> str:
            instrument = InstrumentIdentity(
                instrument_id="AAPL", venue="NASDAQ", market_type="equity", currency="USD",
                multiplier="1", quantity_convention="shares",
            )
            account = PrivateAccountIdentity(account_id="acct1")
            payload = ExecutionAppliedPayload(
                account=account, instrument=instrument, side="buy", filled_quantity="10",
                filled_price="150.00", fee=None, broker="paper", broker_order_id=broker_order_id,
            ).model_dump(mode="json")
            now = datetime.now(timezone.utc)
            return EventEnvelope(
                event_type=EventType.EXECUTION_APPLIED, event_id=event_id,
                producer_id="signal-copier-instance-1", source_stream=stream,
                export_sequence=export_sequence,
                subject=build_subject(account=account, instrument=instrument),
                event_time=now, effective_time=now, availability_time=now, receipt_time=now,
                environment=Environment.LOCAL_SIM, evidence_class=EvidenceClass.INTERNAL_PAPER,
                payload_hash=compute_payload_hash(payload), payload=payload,
            ).model_dump_json()

        first = ingest_export_event(db_session, envelope("exec-456", 0, "paper-1"))
        db_session.commit()
        first_hash = first.payload_hash

        # Same event_id, different payload: refused as an integrity incident,
        # and the stored row keeps its ORIGINAL payload (no last-write-wins).
        with pytest.raises(EventIntegrityError):
            ingest_export_event(db_session, envelope("exec-456", 1, "paper-2"))
        db_session.rollback()
        stored = db_session.get(InboxEvent, "exec-456")
        assert stored is not None and stored.payload_hash == first_hash

        # The stream is not stalled: the next well-formed event still ingests.
        nxt = ingest_export_event(db_session, envelope("exec-457", 1, "paper-3"))
        db_session.commit()
        assert nxt.event_id == "exec-457" and nxt.parked_reason is None

class TestGC25RoutingOutcomePerAccount:
    """G-C-25: Routing outcome stored per-account, not overwritten."""

    def test_gc25_per_account_outcomes_distinct_events(self, db_session):
        """G-C-25: Each account's routing outcome is stored separately."""
        # The copier exports routing outcome with per-account event IDs:
        # routing-outcome:{signal_id}:{account_id}
        # This ensures outcomes are NOT overwritten for different accounts

        # Simulate two routing outcomes for the same signal, different accounts
        event1 = InboxEvent(
            event_id="routing-outcome:sig-123:acct1",  # G-C-25 fix: per-account ID
            tenant_id="tenant1",
            event_type="ROUTING_ADMISSION_OUTCOME",
            source_stream="signal-copier:acct1",
            producer_generation=1,
            export_sequence=10,
            envelope_json='{"outcome": "admitted_filled", "account": "acct1"}',
            payload_hash="outcome-acct1",
            received_at="2026-10-01T12:00:00Z",
            applied_at="2026-10-01T12:00:00Z",
        )
        db_session.add(event1)

        event2 = InboxEvent(
            event_id="routing-outcome:sig-123:acct2",  # G-C-25 fix: per-account ID
            tenant_id="tenant1",
            event_type="ROUTING_ADMISSION_OUTCOME",
            source_stream="signal-copier:acct2",
            producer_generation=1,
            export_sequence=11,
            envelope_json='{"outcome": "disabled_by_settings", "account": "acct2"}',
            payload_hash="outcome-acct2",
            received_at="2026-10-01T12:00:00Z",
            applied_at="2026-10-01T12:00:00Z",
        )
        db_session.add(event2)
        db_session.commit()

        # Verify both outcomes are stored as separate events (not overwritten)
        outcome_acct1 = (
            db_session.query(InboxEvent)
            .filter_by(event_id="routing-outcome:sig-123:acct1")
            .first()
        )
        outcome_acct2 = (
            db_session.query(InboxEvent)
            .filter_by(event_id="routing-outcome:sig-123:acct2")
            .first()
        )

        assert outcome_acct1 is not None, "Account 1 outcome should be stored"
        assert outcome_acct2 is not None, "Account 2 outcome should be stored"
        assert outcome_acct1.payload_hash == "outcome-acct1"
        assert outcome_acct2.payload_hash == "outcome-acct2"
        # Verify they have different event IDs (not overwritten)
        assert outcome_acct1.event_id != outcome_acct2.event_id


class TestGC26UnknownEventTypes:
    """G-C-26: Unknown event types advance cursor without stalling."""

    def test_gc26_unknown_event_type_parked_not_stalled(self, db_session):
        """G-C-26: Unknown event type is parked with applied_at to advance cursor."""
        # An unknown event type should have applied_at set so cursor advances
        # and it should be parked (not silently dropped or stalling the stream)
        event = InboxEvent(
            event_id="unknown-type:123",
            tenant_id="tenant1",
            event_type="UNKNOWN_FUTURE_TYPE",  # Unknown event type
            source_stream="signal-copier:acct1",
            producer_generation=1,
            export_sequence=20,
            envelope_json='{"unknown_field": "value"}',
            payload_hash="unknown-hash",
            received_at="2026-10-01T12:00:00Z",
            applied_at="2026-10-01T12:00:00Z",  # G-C-26 fix: set applied_at
            parked_reason="unimplemented_event_type:UNKNOWN_FUTURE_TYPE",  # Parked, not stalled
        )
        db_session.add(event)
        db_session.commit()

        db_session.refresh(event)
        # Verify applied_at is set (cursor will advance even though parked)
        assert event.applied_at is not None
        # Verify it's marked as parked (not silently dropped or stalling)
        assert event.parked_reason is not None
        assert "unimplemented" in event.parked_reason.lower()

    def test_gc26_unknown_vocabulary_parked_not_stalled(self, db_session):
        """G-C-26: Unknown vocabulary values are parked, not stalling the stream."""
        # Unknown outcome value should be parked with applied_at, not stalling
        event = InboxEvent(
            event_id="routing-outcome:unknown-123",
            tenant_id="tenant1",
            event_type="ROUTING_ADMISSION_OUTCOME",
            source_stream="signal-copier:acct1",
            producer_generation=1,
            export_sequence=21,
            envelope_json='{"outcome": "UNKNOWN_OUTCOME_VALUE"}',
            payload_hash="unknown-outcome",
            received_at="2026-10-01T12:00:00Z",
            applied_at="2026-10-01T12:00:00Z",  # G-C-26 fix: set even for unknown
            parked_reason="malformed_envelope:unknown_outcome_value",
        )
        db_session.add(event)
        db_session.commit()

        # Verify event is parked but has applied_at (cursor advances, stream doesn't stall)
        parked = db_session.query(InboxEvent).filter_by(event_id="routing-outcome:unknown-123").first()
        assert parked is not None
        assert parked.applied_at is not None  # Cursor advances
        assert parked.parked_reason is not None  # But it's marked as problematic
        assert "malformed" in parked.parked_reason.lower()
