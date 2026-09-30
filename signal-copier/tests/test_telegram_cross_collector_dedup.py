"""Track 5, point 6: if a bot collector AND a user-account collector are
both (mis)configured against the same Telegram channel, or one collector
redelivers on reconnect, the SAME (channel_id, message_id, revision)
provider event must never produce two separate engine dispatches / two
broker orders -- even though each collector mints its OWN, distinct,
random `Signal.id` when it parses the message.

This extends SIG-01 (tests/test_sig01_duplicate_submission_protection.py,
which covers same-signal-id replay) to the cross-collector case: two
DIFFERENT signal ids sharing the same real provider identity."""
import pytest

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.models import DestinationAccount, OrderStatus, Side, Signal
from app.routing import RoutingConfig, RoutingRule


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _engine(store):
    broker = PaperBroker()
    routing = RoutingConfig(
        rules=[RoutingRule(source="telegram_user", destinations=["acct1"])],
        accounts={"acct1": DestinationAccount(account_id="acct1", broker="paper")},
    )
    return SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store), broker


@pytest.mark.asyncio
async def test_bot_and_user_account_collector_both_observing_same_message_produce_one_order(store):
    """Two Signal objects with DIFFERENT Signal.id (as if parsed
    independently by TelegramSource and TelegramUserSource) but the SAME
    (channel_id, message_id) -- the real scenario if an operator ever had
    both a bot and a user-account collector pointed at the same chat."""
    engine, broker = _engine(store)

    from_bot = Signal(
        source="telegram_user", symbol="BTCUSDT", side=Side.BUY, quantity=1.0,
        channel_id="-100123", message_id="42",
    )
    from_user_account = Signal(
        source="telegram_user", symbol="BTCUSDT", side=Side.BUY, quantity=1.0,
        channel_id="-100123", message_id="42",
    )
    assert from_bot.id != from_user_account.id  # distinct, independently-minted ids

    place_order_calls = []
    original_place_order = broker.place_order

    async def tracking_place_order(*args, **kwargs):
        place_order_calls.append(args)
        return await original_place_order(*args, **kwargs)

    broker.place_order = tracking_place_order

    first = await engine.handle_signal(from_bot)
    second = await engine.handle_signal(from_user_account)

    assert len(place_order_calls) == 1  # only ONE real broker order, not two
    assert len(first) == 1
    assert len(second) == 1
    assert first[0].status == OrderStatus.FILLED
    assert second[0].status == OrderStatus.FILLED
    # The replayed result is the SAME underlying broker order.
    assert second[0].broker_order_id == first[0].broker_order_id


@pytest.mark.asyncio
async def test_reconnect_redelivery_of_the_same_message_by_the_same_collector_does_not_resubmit(store):
    """One collector's own reconnect (MTProto's own gap-recovery, or a
    process restart before the checkpoint was durably advanced) handing
    the SAME message back through `handle_signal` a second time, as a
    freshly-parsed Signal with a new id -- must not resubmit either."""
    engine, broker = _engine(store)

    original = Signal(
        source="telegram_user", symbol="ETHUSDT", side=Side.BUY, quantity=2.0,
        channel_id="-100999", message_id="7",
    )
    redelivered = Signal(
        source="telegram_user", symbol="ETHUSDT", side=Side.BUY, quantity=2.0,
        channel_id="-100999", message_id="7",
    )

    place_order_calls = []
    original_place_order = broker.place_order

    async def tracking_place_order(*args, **kwargs):
        place_order_calls.append(args)
        return await original_place_order(*args, **kwargs)

    broker.place_order = tracking_place_order

    await engine.handle_signal(original)
    await engine.handle_signal(redelivered)

    assert len(place_order_calls) == 1


@pytest.mark.asyncio
async def test_an_edit_with_a_distinct_revision_id_is_not_deduped_against_the_original(store):
    """A genuine EDIT (same message_id, new revision_id) is a real, new
    event -- it must NOT be collapsed onto the original's dedup identity
    (that would silently swallow a legitimate revised instruction)."""
    engine, broker = _engine(store)

    original = Signal(
        source="telegram_user", symbol="BTCUSDT", side=Side.BUY, quantity=1.0,
        channel_id="-100123", message_id="42",
    )
    edited = Signal(
        source="telegram_user", symbol="BTCUSDT", side=Side.BUY, quantity=1.5,
        channel_id="-100123", message_id="42", revision_id="42:rev1", original_message_id="42",
    )

    place_order_calls = []
    original_place_order = broker.place_order

    async def tracking_place_order(*args, **kwargs):
        place_order_calls.append(args)
        return await original_place_order(*args, **kwargs)

    broker.place_order = tracking_place_order

    await engine.handle_signal(original)
    await engine.handle_signal(edited)

    assert len(place_order_calls) == 2  # two DIFFERENT real provider events


@pytest.mark.asyncio
async def test_signals_with_no_provider_identity_are_never_deduped_against_each_other(store):
    """Every adapter this task didn't touch (channel_id/message_id both
    None) must behave exactly as before -- no accidental collapsing of
    two genuinely unrelated signals that both happen to have None/None."""
    engine, broker = _engine(store)

    a = Signal(source="telegram_user", symbol="BTCUSDT", side=Side.BUY, quantity=1.0)
    b = Signal(source="telegram_user", symbol="BTCUSDT", side=Side.BUY, quantity=1.0)
    assert a.channel_id is None and b.channel_id is None

    place_order_calls = []
    original_place_order = broker.place_order

    async def tracking_place_order(*args, **kwargs):
        place_order_calls.append(args)
        return await original_place_order(*args, **kwargs)

    broker.place_order = tracking_place_order

    await engine.handle_signal(a)
    await engine.handle_signal(b)

    assert len(place_order_calls) == 2
