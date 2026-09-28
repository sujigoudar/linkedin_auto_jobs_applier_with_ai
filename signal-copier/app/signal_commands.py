"""Canonical command vocabulary for signal revisions.

Today the engine only understands BUY/SELL/CLOSE (`app/models.py`'s
`Signal`/`Side`) plus whatever lifecycle plan fields ride along with a fresh
entry. Real provider channels also send REVISIONS to a trade already in
flight -- "close half", "move sl to breakeven", "cancel" -- that don't fit
that grammar at all. This module gives those revisions an explicit, typed
shape every source adapter can emit, independent of that source's own
parsing quirks, so the engine has one vocabulary to dispatch regardless of
whether the words came from Telegram, a webhook JSON body, or anywhere else.

## Execution posture -- read this before adding a new source producer

A `CanonicalCommand` is a RECORDED, TYPED INTENT. It is not automatically
"the engine did something." `app/engine.py`'s `SignalCopierEngine.apply_canonical_command`
is the ONLY place that turns one into a real broker action, and it does so
by calling straight into the EXISTING `PositionLifecycleManager`/`CloseArbiter`
machinery (`request_exit`, the stop-replace transition, `cancel_order`) --
there is no second, parallel execution path here.

Three command types have a real, already-tested backing capability in this
engine and are wired end-to-end:

    CLOSE_PERCENT   -> PositionLifecycleManager.request_exit (partial close)
    MOVE_STOP       -> PositionLifecycleManager.move_stop (stop replace/tighten)
    CANCEL_ENTRY    -> PositionLifecycleManager.cancel_entry (order cancel)

One more reuses that same close capability at fraction=1.0, so it is wired
too:

    CLOSE_REMAINDER -> PositionLifecycleManager.request_exit (full close)

Three command types have NO backing capability in the current lifecycle
manager -- there is no way to add a second target, remove one, or scale
into an already-open managed-lifecycle position once entered (see
`app/lifecycle/manager.py`'s `validate_plan`, which explicitly REJECTS a
second entry for an already-open (account, symbol), and `PositionPlan.targets`,
which is fixed at entry time with no "add one more" method at all). For
these, `apply_canonical_command` records the command (so it is never
silently dropped) and returns an explicit REJECTED result explaining that
there is no backing capability yet -- it never approximates one:

    ADD_TARGET      -> classification only, no execution
    REMOVE_TARGET   -> classification only, no execution
    ADD_ENTRY       -> classification only, no execution
"""
from __future__ import annotations

import enum
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Union


class CommandType(str, enum.Enum):
    CLOSE_PERCENT = "close_percent"
    MOVE_STOP = "move_stop"
    CANCEL_ENTRY = "cancel_entry"
    ADD_TARGET = "add_target"
    REMOVE_TARGET = "remove_target"
    ADD_ENTRY = "add_entry"
    CLOSE_REMAINDER = "close_remainder"


#: Commands with a real, already-tested execution path through
#: `PositionLifecycleManager`/`CloseArbiter` -- see this module's docstring.
BACKED_COMMAND_TYPES = frozenset(
    {CommandType.CLOSE_PERCENT, CommandType.MOVE_STOP, CommandType.CANCEL_ENTRY, CommandType.CLOSE_REMAINDER}
)


@dataclass
class CanonicalCommand:
    """A typed, source-independent revision instruction. `symbol` and
    `analyst` mirror `app/models.py`'s `Signal` fields so a command can be
    correlated to a `SignalEpisode`/managed-lifecycle position the same way
    a `Signal` is. Fields not relevant to a given `command_type` are left
    `None` -- there is deliberately no subclassing here (one flat shape,
    same posture as `Signal` itself)."""

    command_type: CommandType
    source: str
    symbol: str
    analyst: Optional[str] = None
    #: CLOSE_PERCENT: fraction of the currently-open quantity to close
    #: (0 < fraction <= 1). ADD_ENTRY: fraction of the ORIGINAL planned
    #: quantity to add (mirrors `app/lifecycle/models.py`'s `Target.reduce_fraction`
    #: convention of "a fraction of the plan," not of whatever happens to be
    #: owned right now).
    fraction: Optional[float] = None
    #: MOVE_STOP: either the literal string "breakeven" or a specific price.
    stop_target: Optional[Union[str, float]] = None
    #: ADD_TARGET / ADD_ENTRY: the price the new target/entry is at.
    price: Optional[float] = None
    #: REMOVE_TARGET: which target to remove. This engine's `Target` model
    #: (app/lifecycle/models.py) has no id field at all yet -- there is
    #: nothing a real id could reference -- so this is always `None` today;
    #: see this module's docstring on why REMOVE_TARGET has no backing
    #: capability.
    target_id: Optional[str] = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.command_type, str):
            self.command_type = CommandType(self.command_type)

    @property
    def is_backed(self) -> bool:
        return self.command_type in BACKED_COMMAND_TYPES


# --- free-text classification -------------------------------------------------
#
# Heuristic, best-effort, same posture as app/sources/text_parser.py: a
# bounded set of real-world phrasings, not an NLP model. Anything this
# doesn't recognize returns None rather than guessing -- a caller must never
# treat "didn't match" as "close everything" or any other specific command.

_CANCEL_PATTERN = re.compile(r"\bcancel(?:led|ed)?\b|\bscratch\b|\bvoid\b", re.IGNORECASE)

_BREAKEVEN_PATTERN = re.compile(
    r"\b(?:sl|stop)\b.{0,20}\b(?:to\s+)?(?:breakeven|break[\s-]?even|be|entry)\b"
    r"|\bmove\b.{0,20}\bstop\b.{0,20}\bentry\b",
    re.IGNORECASE,
)

_FRACTION_WORDS = {
    "half": 0.5,
    "a half": 0.5,
    "third": 1.0 / 3.0,
    "a third": 1.0 / 3.0,
    "quarter": 0.25,
    "a quarter": 0.25,
    "three quarters": 0.75,
    "all": 1.0,
    "everything": 1.0,
}

_CLOSE_FRACTION_PATTERN = re.compile(
    r"\b(?:close|sell|take|book|reduce)\b[^%\n]{0,20}?"
    r"(?P<amount>half|a half|third|a third|quarter|three quarters|\d{1,3}(?:\.\d+)?\s*%)",
    re.IGNORECASE,
)

_CLOSE_ALL_PATTERN = re.compile(
    r"\bclose\s+(?:all|everything|full(?:y)?|out)\b|\bflatten\b|\bexit\s+all\b|\bfull\s+close\b",
    re.IGNORECASE,
)

_STOP_PATTERN = re.compile(
    r"\b(?:sl|stop(?:\s*loss)?)\b\s*(?:to|now|[:=@])?\s*(?P<price>\d+(?:\.\d+)?)", re.IGNORECASE
)

_ADD_TARGET_PATTERN = re.compile(
    r"\b(?:add|new)\s+(?:tp|target)s?\b.{0,20}?(?P<price>\d+(?:\.\d+)?)"
    r"|\btp\s*[2-9]\b.{0,20}?(?P<price2>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

_CHANGE_TARGET_PATTERN = re.compile(
    r"\b(?:move|change|update)\s+(?:tp|target)\b.{0,20}?(?P<price>\d+(?:\.\d+)?)"
    r"|\btp\s*(?:now|to)\b\s*(?P<price2>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)

_ADD_ENTRY_PATTERN = re.compile(
    r"\b(?:add|scale\s*in|add\s+to\s+position|buy\s+more|sell\s+more)\b.{0,20}?(?:at\s*)?(?P<price>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)


def _extract_fraction(amount: str) -> float | None:
    amount = amount.strip().lower()
    if amount in _FRACTION_WORDS:
        return _FRACTION_WORDS[amount]
    if amount.endswith("%"):
        try:
            value = float(amount[:-1].strip())
        except ValueError:
            return None
        if 0 < value <= 100:
            return value / 100.0
    return None


def classify_command_text(
    text: str, *, source: str, symbol: str, analyst: str | None = None
) -> CanonicalCommand | None:
    """Best-effort classification of one free-text revision message into a
    `CanonicalCommand`. Returns `None` when nothing recognizable matched --
    the caller (a source adapter) must fall back to whatever it already
    does for unparsed text (e.g. `app/sources/text_parser.py`'s NEW_ENTRY
    grammar, or simply ignoring the message), never invent a command for
    text this didn't confidently recognize.

    `symbol` must already be resolved by the caller (the open position/
    episode this revision applies to) -- this function has no symbol of its
    own to infer from free text like "close half" alone."""
    stripped = text.strip()
    if not stripped:
        return None

    def _make(command_type: CommandType, **kwargs: Any) -> CanonicalCommand:
        return CanonicalCommand(
            command_type=command_type, source=source, symbol=symbol, analyst=analyst, raw={"text": text}, **kwargs
        )

    if _CANCEL_PATTERN.search(stripped):
        return _make(CommandType.CANCEL_ENTRY)

    if _BREAKEVEN_PATTERN.search(stripped):
        return _make(CommandType.MOVE_STOP, stop_target="breakeven")

    frac_match = _CLOSE_FRACTION_PATTERN.search(stripped)
    if frac_match:
        fraction = _extract_fraction(frac_match.group("amount"))
        if fraction is not None:
            if fraction >= 1.0:
                return _make(CommandType.CLOSE_REMAINDER)
            return _make(CommandType.CLOSE_PERCENT, fraction=fraction)

    if _CLOSE_ALL_PATTERN.search(stripped):
        return _make(CommandType.CLOSE_REMAINDER)

    add_target_match = _ADD_TARGET_PATTERN.search(stripped)
    if add_target_match:
        price = add_target_match.group("price") or add_target_match.group("price2")
        return _make(CommandType.ADD_TARGET, price=float(price) if price else None)

    change_target_match = _CHANGE_TARGET_PATTERN.search(stripped)
    if change_target_match:
        price = change_target_match.group("price") or change_target_match.group("price2")
        # No target-id scheme exists to say WHICH target changes (see
        # REMOVE_TARGET's docstring on `target_id`) -- classify as a
        # REMOVE_TARGET (of the one implicit target) plus an ADD_TARGET is
        # over-modeling what a single flat command can represent; recorded
        # as ADD_TARGET at the new price, which is exactly as unactionable
        # as a genuine add (no backing capability either way).
        return _make(CommandType.ADD_TARGET, price=float(price) if price else None)

    add_entry_match = _ADD_ENTRY_PATTERN.search(stripped)
    if add_entry_match:
        price = add_entry_match.group("price")
        return _make(CommandType.ADD_ENTRY, price=float(price) if price else None)

    stop_match = _STOP_PATTERN.search(stripped)
    if stop_match:
        return _make(CommandType.MOVE_STOP, stop_target=float(stop_match.group("price")))

    return None
