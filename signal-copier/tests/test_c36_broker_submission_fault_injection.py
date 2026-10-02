"""Track 40: fault injection for `broker.place_order` itself -- the real
ORDER SUBMISSION call, not `get_order_status` (already covered by
tests/test_c32_fault_injection.py) or the generic raised-exception cases
in tests/test_exe01_submission_response_lost.py (which use a plain
`TimeoutError`/`RuntimeError`, not a genuine transport-layer fault from
inside the real httpx client stack a broker actually uses).

Same bounded approach as test_c32_fault_injection.py's own docstring
explains at length (no real Toxiproxy in this sandbox; `httpx.
MockTransport` raises genuine httpx transport exceptions from inside
the real client a broker adapter actually builds), applied here to
AlpacaBroker.place_order specifically, plus the one failure mode C32
never covered for ANY broker method: a real HTTP 200 whose body is a
well-formed JSON *object* but with a value of the wrong unexpected
shape (`order.get("id")` yielding something that isn't a plain string)
-- the "returns a malformed/unexpected response shape" case this
track's own brief asks for, distinct from C32's "corrupted/truncated
body" (that one doesn't even parse as JSON at all).

What this proves, in each case: `app/engine.py`'s own `except Exception
as exc: ... ambiguous_evidence_for_exception(exc)` wrapping around every
`broker.place_order` call site (already exercised with plain Python
exceptions by test_exe01_submission_response_lost.py) behaves exactly
the same way for a REAL transport-level fault or a genuinely malformed
success response -- the order is never left in an ambiguous state
without eventual reconciliation (a `PENDING` entry with no `broker_
order_id`, discoverable later via `OrderReconciler`'s broker-position
readback -- OPS-03), and the engine never silently treats a failed/
malformed submission as a successful one."""
from __future__ import annotations

import httpx
import pytest

from app.brokers.alpaca import AlpacaBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.lifecycle.manager import PositionLifecycleManager
from app.models import AssetClass, DestinationAccount, OrderStatus, Signal, Side
from app.reconciliation import OrderReconciler
from app.routing import RoutingConfig, RoutingRule


@pytest.fixture(autouse=True)
def _alpaca_credentials(monkeypatch):
    monkeypatch.setenv("ALPACA_ACCT_A_API_KEY", "test-a")
    monkeypatch.setenv("ALPACA_ACCT_A_API_SECRET", "test-a")


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def _force_release_approved(store, *, adapter_type, route_key, asset_class="equity", product_type="default", environment="paper"):
    """Track 1b's live-routing qualification gate (app/engine.py's own
    `_check_route_qualified`) refuses ANY live (non-paper) entry whose
    route has no recorded `release_approved` -- same bypass-the-ladder
    test-fixture idiom tests/test_broker_capability_gate.py's own
    `_force_release_approved` already uses, reused verbatim here since
    this file is about broker-transport fault injection, not the
    qualification ladder itself."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    with store._connect() as conn:
        for state in [
            "implemented", "configured", "authenticated", "account_entitled", "protocol_tested", "venue_tested",
            "release_approved",
        ]:
            conn.execute(
                "INSERT OR REPLACE INTO route_qualifications "
                "(adapter_type, route_key, asset_class, product_type, environment, state, recorded_at, recorded_by, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (adapter_type, route_key, asset_class, product_type, environment, state, now, "test-fixture", "test-only bypass"),
            )


def _engine(store, broker, account):
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=[account.account_id])],
        accounts={account.account_id: account},
    )
    lifecycle_manager = PositionLifecycleManager(brokers={"alpaca": broker}, store=store)
    engine = SignalCopierEngine(
        routing=routing, brokers={"alpaca": broker}, store=store, lifecycle_manager=lifecycle_manager
    )
    return engine, lifecycle_manager


def _faulty_transport_alpaca_broker(fault: Exception) -> AlpacaBroker:
    """A real AlpacaBroker whose transport raises `fault` on every
    request -- a genuine connection-level/timeout failure reaching
    `place_order`'s own POST, not a crafted HTTP response. Also mocks
    get_account_balance to return a valid balance so the buying_power
    gate passes (WP-32: buying_power check is fail-closed when adapter
    can't report it, but a mocked transport can't reach the account
    endpoint either, so we mock it directly)."""
    broker = AlpacaBroker()

    def transport(request: httpx.Request) -> httpx.Response:
        raise fault

    broker._client = httpx.AsyncClient(transport=httpx.MockTransport(transport))

    # Mock get_account_balance to return a valid balance so WP-32's
    # buying_power check passes (instead of fail-closed rejection)
    async def mock_get_account_balance(account):
        from app.models import AccountBalance
        return AccountBalance(
            account_id=account.account_id,
            cash=100000.0,
            equity=100000.0,
            buying_power=100000.0,
            maintenance_margin=None,
        )

    broker.get_account_balance = mock_get_account_balance
    return broker


def _malformed_success_response_alpaca_broker() -> AlpacaBroker:
    """A real AlpacaBroker whose POST /v2/orders returns a genuine HTTP
    200 (so `response.raise_for_status()` never trips `place_order`'s own
    `except httpx.HTTPError` at all) with a well-formed JSON OBJECT, but
    one that does not carry the shape `place_order` expects --
    `order.get("id")` returns a nested dict instead of a string
    `broker_order_id`, and `order.get("status")` is entirely absent. A
    real venue returning this would mean its own API contract drifted
    out from under this adapter -- `place_order` itself does no
    validation on `order` at all (see app/brokers/alpaca.py), so this
    reaches `OrderResult(broker_order_id=order.get("id"), ...)` as-is.
    Also mocks get_account_balance to return a valid balance so the
    buying_power gate passes (WP-32)."""
    broker = AlpacaBroker()

    def transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": {"unexpected": "nested-object-not-a-string"}}, request=request)

    broker._client = httpx.AsyncClient(transport=httpx.MockTransport(transport))

    # Mock get_account_balance to return a valid balance so WP-32's
    # buying_power check passes
    async def mock_get_account_balance(account):
        from app.models import AccountBalance
        return AccountBalance(
            account_id=account.account_id,
            cash=100000.0,
            equity=100000.0,
            buying_power=100000.0,
            maintenance_margin=None,
        )

    broker.get_account_balance = mock_get_account_balance
    return broker


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [httpx.ConnectError("connection refused"), httpx.ReadTimeout("timed out"), httpx.ConnectTimeout("timed out")],
    ids=["connect-error", "read-timeout", "connect-timeout"],
)
async def test_place_order_surviving_a_real_transport_fault_retains_recovery_intent_not_a_silent_assumed_success(
    store, fault
):
    """`place_order` itself catches `httpx.HTTPError` (app/brokers/
    alpaca.py's own try/except around the POST) and returns a normal
    `OrderResult(status=ERROR, ...)` -- it never raises past its own
    boundary for this fault class. The real property under test is one
    level up: the engine must treat that ERROR result as a genuinely
    failed submission (never retained as a false "filled"/"pending-with-
    confirmed-fill"), and -- since a connection-level fault reaching the
    REAL venue is genuinely ambiguous (the order may have been accepted
    before the response was lost) -- must still retain SOME durable
    recovery intent for a managed-lifecycle account, exactly like
    test_exe01_submission_response_lost.py's own plain-exception cases."""
    broker = _faulty_transport_alpaca_broker(fault)
    account = DestinationAccount(account_id="acct_a", broker="alpaca", managed_lifecycle=True)
    engine, lifecycle_manager = _engine(store, broker, account)
    _force_release_approved(store, adapter_type="alpaca", route_key="acct_a")

    try:
        signal = Signal(
            source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, price=100.0, stop_loss=90.0,
            asset_class=AssetClass.EQUITY,
        )
        results = await engine.handle_signal(signal)
    finally:
        await broker.close()

    assert results[0].status == OrderStatus.ERROR
    lifecycle = lifecycle_manager.get_lifecycle("acct_a", "AAPL")
    assert lifecycle is not None
    assert lifecycle.pending_entry is not None, (
        "a real transport fault during submission must retain recovery intent, "
        "never be treated as if nothing was ever submitted"
    )


@pytest.mark.asyncio
async def test_place_order_returning_a_malformed_success_shape_never_silently_assumes_a_clean_fill(store):
    """A genuinely malformed-but-200 response is the inverse danger from
    the connect-error case above: here the venue DID respond successfully
    at the transport level, so there's no ambiguity about whether the
    request reached it -- the danger is the engine (or a later
    reconciliation pass) trusting a garbage `broker_order_id` value as
    if it were a normal opaque string. This proves the lifecycle's own
    pending-entry bookkeeping tolerates the malformed id (never crashes
    on it) and still leaves the position correctly recorded as not-yet-
    confirmed, rather than treating the malformed response as "done"."""
    broker = _malformed_success_response_alpaca_broker()
    account = DestinationAccount(account_id="acct_a", broker="alpaca", managed_lifecycle=True)
    engine, lifecycle_manager = _engine(store, broker, account)
    _force_release_approved(store, adapter_type="alpaca", route_key="acct_a")

    try:
        signal = Signal(
            source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, price=100.0, stop_loss=90.0,
            asset_class=AssetClass.EQUITY,
        )
        results = await engine.handle_signal(signal)
    finally:
        await broker.close()

    # place_order itself never raised (a real 200 reached it) -- the
    # result is PENDING, same as any other accepted-but-not-yet-filled
    # submission. `broker_order_id` is coerced to a real string (Track
    # 40's own fix, app/brokers/alpaca.py's `_coerce_broker_order_id` --
    # this exact test reproduced an unhandled `sqlite3.ProgrammingError`
    # crash before that fix existed, see that function's own docstring)
    # rather than carrying the malformed JSON value through unchanged,
    # which used to reach the database layer as a raw `dict` and crash.
    assert results[0].status == OrderStatus.PENDING
    assert results[0].broker_order_id == "{'unexpected': 'nested-object-not-a-string'}"

    lifecycle = lifecycle_manager.get_lifecycle("acct_a", "AAPL")
    assert lifecycle is not None
    # Not yet CONFIRMED owned -- this build never treats "the venue
    # responded 200" alone as proof of a real fill; only a genuine fill
    # notification/reconciliation readback would move this.
    assert lifecycle.confirmed_owned_quantity == 0.0


@pytest.mark.asyncio
async def test_reconciler_survives_the_same_malformed_id_without_crashing_the_whole_pass(store):
    """Follow-up at the reconciliation layer: OrderReconciler polls
    PENDING orders by their own `broker_order_id` -- a malformed
    (non-string) id stored verbatim above must not crash the WHOLE
    reconciliation pass when it's this exact order's turn, the same
    "one order's failure must not block the rest" contract
    test_c32_fault_injection.py already proves for a different fault
    type."""
    broker = _malformed_success_response_alpaca_broker()
    account = DestinationAccount(account_id="acct_a", broker="alpaca", managed_lifecycle=True)
    engine, lifecycle_manager = _engine(store, broker, account)
    _force_release_approved(store, adapter_type="alpaca", route_key="acct_a")

    signal = Signal(
            source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0, price=100.0, stop_loss=90.0,
            asset_class=AssetClass.EQUITY,
        )
    await engine.handle_signal(signal)

    reconciler = OrderReconciler(store, {"alpaca": broker}, lifecycle_manager=lifecycle_manager)
    try:
        # Must not raise -- a malformed broker_order_id reaching
        # get_order_status (a dict, where a string was expected in the
        # URL path) is exactly the kind of fault this pass must survive.
        corrected = await reconciler.reconcile_once()
    finally:
        await broker.close()

    assert corrected == 0  # nothing correctable from this one malformed order, but the pass itself completed cleanly
