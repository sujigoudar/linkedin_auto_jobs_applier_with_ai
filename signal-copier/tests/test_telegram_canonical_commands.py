"""app/sources/telegram.py wired to emit real canonical commands for
realistic free-text patterns ("close half", "move sl to breakeven",
"cancel") and to feed app/signal_episode.py's EpisodeCorrelator so a bare
revision message's symbol can be resolved from the analyst's own open
episode -- then applied end-to-end through the real engine.

This drives TelegramSource's own `parse`/`parse_command` and the same
`handle_message` decision logic app/main.py's real bot polling loop uses
(SignalValidationError -> try parse_command -> on_command), without
needing the real python-telegram-bot network stack -- the same "test the
adapter's own logic directly" pattern this codebase already uses (see
app/sources/text_parser.py's own tests calling `parse`/`classify_*`
directly rather than exercising a live bot connection).
"""
from __future__ import annotations

import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.lifecycle.manager import PositionLifecycleManager
from app.models import AssetClass, DestinationAccount, OrderStatus, Side
from app.routing import RoutingConfig, RoutingRule
from app.signal_episode import EpisodeCorrelator
from app.sources.telegram import TelegramSource


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _engine(store, paper, account):
    routing = RoutingConfig(
        rules=[RoutingRule(source="telegram", destinations=[account.account_id])],
        accounts={account.account_id: account},
    )
    lifecycle_manager = PositionLifecycleManager(brokers={"paper": paper}, store=store)
    engine = SignalCopierEngine(
        routing=routing, brokers={"paper": paper}, store=store, lifecycle_manager=lifecycle_manager
    )
    return engine, lifecycle_manager


async def _deliver(source: TelegramSource, text: str, analyst: str) -> None:
    """`TelegramSource.route_message` IS the real decision logic
    `start()`'s bot-polling handler runs per inbound update -- calling it
    directly here exercises that same code without the network stack."""
    await source.route_message(text, analyst)


@pytest.mark.asyncio
async def test_close_half_move_sl_breakeven_and_cancel_flow_end_to_end(store):
    paper = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _engine(store, paper, account)

    correlator = EpisodeCorrelator()
    applied_results = []

    async def on_command(command):
        result = await engine.apply_canonical_command(command, account)
        applied_results.append(result)

    def symbol_for_command(analyst):
        candidates = correlator.store.open_episodes_for("telegram", analyst)
        return candidates[0].instrument if len(candidates) == 1 else None

    source = TelegramSource(
        on_signal=engine.handle_signal,
        bot_token="x",
        chat_id="1",
        asset_class=AssetClass.EQUITY,
        on_command=on_command,
        symbol_for_command=symbol_for_command,
        episode_correlator=correlator,
    )

    # 1) A fresh entry -- opens an episode AND actually enters the position
    #    through the real engine.
    await _deliver(source, "BUY AAPL 10 @ 100 SL 90", "trader_a")
    lifecycle = lifecycle_manager.get_lifecycle("acct1", "AAPL")
    assert lifecycle is not None
    assert lifecycle.confirmed_owned_quantity == 10.0
    assert lifecycle.stop.desired_price == 90.0

    # 2) "close half" -- classified as CLOSE_PERCENT(0.5), symbol resolved
    #    from the one open episode, executed for real via request_exit.
    await _deliver(source, "close half", "trader_a")
    assert applied_results[-1].status == OrderStatus.FILLED
    assert lifecycle.confirmed_owned_quantity == 5.0
    assert paper.positions["acct1"]["AAPL"] == 5.0

    # 3) "move sl to breakeven" -- resolved against the entry signal's
    #    stated price (100), actually replaces the resting stop.
    await _deliver(source, "move sl to breakeven", "trader_a")
    assert applied_results[-1].status == OrderStatus.FILLED
    assert lifecycle.stop.desired_price == 100.0

    # 4) "cancel" -- already filled, so this is correctly REJECTED (not a
    #    silent no-op, not treated as a close).
    await _deliver(source, "cancel", "trader_a")
    assert applied_results[-1].status == OrderStatus.REJECTED
    assert "already filled" in applied_results[-1].message


@pytest.mark.asyncio
async def test_cancel_before_fill_via_telegram_flow(store):
    paper = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, lifecycle_manager = _engine(store, paper, account)

    from app.lifecycle.models import PositionPlan

    # Simulate a not-yet-filled entry plan (bypassing place_order, which
    # PaperBroker always fills synchronously -- see PaperBroker's own
    # docstring) so there's something real to cancel.
    lifecycle_manager.start_plan(
        PositionPlan(account_id="acct1", symbol="AAPL", side=Side.BUY, planned_quantity=10.0, broker="paper", initial_stop=90.0)
    )

    applied_results = []

    async def on_command(command):
        applied_results.append(await engine.apply_canonical_command(command, account))

    source = TelegramSource(
        on_signal=engine.handle_signal,
        bot_token="x",
        chat_id="1",
        on_command=on_command,
        symbol_for_command=lambda analyst: "AAPL",
    )

    await _deliver(source, "cancel this trade", "trader_a")
    assert applied_results[-1].status == OrderStatus.FILLED
    assert lifecycle_manager.get_lifecycle("acct1", "AAPL") is None


@pytest.mark.asyncio
async def test_no_symbol_for_command_means_no_command_emitted(store):
    """Without symbol_for_command (or when it returns None -- no open
    episode, or an ambiguous >1), a revision message must never be guessed
    into a command against the wrong (or no) symbol."""
    paper = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    engine, _ = _engine(store, paper, account)

    called = []

    async def on_command(command):
        called.append(command)

    source = TelegramSource(on_signal=engine.handle_signal, bot_token="x", chat_id="1", on_command=on_command)

    await _deliver(source, "close half", "trader_a")
    assert called == []


def test_parse_command_directly_for_realistic_patterns():
    source = TelegramSource(
        on_signal=lambda s: None, bot_token="x", chat_id="1", symbol_for_command=lambda analyst: "GOLD"
    )
    from app.signal_commands import CommandType

    assert source.parse_command("close half", analyst="a").command_type == CommandType.CLOSE_PERCENT
    assert source.parse_command("move sl to breakeven", analyst="a").command_type == CommandType.MOVE_STOP
    assert source.parse_command("cancel", analyst="a").command_type == CommandType.CANCEL_ENTRY
