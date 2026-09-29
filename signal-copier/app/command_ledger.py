"""P0-2 (external release audit): the durable, pre-effect command ledger
that backs EVERY real broker-command call site in app/engine.py and
app/lifecycle/manager.py.

## The gap this closes

The audit's own words: "Every entry, close, stop, target, replace, cancel
and flatten should have a pre-effect durable intent, idempotency identity,
account/environment, expected revision, request fingerprint, remote
identifiers, uncertainty state and terminal evidence." And separately:
"A PENDING result without a broker order ID remains an acknowledged
exposure gap... Persist the intent before submission and retain an
uncertainty reservation until independent reconciliation resolves it."

Before this module, this codebase's only record of "we tried to submit an
order" was the `orders` table row -- written AFTER the broker call returns
(see every `SignalStore.save_order_result` call site). If the process died
between deciding to submit and that write landing, there was no durable
trace the command was ever attempted at all -- exactly the exposure gap
the audit names. `command_ledger` (see app/db.py's own schema comment) is
written and COMMITTED before the broker call, every time, so that gap is
now a `pending_submission` row a restart can find instead of nothing.

## The three-step shape every call site in this codebase follows

    1. `entry = store.open_command_ledger_entry(...)` -- pre-effect,
       durable, committed. If this raises `CommandFingerprintMismatch`,
       the caller has a bug (reused an idempotency key for a different
       command) and must not proceed. If `entry.uncertainty_state` is
       already NOT `PENDING_SUBMISSION` when this returns, a prior attempt
       under the same key already ran (or is running) -- the caller must
       NOT call the broker again; it replays `entry`'s own tracked
       state instead (see `is_duplicate_submission`).
    2. Call the broker. On success, classify the `OrderResult` with
       `classify_order_result` (or the matching classifier for a
       cancel/replace/place_protective_stop call) and call
       `store.mark_command_ledger_outcome(...)` with the result.
    3. On the broker call *raising*, classify with `ambiguous_evidence_for_exception`
       and mark `UNKNOWN_AMBIGUOUS` -- never let an exception skip this
       ledger update; the whole point of this module is that an ambiguous
       outcome is recorded, not silently dropped.

Every real call site doing this is in app/engine.py (`handle_signal`'s
plain-account entry, `_submit_order` for a plain-account close, and
`_handle_managed_entry`'s broker.place_order) and app/lifecycle/manager.py
(`_submit_exit_order` for close/flatten, `_place_or_replace_stop`-style
call sites for stop_change/replace/cancel -- see each call site's own
inline comment for exactly which `CommandType` it uses and why).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from app import config
from app.models import OrderResult, OrderStatus, UncertaintyState

#: This codebase's existing, real environment identity -- the same
#: `RELAY_ENVIRONMENT` setting every EXECUTION_APPLIED export envelope is
#: already labeled with (see app/config.py's own docstring on it and
#: app/engine.py's `Environment[config.RELAY_ENVIRONMENT]` uses). Reused
#: here rather than inventing a second, parallel environment concept: a
#: `command_ledger` row's `environment` column means exactly what an
#: export envelope's `environment` field already means for this same
#: deployment. `DestinationAccount` itself carries no per-account
#: paper/live field (several broker adapters have no such distinction at
#: all, e.g. app/brokers/schwab.py: "Schwab has NO SANDBOX/PAPER
#: environment at all"), so this is deployment-scoped, not per-account --
#: read live (not cached at import time) so a test that monkeypatches
#: `config.RELAY_ENVIRONMENT` sees its own value reflected here too.
def current_environment() -> str:
    return config.RELAY_ENVIRONMENT


class CommandFingerprintMismatch(ValueError):
    """Raised by `SignalStore.open_command_ledger_entry` when a caller
    reuses an `idempotency_key` for a request whose `request_fingerprint`
    doesn't match the one already stored under that key -- a caller bug
    (a genuinely different command reusing an old key), never something to
    silently allow through. Fail closed: the caller must surface this as
    an error, not guess which request was "real"."""


def compute_fingerprint(payload: dict[str, Any]) -> str:
    """Deterministic SHA-256 over the exact request parameters for one
    command -- the same `idempotency_key` arriving twice hashes to the
    SAME fingerprint iff it's really the same request repeated (a
    legitimate retry to replay, not resubmit); any difference means the
    key was reused for a different request, which is a caller bug (see
    `CommandFingerprintMismatch`). `sort_keys=True` makes key order
    irrelevant; `default=str` handles enums/datetimes/etc. in the payload
    without the caller having to pre-serialize every field by hand."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def is_duplicate_submission(entry_uncertainty_state: UncertaintyState) -> bool:
    """True iff a command_ledger entry `open_command_ledger_entry` just
    returned was NOT freshly created by this call -- i.e. a prior attempt
    under the same idempotency_key already opened (and possibly already
    resolved) it. A caller sees this by checking whether the returned
    entry's `uncertainty_state` is still the fresh-row default
    (`PENDING_SUBMISSION`, meaning genuinely brand new -- proceed to call
    the broker) or anything else (a prior attempt already ran further than
    that -- replay its tracked state, never call the broker again)."""
    return entry_uncertainty_state != UncertaintyState.PENDING_SUBMISSION


def classify_order_result(result: OrderResult) -> tuple[UncertaintyState, dict[str, Any], dict[str, Any]]:
    """Map a broker `OrderResult` to the `(uncertainty_state,
    remote_identifiers, terminal_evidence)` a command_ledger row should be
    updated with after a `place_order`/`place_protective_stop`/
    `replace_stop_quantity` call returns normally (did not raise).

    - FILLED: a definite, broker-confirmed success -> CONFIRMED (terminal).
    - REJECTED: a definite, broker-confirmed rejection -> REJECTED_CONFIRMED
      (terminal) -- nothing was ever accepted at the venue.
    - PENDING with a real `broker_order_id`: accepted but not yet filled,
      with something app/reconciliation.py's polling loop can chase to a
      terminal status later -> SUBMITTED_UNCONFIRMED (not yet resolved).
    - PENDING with NO `broker_order_id`, or ERROR: the exact "PENDING
      result without a broker order ID" gap the audit names -- nothing
      durable to poll, so nothing guarantees this is ever revisited ->
      UNKNOWN_AMBIGUOUS (not resolved; needs independent reconciliation,
      e.g. a broker position/order readback, to ever close out).
    """
    remote_identifiers: dict[str, Any] = {"broker_order_id": result.broker_order_id} if result.broker_order_id else {}
    if result.status == OrderStatus.FILLED:
        evidence = {
            "broker_status": "filled",
            "filled_quantity": result.filled_quantity,
            "filled_price": result.filled_price,
            "message": result.message,
        }
        return UncertaintyState.CONFIRMED, remote_identifiers, evidence
    if result.status == OrderStatus.REJECTED:
        return UncertaintyState.REJECTED_CONFIRMED, remote_identifiers, {"broker_status": "rejected", "message": result.message}
    if result.status == OrderStatus.PENDING:
        if result.broker_order_id:
            return UncertaintyState.SUBMITTED_UNCONFIRMED, remote_identifiers, {}
        return (
            UncertaintyState.UNKNOWN_AMBIGUOUS,
            remote_identifiers,
            {"broker_status": "pending_no_broker_order_id", "message": result.message},
        )
    # OrderStatus.ERROR: several adapters catch their own transport/timeout
    # errors and return ERROR rather than letting the exception propagate
    # (see app/engine.py's own EXE-01 comment) -- identical ambiguity to a
    # raised exception, from this ledger's point of view.
    return UncertaintyState.UNKNOWN_AMBIGUOUS, remote_identifiers, {"broker_status": "error", "message": result.message}


def ambiguous_evidence_for_exception(exc: BaseException) -> dict[str, Any]:
    """`terminal_evidence` (despite the name, always paired with
    `UncertaintyState.UNKNOWN_AMBIGUOUS`, i.e. NOT actually terminal -- the
    dict key name matches the column, not a claim of resolution) for a
    broker call that raised. A network timeout/connection reset reading
    the response is genuinely ambiguous about whether the request reached
    the venue before the failure -- recorded honestly as unknown, never
    guessed either way."""
    return {"broker_status": "exception", "exception_type": type(exc).__name__, "message": str(exc)}


def classify_cancel_result(cancelled: bool) -> tuple[UncertaintyState, dict[str, Any]]:
    """Map `BrokerAdapter.cancel_order`'s bool return to a command_ledger
    outcome. `True` is a confirmed cancel -> CONFIRMED. `False` is
    deliberately treated as ambiguous, not a definite failure: per
    `BrokerAdapter.cancel_order`'s own docstring, False means "isn't
    supported or confirmed" -- which covers both "definitely never
    cancelled" AND "the order may have already filled just before the
    cancel landed." Every real caller in app/lifecycle/manager.py already
    treats a False cancel as "don't assume, refuse rather than guess" --
    this ledger entry stays UNKNOWN_AMBIGUOUS for the same reason, not
    REJECTED_CONFIRMED, which would claim more certainty than the broker
    actually gave."""
    if cancelled:
        return UncertaintyState.CONFIRMED, {"broker_status": "cancelled"}
    return UncertaintyState.UNKNOWN_AMBIGUOUS, {"broker_status": "cancel_not_confirmed"}


def classify_optional_order_result(
    result: OrderResult | None,
) -> tuple[UncertaintyState, dict[str, Any], dict[str, Any]]:
    """For `place_protective_stop`/`replace_stop_quantity`, whose base
    (unsupported) implementation returns `None` rather than an
    `OrderResult` -- see app/brokers/base.py. `None` here means "this
    broker has no verified implementation of this capability," a definite,
    immediate, local answer (never even attempted a network call) -- not
    an ambiguous outcome. Every real caller already falls back to a
    different strategy (cancel-then-resubmit, or refusing to admit the
    account) when this happens, so REJECTED_CONFIRMED (never attempted,
    definitely didn't happen) is the honest terminal state, not
    UNKNOWN_AMBIGUOUS (which would wrongly suggest reconciliation could
    ever resolve it)."""
    if result is None:
        return UncertaintyState.REJECTED_CONFIRMED, {}, {"broker_status": "capability_not_supported"}
    return classify_order_result(result)


__all__ = [
    "CommandFingerprintMismatch",
    "ambiguous_evidence_for_exception",
    "classify_cancel_result",
    "classify_optional_order_result",
    "classify_order_result",
    "compute_fingerprint",
    "current_environment",
    "is_duplicate_submission",
]
