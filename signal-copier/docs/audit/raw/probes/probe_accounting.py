"""Throwaway empirical probe for the accounting audit. Uses temp DBs only.
Reuses tests/test_trk23_managed_lifecycle_export.py's scripted broker helper."""
import asyncio
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, "/home/user/linkedin_auto_jobs_applier_with_ai/signal-copier")
sys.path.insert(0, "/home/user/linkedin_auto_jobs_applier_with_ai")

from signal_platform_contracts import EventType  # noqa: E402

from app.capital_allocator import confirmed_open_notional, confirmed_strategy_notional  # noqa: E402
from app.db import SignalStore  # noqa: E402
from app.economics import compute_account_economics  # noqa: E402
from app.engine import SignalCopierEngine  # noqa: E402
from app.lifecycle.manager import PositionLifecycleManager  # noqa: E402
from app.models import DestinationAccount, OrderResult, OrderStatus, Side, Signal  # noqa: E402
from app.provider_value import compute_provider_value_from_episodes  # noqa: E402
from app.reconciliation import OrderReconciler  # noqa: E402
from app.routing import RoutingConfig, RoutingRule  # noqa: E402
from app.trade_episode import compute_trade_episodes  # noqa: E402
from tests.test_trk23_managed_lifecycle_export import _ScriptedBroker  # noqa: E402


def _store():
    d = tempfile.mkdtemp()
    return SignalStore(Path(d) / "probe.db")


def _rows(store, account_id):
    return [
        (r["id"], r["side"], r["status"], r["purpose"], r["family_id"], r["filled_quantity"], r["filled_price"], r["executed_at"])
        for r in store.list_recent_orders(limit=50, account_id=account_id)
    ]


def _exec_exports(store):
    return [e for e in store.list_undelivered_export_events() if e.event_type == EventType.EXECUTION_APPLIED]


def _managed(store, broker, account_id="acct1"):
    account = DestinationAccount(account_id=account_id, broker="paper", managed_lifecycle=True)
    routing = RoutingConfig(rules=[RoutingRule(source="tradingview", destinations=[account_id])], accounts={account_id: account})
    lm = PositionLifecycleManager(brokers={"paper": broker}, store=store)
    engine = SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store, lifecycle_manager=lm)
    return engine, account, lm


def _plain(store, broker, account_id="plain1"):
    account = DestinationAccount(account_id=account_id, broker="paper", managed_lifecycle=False)
    routing = RoutingConfig(rules=[RoutingRule(source="tradingview", destinations=[account_id])], accounts={account_id: account})
    engine = SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store)
    return engine, account


async def scenario_a():
    print("\n=== A: managed provider CLOSE (sync fill) ===")
    store = _store()
    broker = _ScriptedBroker()
    engine, account, lm = _managed(store, broker)
    await engine.handle_signal(Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, price=50.0, stop_loss=45.0))
    res = await engine.handle_signal(Signal(source="tradingview", symbol="AAPL", side=Side.CLOSE, price=55.0))
    print("close result:", res[0].status, res[0].filled_quantity, res[0].filled_price)
    print("orders rows (id, side, status, purpose, family, qty, price):", [r[:7] for r in _rows(store, "acct1")])
    econ = compute_account_economics(store, "acct1")
    print("economics realized_pnl=", econ.realized_pnl, "incomplete_symbols=", econ.incomplete_symbols,
          "open_qty=", {s: e.open_quantity for s, e in econ.per_symbol.items()},
          "completed_episodes=", {s: e.completed_episodes for s, e in econ.per_symbol.items()})
    print("positions.net_quantity=", store.get_position("acct1", "AAPL"))
    episodes, skipped = compute_trade_episodes(store)
    print("episodes:", {k: (v.outcome, v.net_open_quantity, v.realized_pnl, len(v.reduction_executions)) for k, v in episodes.items()}, "skipped_no_family=", skipped)
    print("provider episode value:", {k: (v.open_episodes, v.closed_episodes, v.win_rate) for k, v in compute_provider_value_from_episodes(store).items()})
    exp = confirmed_open_notional(store, "acct1")
    print("confirmed_open_notional:", exp.notional, "unresolved=", exp.unresolved_symbols)
    print("EXECUTION_APPLIED exports:", [(e.payload["side"], e.payload["filled_price"], e.payload["fee"]) for e in _exec_exports(store)])
    # Does the capital gate now refuse a NEW entry for this account?
    account2 = replace(account, max_notional_exposure=1_000_000.0)
    engine.routing.accounts["acct1"] = account2
    res2 = await engine.handle_signal(Signal(source="tradingview", symbol="MSFT", side=Side.BUY, quantity=1.0, price=10.0, stop_loss=9.0))
    print("next entry with a ceiling configured:", res2[0].status, "|", res2[0].message[:160])


async def scenario_b():
    print("\n=== B: plain entry PENDING -> partial fill 30 then CANCELED ===")
    store = _store()
    broker = _ScriptedBroker()
    engine, account = _plain(store, broker)
    broker.queue_place_order_result(OrderResult(account_id="plain1", status=OrderStatus.PENDING, signal_id="", broker_order_id="o1", filled_quantity=None, message="working"))
    sig = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=100.0, price=10.0)
    await engine.handle_signal(sig)
    before = _rows(store, "plain1")
    broker.script_terminal_result("o1", status=OrderStatus.REJECTED, filled_quantity=30.0, filled_price=10.0)
    rec = OrderReconciler(store, {"paper": broker})
    await rec.reconcile_once()
    print("orders rows before:", [r[:7] for r in before])
    print("orders rows after :", [r[:7] for r in _rows(store, "plain1")])
    print("positions.net_quantity=", store.get_position("plain1", "AAPL"))
    econ = compute_account_economics(store, "plain1")
    print("economics per_symbol=", {s: (e.open_quantity, e.average_cost) for s, e in econ.per_symbol.items()}, "incomplete=", econ.incomplete_symbols)
    print("EXECUTION_APPLIED exports:", len(_exec_exports(store)))


async def scenario_c():
    print("\n=== C: per-source economics filter vs a stop exit (source='lifecycle_manager') ===")
    store = _store()
    broker = _ScriptedBroker()
    engine, account, lm = _managed(store, broker)
    await engine.handle_signal(Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, price=50.0, stop_loss=45.0))
    await lm.on_stop_filled(account, "AAPL", 10.0, filled_price=44.0)
    print("orders rows:", [r[:7] for r in _rows(store, "acct1")])
    print("signals sources for filled orders:", [(o["source"], o["purpose"]) for o in store.list_filled_orders_with_signal_chronological()])
    e_all = compute_account_economics(store, "acct1")
    e_src = compute_account_economics(store, "acct1", source="tradingview")
    print("ALL-source economics: realized=", e_all.realized_pnl, "open=", {s: e.open_quantity for s, e in e_all.per_symbol.items()})
    print("source='tradingview' economics: realized=", e_src.realized_pnl, "open=", {s: e.open_quantity for s, e in e_src.per_symbol.items()})
    print("confirmed_strategy_notional('tradingview')=", confirmed_strategy_notional(store, "tradingview"))
    print("confirmed_open_notional(acct1)=", confirmed_open_notional(store, "acct1"))


async def scenario_d():
    print("\n=== D: managed TARGET exit reported PENDING, resolved by reconciliation with a real price ===")
    store = _store()
    broker = _ScriptedBroker()
    engine, account, lm = _managed(store, broker)
    await engine.handle_signal(Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, price=50.0, stop_loss=45.0))
    broker.queue_place_order_result(OrderResult(account_id="acct1", status=OrderStatus.PENDING, signal_id="", broker_order_id="tgt-1", filled_quantity=None, message="working"))
    r = await lm.request_exit(account, "AAPL", 10.0, source="target", reason="target @ 60")
    print("request_exit ->", r.status, r.broker_order_id)
    broker.script_terminal_result("tgt-1", status=OrderStatus.FILLED, filled_quantity=10.0, filled_price=60.0)
    rec = OrderReconciler(store, {"paper": broker}, lifecycle_manager=lm)
    await rec.reconcile_once()
    print("orders rows (id, side, status, purpose, family, qty, price):", [r[:7] for r in _rows(store, "acct1")])
    econ = compute_account_economics(store, "acct1")
    print("economics realized=", econ.realized_pnl, "incomplete=", econ.incomplete_symbols, "open=", {s: e.open_quantity for s, e in econ.per_symbol.items()})
    episodes, _ = compute_trade_episodes(store)
    print("episodes:", {k: (v.outcome, v.realized_pnl, v.has_unknown_price_execution) for k, v in episodes.items()})
    print("EXECUTION_APPLIED exports (side, price):", [(e.payload["side"], e.payload["filled_price"]) for e in _exec_exports(store)])
    print("confirmed_open_notional(acct1)=", confirmed_open_notional(store, "acct1"))


async def scenario_e():
    print("\n=== E: plain PENDING -> FILLED via reconciliation: executed_at / latency basis ===")
    store = _store()
    broker = _ScriptedBroker()
    engine, account = _plain(store, broker)
    broker.queue_place_order_result(OrderResult(account_id="plain1", status=OrderStatus.PENDING, signal_id="", broker_order_id="o2", filled_quantity=None, message="working"))
    sig = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, price=10.0)
    await engine.handle_signal(sig)
    before = _rows(store, "plain1")[0]
    await asyncio.sleep(1.2)
    broker.script_terminal_result("o2", status=OrderStatus.FILLED, filled_quantity=10.0, filled_price=10.0)
    await OrderReconciler(store, {"paper": broker}).reconcile_once()
    after = _rows(store, "plain1")[0]
    print("signal received_at:", sig.received_at.isoformat())
    print("executed_at at placement:", before[7])
    print("executed_at after reconcile:", after[7], "(status", after[2] + ")")
    timing = store.list_filled_orders_with_signal_timing("plain1")
    print("timing row:", timing)


async def main():
    await scenario_a()
    await scenario_b()
    await scenario_c()
    await scenario_d()
    await scenario_e()


asyncio.run(main())
