"""Track 1b: two release-review findings closed by app/engine.py --

1. The qualification ladder (app/qualification.py) was recorded via
   `SignalStore.record_route_qualification` but never READ or checked
   anywhere before routing a live entry -- an account could be fully
   wired for live trading with no route ever having reached
   `release_approved`. `SignalCopierEngine._check_route_qualified` closes
   this: a live ENTRY on a route with no recorded `release_approved` is
   refused, explicitly and distinguishably ("route not qualified for
   live release"), before any broker/asset-class/capital check runs.
   PAPER accounts (`PaperBroker`) and CLOSE signals are exempt -- see
   that method's own docstring for why.

2. `AccountBalance.buying_power` was fetched and displayed (`/accounts`)
   but never used as an admission gate -- only the independent
   risk-basis/notional ceiling was enforced. `SignalCopierEngine.
   _check_buying_power` closes this: an entry whose notional would
   exceed the broker's real, just-fetched buying power is refused,
   independent of (and never a substitute for) `max_notional_exposure`/
   `risk_percent_of_equity`. It fails closed where buying power genuinely
   isn't verifiable AND no ceiling is configured; otherwise it skips the
   check and relies on the configured ceiling.
"""
from __future__ import annotations

import pytest

from app.brokers.base import BrokerAdapter
from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.lifecycle.manager import PositionLifecycleManager
from app.models import AccountBalance, AssetClass, DestinationAccount, OrderResult, OrderStatus, Side, Signal
from app.routing import RoutingConfig, RoutingRule

SOURCE = "tradingview"
SYMBOL = "AAPL"


class _FillsEverythingBroker(BrokerAdapter):
    """A minimal, generic BrokerAdapter -- deliberately NOT a PaperBroker
    subclass (the qualification gate's PAPER exemption is `isinstance`-
    based, not by registration-key string, precisely so an operator can't
    dodge the gate by naming a real adapter "paper" -- see
    `SignalCopierEngine._check_route_qualified`'s own docstring). Fills
    every order instantly, standing in for "some real, non-paper broker
    adapter" wherever a test needs one without needing real credentials."""

    name = "fake-real-broker"

    def __init__(self) -> None:
        self.fills: list[OrderResult] = []

    async def place_order(self, signal, account, quantity, symbol) -> OrderResult:
        from datetime import datetime, timezone
        result = OrderResult(
            account_id=account.account_id,
            status=OrderStatus.FILLED,
            signal_id=signal.id,
            broker_order_id=f"fake-{len(self.fills) + 1}",
            filled_quantity=quantity,
            filled_price=signal.price or 0.0,
            message="filled by fake real broker",
            executed_at=datetime.now(timezone.utc),
        )
        self.fills.append(result)
        return result


def _engine(store: SignalStore, account: DestinationAccount, broker: BrokerAdapter) -> SignalCopierEngine:
    routing = RoutingConfig(rules=[RoutingRule(source=SOURCE, destinations=["acct1"])], accounts={"acct1": account})
    lifecycle_manager = PositionLifecycleManager(brokers={account.broker: broker}, store=store)
    return SignalCopierEngine(
        routing=routing, brokers={account.broker: broker}, store=store, lifecycle_manager=lifecycle_manager
    )


def _record_release_approved(store: SignalStore, *, adapter_type: str, route_key: str, asset_class: str) -> None:
    """Walks the full ladder to `release_approved` for one route -- the
    write path (`record_route_qualification`) enforces strict-sequential
    prerequisites, so every rung below must be recorded first (see
    app/qualification.py)."""
    for state in [
        "implemented",
        "configured",
        "authenticated",
        "account_entitled",
        "protocol_tested",
        "venue_tested",
        "release_approved",
    ]:
        store.record_route_qualification(
            adapter_type=adapter_type,
            route_key=route_key,
            asset_class=asset_class,
            product_type="default",
            state=state,
            supports_feedback=True,
            recorded_by="owner",
        )


# --- Gap 1: qualification ladder as a live-routing gate -----------------


@pytest.mark.asyncio
async def test_live_entry_rejected_when_route_has_no_qualification_record(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = _FillsEverythingBroker()  # a real (non-PaperBroker) adapter -- the PAPER exemption must not apply
    account = DestinationAccount(account_id="acct1", broker="alpaca")
    engine = _engine(store, account, broker)

    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=1.0, price=100.0, asset_class=AssetClass.EQUITY)
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.REJECTED
    assert "route not qualified for live release" in results[0].message
    assert "release_approved" in results[0].message
    assert broker.fills == []  # never reached the broker


@pytest.mark.asyncio
async def test_live_entry_admitted_once_release_approved_is_recorded_for_the_exact_route(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = _FillsEverythingBroker()
    # WP-32: account needs capital ceiling since alpaca adapter is not fully
    # initialized in test context, so buying power gate needs a fallback
    account = DestinationAccount(account_id="acct1", broker="alpaca", max_notional_exposure=100_000.0)
    engine = _engine(store, account, broker)

    _record_release_approved(store, adapter_type="alpaca", route_key="acct1", asset_class="equity")

    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=1.0, price=100.0, asset_class=AssetClass.EQUITY)
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.FILLED
    assert broker.fills != []


@pytest.mark.asyncio
async def test_qualification_gate_is_per_exact_route_not_just_adapter(tmp_path):
    """Qualifying acct1/equity must not qualify a DIFFERENT asset_class on
    the exact same account/adapter -- app/qualification.py's own module
    docstring: 'Live qualification must be per exact route.'"""
    store = SignalStore(tmp_path / "test.db")
    broker = _FillsEverythingBroker()
    account = DestinationAccount(account_id="acct1", broker="alpaca")
    engine = _engine(store, account, broker)

    _record_release_approved(store, adapter_type="alpaca", route_key="acct1", asset_class="equity")

    # This account is release_approved for asset_class=equity, but this signal is OPTION.
    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=1.0, price=100.0, asset_class=AssetClass.OPTION)
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.REJECTED
    assert "route not qualified for live release" in results[0].message


@pytest.mark.asyncio
async def test_paper_account_is_unaffected_by_the_qualification_gate(tmp_path):
    """PaperBroker never sends an order anywhere outside this process's
    own memory -- there is no live capital for this gate to protect on a
    paper route, and gating it would make PaperBroker useless for the
    dev/test workflows it exists for. No qualification row is ever
    recorded here."""
    store = SignalStore(tmp_path / "test.db")
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    engine = _engine(store, account, broker)

    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=1.0, price=100.0)
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.FILLED
    assert broker.fills != []


@pytest.mark.asyncio
async def test_close_signal_is_not_blocked_by_the_qualification_gate(tmp_path):
    """A CLOSE on an unqualified route must still be allowed to flatten an
    already-open (locally tracked) position -- refusing it would trap a
    real position on a route this gate would otherwise block from ever
    opening in the first place. Same "entry pause, not exit block"
    reasoning as EXE-10's account.enabled handling elsewhere in this
    engine."""
    store = SignalStore(tmp_path / "test.db")
    broker = _FillsEverythingBroker()  # a real (non-PaperBroker) adapter, genuinely unqualified
    # `_FillsEverythingBroker` has no `get_broker_position` readback at all, so this plain
    # close needs the account's own explicit exclusive_writer_qualified assertion (P0-5) to
    # proceed against this service's own tracked position -- unrelated to THIS gate, but a
    # real prerequisite this test opts into deliberately so it isolates the qualification
    # gate's own CLOSE exemption instead of being blocked by an unrelated check.
    account = DestinationAccount(account_id="acct1", broker="alpaca", exclusive_writer_qualified=True)
    engine = _engine(store, account, broker)

    # Seed a tracked open long position directly (bypassing the entry gate
    # entirely, since that's not what this test is about) so the CLOSE has
    # something real to flatten.
    store.record_fill(account_id="acct1", symbol=SYMBOL, side=Side.BUY, quantity=5.0)

    close_signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.CLOSE, asset_class=AssetClass.EQUITY)
    results = await engine.handle_signal(close_signal)

    assert results[0].status == OrderStatus.FILLED
    assert broker.fills != []


# --- Gap 2: broker buying-power as an admission gate ---------------------


class _BuyingPowerBroker(PaperBroker):
    """A PaperBroker whose get_account_balance reports a real, fixed
    buying_power figure independent of PaperBroker's own simulated cash
    tracking -- isolates this gate's own behavior from the notional/risk
    ceiling tests' reliance on PaperBroker's real STARTING_CASH."""

    def __init__(self, buying_power: float | None) -> None:
        super().__init__()
        self._buying_power = buying_power

    async def get_account_balance(self, account: DestinationAccount) -> AccountBalance | None:
        return AccountBalance(account_id=account.account_id, buying_power=self._buying_power)


class _NoBalanceBroker(BrokerAdapter):
    """A broker with NO real get_account_balance override at all (and
    deliberately NOT a PaperBroker subclass, which would inherit ITS real
    override) -- has_balance_capability is False, same as e.g. CCXTBroker
    on a spot market or SignalStackBroker."""

    name = "no-balance-broker"

    def __init__(self) -> None:
        self.fills: list[OrderResult] = []

    async def place_order(self, signal, account, quantity, symbol) -> OrderResult:
        result = OrderResult(
            account_id=account.account_id,
            status=OrderStatus.FILLED,
            signal_id=signal.id,
            broker_order_id=f"nb-{len(self.fills) + 1}",
            filled_quantity=quantity,
            filled_price=signal.price or 0.0,
            message="filled",
        )
        self.fills.append(result)
        return result


@pytest.mark.asyncio
async def test_entry_rejected_when_notional_exceeds_broker_buying_power(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = _BuyingPowerBroker(buying_power=500.0)
    account = DestinationAccount(account_id="acct1", broker="paper")
    engine = _engine(store, account, broker)
    _record_release_approved(store, adapter_type="paper", route_key="acct1", asset_class="crypto")

    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=10.0, price=100.0)  # notional 1000 > 500
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.REJECTED
    assert "insufficient buying power" in results[0].message
    assert broker.fills == []


@pytest.mark.asyncio
async def test_entry_admitted_when_notional_is_within_buying_power_and_risk_ceiling(tmp_path):
    store = SignalStore(tmp_path / "test.db")
    broker = _BuyingPowerBroker(buying_power=5_000.0)
    account = DestinationAccount(account_id="acct1", broker="paper", max_notional_exposure=2_000.0)
    engine = _engine(store, account, broker)
    _record_release_approved(store, adapter_type="paper", route_key="acct1", asset_class="crypto")

    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=10.0, price=100.0)  # notional 1000
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.FILLED


@pytest.mark.asyncio
async def test_broker_that_cannot_report_buying_power_fails_closed(tmp_path):
    """A broker with no balance capability and no configured ceiling
    must refuse entry fail-closed, naming both the missing figure
    (balance capability) and the mitigation option (ceiling)."""
    store = SignalStore(tmp_path / "test.db")
    broker = _NoBalanceBroker()
    assert broker.has_balance_capability is False
    account = DestinationAccount(account_id="acct1", broker="paper")
    engine = _engine(store, account, broker)
    _record_release_approved(store, adapter_type="paper", route_key="acct1", asset_class="crypto")

    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=10.0, price=100.0)
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.REJECTED
    assert "has no verified balance capability" in results[0].message
    assert "no capital ceiling is configured" in results[0].message
    assert "(max_notional_exposure or risk_percent_of_equity)" in results[0].message


@pytest.mark.asyncio
async def test_broker_reporting_none_buying_power_also_fails_closed(tmp_path):
    """has_balance_capability is True (a real override exists) but this
    specific account/call genuinely has no buying_power concept (e.g. a
    ccxt spot account) -- must not be treated as zero buying power. Must
    instead fail closed, naming both the missing figure and the ceiling
    option, when no ceiling is configured."""
    store = SignalStore(tmp_path / "test.db")
    broker = _BuyingPowerBroker(buying_power=None)
    assert broker.has_balance_capability is True
    account = DestinationAccount(account_id="acct1", broker="paper")
    engine = _engine(store, account, broker)
    _record_release_approved(store, adapter_type="paper", route_key="acct1", asset_class="crypto")

    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=10.0, price=100.0)
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.REJECTED
    assert "does not report a buying_power figure" in results[0].message
    assert "no capital ceiling is configured" in results[0].message
    assert "(max_notional_exposure or risk_percent_of_equity)" in results[0].message


@pytest.mark.asyncio
async def test_buying_power_check_is_independent_of_the_notional_ceiling_check(tmp_path):
    """Ample buying power must NOT override or weaken the existing
    max_notional_exposure ceiling -- both are independently required."""
    store = SignalStore(tmp_path / "test.db")
    broker = _BuyingPowerBroker(buying_power=1_000_000.0)  # plenty of buying power
    account = DestinationAccount(account_id="acct1", broker="paper", max_notional_exposure=500.0)  # tight ceiling
    engine = _engine(store, account, broker)
    _record_release_approved(store, adapter_type="paper", route_key="acct1", asset_class="crypto")

    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=10.0, price=100.0)  # notional 1000 > 500
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.REJECTED
    assert "notional exposure ceiling" in results[0].message  # the OTHER gate's message, not buying power's
    assert broker.fills == []


@pytest.mark.asyncio
async def test_notional_ceiling_passing_does_not_bypass_the_buying_power_check(tmp_path):
    """The reverse direction of independence: passing the notional
    ceiling must not admit an entry that fails the buying-power check."""
    store = SignalStore(tmp_path / "test.db")
    broker = _BuyingPowerBroker(buying_power=100.0)  # very little buying power
    account = DestinationAccount(account_id="acct1", broker="paper", max_notional_exposure=1_000_000.0)  # generous ceiling
    engine = _engine(store, account, broker)
    _record_release_approved(store, adapter_type="paper", route_key="acct1", asset_class="crypto")

    signal = Signal(source=SOURCE, symbol=SYMBOL, side=Side.BUY, quantity=10.0, price=100.0)  # notional 1000 > 100
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.REJECTED
    assert "insufficient buying power" in results[0].message
    assert broker.fills == []
