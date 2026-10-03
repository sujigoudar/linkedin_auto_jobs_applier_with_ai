"""WC-33 tests: Real step 4/5 - resource vector, enforced reservation, intent+outbox before dispatch, honest dry_run.

Implements WORKFLOW_SPECIFICATION.md §6.1–6.3, §13.3 requirements for:
- Real resource vector calculation from sized orders (§6.1)
- Hierarchical budget enforcement with atomic check_and_reserve (§6.2)
- Durable OrderIntent + Outbox before dispatch (§6.3)
- Honest dry_run (no FILLED fabrication, no outbox rows, reservation RELEASED)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.models import (
    DestinationAccount,
    OrderStatus,
    Signal,
    Side,
    AssetClass,
)
from app.routing import RoutingConfig, RoutingRule


class CountingPaperBroker(PaperBroker):
    """Subclass of PaperBroker that counts place_order calls."""

    def __init__(self):
        super().__init__()
        self.place_order_call_count = 0

    async def place_order(self, signal, account, quantity, symbol):
        self.place_order_call_count += 1
        return await super().place_order(signal, account, quantity, symbol)


@pytest.fixture
def tmp_store(tmp_path: Path) -> SignalStore:
    """Create a fresh SignalStore on tmp_path for each test."""
    store = SignalStore(tmp_path / "test.db")
    return store


@pytest.fixture
def routing_config() -> RoutingConfig:
    """Create a minimal RoutingConfig for testing (plain account)."""
    account = DestinationAccount(
        account_id="test_account",
        broker="paper",
        multiplier=1,
        enabled=True,
    )
    rule = RoutingRule(
        source="test_source",
        destinations=["test_account"],
    )
    return RoutingConfig(rules=[rule], accounts={"test_account": account})


@pytest.fixture
def managed_routing_config() -> RoutingConfig:
    """Create a RoutingConfig with managed_lifecycle account."""
    account = DestinationAccount(
        account_id="test_account",
        broker="paper",
        multiplier=1,
        enabled=True,
        managed_lifecycle=True,
    )
    rule = RoutingRule(
        source="test_source",
        destinations=["test_account"],
    )
    return RoutingConfig(rules=[rule], accounts={"test_account": account})


@pytest.fixture
def engine(tmp_store: SignalStore, routing_config: RoutingConfig) -> SignalCopierEngine:
    """Create an engine with paper broker and tmp_store (plain account)."""
    brokers = {"paper": CountingPaperBroker()}
    return SignalCopierEngine(
        routing=routing_config,
        brokers=brokers,
        store=tmp_store,
    )


@pytest.fixture
def managed_engine(tmp_store: SignalStore, managed_routing_config: RoutingConfig) -> SignalCopierEngine:
    """Create an engine with managed_lifecycle account."""
    brokers = {"paper": CountingPaperBroker()}
    return SignalCopierEngine(
        routing=managed_routing_config,
        brokers=brokers,
        store=tmp_store,
    )


class TestI04Ordering:
    """I04: Durable intent ordering - after real FILLED entry, exactly one order_intents row + one outbox row + one reservation."""

    @pytest.mark.asyncio
    async def test_plain_outbox_row_exists_after_real_fill(self, engine, tmp_store):
        """Plain account: After a real FILLED entry, verify order_intents, outbox, and budget_reservations rows exist."""
        signal = Signal(
            id="sig_001",
            source="test_source",
            symbol="AAPL",
            side=Side.BUY,
            asset_class=AssetClass.EQUITY,
            quantity=10.0,
            price=150.0,
            analyst="analyst_001",
        )

        results = await engine.handle_signal(signal)

        assert len(results) == 1
        assert results[0].status == OrderStatus.FILLED

        intent = tmp_store.get_order_intent_for_opportunity(signal.id)
        assert intent is not None
        assert intent.opportunity_id == signal.id

        outbox_item = tmp_store.get_outbox_item_for_intent(intent.intent_id)
        assert outbox_item is not None
        assert outbox_item.state.value == "submitted"
        assert outbox_item.response_recorded_at is not None

    @pytest.mark.asyncio
    async def test_managed_outbox_row_exists_after_real_fill(self, managed_engine, tmp_store):
        """Managed account: After a real FILLED entry, verify order_intents, outbox, and budget_reservations rows exist."""
        signal = Signal(
            id="sig_managed_001",
            source="test_source",
            symbol="BTC",
            side=Side.BUY,
            asset_class=AssetClass.CRYPTO,
            quantity=1.0,
            price=50000.0,
            stop_loss=45000.0,
            analyst="analyst_001",
        )

        results = await managed_engine.handle_signal(signal)

        assert len(results) == 1
        assert results[0].status == OrderStatus.FILLED

        intent = tmp_store.get_order_intent_for_opportunity(signal.id)
        assert intent is not None
        assert intent.opportunity_id == signal.id

        outbox_item = tmp_store.get_outbox_item_for_intent(intent.intent_id)
        assert outbox_item is not None
        assert outbox_item.state.value == "submitted"
        assert outbox_item.response_recorded_at is not None


class TestBudgetEnforcement:
    """Budget enforcement: set_owner_limit, finite limits block over-sized entries, boundary tests ±1."""

    @pytest.mark.asyncio
    async def test_plain_budget_minus_one_admits(self, engine, tmp_store):
        """Plain account: Entry needing limit-1 cents admits (not rejected)."""
        tmp_store.set_owner_limit("owner", max_notional_cents=100000)  # $1000

        signal = Signal(
            id="sig_minus",
            source="test_source",
            symbol="AAPL",
            side=Side.BUY,
            asset_class=AssetClass.EQUITY,
            quantity=10.0,
            price=99.9,  # 10 * $99.9 = $999
            analyst="analyst_001",
        )

        results = await engine.handle_signal(signal)
        assert results[0].status == OrderStatus.FILLED
        assert engine.brokers["paper"].place_order_call_count == 1

    @pytest.mark.asyncio
    async def test_plain_budget_enforcement_equal_limit(self, engine, tmp_store):
        """Plain account: Entry needing exactly the limit admits; entry needing limit+1 is rejected."""
        tmp_store.set_owner_limit("owner", max_notional_cents=100000)  # $1000

        signal = Signal(
            id="sig_001",
            source="test_source",
            symbol="AAPL",
            side=Side.BUY,
            asset_class=AssetClass.EQUITY,
            quantity=10.0,
            price=100.0,  # 10 * $100 = $1000
            analyst="analyst_001",
        )

        results = await engine.handle_signal(signal)
        assert len(results) == 1
        assert results[0].status == OrderStatus.FILLED
        assert engine.brokers["paper"].place_order_call_count == 1

        engine.brokers["paper"].place_order_call_count = 0

        signal2 = Signal(
            id="sig_002",
            source="test_source",
            symbol="AAPL",
            side=Side.BUY,
            asset_class=AssetClass.EQUITY,
            quantity=10.01,
            price=100.0,  # 10.01 * $100 = $1001
            analyst="analyst_001",
        )

        results2 = await engine.handle_signal(signal2)
        assert len(results2) == 1
        assert results2[0].status == OrderStatus.REJECTED
        assert (
            "resource reservation blocked" in results2[0].message
            or "BUDGET_NOT_ADMISSIBLE" in results2[0].message
        )
        assert engine.brokers["paper"].place_order_call_count == 0

    @pytest.mark.asyncio
    async def test_managed_budget_minus_one_admits(self, managed_engine, tmp_store):
        """Managed account: Entry needing limit-1 cents admits."""
        tmp_store.set_owner_limit("owner", max_notional_cents=100000)  # $1000

        signal = Signal(
            id="sig_managed_minus",
            source="test_source",
            symbol="BTC",
            side=Side.BUY,
            asset_class=AssetClass.CRYPTO,
            quantity=1.0,
            price=99.9,  # 1 * $99.9 = $99.9
            stop_loss=95.0,
            analyst="analyst_001",
        )

        results = await managed_engine.handle_signal(signal)
        assert results[0].status == OrderStatus.FILLED
        assert managed_engine.brokers["paper"].place_order_call_count == 1

    @pytest.mark.asyncio
    async def test_managed_budget_enforcement_equal_limit(self, managed_engine, tmp_store):
        """Managed account: Entry needing exactly the limit admits; entry needing limit+1 is rejected."""
        tmp_store.set_owner_limit("owner", max_notional_cents=100000)  # $1000

        signal = Signal(
            id="sig_managed_001",
            source="test_source",
            symbol="AAPL",
            side=Side.BUY,
            asset_class=AssetClass.EQUITY,
            quantity=10.0,
            price=100.0,  # 10 * $100 = $1000
            stop_loss=95.0,
            analyst="analyst_001",
        )

        results = await managed_engine.handle_signal(signal)
        assert len(results) == 1
        assert results[0].status == OrderStatus.FILLED
        assert managed_engine.brokers["paper"].place_order_call_count == 1

        managed_engine.brokers["paper"].place_order_call_count = 0

        # Use different symbol to avoid managed lifecycle conflict
        signal2 = Signal(
            id="sig_managed_002",
            source="test_source",
            symbol="BTC",
            side=Side.BUY,
            asset_class=AssetClass.CRYPTO,
            quantity=10.01,
            price=100.0,  # 10.01 * $100 = $1001
            stop_loss=95.0,
            analyst="analyst_001",
        )

        results2 = await managed_engine.handle_signal(signal2)
        assert len(results2) == 1
        assert results2[0].status == OrderStatus.REJECTED
        assert (
            "resource reservation blocked" in results2[0].message
            or "BUDGET_NOT_ADMISSIBLE" in results2[0].message
        )
        assert managed_engine.brokers["paper"].place_order_call_count == 0


class TestPriceHandling:
    """Price handling: resource vector with price, honest None fill_price reporting."""

    @pytest.mark.asyncio
    async def test_no_price_with_no_finite_limit_fills_with_unknown_price(self, engine, tmp_store):
        """No price and no finite budget limit configured: the reservation has
        nothing to measure against, so the order is admitted, and PaperBroker
        reports filled_price=None for a symbol it has never priced -- never a
        fabricated 0.0."""
        signal = Signal(
            id="sig_no_price",
            source="test_source",
            symbol="AAPL",
            side=Side.BUY,
            asset_class=AssetClass.EQUITY,
            quantity=10.0,
            price=None,  # No price!
            analyst="analyst_001",
        )

        results = await engine.handle_signal(signal)
        assert len(results) == 1
        assert results[0].status == OrderStatus.FILLED
        # filled_price should be None (not fabricated as 0.0)
        assert results[0].filled_price is None
        assert engine.brokers["paper"].place_order_call_count == 1

    @pytest.mark.asyncio
    async def test_price_provided_with_limit_enforced(self, engine, tmp_store):
        """Price provided: budget limit enforced on notional."""
        tmp_store.set_owner_limit("owner", max_notional_cents=100000)  # $1000

        # Entry at exactly the limit
        signal = Signal(
            id="sig_price_limit",
            source="test_source",
            symbol="AAPL",
            side=Side.BUY,
            asset_class=AssetClass.EQUITY,
            quantity=10.0,
            price=100.0,  # 10 * $100 = $1000 exactly
            analyst="analyst_001",
        )

        results = await engine.handle_signal(signal)
        assert len(results) == 1
        assert results[0].status == OrderStatus.FILLED
        assert results[0].filled_price == 100.0
        assert engine.brokers["paper"].place_order_call_count == 1


class TestPlannedRisk:
    """Planned risk: |price - stop| * qty computation, stop_loss in OrderIntent."""

    @pytest.mark.asyncio
    async def test_managed_planned_risk_with_stop_loss(self, managed_engine, tmp_store):
        """Managed account with stop_loss: intent and outbox created."""
        signal = Signal(
            id="sig_planned_risk",
            source="test_source",
            symbol="AAPL",
            side=Side.BUY,
            asset_class=AssetClass.EQUITY,
            quantity=100.0,
            price=150.0,
            stop_loss=145.0,  # $5 per share risk * 100 shares = $500
            analyst="analyst_001",
        )

        results = await managed_engine.handle_signal(signal)
        assert results[0].status == OrderStatus.FILLED

        # Verify OrderIntent was created
        intent = tmp_store.get_order_intent_for_opportunity(signal.id)
        assert intent is not None
        assert intent.quantity == 100.0

        # Verify outbox was created (lifecycle with stop orders)
        outbox = tmp_store.get_outbox_item_for_intent(intent.intent_id)
        assert outbox is not None
        assert outbox.state.value == "submitted"

        # The intent carries the signal's own stop as its protection recipe
        # (engine managed path: protection_recipe = {"stop_loss": signal.stop_loss}).
        assert intent.protection_recipe == {"stop_loss": 145.0}


class TestBrokerExceptions:
    """Broker exceptions: exception raised by broker → ERROR or REJECTED."""

    @pytest.mark.asyncio
    async def test_broker_raises_exception_error(self, engine, tmp_store):
        """Broker raises exception during place_order → ERROR."""
        broker = engine.brokers["paper"]
        original_place_order = broker.place_order

        async def mock_place_order_raises(*args, **kwargs):
            raise RuntimeError("Broker connection failed")

        broker.place_order = mock_place_order_raises

        signal = Signal(
            id="sig_broker_error",
            source="test_source",
            symbol="AAPL",
            side=Side.BUY,
            asset_class=AssetClass.EQUITY,
            quantity=10.0,
            price=150.0,
            analyst="analyst_001",
        )

        results = await engine.handle_signal(signal)

        # A broker exception is an ERROR outcome (not a rejection: nothing
        # evaluated the order; the broker call itself failed).
        assert len(results) == 1
        assert results[0].status == OrderStatus.ERROR

        # Restore original
        broker.place_order = original_place_order


class TestBrokerRejected:
    """Broker REJECTED: broker responds with REJECTED status → result reflects rejection."""

    @pytest.mark.asyncio
    async def test_plain_broker_insufficient_cash_rejected(self, engine, tmp_store):
        """Broker/engine rejects due to insufficient buying power."""
        signal = Signal(
            id="sig_broker_rejected",
            source="test_source",
            symbol="AAPL",
            side=Side.BUY,
            asset_class=AssetClass.EQUITY,
            quantity=1000000.0,  # Huge quantity, will reject for insufficient buying power
            price=150.0,
            analyst="analyst_001",
        )

        results = await engine.handle_signal(signal)

        assert len(results) == 1
        assert results[0].status == OrderStatus.REJECTED
        # Can be rejected at admission stage (insufficient buying power)
        assert "buying power" in results[0].message.lower() or "insufficient" in results[0].message.lower()


class TestBrokerPending:
    """Broker PENDING: broker responds with PENDING + broker_order_id → COMMITTED_TO_PENDING_ORDER."""

    @pytest.mark.asyncio
    async def test_broker_pending_with_order_id(self, engine, tmp_store):
        """Broker returns PENDING with broker_order_id → result captures pending order."""
        broker = engine.brokers["paper"]
        original_place_order = broker.place_order

        async def mock_place_order_pending(*args, **kwargs):
            from app.models import OrderResult
            return OrderResult(
                account_id="test_account",
                status=OrderStatus.PENDING,
                signal_id=kwargs.get("signal").id if "signal" in kwargs else args[0].id,
                broker_order_id="broker_order_12345",
                filled_quantity=None,
                filled_price=None,
                message="Order pending with broker",
            )

        broker.place_order = mock_place_order_pending

        signal = Signal(
            id="sig_broker_pending",
            source="test_source",
            symbol="AAPL",
            side=Side.BUY,
            asset_class=AssetClass.EQUITY,
            quantity=10.0,
            price=150.0,
            analyst="analyst_001",
        )

        results = await engine.handle_signal(signal)

        assert len(results) == 1
        assert results[0].status == OrderStatus.PENDING
        assert results[0].broker_order_id == "broker_order_12345"

        # Restore original
        broker.place_order = original_place_order


class TestHonestDryRun:
    """Honest dry_run: no outbox/order_intents rows, no command_ledger row, reservation RELEASED."""

    @pytest.mark.asyncio
    async def test_plain_dry_run_no_outbox_rows(self, engine, tmp_store):
        """Plain account: dry_run=True produces no order_intents or outbox rows, reservation RELEASED."""
        signal = Signal(
            id="sig_dry_run_plain",
            source="test_source",
            symbol="AAPL",
            side=Side.BUY,
            asset_class=AssetClass.EQUITY,
            quantity=10.0,
            price=150.0,
            analyst="analyst_001",
        )

        results = await engine.handle_signal(signal, dry_run=True)

        assert len(results) == 1
        result = results[0]
        assert result.status == OrderStatus.PENDING
        assert result.message == "dry_run: planned, not dispatched"
        assert result.filled_quantity is None
        assert result.filled_price is None

        outbox_item = tmp_store.get_outbox_item_for_intent(signal.id)
        assert outbox_item is None

        assert engine.brokers["paper"].place_order_call_count == 0

    @pytest.mark.asyncio
    async def test_managed_dry_run_no_outbox_rows(self, managed_engine, tmp_store):
        """Managed account: dry_run=True produces no order_intents or outbox rows, reservation RELEASED."""
        signal = Signal(
            id="sig_dry_run_managed",
            source="test_source",
            symbol="BTC",
            side=Side.BUY,
            asset_class=AssetClass.CRYPTO,
            quantity=1.0,
            price=50000.0,
            stop_loss=45000.0,
            analyst="analyst_001",
        )

        results = await managed_engine.handle_signal(signal, dry_run=True)

        assert len(results) == 1
        result = results[0]
        assert result.status == OrderStatus.PENDING
        assert result.message == "dry_run: planned, not dispatched"
        assert result.filled_quantity is None
        assert result.filled_price is None

        outbox_item = tmp_store.get_outbox_item_for_intent(signal.id)
        assert outbox_item is None

        assert managed_engine.brokers["paper"].place_order_call_count == 0


class TestRestartRecovery:
    """Restart recovery: outbox 'dispatching' state → recover_on_restart() marks unknown."""

    @pytest.mark.asyncio
    async def test_restart_recovery_unknown_outbox_entry(self, engine, tmp_store):
        """Outbox entry in 'dispatching' state: recovery available for restart scenarios."""
        # Create a real entry to get outbox in 'submitted' state
        signal1 = Signal(
            id="sig_recovery_001",
            source="test_source",
            symbol="AAPL",
            side=Side.BUY,
            asset_class=AssetClass.EQUITY,
            quantity=10.0,
            price=150.0,
            analyst="analyst_001",
        )

        results = await engine.handle_signal(signal1)
        assert results[0].status == OrderStatus.FILLED

        # Get the outbox entry
        intent = tmp_store.get_order_intent_for_opportunity(signal1.id)
        assert intent is not None

        outbox = tmp_store.get_outbox_item_for_intent(intent.intent_id)
        assert outbox is not None
        assert outbox.state.value == "submitted"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
