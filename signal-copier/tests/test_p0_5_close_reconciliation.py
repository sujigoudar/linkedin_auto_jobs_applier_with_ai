"""P0-5: a plain (non-managed_lifecycle) account's CLOSE used to resolve
purely against SignalCopierEngine's own locally tracked position
(SignalStore.get_position) with no check against what the broker actually
holds -- app/engine.py's own module docstring already documented this
honestly as a real gap ("this service's own record of what it has sent,
not a live read of the broker's book"). That's unacceptable for a live
account after manual intervention, an external fill, a corporate action,
reconciliation lag, or another authorized writer.

SignalCopierEngine._reconcile_before_plain_close now requires ONE of:
  (a) a fresh broker position readback that matches the locally tracked
      quantity within tolerance, or
  (b) the account's own explicit, off-by-default
      DestinationAccount.exclusive_writer_qualified flag.
Neither holding is a fail-closed REJECTED result, never a close that
proceeds anyway.

This file also covers app/models.py's DestinationAccount.management_recipe
/ qualification_level fields -- P0-5's explicit, persisted management-recipe
declaration -- round-tripping through SignalStore's config_accounts table.
"""
from __future__ import annotations

import pytest

from app.brokers.base import BrokerAdapter
from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.models import (
    DestinationAccount,
    ManagementRecipe,
    OrderResult,
    OrderStatus,
    Side,
    Signal,
)
from app.routing import RoutingConfig, RoutingRule, load_routing_config_from_store


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _engine_for(store, account, broker, broker_name="paper"):
    routing = RoutingConfig(
        rules=[RoutingRule(source="tv", destinations=[account.account_id])],
        accounts={account.account_id: account},
    )
    return SignalCopierEngine(routing=routing, brokers={broker_name: broker}, store=store)


# --- (a) broker position readback: mismatch blocks, match allows ---


@pytest.mark.asyncio
async def test_mismatched_broker_position_blocks_plain_close(store):
    """PaperBroker has a real get_broker_position -- simulate an external
    writer (manual intervention / a fill placed directly at the broker)
    moving the broker's real book to 7 while this service's own tracked
    position still says 10."""
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    engine = _engine_for(store, account, broker)

    store.record_fill("acct1", "AAPL", Side.BUY, 10.0)
    broker.positions.setdefault("acct1", {})["AAPL"] = 7.0  # diverged from the tracked 10

    result = await engine.close_position(account, "AAPL", reason="dashboard_manual_exit")

    assert result.status == OrderStatus.REJECTED
    assert "does not match" in result.message
    assert "7.0" in result.message
    assert "10.0" in result.message
    # The order must never have reached the broker as a real close -- the
    # broker's own book is untouched (still 7.0, not flattened to 0).
    assert broker.positions["acct1"]["AAPL"] == 7.0
    # And the (unreconciled) tracked position must not have been silently
    # zeroed out either.
    assert store.get_position("acct1", "AAPL") == 10.0


@pytest.mark.asyncio
async def test_matched_broker_position_allows_plain_close(store):
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    engine = _engine_for(store, account, broker)

    await broker.place_order(Signal(source="test", symbol="AAPL", side=Side.BUY), account, 10.0, "AAPL")
    store.record_fill("acct1", "AAPL", Side.BUY, 10.0)  # matches broker's real book

    result = await engine.close_position(account, "AAPL", reason="dashboard_manual_exit")

    assert result.status == OrderStatus.FILLED
    assert result.filled_quantity == 10.0
    assert store.get_position("acct1", "AAPL") == 0.0
    assert broker.positions["acct1"]["AAPL"] == 0.0


@pytest.mark.asyncio
async def test_provider_close_signal_is_also_blocked_by_a_mismatch(store):
    """The same gate applies to a real provider CLOSE signal reaching
    SignalCopierEngine.handle_signal, not just the manual close_position
    path -- both go through _resolve_and_submit_plain_close."""
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    engine = _engine_for(store, account, broker)

    store.record_fill("acct1", "AAPL", Side.BUY, 10.0)
    broker.positions.setdefault("acct1", {})["AAPL"] = 4.0

    results = await engine.handle_signal(Signal(source="tv", symbol="AAPL", side=Side.CLOSE))

    assert len(results) == 1
    assert results[0].status == OrderStatus.REJECTED
    assert "does not match" in results[0].message


# --- (b) exclusive_writer_qualified: only reachable with no usable readback ---


class _NoReadbackBroker(BrokerAdapter):
    """A broker with NO get_broker_position override at all -- the honest
    "not supported" default from BrokerAdapter itself, same shape as most
    of this codebase's real adapters (SignalStack, IBKR, NinjaTrader,
    Rithmic, Schwab, Robinhood, Tastytrade, TradeStation, Tradovate, OANDA,
    MT4/MT5 as of this writing)."""

    name = "no-readback"

    def __init__(self):
        self.sells: list[float] = []

    async def place_order(self, signal, account, quantity, symbol):
        if signal.side == Side.SELL:
            self.sells.append(quantity)
        return OrderResult(
            account_id=account.account_id,
            status=OrderStatus.FILLED,
            signal_id=signal.id,
            filled_quantity=quantity,
            filled_price=signal.price or 0.0,
            message="filled",
        )


@pytest.mark.asyncio
async def test_unqualified_account_on_a_no_readback_broker_is_blocked(store):
    broker = _NoReadbackBroker()
    assert broker.has_position_readback_capability is False
    account = DestinationAccount(account_id="acct1", broker="no-readback")  # exclusive_writer_qualified defaults False
    engine = _engine_for(store, account, broker, broker_name="no-readback")

    store.record_fill("acct1", "AAPL", Side.BUY, 10.0)

    result = await engine.close_position(account, "AAPL", reason="dashboard_manual_exit")

    assert result.status == OrderStatus.REJECTED
    assert "exclusive_writer_qualified" in result.message
    assert broker.sells == []  # never reached the broker at all
    assert store.get_position("acct1", "AAPL") == 10.0  # untouched


@pytest.mark.asyncio
async def test_exclusive_writer_qualified_allows_close_without_a_broker_read(store):
    broker = _NoReadbackBroker()
    assert broker.has_position_readback_capability is False
    account = DestinationAccount(account_id="acct1", broker="no-readback", exclusive_writer_qualified=True)
    engine = _engine_for(store, account, broker, broker_name="no-readback")

    store.record_fill("acct1", "AAPL", Side.BUY, 10.0)

    result = await engine.close_position(account, "AAPL", reason="dashboard_manual_exit")

    assert result.status == OrderStatus.FILLED
    assert result.filled_quantity == 10.0
    assert broker.sells == [10.0]
    assert store.get_position("acct1", "AAPL") == 0.0


@pytest.mark.asyncio
async def test_exclusive_writer_qualified_does_not_override_a_real_mismatch(store):
    """(b) is only ever consulted when (a) genuinely isn't available -- a
    broker WITH a real readback capability that actively disagrees with the
    tracked position must still block the close even if the account is
    marked exclusive_writer_qualified. The flag is not a blanket override."""
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", exclusive_writer_qualified=True)
    engine = _engine_for(store, account, broker)

    store.record_fill("acct1", "AAPL", Side.BUY, 10.0)
    broker.positions.setdefault("acct1", {})["AAPL"] = 3.0

    result = await engine.close_position(account, "AAPL", reason="dashboard_manual_exit")

    assert result.status == OrderStatus.REJECTED
    assert "does not match" in result.message


# --- management_recipe / qualification_level: declaration + persistence ---


def test_management_recipe_defaults_from_managed_lifecycle():
    managed = DestinationAccount(account_id="a", broker="paper", managed_lifecycle=True)
    plain = DestinationAccount(account_id="b", broker="paper", managed_lifecycle=False)
    assert managed.management_recipe == ManagementRecipe.FULL_MANAGED_LIFECYCLE
    assert plain.management_recipe == ManagementRecipe.PLAIN_UNMANAGED
    assert plain.qualification_level is None
    assert plain.exclusive_writer_qualified is False


def test_management_recipe_explicit_value_is_honored_even_if_it_disagrees():
    # A real misconfiguration worth surfacing/auditing, not silently fixed.
    account = DestinationAccount(
        account_id="a",
        broker="paper",
        managed_lifecycle=True,
        management_recipe=ManagementRecipe.PLAIN_UNMANAGED,
    )
    assert account.management_recipe == ManagementRecipe.PLAIN_UNMANAGED
    assert account.managed_lifecycle is True


def test_management_recipe_and_qualification_level_round_trip_through_store(store):
    store.upsert_config_account(
        account_id="acct1",
        broker="paper",
        managed_lifecycle=True,
        management_recipe="full_managed_lifecycle",
        qualification_level="qualified",
        exclusive_writer_qualified=True,
    )
    rows = store.list_config_accounts()
    assert len(rows) == 1
    row = rows[0]
    assert row["management_recipe"] == "full_managed_lifecycle"
    assert row["qualification_level"] == "qualified"
    assert row["exclusive_writer_qualified"] is True

    # And through app/routing.py's *_from_store loader into a real
    # DestinationAccount, same as production reads it.
    routing = load_routing_config_from_store(store)
    account = routing.accounts["acct1"]
    assert account.management_recipe == ManagementRecipe.FULL_MANAGED_LIFECYCLE
    assert account.qualification_level == "qualified"
    assert account.exclusive_writer_qualified is True


def test_management_recipe_derived_default_round_trips_when_not_given(store):
    """A caller that never passes management_recipe explicitly (e.g. an
    older code path, or a plain upsert_config_account call) still gets a
    real, non-NULL, correctly-derived value back -- persisted, not just
    computed on read."""
    store.upsert_config_account(account_id="acct1", broker="paper", managed_lifecycle=False)
    row = store.list_config_accounts()[0]
    assert row["management_recipe"] == "plain_unmanaged"
    assert row["exclusive_writer_qualified"] is False

    store.upsert_config_account(account_id="acct2", broker="paper", managed_lifecycle=True)
    row2 = next(r for r in store.list_config_accounts() if r["account_id"] == "acct2")
    assert row2["management_recipe"] == "full_managed_lifecycle"


def test_upsert_config_account_updates_management_recipe_in_place(store):
    store.upsert_config_account(account_id="acct1", broker="paper", managed_lifecycle=False)
    assert store.list_config_accounts()[0]["management_recipe"] == "plain_unmanaged"

    store.upsert_config_account(
        account_id="acct1",
        broker="paper",
        managed_lifecycle=False,
        management_recipe="full_managed_lifecycle",
        qualification_level="pending_review",
    )
    row = store.list_config_accounts()[0]
    assert row["management_recipe"] == "full_managed_lifecycle"
    assert row["qualification_level"] == "pending_review"
