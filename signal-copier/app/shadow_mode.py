"""Track 17: SHADOW MODE -- "what would the live system have done?"
without submitting anything.

The user's own words: "different from paper trading... Show: Would buy:
5 SPY 610C 10/2, Expected entry: 1.42, Stop: 1.05, Targets: 1.80/2.20,
Why: TradeAlgo Options Policy v4. Ideal for newly added providers."

## Why this is NOT built on `app/backtest/replay.py`

`app/backtest/replay.py`/`app/backtest/simulator.py` (read in full
before writing this module) replay HISTORICAL bars in bulk for research:
"given a set of historical signals and a price history source, ask what
would have happened to each signal's own resolved stop/target" -- a
walk-forward through OHLC bars that already exist, producing WIN/LOSS/
STILL_OPEN outcomes. Shadow mode is a genuinely different shape, exactly
as this track's own brief anticipated: it runs in REAL TIME as ONE new
signal arrives, non-blocking, and its output is not a resolved trade
outcome at all -- it's the single hypothetical ORDER the live engine
would have constructed for that signal RIGHT NOW (destination accounts,
sizing, stop, targets), before any price has had a chance to move
against it. There is no price-history walk to reuse here; the
`app.backtest` module's actual job (bar-by-bar stop/target resolution
against historical data) doesn't apply to a signal that just arrived.

## What IS reused: the real decision logic, not reimplemented

Per this track's own guardrail ("a shadow-mode decision that uses
different logic than live would be a lie about 'what would the live
system have done'"), `evaluate_shadow` below calls the EXACT SAME
functions `app/engine.py`'s own `_handle_signal` calls for the
destination-resolution and sizing steps every real signal goes through:

  - `app.routing.RoutingConfig.destinations_for` -- the SAME routing-
    rule matching (source/symbol/precedence/disabled-account handling)
    `_handle_signal` calls to decide which accounts a signal reaches.
  - `app.risk.size_for_account` -- the SAME sizing arithmetic
    (fixed_quantity-wins-outright, else quantity * multiplier)
    `_handle_signal` calls per destination.
  - `app.risk.symbol_for_account` -- the SAME per-account symbol
    translation.

This module does NOT reimplement destination matching or position
sizing -- it imports and calls the real functions, so a change to
either one is automatically reflected in shadow mode's own output with
no separate code path to keep in sync (see
`tests/test_t17_certification.py::
test_shadow_mode_reuses_real_routing_and_sizing_not_a_reimplementation`,
which asserts this module's own source imports those exact functions
and calls no reimplemented equivalent).

Deliberately NOT reused (out of scope for "what would the live system
have done" at signal-arrival time, before any order attempt): capital-
allocator admission (`app.capital_allocator`), risk-basis sizing
against a live equity fetch (`app.engine._check_risk_basis`), and route-
qualification (`app.qualification`) -- all three depend on a REAL broker
round-trip (a live equity balance, a real reservation ledger) shadow
mode must never make (see the "structurally incapable of submitting"
section below); reporting the sizing/routing decision honestly, without
pretending to also know what a live equity-dependent gate would have
decided, is the honest scope for this first build.

## Structurally incapable of submitting a real order

This module imports NOTHING from `app.brokers` (no `BrokerAdapter`, no
concrete broker class) and calls no `SignalStore.save_order_result`/
`record_fill`/anything that mutates `orders`/`positions`. `evaluate_shadow`
takes a `Signal` and a `RoutingConfig` and returns a plain, inert list of
`ShadowOrderIntent` dataclasses -- there is no code path, anywhere in
this module, that could reach a broker even by accident, because no
broker-shaped object is ever constructed, imported, or referenced here.
See `tests/test_t17_certification.py::
test_shadow_evaluation_module_imports_no_broker_adapter` for the AST-
based static proof (mirrors `app/phone_escalation.py`'s own
`test_adapter_public_surface_has_no_action_capable_of_submitting_or_typing`
precedent for a different structural guarantee) and
`test_shadow_evaluation_never_calls_save_order_result` for the
behavioral proof (a `SignalStore` double whose `save_order_result` would
raise if ever called survives an `evaluate_shadow` run untouched).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.models import ProfitTarget, Signal
from app.risk import size_for_account, symbol_for_account
from app.routing import RoutingConfig


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ShadowOrderIntent:
    """One hypothetical order shadow mode computed for one destination
    account -- the user's own example, field for field: "Would buy: 5
    SPY 610C 10/2, Expected entry: 1.42, Stop: 1.05, Targets:
    1.80/2.20, Why: TradeAlgo Options Policy v4."."""

    id: str
    signal_id: str
    provider_id: str
    account_id: str
    symbol: str
    side: str
    quantity: float
    expected_entry: Optional[float]
    stop_price: Optional[float]
    targets: list[float] = field(default_factory=list)
    #: The real policy/config reference this decision was actually
    #: computed from -- "TradeAlgo Options Policy v4" in the user's own
    #: example. Built from real, inspectable inputs (see
    #: `_policy_reference` below) -- never an invented label.
    policy_reference: str = ""
    #: Human-readable "why" -- built from the SAME real routing-rule/
    #: sizing values `policy_reference` names, not a separate narrative.
    reasoning: str = ""
    computed_at: datetime = field(default_factory=now_utc)


def _policy_reference(*, provider_id: str, certification_version: str | None) -> str:
    """The real config/version reference this shadow decision was
    computed from -- `providers.certification_version` (Track 14's own
    field, `None` until an operator sets one) when the provider has one,
    else an honest fallback naming the provider itself (never a
    fabricated version number)."""
    if certification_version:
        return f"{provider_id} policy v{certification_version}"
    return f"{provider_id} policy (unversioned)"


def evaluate_shadow(
    signal: Signal,
    routing: RoutingConfig,
    *,
    certification_version: str | None = None,
) -> list[ShadowOrderIntent]:
    """Computes the hypothetical order(s) the live engine would have
    constructed for `signal`, for every destination account routing
    would have reached -- WITHOUT touching any broker, store, or engine
    state. See this module's own docstring for exactly which real
    functions this reuses (`RoutingConfig.destinations_for`,
    `size_for_account`, `symbol_for_account`) and why capital/risk-basis/
    route-qualification gates are deliberately not evaluated here.

    A CLOSE signal still produces an intent (mirrors `_handle_signal`'s
    own "an exit is never suppressed the way an entry is" EXE-10
    convention) -- `include_disabled=True` for a CLOSE, exactly as
    `_handle_signal` itself passes to `destinations_for`."""
    destinations = routing.destinations_for(
        signal.source, signal.symbol, include_disabled=(signal.side.value == "close")
    )
    intents: list[ShadowOrderIntent] = []
    policy_reference = _policy_reference(provider_id=signal.source, certification_version=certification_version)
    targets = _target_prices(signal)
    for account in destinations:
        quantity = size_for_account(signal, account)
        symbol = symbol_for_account(signal, account)
        reasoning = (
            f"Would {signal.side.value} {quantity:g} {symbol} on account={account.account_id!r} "
            f"(source={signal.source!r}, multiplier={account.multiplier:g}"
            + (f", fixed_quantity={account.fixed_quantity:g}" if account.fixed_quantity is not None else "")
            + f") per {policy_reference}"
        )
        intents.append(
            ShadowOrderIntent(
                id=str(uuid.uuid4()),
                signal_id=signal.id,
                provider_id=signal.source,
                account_id=account.account_id,
                symbol=symbol,
                side=signal.side.value,
                quantity=quantity,
                expected_entry=signal.price,
                stop_price=signal.stop_loss,
                targets=targets,
                policy_reference=policy_reference,
                reasoning=reasoning,
            )
        )
    return intents


def _target_prices(signal: Signal) -> list[float]:
    """`signal.targets` (the ordered multi-target list) when the source
    gave one, else the single `take_profit` as a one-element list, else
    empty -- never invents a target the signal itself didn't carry."""
    if signal.targets:
        return [t.price for t in signal.targets if isinstance(t, ProfitTarget)]
    if signal.take_profit is not None:
        return [signal.take_profit]
    return []


def to_result_row(intent: ShadowOrderIntent) -> dict[str, Any]:
    """The plain-dict shape `SignalStore.record_shadow_mode_result`
    persists -- kept as a free function (not a dataclass method) so this
    module's own dataclasses stay pure data, matching this codebase's
    existing split between a domain dataclass and its own persistence
    shape (see e.g. `app.phone_escalation.EscalationAttempt` vs.
    `app/db.py`'s own row-shape helpers)."""
    return {
        "id": intent.id,
        "signal_id": intent.signal_id,
        "provider_id": intent.provider_id,
        "account_id": intent.account_id,
        "symbol": intent.symbol,
        "side": intent.side,
        "quantity": intent.quantity,
        "expected_entry": intent.expected_entry,
        "stop_price": intent.stop_price,
        "targets": intent.targets,
        "policy_reference": intent.policy_reference,
        "reasoning": intent.reasoning,
        "computed_at": intent.computed_at.isoformat(),
    }
