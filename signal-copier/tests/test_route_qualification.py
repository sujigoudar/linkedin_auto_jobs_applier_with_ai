"""Tests for app/qualification.py's per-exact-route live qualification
ladder and its enforcement in app/db.py's SignalStore.
record_route_qualification -- a SEPARATE, higher-bar concept from
app/brokers/base.py's implementation-derived capability introspection
(see app/qualification.py's own module docstring).

Covers, per the audit findings this closes:
  - a route claiming a state without its ladder prerequisites is rejected
  - SignalStack (no real order-status/position/balance feedback) cannot
    have account_entitled-or-higher recorded for ANY route, structurally
    -- not merely by operator discipline
  - two different products on the same adapter CLASS (a CCXT spot route
    vs a CCXT perpetual route) are tracked with fully independent state
"""
from __future__ import annotations

import pytest

from app.brokers.alpaca import AlpacaBroker
from app.brokers.ccxt_broker import CCXTBroker
from app.brokers.signalstack import SignalStackBroker
from app.db import SignalStore
from app.qualification import (
    FEEDBACK_DEPENDENT_FLOOR,
    QUALIFICATION_STATE_ORDER,
    QualificationError,
    QualificationState,
    missing_prerequisites,
    requires_feedback,
)


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


# --- Ladder vocabulary sanity -------------------------------------------


def test_ladder_order_matches_spec():
    assert [s.value for s in QUALIFICATION_STATE_ORDER] == [
        "implemented",
        "configured",
        "authenticated",
        "account_entitled",
        "protocol_tested",
        "venue_tested",
        "release_approved",
    ]


def test_feedback_dependent_floor_is_account_entitled():
    assert FEEDBACK_DEPENDENT_FLOOR == QualificationState.ACCOUNT_ENTITLED
    assert requires_feedback(QualificationState.AUTHENTICATED) is False
    assert requires_feedback(QualificationState.ACCOUNT_ENTITLED) is True
    assert requires_feedback(QualificationState.RELEASE_APPROVED) is True


def test_missing_prerequisites_pure_function():
    assert missing_prerequisites(QualificationState.IMPLEMENTED, set()) == []
    assert missing_prerequisites(QualificationState.AUTHENTICATED, set()) == [
        QualificationState.IMPLEMENTED,
        QualificationState.CONFIGURED,
    ]
    assert (
        missing_prerequisites(
            QualificationState.AUTHENTICATED,
            {QualificationState.IMPLEMENTED, QualificationState.CONFIGURED},
        )
        == []
    )


# --- SignalStack's real, disclosed feedback gap -------------------------


def test_signalstack_has_no_account_order_position_feedback():
    """This repo's own module docstring for signalstack.py already
    discloses the gap this asserts: SignalStack POSTs to a webhook and
    gets only an HTTP accept, never fill/position/balance confirmation
    back from the downstream broker -- no get_order_status,
    get_broker_position, or get_account_balance override exists."""
    broker = SignalStackBroker()
    assert broker.has_order_status_capability is False
    assert broker.has_position_readback_capability is False
    assert broker.has_balance_capability is False
    assert broker.has_account_order_position_feedback is False


def test_alpaca_has_real_feedback_channel():
    broker = AlpacaBroker()
    assert broker.has_account_order_position_feedback is True


# --- SignalStore.record_route_qualification: prerequisite ordering ------


def test_rejects_state_with_missing_prerequisite(store):
    with pytest.raises(QualificationError, match="missing prerequisite"):
        store.record_route_qualification(
            adapter_type="alpaca",
            route_key="alpaca_main",
            asset_class="equity",
            product_type="cash_equity",
            state="venue_tested",  # authenticated (and everything below it) was never recorded
            supports_feedback=True,
            recorded_by="owner",
        )
    # Nothing was persisted -- the route has zero recorded events.
    routes = store.list_route_qualifications(adapter_type="alpaca", route_key="alpaca_main")
    assert routes == []


def test_accepts_state_once_every_prerequisite_is_recorded(store):
    route = dict(adapter_type="alpaca", route_key="alpaca_main", asset_class="equity", product_type="cash_equity")
    for state in ["implemented", "configured", "authenticated"]:
        store.record_route_qualification(**route, state=state, supports_feedback=True, recorded_by="owner")

    result = store.record_route_qualification(
        **route, state="account_entitled", supports_feedback=True, recorded_by="owner", notes="checked options-trading-level flag: n/a for equities"
    )
    assert result["state"] == "account_entitled"

    routes = store.list_route_qualifications(adapter_type="alpaca", route_key="alpaca_main")
    assert len(routes) == 1
    assert routes[0]["current_state"] == "account_entitled"
    assert len(routes[0]["events"]) == 4


def test_invalid_state_value_rejected(store):
    with pytest.raises(QualificationError, match="not a valid qualification state"):
        store.record_route_qualification(
            adapter_type="alpaca",
            route_key="alpaca_main",
            asset_class="equity",
            product_type="cash_equity",
            state="fully_verified_forever",
            supports_feedback=True,
            recorded_by="owner",
        )


def test_re_recording_an_already_achieved_state_is_idempotent_update(store):
    route = dict(adapter_type="alpaca", route_key="alpaca_main", asset_class="equity", product_type="cash_equity")
    store.record_route_qualification(**route, state="implemented", supports_feedback=True, recorded_by="owner", notes="first")
    store.record_route_qualification(**route, state="implemented", supports_feedback=True, recorded_by="owner", notes="updated")

    routes = store.list_route_qualifications(adapter_type="alpaca", route_key="alpaca_main")
    assert len(routes) == 1
    assert len(routes[0]["events"]) == 1  # still one row, not a duplicate
    assert routes[0]["events"][0]["notes"] == "updated"


# --- SignalStack cannot structurally claim account_entitled-or-higher ---


def test_signalstack_cannot_be_recorded_as_account_entitled(store):
    route = dict(adapter_type="signalstack", route_key="ss_acct1", asset_class="equity", product_type="cash_equity")
    for state in ["implemented", "configured", "authenticated"]:
        # supports_feedback is what the real write path (app/main.py) would
        # compute from the real, registered SignalStackBroker instance --
        # False, for every state, on every route, always.
        store.record_route_qualification(**route, state=state, supports_feedback=False, recorded_by="owner")

    with pytest.raises(QualificationError, match="no real order-status, position-readback, or balance-readback"):
        store.record_route_qualification(**route, state="account_entitled", supports_feedback=False, recorded_by="owner")

    # current_state never moved past authenticated for this route.
    routes = store.list_route_qualifications(adapter_type="signalstack", route_key="ss_acct1")
    assert routes[0]["current_state"] == "authenticated"


def test_signalstack_cannot_be_recorded_as_release_approved_either(store):
    """release_approved is downstream of account_entitled on the ladder --
    if account_entitled can never be recorded, release_approved (and
    protocol_tested/venue_tested) can never be reached either, without any
    special-casing beyond the one feedback-capability check plus the
    ordinary ladder-prerequisite check."""
    route = dict(adapter_type="signalstack", route_key="ss_acct1", asset_class="equity", product_type="cash_equity")
    for state in ["implemented", "configured", "authenticated"]:
        store.record_route_qualification(**route, state=state, supports_feedback=False, recorded_by="owner")

    with pytest.raises(QualificationError):
        store.record_route_qualification(**route, state="release_approved", supports_feedback=False, recorded_by="owner")


def test_signalstack_can_still_reach_authenticated_honestly(store):
    """The gate is specifically at account_entitled-and-above -- a real
    auth handshake (SignalStack's webhook accepting a request with a valid
    token) is genuine evidence, and 'authenticated' does not require any
    account/order/position feedback to be honest."""
    route = dict(adapter_type="signalstack", route_key="ss_acct1", asset_class="equity", product_type="cash_equity")
    store.record_route_qualification(**route, state="implemented", supports_feedback=False, recorded_by="owner")
    store.record_route_qualification(**route, state="configured", supports_feedback=False, recorded_by="owner")
    result = store.record_route_qualification(**route, state="authenticated", supports_feedback=False, recorded_by="owner")
    assert result["state"] == "authenticated"


# --- Per-route independence: CCXT spot vs CCXT perpetual on same class --


def test_ccxt_spot_and_perpetual_routes_are_fully_independent(store):
    spot = dict(adapter_type="ccxt", route_key="ccxt_binance_spot", asset_class="crypto", product_type="spot")
    perp = dict(adapter_type="ccxt", route_key="ccxt_binance_perp", asset_class="crypto", product_type="perpetual")

    for state in ["implemented", "configured", "authenticated", "account_entitled"]:
        store.record_route_qualification(**spot, state=state, supports_feedback=True, recorded_by="owner")
    # perp route gets only as far as "configured" -- deliberately left behind.
    store.record_route_qualification(**perp, state="implemented", supports_feedback=True, recorded_by="owner")
    store.record_route_qualification(**perp, state="configured", supports_feedback=True, recorded_by="owner")

    routes = {(r["route_key"]): r for r in store.list_route_qualifications(adapter_type="ccxt")}
    assert routes["ccxt_binance_spot"]["current_state"] == "account_entitled"
    assert routes["ccxt_binance_perp"]["current_state"] == "configured"
    assert len(routes["ccxt_binance_spot"]["events"]) == 4
    assert len(routes["ccxt_binance_perp"]["events"]) == 2

    # The perp route can't skip ahead to account_entitled either -- its own
    # history is independently missing the authenticated prerequisite.
    with pytest.raises(QualificationError, match="missing prerequisite"):
        store.record_route_qualification(**perp, state="account_entitled", supports_feedback=True, recorded_by="owner")


def test_ccxt_broker_class_itself_has_real_feedback_channel_for_the_gate():
    """CCXT's get_broker_position/get_last_price overrides give it a real
    feedback channel (unlike SignalStack) -- account_entitled is reachable
    for a CCXT route once the ladder below it is honestly achieved, which
    is exactly what the previous test exercises with supports_feedback=True."""
    pytest.importorskip("ccxt")  # optional dependency -- not installed in CI (see requirements.txt)
    broker = CCXTBroker()
    assert broker.has_account_order_position_feedback is True
