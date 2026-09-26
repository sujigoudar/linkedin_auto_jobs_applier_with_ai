"""E05 (bounded): signal-to-fill latency reporting from what this schema
actually timestamps today -- a signal's `received_at` (when this service
first saw it) and its resulting order's `executed_at` (when the broker
call returned).

Honesty about scope, per the adoption plan's own E05 acceptance
obligations: this is NOT the full "publication -> decision -> submit ->
acknowledge -> fill -> protection" pipeline the plan describes -- this
schema has no separately tracked decision/submission/acknowledgement
timestamp, only the two above. Reporting a single "signal_to_fill"
latency is an honest, narrower measurement, not a claim of finer-grained
attribution this data can't support. A future schema change (adding
submitted_at/acknowledged_at columns to `orders`) would let this module
report the fuller breakdown without changing its public shape.

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


@dataclass
class SymbolLatency:
    symbol: str
    sample_count: int
    mean_seconds: float
    median_seconds: float
    max_seconds: float


@dataclass
class AccountExecutionQuality:
    account_id: str
    per_symbol: dict[str, SymbolLatency] = field(default_factory=dict)
    #: Filled orders whose signal_id didn't match any stored signal (e.g.
    #: an internal exit signal saved without a store wired in) -- excluded
    #: from every latency figure rather than silently treated as instant.
    unmatched_order_count: int = 0

    def to_dict(self) -> dict:
        return {
            "account_id": self.account_id,
            "note": "Signal-received-to-fill latency only -- this schema does not separately track "
            "decision/submission/acknowledgement timestamps, so no finer-grained breakdown is "
            "reported. Mixes this process's own processing time with real network/broker latency.",
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
        }


def compute_execution_quality(store: SignalStore, account_id: str) -> AccountExecutionQuality:
    result = AccountExecutionQuality(account_id=account_id)
    latencies_by_symbol: dict[str, list[float]] = {}

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

    for symbol, latencies in latencies_by_symbol.items():
        result.per_symbol[symbol] = SymbolLatency(
            symbol=symbol,
            sample_count=len(latencies),
            mean_seconds=statistics.mean(latencies),
            median_seconds=statistics.median(latencies),
            max_seconds=max(latencies),
        )

    return result
