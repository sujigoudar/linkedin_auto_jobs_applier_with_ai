"""TRK-Q1: assembles `app/models.py`'s `QuantityBreakdown` from this
codebase's own already-tracked, real order/position/protection state --
never a second, independently-computed source of truth for any of the
seven fields (see `QuantityBreakdown`'s own docstring for exactly which
existing concept each field formalizes).

This module intentionally contains no admission/risk/capital decision
logic of its own -- it only reads and reports values app/engine.py,
app/lifecycle/manager.py and app/capital_allocator.py already computed,
so it stays out of this repo's Level-3-gated financial-logic modules (see
signal-copier/.agent/autonomy.yaml) while still giving every call site one
place to build the full, explicit breakdown instead of five ad hoc local
variables.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.models import OrderResult, OrderStatus, QuantityBreakdown

if TYPE_CHECKING:  # pragma: no cover - import-cycle avoidance only
    from app.lifecycle.models import PositionLifecycle


def acknowledged_quantity_for(result: OrderResult, requested_quantity: float) -> float | None:
    """Whether (and how much of) `requested_quantity` the broker/venue has
    actually ACCEPTED an order for -- see `QuantityBreakdown
    .acknowledged_quantity`'s own docstring for exactly what this is and
    isn't. This codebase's brokers never report a partial-acknowledgment
    size distinct from what was requested (ccxt/Alpaca/IBKR/etc. either
    accept the whole order or reject it outright), so the only two honest
    answers are "all of it" or "none of it" -- never a fabricated partial
    figure.

    - FILLED, or PENDING with a real `broker_order_id`: the venue is on
      record as having accepted this order -- the full `requested_quantity`
      is acknowledged, matching app/command_ledger.py's
      `SUBMITTED_UNCONFIRMED`/`CONFIRMED` classification for these same two
      cases.
    - REJECTED, ERROR, or PENDING with no `broker_order_id` at all: nothing
      the venue is on record as having accepted -- `0.0`, matching
      app/command_ledger.py's `REJECTED_CONFIRMED`/`UNKNOWN_AMBIGUOUS`
      classification (see `classify_order_result`)."""
    if result.status == OrderStatus.FILLED:
        return requested_quantity
    if result.status == OrderStatus.PENDING and result.broker_order_id:
        return requested_quantity
    return 0.0


def quantity_still_executable_for(
    result: OrderResult, requested_quantity: float, confirmed_cumulative_fill: float | None
) -> float:
    """`QuantityBreakdown.quantity_still_executable` for one `OrderResult` --
    the exact same rule app/engine.py's own AUD-01 `outstanding_possible_fill`
    computation already applies at its call sites, formalized here so a
    caller building a full `QuantityBreakdown` doesn't have to re-derive it
    inline. `0.0` once the order is terminal (nothing more can possibly
    fill); `requested_quantity - confirmed so far` while it remains
    PENDING."""
    if result.status != OrderStatus.PENDING:
        return 0.0
    return max(0.0, requested_quantity - (confirmed_cumulative_fill or 0.0))


def build_quantity_breakdown(
    *,
    requested_quantity: float,
    result: OrderResult | None = None,
    reserved_quantity: float | None = None,
    confirmed_cumulative_fill: float | None = None,
    currently_owned_quantity: float | None = None,
    protected_quantity: float | None = None,
) -> QuantityBreakdown:
    """Build one `QuantityBreakdown` for an order/position event.

    `result` is optional: a caller reporting a breakdown for something that
    hasn't reached the broker yet at all (e.g. a pre-submission intent, or a
    pure capital-admission decision with no order attached) omits it, and
    `acknowledged_quantity`/`quantity_still_executable` are left `None`
    rather than fabricated. Every other argument is passed straight through
    as the caller's own already-computed real value -- this function never
    invents one."""
    acknowledged_quantity: float | None = None
    quantity_still_executable: float | None = None
    if result is not None:
        acknowledged_quantity = acknowledged_quantity_for(result, requested_quantity)
        quantity_still_executable = quantity_still_executable_for(
            result, requested_quantity, confirmed_cumulative_fill
        )
    return QuantityBreakdown(
        requested_quantity=requested_quantity,
        reserved_quantity=reserved_quantity,
        acknowledged_quantity=acknowledged_quantity,
        cumulative_executed_quantity=confirmed_cumulative_fill,
        currently_owned_quantity=currently_owned_quantity,
        quantity_still_executable=quantity_still_executable,
        protected_quantity=protected_quantity,
    )


def quantity_breakdown_for_lifecycle(lifecycle: "PositionLifecycle") -> QuantityBreakdown:
    """`QuantityBreakdown` for a managed-lifecycle position's CURRENT
    state, read entirely from `PositionLifecycle`'s own already-tracked
    fields (never a second, independently-maintained copy of any of them):

    - `requested_quantity`: the plan's originally requested size
      (`plan.planned_quantity`) -- the position's ENTRY ask, not a
      per-fill-event figure (a lifecycle spans many fill/exit events, so
      there is no single "this event's request" the way one `OrderResult`
      has).
    - `reserved_quantity`: the entry's still-held capital reservation, if
      any (`pending_entry.reserved_quantity`) -- `None` once there's no
      unresolved pending entry (nothing reserved right now).
    - `acknowledged_quantity`/`cumulative_executed_quantity`: not
      meaningful as a single current-state figure for a lifecycle spanning
      many orders over time (entry + however many partial exits) --
      `None` here; a per-order breakdown for one specific fill event should
      instead be built with `build_quantity_breakdown` at that order's own
      call site (see app/engine.py's `_handle_managed_entry`).
    - `currently_owned_quantity`: `lifecycle.confirmed_owned_quantity`.
    - `quantity_still_executable`: the pending entry's own
      `unresolved_remainder`, if any (0.0 once there's no unresolved entry
      -- nothing left that could still fill and change ownership).
    - `protected_quantity`: `lifecycle.covered_quantity` -- 0.0 unless the
      stop is broker-CONFIRMED, per that property's own docstring."""
    reserved_quantity = lifecycle.pending_entry.reserved_quantity if lifecycle.pending_entry is not None else None
    quantity_still_executable = (
        lifecycle.pending_entry.unresolved_remainder if lifecycle.pending_entry is not None else 0.0
    )
    return QuantityBreakdown(
        requested_quantity=lifecycle.plan.planned_quantity,
        reserved_quantity=reserved_quantity,
        acknowledged_quantity=None,
        cumulative_executed_quantity=None,
        currently_owned_quantity=lifecycle.confirmed_owned_quantity,
        quantity_still_executable=quantity_still_executable,
        protected_quantity=lifecycle.covered_quantity,
    )


__all__ = [
    "acknowledged_quantity_for",
    "build_quantity_breakdown",
    "quantity_breakdown_for_lifecycle",
    "quantity_still_executable_for",
]
