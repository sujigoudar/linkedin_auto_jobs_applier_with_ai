"""Live qualification state model for broker/exchange execution ROUTES.

This is a deliberately SEPARATE, higher-bar concept from
`app/brokers/base.py`'s "Capability introspection" section
(`has_protective_stop_capability` etc.). That existing mechanism answers
"does this adapter CLASS have a real method override" -- useful
engineering metadata, computed purely from code, and it stays exactly as
it is (see app/brokers/base.py's own docstring on why method-override
introspection is honest for what IT claims). It is NOT evidence that any
particular account, on any particular venue, trading any particular
product, has actually been checked end-to-end -- an audit finding this
module exists to close:

    "The broker capability matrix is implementation-derived, not
    venue-qualified... Add separate states for implemented, configured,
    authenticated, account-entitled, protocol-tested, venue-tested and
    release-approved."

    "Live qualification must be per exact route. Alpaca equities, IBKR
    options, a CCXT spot exchange, a CCXT perpetual exchange, MT5 and
    SignalStack are different execution products. One generic adapter
    passing tests cannot qualify the others."

So: a ROUTE here is the tuple (adapter_type, route_key, asset_class,
product_type) -- e.g. ("ccxt", "ccxt_binance_spot", "crypto", "spot") is a
DIFFERENT route from ("ccxt", "ccxt_binance_perp", "crypto", "perpetual"),
even though both run through the exact same `CCXTBroker` class and report
the same `has_*_capability` flags. Each route's qualification is tracked
and gated completely independently -- qualifying one CCXT venue/product
combination proves nothing about another.

The ladder (`QUALIFICATION_STATE_ORDER`) is a strict, sequential
prerequisite chain, enforced by `app/db.py`'s `SignalStore.
record_route_qualification` (never bypassable from the write path,
including a direct call to that method with no HTTP layer involved):
recording state N for a route requires that states 0..N-1 were already
recorded for that SAME route. There is no way to jump straight to
`venue_tested` for a route that was never even `authenticated`.

`release_approved` is a DELIBERATE HUMAN SIGN-OFF, not a technical
check -- app/main.py's `POST /qualifications` is owner-gated (CSRF-
protected session), same as every other mutation in this build. Nothing
in this codebase ever sets a qualification record on its own initiative;
every row is written because an operator asked for it.
"""
from __future__ import annotations

import enum


class QualificationState(str, enum.Enum):
    #: The adapter's code exists and implements `place_order` (and
    #: whatever else this route needs) -- a Python-level fact, checked by
    #: `app/brokers/base.py`'s existing capability introspection, not by
    #: this module.
    IMPLEMENTED = "implemented"
    #: The specific account/venue variant this route names has real
    #: config -- an entry in accounts.yaml / config_accounts, credentials
    #: pointed at (even if not yet verified to work).
    CONFIGURED = "configured"
    #: A real auth handshake against the actual broker/venue API has
    #: succeeded for this specific route (a login, a token exchange, or --
    #: for a router like SignalStack -- its webhook accepting a request
    #: with a valid, recognized token).
    AUTHENTICATED = "authenticated"
    #: The specific account/product is confirmed ENTITLED for this exact
    #: asset class/instrument type -- checked wherever the broker actually
    #: exposes that information (e.g. an options-trading-level flag, a
    #: margin/derivatives permission). Requires genuine account/order/
    #: position feedback to verify at all -- see
    #: `FEEDBACK_DEPENDENT_FLOOR` below.
    ACCOUNT_ENTITLED = "account_entitled"
    #: This repo's OWN test suite exercises the real protocol path for
    #: this route (even against a mock/fake transport) -- e.g. a test that
    #: actually drives `place_order`/`get_order_status`/etc. against this
    #: adapter, not just a unit test of unrelated logic.
    PROTOCOL_TESTED = "protocol_tested"
    #: A real, deliberate test against the ACTUAL venue/sandbox (not a
    #: mock) has been performed and recorded for this exact route. Almost
    #: always NOT ACHIEVED in a sandbox/CI environment with no live
    #: credentials -- that is the honest, expected state, not a bug.
    VENUE_TESTED = "venue_tested"
    #: An explicit human/operator sign-off that this route is approved for
    #: real, live trading -- separate from every technical state above it.
    RELEASE_APPROVED = "release_approved"


#: The strict order of the ladder. Index N requires every state at index
#: < N to already be recorded for the same route before it may be
#: recorded (see `missing_prerequisites`).
QUALIFICATION_STATE_ORDER: list[QualificationState] = [
    QualificationState.IMPLEMENTED,
    QualificationState.CONFIGURED,
    QualificationState.AUTHENTICATED,
    QualificationState.ACCOUNT_ENTITLED,
    QualificationState.PROTOCOL_TESTED,
    QualificationState.VENUE_TESTED,
    QualificationState.RELEASE_APPROVED,
]

_STATE_INDEX = {state: i for i, state in enumerate(QUALIFICATION_STATE_ORDER)}

#: The lowest rung that REQUIRES the adapter to be able to verify
#: something back about the account/an order/a position after submission.
#: `implemented`/`configured`/`authenticated` need no feedback channel at
#: all (code exists, config exists, a handshake succeeded) -- but you
#: cannot honestly claim a specific account is ENTITLED for a product, or
#: that this repo's tests exercised the real protocol round-trip, or that
#: a real venue test was recorded, without SOME way to read back what
#: actually happened. See `app/brokers/base.py`'s
#: `has_account_order_position_feedback` -- this is the property the
#: write path checks against for every route on that adapter, not just
#: SignalStack (SignalStack is simply the concrete case this was written
#: for: see its own module docstring).
FEEDBACK_DEPENDENT_FLOOR = QualificationState.ACCOUNT_ENTITLED


class QualificationError(ValueError):
    """Raised by the write path when a requested qualification state
    can't honestly be recorded for a route -- a missing ladder
    prerequisite, or an adapter that structurally cannot provide the
    account/order/position feedback a rung at or above
    `FEEDBACK_DEPENDENT_FLOOR` requires. Always fails closed: never
    silently downgrades or drops the request."""


def parse_state(value: str) -> QualificationState:
    try:
        return QualificationState(value)
    except ValueError as exc:
        valid = ", ".join(s.value for s in QUALIFICATION_STATE_ORDER)
        raise QualificationError(f"'{value}' is not a valid qualification state (valid: {valid})") from exc


def state_index(state: QualificationState) -> int:
    return _STATE_INDEX[state]


def missing_prerequisites(
    state: QualificationState, achieved: "set[QualificationState]"
) -> list[QualificationState]:
    """Every state strictly below `state` in the ladder that is NOT
    already in `achieved` -- i.e. exactly what's missing before `state`
    may honestly be recorded for this route. Empty means `state` is
    either already achieved or every rung below it is."""
    idx = state_index(state)
    return [s for s in QUALIFICATION_STATE_ORDER[:idx] if s not in achieved]


def requires_feedback(state: QualificationState) -> bool:
    return state_index(state) >= state_index(FEEDBACK_DEPENDENT_FLOOR)
