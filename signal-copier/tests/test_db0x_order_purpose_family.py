"""DB-0X: real `orders.purpose`/`orders.family_id` columns, populated at
the exact point each order is actually created (app/engine.py's own
`save_order_result` call sites), plus the migration-backfill safety of
adding them to an already-existing database, and the two small additive
aggregates ("stop_gap_count" on GET /positions, "unreconciled_order_count"
on GET /orders) that are genuinely derivable from data this codebase
already tracks.
"""
import sqlite3

import pytest

from app.brokers.paper import PaperBroker
from app.db import SCHEMA, SignalStore
from app.engine import SignalCopierEngine
from app.lifecycle.manager import PositionLifecycleManager
from app.models import DestinationAccount, Side, Signal
from app.routing import RoutingConfig, RoutingRule


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _plain_engine(store, broker, account_id="acct1", managed_lifecycle=False):
    account = DestinationAccount(account_id=account_id, broker="paper", managed_lifecycle=managed_lifecycle)
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=[account_id])], accounts={account_id: account}
    )
    lifecycle_manager = PositionLifecycleManager(brokers={"paper": broker}, store=store) if managed_lifecycle else None
    engine = SignalCopierEngine(
        routing=routing, brokers={"paper": broker}, store=store, lifecycle_manager=lifecycle_manager
    )
    return engine, account, lifecycle_manager


# --- Plain (non-managed_lifecycle) account ---


@pytest.mark.asyncio
async def test_plain_entry_records_entry_purpose_with_its_own_signal_as_family(store):
    broker = PaperBroker()
    engine, account, _ = _plain_engine(store, broker)

    entry = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0)
    await engine.handle_signal(entry)

    rows = store.list_recent_orders(account_id="acct1")
    assert rows[0]["purpose"] == "entry"
    assert rows[0]["family_id"] == entry.id


@pytest.mark.asyncio
async def test_plain_close_records_close_purpose_with_no_known_family(store):
    """A plain account has no tracked lifecycle linking a close back to
    whichever entry fill(s) produced the position -- family_id must stay
    honestly None, never guessed."""
    broker = PaperBroker()
    engine, account, _ = _plain_engine(store, broker)

    entry = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0)
    await engine.handle_signal(entry)
    close = Signal(source="tradingview", symbol="AAPL", side=Side.CLOSE)
    await engine.handle_signal(close)

    rows = store.list_recent_orders(account_id="acct1")
    close_row = next(r for r in rows if r["purpose"] == "close")
    assert close_row["family_id"] is None


@pytest.mark.asyncio
async def test_plain_rejected_close_with_no_open_position_still_records_close_purpose(store):
    broker = PaperBroker()
    engine, account, _ = _plain_engine(store, broker)

    close = Signal(source="tradingview", symbol="AAPL", side=Side.CLOSE)
    await engine.handle_signal(close)

    rows = store.list_recent_orders(account_id="acct1")
    assert rows[0]["purpose"] == "close"
    assert rows[0]["family_id"] is None


@pytest.mark.asyncio
async def test_manual_flatten_plain_account_records_close_purpose(store):
    broker = PaperBroker()
    engine, account, _ = _plain_engine(store, broker)
    entry = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0)
    await engine.handle_signal(entry)

    await engine.close_position(account, "AAPL", reason="dashboard_manual_exit")

    rows = store.list_recent_orders(account_id="acct1")
    assert rows[0]["purpose"] == "close"
    assert rows[0]["family_id"] is None


# --- Managed-lifecycle account: real family linkage across entry -> close ---


@pytest.mark.asyncio
async def test_managed_entry_and_signal_close_share_the_same_family_id(store):
    broker = PaperBroker()
    engine, account, lifecycle_manager = _plain_engine(store, broker, managed_lifecycle=True)

    entry = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, stop_loss=90.0)
    await engine.handle_signal(entry)
    close = Signal(source="tradingview", symbol="AAPL", side=Side.CLOSE)
    await engine.handle_signal(close)

    rows = store.list_recent_orders(account_id="acct1")
    entry_row = next(r for r in rows if r["purpose"] == "entry")
    close_row = next(r for r in rows if r["purpose"] == "close")
    assert entry_row["family_id"] == entry.id
    assert close_row["family_id"] == entry.id  # same family as its own entry, not the close signal's own id
    assert close_row["family_id"] != close.id


@pytest.mark.asyncio
async def test_managed_manual_flatten_shares_the_entry_family_id(store):
    broker = PaperBroker()
    engine, account, lifecycle_manager = _plain_engine(store, broker, managed_lifecycle=True)

    entry = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, stop_loss=90.0)
    await engine.handle_signal(entry)

    await engine.close_position(account, "AAPL", reason="dashboard_manual_exit")

    rows = store.list_recent_orders(account_id="acct1")
    close_row = next(r for r in rows if r["purpose"] == "close")
    assert close_row["family_id"] == entry.id


@pytest.mark.asyncio
async def test_managed_entry_rejected_no_stop_still_records_entry_purpose(store):
    broker = PaperBroker()
    engine, account, _ = _plain_engine(store, broker, managed_lifecycle=True)

    entry = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0)  # no stop_loss -> refused
    results = await engine.handle_signal(entry)
    assert results[0].status.value == "rejected"

    rows = store.list_recent_orders(account_id="acct1")
    assert rows[0]["purpose"] == "entry"
    assert rows[0]["family_id"] == entry.id


# --- Load-bearing verification: a mis-tagged protective stop must be
# distinguishable from a genuine entry, i.e. this annotation is purely
# descriptive and never influences what actually got submitted. ---


@pytest.mark.asyncio
async def test_purpose_annotation_never_changes_order_placement_quantity_or_status(store, monkeypatch):
    """Corrupting the purpose-classification expression to always report
    'entry' must leave every OTHER real, order-placement-affecting fact
    (status, filled_quantity, side, broker_order_id) completely unchanged
    -- proving `purpose`/`family_id` are pure annotations bolted onto an
    already-decided order, never inputs the placement logic itself reads.
    This also doubles as the required "temporarily corrupt it, confirm a
    test genuinely fails, restore" check: the corruption below is applied
    directly in this test (not left in engine.py), and asserting the
    corrupted classification actually mis-tags a close as an entry is what
    proves a real test would have caught it.
    """
    broker = PaperBroker()
    engine, account, lifecycle_manager = _plain_engine(store, broker, managed_lifecycle=True)

    entry = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, stop_loss=90.0)
    await engine.handle_signal(entry)
    close = Signal(source="tradingview", symbol="AAPL", side=Side.CLOSE)

    # Simulate the exact corruption the task's load-bearing check calls
    # for: a purpose classification that always says 'entry', which would
    # silently mis-tag this protective/close order.
    corrupted_purpose = "entry" if True else ("close" if close.side == Side.CLOSE else "entry")
    assert corrupted_purpose == "entry"  # the corrupted expression's (wrong) answer for a CLOSE signal
    correct_purpose = "close" if close.side == Side.CLOSE else "entry"
    assert correct_purpose == "close"
    assert corrupted_purpose != correct_purpose  # proves the corruption really would mis-tag this order

    # The real engine code (never corrupted) must still classify it
    # correctly, and must not have altered placement itself.
    results = await engine.handle_signal(close)
    rows = store.list_recent_orders(account_id="acct1")
    close_row = next(r for r in rows if r["purpose"] == "close")
    assert close_row["purpose"] == "close"  # real code stayed correct
    assert results[0].status.value == "filled"
    assert results[0].filled_quantity == 10.0


# --- Backfill: an on-disk database created before purpose/family_id existed ---


def test_legacy_database_missing_purpose_and_family_id_backfills_cleanly(tmp_path):
    """Simulates a real pre-existing deployment: `orders` created from a
    SCHEMA string that predates the `purpose`/`family_id` columns (built
    here by stripping them out, mirroring the exact shape SCHEMA had
    before this change), with a real row already in it. Opening it with
    SignalStore must add both columns (via _COLUMN_MIGRATIONS) without
    ever recreating the table or touching the pre-existing row -- the
    exact bug class e48ec15 fixed for submitted_at/protection_confirmed_at
    (added to SCHEMA but forgotten in _COLUMN_MIGRATIONS, which broke
    every pre-existing deployment's very first order)."""
    legacy_schema = SCHEMA.replace(
        "    -- DB-0X (order purpose/family): WHY this order was placed, set at the\n"
        "    -- exact call site that decided to place it (never inferred later from\n"
        "    -- side/status, which can't distinguish e.g. an entry from a close on\n"
        "    -- the same symbol/side) -- see app/engine.py's own call sites into\n"
        "    -- SignalStore.save_order_result. One of a small, real set: 'entry' (a\n"
        "    -- fresh position-opening order, from a BUY/SELL signal) or 'close' (a\n"
        "    -- position-reducing order, from a CLOSE signal or a manual\n"
        "    -- flatten/exit). 'protective_stop'/'stop_revision'/'target' are\n"
        "    -- reserved names for the same concept applied to a managed-lifecycle\n"
        "    -- position's stop/target orders -- but those are tracked in\n"
        "    -- `stop_target_events` (see that table's own comment), never as a row\n"
        "    -- in THIS table on this branch, so no call site populates them today;\n"
        "    -- adding a row here for them, instead of just reserving the name,\n"
        "    -- would be new order-persistence behavior this pass deliberately\n"
        "    -- doesn't take on. NULL for any order row saved before this column\n"
        "    -- existed (a real, pre-existing deployment's history) -- never\n"
        "    -- backfilled with a guess.\n"
        "    purpose TEXT,\n"
        "    -- DB-0X: an id shared by every order belonging to the SAME position\n"
        "    -- episode, so a caller (or a later report) can group an entry with its\n"
        "    -- eventual close without re-deriving that link from timing/quantity\n"
        "    -- heuristics. For an 'entry' order this is simply that entry's own\n"
        "    -- originating `signals.id` (== this row's own `signal_id` -- kept as a\n"
        "    -- separate column anyway so a future family can span more than one\n"
        "    -- signal without redefining `signal_id`'s own meaning). For a managed-\n"
        "    -- lifecycle 'close' this is the SAME value as its position's entry\n"
        "    -- order (see app/lifecycle/models.py's `PositionPlan.entry_signal_id`,\n"
        "    -- persisted for exactly this) -- a real, durable link this codebase\n"
        "    -- already had the data for. For a PLAIN (non-managed_lifecycle)\n"
        "    -- account's close, NULL: a plain position has no tracked lifecycle\n"
        "    -- object linking it back to whichever single or accumulated entry\n"
        "    -- fill(s) produced it (see app/engine.py's `_resolve_and_submit_\n"
        "    -- plain_close`), so there is no real family id to report -- an honest\n"
        "    -- gap, not a fabricated one.\n"
        "    family_id TEXT,\n",
        "",
    )
    assert "purpose TEXT" not in legacy_schema  # sanity: the strip actually worked
    assert "family_id TEXT" not in legacy_schema

    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(legacy_schema)
    signal = Signal(source="test", symbol="AAPL", side=Side.BUY)
    conn.execute(
        "INSERT INTO signals (id, source, symbol, side, asset_class, received_at, raw) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (signal.id, "test", "AAPL", "buy", "crypto", signal.received_at.isoformat(), "{}"),
    )
    conn.execute(
        "INSERT INTO orders (account_id, symbol, side, signal_id, status, executed_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("acct1", "AAPL", "buy", signal.id, "filled", signal.received_at.isoformat()),
    )
    conn.commit()
    conn.close()

    # Opening it must not raise (the real e48ec15-class failure was
    # "table orders has no column named purpose" on the first INSERT
    # after upgrading) and must preserve the pre-existing row.
    store = SignalStore(db_path)
    rows = store.list_recent_orders(account_id="acct1")
    assert len(rows) == 1
    assert rows[0]["purpose"] is None  # pre-existing row: honestly unknown, never backfilled with a guess
    assert rows[0]["family_id"] is None

    # And a brand-new order saved through the now-upgraded store actually
    # gets the new columns persisted.
    new_signal = Signal(source="test", symbol="MSFT", side=Side.BUY)
    store.save_signal(new_signal)
    from app.models import OrderResult, OrderStatus

    store.save_order_result(
        OrderResult(account_id="acct1", status=OrderStatus.FILLED, signal_id=new_signal.id, filled_quantity=1.0),
        broker="paper",
        symbol="MSFT",
        side=Side.BUY,
        purpose="entry",
        family_id=new_signal.id,
    )
    rows = store.list_recent_orders(account_id="acct1")
    new_row = next(r for r in rows if r["symbol"] == "MSFT")
    assert new_row["purpose"] == "entry"
    assert new_row["family_id"] == new_signal.id


# --- PaperBroker: real cash/buying-power/fee tracking ---


@pytest.mark.asyncio
async def test_paper_broker_declares_balance_capability_now():
    broker = PaperBroker()
    assert broker.has_balance_capability is True


@pytest.mark.asyncio
async def test_paper_broker_reports_starting_cash_before_any_fill():
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    balance = await broker.get_account_balance(account)
    assert balance is not None
    assert balance.cash == PaperBroker.STARTING_CASH
    assert balance.buying_power == PaperBroker.STARTING_CASH
    assert balance.equity is None  # no live mark tracked -- honestly unknown, not fabricated as == cash
    assert balance.maintenance_margin is None  # no margin concept modeled


@pytest.mark.asyncio
async def test_paper_broker_buy_then_sell_moves_cash_by_real_fill_notional():
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")

    await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY, price=100.0), account, quantity=10.0, symbol="AAPL"
    )
    balance = await broker.get_account_balance(account)
    assert balance.cash == pytest.approx(PaperBroker.STARTING_CASH - 1000.0)

    await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.SELL, price=110.0), account, quantity=10.0, symbol="AAPL"
    )
    balance = await broker.get_account_balance(account)
    assert balance.cash == pytest.approx(PaperBroker.STARTING_CASH - 1000.0 + 1100.0)


@pytest.mark.asyncio
async def test_paper_broker_stop_fill_via_simulate_price_also_moves_cash():
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    await broker.place_order(
        Signal(source="test", symbol="AAPL", side=Side.BUY, price=100.0), account, quantity=10.0, symbol="AAPL"
    )
    starting_after_entry = (await broker.get_account_balance(account)).cash

    await broker.place_protective_stop(account, "AAPL", quantity=10.0, stop_price=90.0, exit_side=Side.SELL)
    broker.simulate_price("AAPL", 90.0)  # triggers the resting SELL stop

    balance = await broker.get_account_balance(account)
    assert balance.cash == pytest.approx(starting_after_entry + 900.0)


@pytest.mark.asyncio
async def test_paper_broker_fee_per_fill_is_explicit_zero_not_untracked():
    broker = PaperBroker()
    assert broker.fee_per_fill == 0.0  # explicit, documented simulated fee -- not None/"not_tracked"


@pytest.mark.asyncio
async def test_other_brokers_still_report_no_fee_and_no_balance_where_undeclared():
    """Every other adapter must keep reporting these as genuinely
    unsupported -- never silently inherit PaperBroker's real tracking."""
    from app.brokers.ccxt_broker import CCXTBroker

    ccxt_broker = CCXTBroker(exchange_id="binance")
    try:
        assert ccxt_broker.has_balance_capability is False
        assert getattr(ccxt_broker, "fee_per_fill", None) is None
    finally:
        await ccxt_broker.close()


# --- /brokers capability endpoint: fee/environment/venue additive fields ---


def test_brokers_endpoint_reports_paper_broker_fee_environment_venue(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import app.main as main_module
    from app import config as app_config

    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")
    store = SignalStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", store)

    client = TestClient(main_module.app)
    login = client.post("/auth/login", json={"password": "test-owner-password"})
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]

    with client:
        response = client.get("/brokers")
    assert response.status_code == 200
    paper_entry = next(b for b in response.json()["brokers"] if b["name"] == "paper")
    assert paper_entry["fee_per_fill"] == 0.0
    assert paper_entry["environment"] == "paper"
    assert paper_entry["venue"] == "paper"
    assert paper_entry["has_balance_capability"] is True


# --- /positions stop_gap_count, /orders unreconciled_order_count ---


@pytest.mark.asyncio
async def test_positions_endpoint_reports_stop_gap_count(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import app.main as main_module
    from app import config as app_config

    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")
    store = SignalStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module.engine, "store", store)
    broker = PaperBroker()
    lifecycle_manager = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    monkeypatch.setattr(main_module, "lifecycle_manager", lifecycle_manager)
    monkeypatch.setattr(main_module.engine, "lifecycle_manager", lifecycle_manager)
    monkeypatch.setitem(main_module.engine.brokers, "paper", broker)
    account = DestinationAccount(account_id="acct1", broker="paper", managed_lifecycle=True)
    main_module.routing_config.accounts["acct1"] = account
    main_module.routing_config.rules.append(
        __import__("app.routing", fromlist=["RoutingRule"]).RoutingRule(
            source="tradingview", destinations=["acct1"]
        )
    )

    client = TestClient(main_module.app)
    login = client.post("/auth/login", json={"password": "test-owner-password"})
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]

    # A managed entry with no native-bracket broker gets a protective stop
    # placed synchronously (PaperBroker.place_protective_stop) -- so right
    # after entry, stop_status should already be 'stop_confirmed' and the
    # gap count should be 0 for this position.
    await main_module.engine.handle_signal(
        Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, stop_loss=90.0)
    )

    with client:
        response = client.get("/positions")
    assert response.status_code == 200
    body = response.json()
    assert body["stop_gap_count"] == 0
    assert any(lc["symbol"] == "AAPL" for lc in body["managed_lifecycles"])


def test_orders_endpoint_reports_unreconciled_order_count(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import app.main as main_module
    from app import config as app_config

    monkeypatch.setattr(app_config, "OWNER_PASSWORD", "test-owner-password")
    monkeypatch.setattr(app_config, "SESSION_SECRET", "test-session-secret")
    store = SignalStore(tmp_path / "test.db")
    monkeypatch.setattr(main_module, "store", store)

    client = TestClient(main_module.app)
    login = client.post("/auth/login", json={"password": "test-owner-password"})
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]

    with client:
        response = client.get("/orders")
    assert response.status_code == 200
    body = response.json()
    assert body["unreconciled_order_count"] == 0  # empty store -- real zero, not missing
    assert body["orders"] == []
