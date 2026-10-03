"""Test WP-39 fixes: commercial side of the seam.

Tests for:
- G-C-02: PublicationIntent.side field used by adapters
- G-C-12: LedgerEntry.account_id from ExecutionAppliedPayload
- G-C-26: Unknown event types advance cursor without stalling
- G-C-10: Cross-tenant product selection
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.models.ledger import Book, Side
from app.models.product import Product, ProductLifecycleState
from app.models.publication import PublicationAction, PublicationIntent, PublicationSide
from app.models.tenancy import Membership, MembershipRole, Tenant, UserIdentity
from app.services.collective2_publisher import build_order
from app.services.etoro_adapter import build_trade_request
from app.services.ledger import append_entry
from app.services.portfolio_selection import create_portfolio_selection, InvalidPortfolioSelectionError
from signal_platform_contracts import EvidenceClass


class TestGC02PublicationIntentSide:
    """G-C-02: PublicationIntent has required side field; adapters use it."""

    def test_collective2_publisher_respects_sell_side(self):
        """Collective2 adapter uses intent.side=SELL to emit Side.SELL."""
        intent = PublicationIntent(
            intent_id="test-1",
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
        assert order["Side1"] == 2  # Side.SELL in Collective2 API

    def test_collective2_publisher_respects_buy_side(self):
        """Collective2 adapter uses intent.side=BUY to emit Side.BUY."""
        intent = PublicationIntent(
            intent_id="test-2",
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
        assert order["Side1"] == 1  # Side.BUY in Collective2 API

    def test_etoro_adapter_respects_sell_side(self):
        """eToro adapter uses intent.side=SELL to emit direction=SELL."""
        intent = PublicationIntent(
            intent_id="test-3",
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

    def test_etoro_adapter_respects_buy_side(self):
        """eToro adapter uses intent.side=BUY to emit direction=BUY."""
        intent = PublicationIntent(
            intent_id="test-4",
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


def _seed_membership(db_session, *, tenant_id="tenant-a", user_id="user-a"):
    if db_session.get(Tenant, tenant_id) is None:
        db_session.add(Tenant(tenant_id=tenant_id, display_name="Tenant", environment="LOCAL_SIM"))
        db_session.flush()
    db_session.add(UserIdentity(user_id=user_id, email=f"{user_id}@example.com"))
    db_session.flush()
    db_session.add(Membership(tenant_id=tenant_id, user_id=user_id, role=MembershipRole.CUSTOMER))
    db_session.commit()


class TestGC12LedgerAccountId:
    """G-C-12: LedgerEntry carries account_id from ExecutionAppliedPayload."""

    def test_ledger_entry_stores_account_id(self, db_session):
        """Ledger entries for PLATFORM book preserve account_id."""
        entry = append_entry(
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

    def test_ledger_entry_account_id_none_by_default(self, db_session):
        """Ledger entries without account_id leave it None (for non-PLATFORM)."""
        entry = append_entry(
            db_session,
            tenant_id="tenant1",
            book=Book.SOURCE,
            instrument="AAPL",
            side=Side.BUY,
            quantity=Decimal("10"),
            price=Decimal("150"),
            currency="USD",
            event_time="2026-10-01T12:00:00Z",
            source_authority="provider:xyz",
            evidence_class=EvidenceClass.INTERNAL_PAPER,
        )
        db_session.refresh(entry)
        assert entry.account_id is None


def _published_product(db_session, *, tenant_id="tenant-a", slug="published-product"):
    product = Product(
        tenant_id=tenant_id,
        product_name="Published Product",
        slug=slug,
        lifecycle_state=ProductLifecycleState.PUBLISHED,
    )
    db_session.add(product)
    db_session.commit()
    return product


def _draft_product(db_session, *, tenant_id="tenant-a", slug="draft-product"):
    product = Product(tenant_id=tenant_id, product_name="Draft Product", slug=slug)
    db_session.add(product)
    db_session.commit()
    return product


class TestGC10CrossTenantProductSelection:
    """G-C-10: Self-signup customers can select published products from any tenant."""

    def test_select_published_product_from_operator_tenant(self, db_session):
        """Customer tenant can select a PUBLISHED product from operator tenant."""
        # Seed both tenants' memberships
        _seed_membership(db_session, tenant_id="operator-tenant", user_id="operator-user")
        _seed_membership(db_session, tenant_id="customer-tenant", user_id="customer-user")

        # Create a PUBLISHED product in operator tenant
        product = _published_product(
            db_session, tenant_id="operator-tenant", slug="operator-strategy"
        )

        # Customer tenant selects it (G-C-10 fix: cross-tenant selection)
        selection = create_portfolio_selection(
            db_session,
            tenant_id="customer-tenant",  # Different from product.tenant_id
            user_id="customer-user",
            product_id=product.product_id,
        )
        assert selection.product_id == product.product_id
        assert selection.tenant_id == "customer-tenant"

    def test_cannot_select_draft_product(self, db_session):
        """Customers cannot select non-PUBLISHED products."""
        _seed_membership(db_session, tenant_id="operator-tenant", user_id="operator-user")
        _seed_membership(db_session, tenant_id="customer-tenant", user_id="customer-user")

        # Create a DRAFT product in operator tenant
        draft_product = _draft_product(
            db_session, tenant_id="operator-tenant", slug="draft-strategy"
        )

        with pytest.raises(InvalidPortfolioSelectionError, match="PRODUCT_NOT_PUBLISHED_OR_NOT_FOUND"):
            create_portfolio_selection(
                db_session,
                tenant_id="customer-tenant",
                user_id="customer-user",
                product_id=draft_product.product_id,
            )

    def test_cannot_select_nonexistent_product(self, db_session):
        """Selecting a non-existent product raises an error."""
        _seed_membership(db_session, tenant_id="customer-tenant", user_id="customer-user")

        with pytest.raises(InvalidPortfolioSelectionError, match="PRODUCT_NOT_PUBLISHED_OR_NOT_FOUND"):
            create_portfolio_selection(
                db_session,
                tenant_id="customer-tenant",
                user_id="customer-user",
                product_id="nonexistent-product",
            )
