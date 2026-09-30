"""Track 12: cross-transport signal correlation/dedup.

The SAME real-world trade alert can arrive through more than one
transport at once -- e.g. a provider posts to a Telegram channel AND
sells the identical alert through a Whop community (Track 10's
notification-bridge fallback capture, app/notification_bridge.py) AND
emails it. `SignalStore.find_signal_id_by_provider_identity` (Track 5,
point 6) already dedupes WITHIN one transport/collector, keyed on that
provider's own (channel_id, message_id, revision_id) identity -- see
that method's own docstring. It does NOT, and per its own docstring was
never meant to, dedupe ACROSS different transports: two different
transports for the same real trade have two different, unrelated
channel_id/message_id native identities, so that exact-identity lookup
correctly returns `None` for each.

This module is the layer ABOVE that one: a canonical "fingerprint" over
a signal's PARSED CONTENT (not provider identity) that two independently-
parsed Signals from two different transports both compute the same way,
so `app/engine.py`'s `_handle_signal` can recognize "this is corroborating
evidence for a trade I've already recorded" (and NOT submit a second
order for it) from "this looks like the same event but disagrees on
substance" (and hold it out of live routing entirely, surfaced as
`CONFLICTING_SOURCE_DATA` -- see `app/db.py`'s `signal_correlation_
evidence` table and `list_conflicting_signal_correlations`).

Design, deliberately conservative (composes with, never weakens, the
existing within-transport dedup -- see this module's own test suite and
`tests/test_telegram_cross_collector_dedup.py`'s existing invariants):

- `fingerprint_key` is a DISCRETE hash over fields that must match
  EXACTLY for two signals to even be considered candidates: normalized
  source (provider) name, symbol, side, asset class, and (for options)
  underlying/expiry/strike/right. Price and timing are deliberately
  EXCLUDED from this discrete key -- a hash can't express "close
  enough" -- and are instead compared with tolerance by
  `classify_candidate` against the actual candidate rows.
- `app/engine.py` only ever calls into this layer for a signal that
  already carries real provider identity (`channel_id`/`message_id` are
  both set) -- a signal with no provider identity at all (most of this
  codebase's existing adapters, and most of its own test fixtures) never
  participates in fingerprint correlation. This is the one guard that
  keeps this layer from ever colliding with
  `test_signals_with_no_provider_identity_are_never_deduped_against_
  each_other`'s own invariant.
- Candidates are further restricted to a DIFFERENT `channel_id` than the
  new signal's own (`SignalStore.find_correlation_candidates`) -- a
  same-channel match is already exact-identity dedup's job, not this
  layer's; this scoping is what makes this genuinely a CROSS-transport
  check, never a redundant or competing same-transport one.
- A candidate is only compared when BOTH signals carry a real `price`
  (`classify_candidate` returns `None` -- "not eligible to compare" --
  otherwise) -- price-band tolerance needs real numbers on both sides;
  this layer never guesses at agreement it can't actually check.
"""
from __future__ import annotations

import enum
import hashlib
from datetime import datetime, timezone
from typing import Optional

from app.models import Signal

#: Relative price-band tolerance (Track 12 brief: "not exact equality --
#: allow a small tolerance, since different transports may show
#: slightly different rounding/timing"). Configurable via
#: `config.SIGNAL_CORRELATION_PRICE_TOLERANCE_PCT`; this is only the
#: module-level default a caller may fall back to.
DEFAULT_PRICE_TOLERANCE_PCT = 0.005  # 0.5%

#: How far apart two transports' own provider-reported timestamps
#: (`Signal.received_at` -- the one timestamp every adapter in this
#: codebase actually sets, whether or not it wires real channel_id/
#: message_id identity) may be and still be considered the SAME
#: real-world alert. Configurable via `config.
#: SIGNAL_CORRELATION_TIMESTAMP_WINDOW_SECONDS`.
DEFAULT_TIMESTAMP_WINDOW_SECONDS = 15 * 60  # 15 minutes


class CorrelationOutcome(str, enum.Enum):
    """What one candidate signal turned out to be, relative to the new
    one being correlated -- see this module's own docstring."""

    #: Same fingerprint key, price/side agree within tolerance, within
    #: the timestamp window -- treat as the SAME underlying trade.
    CORROBORATING = "corroborating"
    #: Same fingerprint key, within the timestamp window, but price or
    #: side disagrees materially -- looks like the same event but
    #: disagrees on substance. Never silently resolved either way.
    CONFLICTING = "conflicting"


def fingerprint_key(signal: Signal) -> str:
    """The discrete part of `signal`'s cross-transport identity -- see
    this module's own docstring for exactly what is and isn't part of
    this key and why. Two Signals from two different transports
    describing the SAME real trade, parsed by two different adapters,
    are expected to compute the SAME key here (same normalized source
    name, same symbol, same side, same asset class, same option
    strike/expiry/right when applicable) -- that agreement is what makes
    them candidates at all; price/timing tolerance is checked
    separately, against the actual rows, by `classify_candidate`."""
    parts = [
        (signal.source or "").strip().lower(),
        (signal.symbol or "").strip().upper(),
        signal.side.value if signal.side else "",
        signal.asset_class.value if signal.asset_class else "",
    ]
    if signal.option is not None:
        parts.extend(
            [
                (signal.option.underlying or "").strip().upper(),
                (signal.option.expiry or "").strip(),
                f"{signal.option.strike:.4f}" if signal.option.strike is not None else "",
                (signal.option.right or "").strip().lower(),
            ]
        )
    payload = "\x1f".join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _as_aware_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def within_timestamp_window(a: datetime, b: datetime, *, window_seconds: float) -> bool:
    return abs((_as_aware_utc(a) - _as_aware_utc(b)).total_seconds()) <= window_seconds


def prices_within_tolerance(a: float, b: float, *, tolerance_pct: float) -> bool:
    """A relative-band comparison, never exact equality -- see this
    module's own docstring. Both values must be genuinely positive
    (a non-positive price is never valid trade data in this codebase --
    see e.g. app/sources/webhook.py's own `_optional_positive_float`) --
    returns `False`, never divides by zero or a negative denominator,
    for anything else."""
    if a <= 0 or b <= 0:
        return False
    return abs(a - b) / max(a, b) <= tolerance_pct


def classify_candidate(
    *,
    new_price: Optional[float],
    new_side: str,
    new_received_at: datetime,
    candidate_price: Optional[float],
    candidate_side: str,
    candidate_received_at: datetime,
    price_tolerance_pct: float = DEFAULT_PRICE_TOLERANCE_PCT,
    window_seconds: float = DEFAULT_TIMESTAMP_WINDOW_SECONDS,
) -> Optional[CorrelationOutcome]:
    """Classifies ONE already-fingerprint-matched candidate against the
    new signal. Returns `None` -- "not eligible to compare, say nothing"
    -- whenever either side is missing a real price to compare (this
    layer never guesses at price agreement it can't check) or the two
    timestamps fall outside `window_seconds` of each other (too far
    apart to plausibly be the same live alert, regardless of price
    agreement -- never silently correlated across an arbitrary time
    gap). Otherwise: `CORROBORATING` when side matches AND price is
    within `price_tolerance_pct`; `CONFLICTING` for anything else that
    reached this point (a fingerprint-key match that disagrees on side
    outright, or whose price is outside tolerance)."""
    if new_price is None or candidate_price is None:
        return None
    if not within_timestamp_window(new_received_at, candidate_received_at, window_seconds=window_seconds):
        return None
    if new_side != candidate_side:
        return CorrelationOutcome.CONFLICTING
    if prices_within_tolerance(new_price, candidate_price, tolerance_pct=price_tolerance_pct):
        return CorrelationOutcome.CORROBORATING
    return CorrelationOutcome.CONFLICTING
