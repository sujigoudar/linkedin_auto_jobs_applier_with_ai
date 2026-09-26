import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.models import DestinationAccount, OrderStatus, Signal, Side
from app.providers import AnalystConfig, ProviderConfig, ProviderRegistry, SettingsOverride
from app.routing import RoutingConfig, RoutingRule


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _engine(store, provider_registry, multiplier=1.0):
    paper = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", multiplier=multiplier)
    routing = RoutingConfig(
        rules=[RoutingRule(source="telegram", destinations=["acct1"])], accounts={"acct1": account}
    )
    engine = SignalCopierEngine(
        routing=routing, brokers={"paper": paper}, store=store, provider_registry=provider_registry
    )
    return engine, paper


@pytest.mark.asyncio
async def test_provider_multiplier_override_applies_to_sizing(store):
    registry = ProviderRegistry(
        providers={"telegram": ProviderConfig(provider_id="telegram", settings=SettingsOverride(multiplier=0.25))}
    )
    engine, paper = _engine(store, registry, multiplier=1.0)

    signal = Signal(source="telegram", symbol="BTCUSDT", side=Side.BUY, quantity=100.0)
    results = await engine.handle_signal(signal)

    assert results[0].status == OrderStatus.FILLED
    assert results[0].filled_quantity == 25.0  # 100 * 0.25, not the account's own multiplier of 1.0


@pytest.mark.asyncio
async def test_analyst_override_wins_over_provider_override(store):
    registry = ProviderRegistry(
        providers={
            "telegram": ProviderConfig(
                provider_id="telegram",
                settings=SettingsOverride(multiplier=0.25),
                analysts={"alice": AnalystConfig(analyst_id="alice", settings=SettingsOverride(multiplier=1.0))},
            )
        }
    )
    engine, paper = _engine(store, registry, multiplier=1.0)

    signal = Signal(source="telegram", symbol="BTCUSDT", side=Side.BUY, quantity=100.0, analyst="alice")
    results = await engine.handle_signal(signal)

    assert results[0].filled_quantity == 100.0  # alice's override, not the provider's 0.25


@pytest.mark.asyncio
async def test_disabled_analyst_is_skipped_entirely(store):
    registry = ProviderRegistry(
        providers={
            "telegram": ProviderConfig(
                provider_id="telegram",
                analysts={"bob": AnalystConfig(analyst_id="bob", settings=SettingsOverride(enabled=False))},
            )
        }
    )
    engine, paper = _engine(store, registry)

    signal = Signal(source="telegram", symbol="BTCUSDT", side=Side.BUY, quantity=1.0, analyst="bob")
    results = await engine.handle_signal(signal)

    assert results == []
    assert paper.positions == {}  # never reached the broker at all


@pytest.mark.asyncio
async def test_provider_can_force_managed_lifecycle_on(store):
    registry = ProviderRegistry(
        providers={"telegram": ProviderConfig(provider_id="telegram", settings=SettingsOverride(managed_lifecycle=True))}
    )
    engine, paper = _engine(store, registry)  # account itself has managed_lifecycle=False

    signal = Signal(source="telegram", symbol="AAPL", side=Side.BUY, quantity=10.0)  # no stop_loss
    results = await engine.handle_signal(signal)

    # forced into the managed path, which refuses an entry with no resolved stop
    assert results[0].status == OrderStatus.REJECTED
    assert "refusing to enter unprotected" in results[0].message


@pytest.mark.asyncio
async def test_no_provider_config_leaves_account_settings_unchanged(store):
    engine, paper = _engine(store, ProviderRegistry(), multiplier=2.0)

    signal = Signal(source="telegram", symbol="BTCUSDT", side=Side.BUY, quantity=10.0)
    results = await engine.handle_signal(signal)

    assert results[0].filled_quantity == 20.0  # unaffected: just the account's own multiplier
