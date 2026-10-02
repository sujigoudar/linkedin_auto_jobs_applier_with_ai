import asyncio, tempfile, pathlib, logging
logging.disable(logging.CRITICAL)
from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.models import DestinationAccount, Side, Signal
from app.routing import RoutingConfig, RoutingRule

def mk(multiplier=1.0, fixed=None):
    d = pathlib.Path(tempfile.mkdtemp())
    store = SignalStore(d / "t.db")
    broker = PaperBroker()
    routing = RoutingConfig(rules=[RoutingRule(source="tg", destinations=["a1"])],
                            accounts={"a1": DestinationAccount(account_id="a1", broker="paper", multiplier=multiplier, fixed_quantity=fixed, exclusive_writer_qualified=True)})
    eng = SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store)
    calls = []
    orig = broker.place_order
    async def tracking(*a, **k):
        calls.append((a[0].side.value, a[2], a[3]))
        return await orig(*a, **k)
    broker.place_order = tracking
    return eng, store, calls

async def main():
    # 1. Edited entry message -> second live entry?
    eng, store, calls = mk()
    o = Signal(source="tg", symbol="BTCUSDT", side=Side.BUY, quantity=1.0, price=100.0, channel_id="-1", message_id="42")
    e = Signal(source="tg", symbol="BTCUSDT", side=Side.BUY, quantity=1.0, price=100.0, channel_id="-1", message_id="42", revision_id="42:rev1", original_message_id="42")
    await eng.handle_signal(o); await eng.handle_signal(e)
    print("EDIT probe: place_order calls =", calls, "| tracked position =", store.get_position("a1", "BTCUSDT"))

    # 1b. Edited message that changes only stop level -> ?
    eng, store, calls = mk()
    o = Signal(source="tg", symbol="BTCUSDT", side=Side.BUY, quantity=1.0, price=100.0, stop_loss=63000.0, channel_id="-1", message_id="43")
    e = Signal(source="tg", symbol="BTCUSDT", side=Side.BUY, quantity=1.0, price=100.0, stop_loss=64000.0, channel_id="-1", message_id="43", revision_id="43:rev1", original_message_id="43")
    r1 = await eng.handle_signal(o); r2 = await eng.handle_signal(e)
    print("EDIT-STOP probe: calls =", calls, "| results:", [x.status.value + ':' + x.message[:160] for x in r1 + r2], "| pos =", store.get_position("a1", "BTCUSDT"))

    # 2. Provider SELL on a long plain account (no quantity in message)
    eng, store, calls = mk(multiplier=1.0)
    await eng.handle_signal(Signal(source="tg", symbol="AAPL", side=Side.BUY, quantity=10.0, price=150.0, asset_class="equity"))
    r = await eng.handle_signal(Signal(source="tg", symbol="AAPL", side=Side.SELL, quantity=None, price=150.0, asset_class="equity"))
    print("SELL-no-qty probe: calls =", calls, "| statuses:", [x.status.value for x in r], "| pos after =", store.get_position("a1", "AAPL"))

    # 2b. Provider SELL when flat (paper broker)
    eng, store, calls = mk(multiplier=1.0)
    r = await eng.handle_signal(Signal(source="tg", symbol="AAPL", side=Side.SELL, quantity=5.0, price=150.0, asset_class="equity"))
    print("SELL-flat probe: calls =", calls, "| statuses:", [x.status.value + ':' + x.message[:160] for x in r], "| pos after =", store.get_position("a1", "AAPL"))

    # 2c. fixed_quantity account: provider SELL half -> ?
    eng, store, calls = mk(fixed=5.0)
    await eng.handle_signal(Signal(source="tg", symbol="AAPL", side=Side.BUY, quantity=100.0, price=150.0, asset_class="equity"))
    r = await eng.handle_signal(Signal(source="tg", symbol="AAPL", side=Side.SELL, quantity=50.0, price=150.0, asset_class="equity"))
    print("SELL-fixedqty probe: calls =", calls, "| pos after =", store.get_position("a1", "AAPL"))

    # 3. CLOSE on plain account
    eng, store, calls = mk()
    await eng.handle_signal(Signal(source="tg", symbol="AAPL", side=Side.BUY, quantity=10.0, price=150.0, asset_class="equity"))
    r = await eng.handle_signal(Signal(source="tg", symbol="AAPL", side=Side.CLOSE, asset_class="equity"))
    print("CLOSE probe: calls =", calls, "| pos after =", store.get_position("a1", "AAPL"))

    # 4. No-quantity BUY -> 1.0 unit
    eng, store, calls = mk(multiplier=2.0)
    r = await eng.handle_signal(Signal(source="tg", symbol="BTCUSDT", side=Side.BUY, quantity=None))
    print("NO-QTY BUY probe (multiplier 2):", calls, [x.status.value for x in r])

    # 5. Same message via two transports w/o price -> correlation?
    eng, store, calls = mk()
    a = Signal(source="tg", symbol="BTCUSDT", side=Side.BUY, quantity=1.0, channel_id="chanA", message_id="1")
    b = Signal(source="tg", symbol="BTCUSDT", side=Side.BUY, quantity=1.0, channel_id="chanB", message_id="9")
    await eng.handle_signal(a); await eng.handle_signal(b)
    print("2-transport no-price probe: calls =", calls)
    eng, store, calls = mk()
    a = Signal(source="tg", symbol="BTCUSDT", side=Side.BUY, quantity=1.0, price=100.0, channel_id="chanA", message_id="1")
    b = Signal(source="tg", symbol="BTCUSDT", side=Side.BUY, quantity=1.0, price=100.0, channel_id="chanB", message_id="9")
    await eng.handle_signal(a); await eng.handle_signal(b)
    print("2-transport with-price probe: calls =", calls)
    # 5b. two analysts same ticker same channel (distinct message ids)
    eng, store, calls = mk()
    a = Signal(source="tg", symbol="BTCUSDT", side=Side.BUY, quantity=1.0, price=100.0, channel_id="chanA", message_id="1", analyst="alice")
    b = Signal(source="tg", symbol="BTCUSDT", side=Side.BUY, quantity=1.0, price=100.0, channel_id="chanA", message_id="2", analyst="bob")
    await eng.handle_signal(a); await eng.handle_signal(b)
    print("2-analysts same channel probe: calls =", calls, "pos=", store.get_position("a1","BTCUSDT"))
    # 5c. re-post of old signal 20 minutes later via other transport
    from datetime import datetime, timezone, timedelta
    eng, store, calls = mk()
    a = Signal(source="tg", symbol="BTCUSDT", side=Side.BUY, quantity=1.0, price=100.0, channel_id="chanA", message_id="1", received_at=datetime.now(timezone.utc)-timedelta(minutes=20))
    b = Signal(source="tg", symbol="BTCUSDT", side=Side.BUY, quantity=1.0, price=100.0, channel_id="chanB", message_id="9")
    await eng.handle_signal(a); await eng.handle_signal(b)
    print("re-post 20min later probe: calls =", calls)

asyncio.run(main())
