"""E05 (bounded) + PU-A2: signal-to-fill latency reporting, now extended
with the real multi-stage execution-latency breakdown app/db.py's PU-A2
timestamp columns make possible.

## Honest scope: which of the 7 conceptual TCA stages this codebase has

The adoption plan's own "publication -> local receipt -> normalized
decision -> broker submission -> acknowledgement -> first/complete fill ->
protection acknowledgement" pipeline has 7 conceptual stages. Verified
directly against app/engine.py's/app/sources/*.py's/app/brokers/*.py's/
app/lifecycle/manager.py's real, already-happening code paths (not
invented), here is exactly what this schema tracks today:

1. **Signal published** -- NOT tracked, for any source, anywhere in this
   codebase today. Checked every source adapter (app/sources/webhook.py,
   discord.py, slack.py, telegram.py, twitter.py, sms_twilio.py,
   mt4_mt5.py, ninjatrader.py, rithmic.py): none of them thread a
   platform-native publish timestamp (a TradingView alert's own "time"
   field, a Discord/Slack message's own timestamp, a tweet's
   `created_at`) into `Signal` at all -- every one is discarded at parse
   time, if it was ever read to begin with. This stage stays honestly
   absent from the schema (no column), not backfilled from `received_at`.
2. **Local receipt** -- `signals.received_at`, real, always present
   (`Signal.received_at`'s default factory stamps it the instant the
   `Signal` object is constructed).
3. **Normalized/parsed decision** -- COLLAPSES with #2. Every source's
   `parse()` builds the `Signal` (stamping `received_at`) as the very
   last step of parsing, in the same call frame, with no separate
   persisted moment in between. Reporting a distinct "decision" instant
   here would be fabricating a gap that doesn't exist in this code today.
4. **Broker submission** -- `orders.submitted_at`, real: app/engine.py
   stamps this immediately before its one call to
   `BrokerAdapter.place_order` (or, for a managed-lifecycle account,
   before its own place_order call inside `_handle_managed_entry`) --
   after routing, sizing, and the capital-admission check, so those are
   correctly folded into "receipt -> submission" latency rather than
   invisible. NULL for any order that never reached a broker call at all
   (rejected by routing/asset-class/bracket-support/capital-admission
   gates first) -- never backfilled to `executed_at`.

   One exception: a managed-lifecycle **CLOSE** (`_handle_managed_close`)
   has no `submitted_at` -- its own submission call happens inside
   app/lifecycle/manager.py's `request_exit`, not at the call site that
   would need to capture it, so this stage is honestly absent for that
   one order shape rather than approximated. The plain-account close path
   (`_resolve_and_submit_plain_close` / `_submit_order`) DOES capture it.
5. **Broker acknowledgement** -- COLLAPSES with #6 for every broker
   adapter in this codebase today. `BrokerAdapter.place_order` is a
   single async call with a single synchronous return
   (`app/brokers/base.py`'s abstract signature; verified against every
   concrete adapter in `app/brokers/*.py`) -- there is no separate
   "accepted, working" callback distinct from that one response, whether
   the response is a synchronous FILLED or an async-settling PENDING.
   `executed_at` (unchanged from E05) is that one real response instant
   for a synchronously FILLED order.
6. **First/complete fill** -- also COLLAPSES with #5/#6 above for a
   synchronously FILLED order (the common case for this schema's own
   `list_filled_orders_with_signal_timing`, which only ever returns
   `status = 'filled'` rows). This schema also has no separate
   partial-fill-event table: `orders.filled_quantity`/`executed_at` are a
   single pair that app/reconciliation.py's `_correct_position`
   *overwrites in place* once a PENDING order's real terminal status is
   confirmed -- there is no durable record of the original PENDING
   response's own instant once that overwrite happens, so this module
   cannot honestly report a distinct "first fill" vs. "complete fill"
   pair even though the underlying event stream (submit -> PENDING ->
   later reconciled FILLED) does have two real moments. A future schema
   change preserving the original response's timestamp separately (not
   made here, to avoid overloading this slice's scope) would let this
   module report that gap too.
7. **Protection acknowledgement** -- `orders.protection_confirmed_at`,
   real, but entry-only and managed_lifecycle-only:
   `app/lifecycle/manager.py`'s `StopRecord.confirmed_at` is set at the
   exact moment `_place_stop_locked` confirms `STOP_CONFIRMED` (the
   broker accepted a standalone protective stop as a real resting order)
   and is read back onto the entry order's own row right after
   `on_entry_fill`/`resolve_pending_entry` return. NULL for: any
   non-managed_lifecycle account (protection there, if any, is embedded
   in a native-bracket entry order with no separately confirmed leg in
   this schema at all); a managed entry whose stop was never confirmed
   (no verified `place_protective_stop` for that broker, or the
   submission failed/was rejected -- see `_place_stop_locked`'s own
   REJECTED/ERROR/ambiguous-result branches, none of which set
   `confirmed_at`); and any exit/CLOSE order (this stage is meaningful
   only for a fresh entry establishing new protection).

## What this module reports

`compute_execution_quality` keeps E05's original `per_symbol` shape
(`SymbolLatency` -- signal-received-to-fill, unchanged, still what TR-14's
chart consumes) and ADDITIVELY reports a `stage_latencies` breakdown per
symbol: named intervals (`receipt_to_submission`,
`submission_to_fill`, `fill_to_protection`), each aggregated the same
mean/median/max/sample_count way as `SymbolLatency`, but only over the
orders where BOTH of that interval's endpoint timestamps are real and
non-null for that specific order -- never a computed gap bridging one
real timestamp and a fabricated/defaulted one, and never a negative
interval (clock skew/malformed data is excluded the same way E05's
original latency already was, not reported as real).

Also never reports a per-provider "was this a good signal" verdict --
latency is one input to that judgment, not the judgment itself (network/
broker/exchange latency and this process's own processing time are all
mixed together here, with no way from this data alone to attribute the
split).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime

from app.db import SignalStore


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _parse_optional(ts: str | None) -> datetime | None:
    return _parse(ts) if ts else None


@dataclass
class SymbolLatency:
    symbol: str
    sample_count: int
    mean_seconds: float
    median_seconds: float
    max_seconds: float


@dataclass
class StageLatency:
    """One named interval between two real, per-order PU-A2 timestamps,
    aggregated across however many orders for this symbol actually had
    BOTH endpoints present -- `sample_count` is independent of (and
    normally <= ) `SymbolLatency.sample_count` for the same symbol, since
    not every filled order reaches every stage (see this module's
    docstring)."""

    stage: str
    sample_count: int
    mean_seconds: float
    median_seconds: float
    max_seconds: float

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "sample_count": self.sample_count,
            "mean_seconds": self.mean_seconds,
            "median_seconds": self.median_seconds,
            "max_seconds": self.max_seconds,
        }


#: (stage name, start field, end field) -- start/end are keys into the row
#: dicts `list_filled_orders_with_signal_timing` returns. Order matches the
#: real pipeline order documented in this module's docstring.
_STAGE_DEFINITIONS: list[tuple[str, str, str]] = [
    ("receipt_to_submission", "received_at", "submitted_at"),
    ("submission_to_fill", "submitted_at", "executed_at"),
    ("fill_to_protection", "executed_at", "protection_confirmed_at"),
]


@dataclass
class AccountExecutionQuality:
    account_id: str
    per_symbol: dict[str, SymbolLatency] = field(default_factory=dict)
    #: PU-A2: per-symbol stage breakdown, additive to `per_symbol` above --
    #: see this module's docstring for exactly what each stage name means
    #: and why a stage may have fewer (or zero) samples than
    #: `per_symbol[symbol].sample_count`.
    stage_latencies: dict[str, list[StageLatency]] = field(default_factory=dict)
    #: Filled orders whose signal_id didn't match any stored signal (e.g.
    #: an internal exit signal saved without a store wired in) -- excluded
    #: from every latency figure rather than silently treated as instant.
    unmatched_order_count: int = 0

    def to_dict(self) -> dict:
        return {
            "account_id": self.account_id,
            "note": "Signal-received-to-fill latency (per_symbol) plus a real, per-stage "
            "breakdown (stage_latencies) built from this schema's actual PU-A2 timestamps -- "
            "see app/execution_quality.py's module docstring for exactly which of the 7 "
            "conceptual publication/receipt/decision/submission/acknowledgement/fill/protection "
            "stages this codebase separately tracks today vs. which honestly collapse together.",
            "unmatched_order_count": self.unmatched_order_count,
            "per_symbol": {
                symbol: {
                    "sample_count": s.sample_count,
                    "mean_seconds": s.mean_seconds,
                    "median_seconds": s.median_seconds,
                    "max_seconds": s.max_seconds,
                }
                for symbol, s in self.per_symbol.items()
            },
            "stage_latencies": {
                symbol: [stage.to_dict() for stage in stages] for symbol, stages in self.stage_latencies.items()
            },
        }


def compute_execution_quality(store: SignalStore, account_id: str) -> AccountExecutionQuality:
    result = AccountExecutionQuality(account_id=account_id)
    latencies_by_symbol: dict[str, list[float]] = {}
    # One inner list per stage definition, indexed the same way as
    # _STAGE_DEFINITIONS -- kept per-symbol like latencies_by_symbol above.
    stage_samples_by_symbol: dict[str, list[list[float]]] = {}

    all_rows = store.list_filled_orders_chronological(account_id)
    timed_rows = store.list_filled_orders_with_signal_timing(account_id)
    result.unmatched_order_count = len(all_rows) - len(timed_rows)

    for row in timed_rows:
        try:
            received_at = _parse(row["received_at"])
            executed_at = _parse(row["executed_at"])
        except (TypeError, ValueError):
            result.unmatched_order_count += 1
            continue
        latency = (executed_at - received_at).total_seconds()
        if latency < 0:
            # Clock skew or a malformed timestamp -- never report a
            # negative latency as if it were real.
            result.unmatched_order_count += 1
            continue
        latencies_by_symbol.setdefault(row["symbol"], []).append(latency)

        # PU-A2: this order's real stage timestamps, if it has them.
        # `submitted_at`/`protection_confirmed_at` are read straight from
        # the row (already-parsed strings or None); `received_at`/
        # `executed_at` are the already-validated datetimes above.
        try:
            submitted_at = _parse_optional(row.get("submitted_at"))
            protection_confirmed_at = _parse_optional(row.get("protection_confirmed_at"))
        except (TypeError, ValueError):
            # A malformed stage timestamp doesn't invalidate the
            # already-validated signal-to-fill latency above -- it just
            # means this order contributes no stage samples at all,
            # never a fabricated one.
            submitted_at = None
            protection_confirmed_at = None

        stage_points = {
            "received_at": received_at,
            "submitted_at": submitted_at,
            "executed_at": executed_at,
            "protection_confirmed_at": protection_confirmed_at,
        }
        symbol_stage_samples = stage_samples_by_symbol.setdefault(
            row["symbol"], [[] for _ in _STAGE_DEFINITIONS]
        )
        for i, (_, start_key, end_key) in enumerate(_STAGE_DEFINITIONS):
            start = stage_points[start_key]
            end = stage_points[end_key]
            if start is None or end is None:
                # This order never reached one (or both) ends of this
                # stage -- never bridge a real timestamp with a missing
                # one. Skip this stage for this order, not the whole row.
                continue
            interval = (end - start).total_seconds()
            if interval < 0:
                # Same clock-skew/malformed-data guard as the top-level
                # latency above -- never report a negative stage.
                continue
            symbol_stage_samples[i].append(interval)

    for symbol, latencies in latencies_by_symbol.items():
        result.per_symbol[symbol] = SymbolLatency(
            symbol=symbol,
            sample_count=len(latencies),
            mean_seconds=statistics.mean(latencies),
            median_seconds=statistics.median(latencies),
            max_seconds=max(latencies),
        )

    for symbol, symbol_stage_samples in stage_samples_by_symbol.items():
        stages: list[StageLatency] = []
        for (stage_name, _, _), samples in zip(_STAGE_DEFINITIONS, symbol_stage_samples, strict=True):
            if not samples:
                # No order for this symbol had both endpoints for this
                # stage -- omit it entirely rather than report a
                # zero-sample stage that looks like real data.
                continue
            stages.append(
                StageLatency(
                    stage=stage_name,
                    sample_count=len(samples),
                    mean_seconds=statistics.mean(samples),
                    median_seconds=statistics.median(samples),
                    max_seconds=max(samples),
                )
            )
        if stages:
            result.stage_latencies[symbol] = stages

    return result
