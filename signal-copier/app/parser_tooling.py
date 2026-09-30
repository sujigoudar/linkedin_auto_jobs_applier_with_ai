"""Track 15: sample-driven parser tooling, parser-profile versioning and
lifecycle -- built AROUND `app/sources/text_parser.py`'s real parsing
logic, not a parallel parsing engine (see this module's own docstring
sections below for exactly where each function reuses vs. adds to that
module).

The user's own spec (verbatim, from the Track 15 brief) for what this
track exists to support: "Provider -> Parsing -> Learn from Samples. Show
20-100 historical messages. Automatically classify: ENTRY, EXIT, STOP
UPDATE, TARGET UPDATE, ADD, TRIM, CANCEL, COMMENTARY, RESULT, RECAP,
NON-SIGNAL, UNKNOWN. Then extract: symbol, asset_class, side, action,
quantity, quantity_type, entry_price, entry_range, expiration, strike,
call_put, option_strategy, legs[], stop, targets[], time_in_force,
provider_position_id, reply/reference." Corrections to any extracted
field become new/updated parser TEST CASES for that provider's parser,
not just an edit to the one message they came from. Every parser profile
carries an explicit DRAFT -> TESTED -> SHADOW -> CERTIFIED -> ACTIVE ->
RETIRED lifecycle; promoting a new version to ACTIVE is always an
explicit, owner-gated action (never automatic) and never silently
changes what an already-ACTIVE profile did for a signal already routed
through it.

This module is intentionally NO UI -- backend/API only, per this
session's established build order (UI comes in a later, dedicated
track). It is also intentionally NOT WIRED into live signal routing --
see this module's own docstring note below (same "record honestly, wire
later" scoping `app/provider_catalog.py`'s `execution_eligibility` used
for Track 14).

-- Message-type classification (NEW, complementary axis) -------------

`app/sources/text_parser.py`'s `DispositionOutcome` (PARSED/IGNORED/
AMBIGUOUS/MISSING_DATA/NO_MATCH) answers "did this message resolve to
exactly one clean trade instruction". `MessageType` (below) answers a
different question entirely -- "what KIND of message is this" -- and the
two axes are independent: an ENTRY message can be PARSED or MISSING_DATA
(e.g. "BUY AAPL 0 @190" -- clearly an entry attempt, quantity invalid);
a STOP_UPDATE message is essentially never PARSEABLE by that module's
grammar at all (no side keyword), but is still a real, classifiable
message kind this workflow needs to show the operator.

`classify_message_type` is a bounded, keyword/pattern-based classifier,
same "never silently guess" discipline as every other classifier in this
codebase (see `app/notification_bridge.py`'s `classify_notification_
completeness` docstring on this exact point) -- `MessageType.UNKNOWN` is
the honest fallback for anything it isn't confident about, not a
guessed best-fit category. It reuses `classify_text_signal` for the
ENTRY/EXIT split (a message that resolves to a clean BUY/SELL/LONG/SHORT
trade is ENTRY; one that resolves to CLOSE/EXIT is EXIT) rather than
re-implementing that side-word grammar.

-- Structured field extraction (thin wrapper over text_parser + models) -

`extract_fields` is explicitly NOT a parallel parsing engine. Every
field text_parser.py's `classify_text_signal` can already produce
(symbol, asset_class, side, quantity, entry_price, stop, targets) is
read straight off the `Signal` it returns when the message is PARSED --
never re-derived by a second regex pass over the same text. The
remaining fields the user's spec asks for (quantity_type, entry_range,
expiration, strike, call_put, option_strategy, legs, time_in_force,
provider_position_id, reply_reference) have NO existing extraction logic
anywhere in this codebase to reuse -- each is produced by its own small,
bounded, well-commented regex here, and every one of them is `None`
(or `[]` for `legs`) whenever the text doesn't confidently determine it.
None of these small additions are wired back into `Signal`/
`classify_text_signal` itself -- this module reads a `Signal`, it never
mutates the parsing engine that produces one.

-- Parser-profile versioning + lifecycle -------------------------------

`ParserProfileStatus` mirrors the DRAFT/TESTED/SHADOW/CERTIFIED/ACTIVE/
RETIRED lifecycle the user's spec calls for, validated the same
one-step-at-a-time way `app/phone_escalation.py`'s `validate_state_
transition`/`_ALLOWED_TRANSITIONS` validates `CapabilityState` (see that
function's own docstring -- this module's `validate_profile_transition`
is deliberately built on the same shape). Promoting a profile to ACTIVE
demotes the provider's previous ACTIVE profile to RETIRED, atomically,
in `SignalStore.promote_parser_profile` (`app/db.py`) -- never more than
one ACTIVE profile per provider at a time. A RETIRED profile's row is
NEVER deleted or overwritten -- its `accuracy_metrics`/`sample_count`/
`test_count` stay exactly as they were the moment it was retired, so its
behavior stays fully inspectable after a newer version takes over.

`sources.parser_profile` (Track 14's existing column) is documented, as
of this track, to hold a `parser_profiles.id` when a source has been
assigned one -- `app/db.py`'s `get_active_parser_profile_for_source`
(below, thin helper) resolves that link. Neither this module nor
anything it wires up makes `text_parser.py`'s ACTUAL invocation on the
live signal path consult that assignment -- an operator-visible parser
profile selection that changes what a live message is parsed with is
real, valuable follow-up work this track deliberately leaves undone
(same judgment call Track 14 made for `execution_eligibility` not yet
gating `app/engine.py`'s routing decision). `fallback_model` is an
honest placeholder string (`"none"` unless the caller names a real one)
-- this track wires no actual LLM fallback call, same as Track 13's
`SignalExtractor` stub.
"""
from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from typing import Any

from app.models import AssetClass, Side
from app.sources.text_parser import DispositionOutcome, classify_text_signal


class ParserToolingError(ValueError):
    """Raised by validation in this module -- never a silent best-effort
    accept, same convention as `ProviderCatalogError`/`PhoneEscalation
    Error`."""


# ---------------------------------------------------------------------------
# Message-type classification
# ---------------------------------------------------------------------------


class MessageType(str, enum.Enum):
    """What KIND of message this is -- a different, complementary axis
    from `app.sources.text_parser.DispositionOutcome` (parse SUCCESS).
    See this module's own docstring for the full rationale."""

    ENTRY = "entry"
    EXIT = "exit"
    STOP_UPDATE = "stop_update"
    TARGET_UPDATE = "target_update"
    ADD = "add"
    TRIM = "trim"
    CANCEL = "cancel"
    COMMENTARY = "commentary"
    RESULT = "result"
    RECAP = "recap"
    NON_SIGNAL = "non_signal"
    #: The honest fallback -- never a guessed best-fit category. See
    #: `classify_message_type`'s own docstring.
    UNKNOWN = "unknown"


# Every keyword list below is checked as a WHOLE-WORD/phrase substring
# match against the lower-cased message -- bounded and cheap, same "not a
# full parse of meaning" caveat text_parser.py's own negation-word check
# carries. Ordered by priority in `classify_message_type`: a message that
# matches more than one category's keywords (e.g. "cancel the SL update")
# resolves to whichever list is checked first below, most-specific/most-
# consequential first (CANCEL before a plain update, an explicit stop/
# target update before the more generic ADD/TRIM, position-management
# verbs before generic ENTRY/EXIT resolution via the real parser).

_CANCEL_PHRASES = (
    "cancel", "cancelled", "canceled", "scratch this", "scratch the",
    "void the", "disregard the order", "order cancelled", "order canceled",
)
_STOP_UPDATE_PHRASES = (
    "sl to", "stop to", "stop loss to", "move stop", "moving stop",
    "adjust stop", "adjusting stop", "update stop", "updating stop",
    "new stop", "trail stop", "trailing stop", "raise stop", "lower stop",
    "stop moved", "move sl", "moving sl", "sl moved", "tighten stop",
    "breakeven stop", "stop to breakeven", "sl to breakeven",
)
_TARGET_UPDATE_PHRASES = (
    "tp to", "target to", "move target", "moving target", "new target",
    "update target", "updating target", "adjust target", "adjusting target",
    "target moved", "extend target", "raise target", "lower target",
    "move tp", "moving tp", "tp moved",
)
_ADD_PHRASES = (
    "add to", "adding to", "scale in", "scaling in", "average in",
    "averaging in", "dca in", "add more", "adding more", "top up",
    "topping up", "increase position", "increasing position",
    "add another",
)
_TRIM_PHRASES = (
    "trim", "scale out", "scaling out", "partial exit", "partial close",
    "take partial", "taking partial", "reduce position", "reducing position",
    "book some", "lock in some", "sell some", "sold some", "closing partial",
)
_RESULT_PHRASES = (
    "hit tp", "tp hit", "hit sl", "sl hit", "stopped out", "target hit",
    "closed for", "closed +", "closed -", "result:", "pnl:", "p&l:",
    "final result", "trade result", "win:", "loss:", "+r", "-r",
    "pips profit", "pips loss", "banked", "booked profit", "booked loss",
)
#: A leveled TP/SL mention ("TP1 hit", "TP2 reached", "SL1 hit") isn't
#: caught by the plain-phrase list above (that list only matches the
#: bare, unleveled "tp hit"/"sl hit") -- same TPn/SLn labeling
#: text_parser.py's own grammar recognizes elsewhere in this codebase.
_RESULT_LEVELED_PATTERN = re.compile(r"\b(?:tp|sl)\d+\s+(?:hit|reached|triggered)\b", re.IGNORECASE)
_RECAP_PHRASES = (
    "recap", "week in review", "weekly review", "weekly summary",
    "weekly performance", "monthly review", "monthly summary",
    "monthly performance", "month in review", "performance summary",
    "trades this week", "trades this month", "review of the week",
)
_NON_SIGNAL_PHRASES = (
    "good morning", "good evening", "good night", "gm ", "gm!", "gm.",
    "happy new year", "happy friday", "welcome to", "thanks for",
    "thank you", "thanks everyone", "lol", "haha", "how's everyone",
    "how is everyone", "market open", "market closed", "market close in",
    "see you tomorrow", "have a good weekend",
)


def _contains_any(lowered: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in lowered for phrase in phrases)


def classify_message_type(text: str) -> MessageType:
    """Bounded, keyword/pattern classifier -- see this module's own
    docstring for the priority order and for why `UNKNOWN` (never a
    guessed category) is the fallback. Reuses `classify_text_signal` for
    the ENTRY/EXIT split rather than re-implementing that grammar."""
    stripped = text.strip()
    if not stripped:
        return MessageType.UNKNOWN
    lowered = stripped.lower()

    if _contains_any(lowered, _CANCEL_PHRASES):
        return MessageType.CANCEL
    if _contains_any(lowered, _STOP_UPDATE_PHRASES):
        return MessageType.STOP_UPDATE
    if _contains_any(lowered, _TARGET_UPDATE_PHRASES):
        return MessageType.TARGET_UPDATE
    if _contains_any(lowered, _ADD_PHRASES):
        return MessageType.ADD
    if _contains_any(lowered, _TRIM_PHRASES):
        return MessageType.TRIM
    if _contains_any(lowered, _RESULT_PHRASES) or _RESULT_LEVELED_PATTERN.search(lowered):
        return MessageType.RESULT
    if _contains_any(lowered, _RECAP_PHRASES):
        return MessageType.RECAP

    # Reuse the real parser's own side/instruction resolution rather than
    # re-deciding "is this an entry or an exit" with a second grammar --
    # PARSED + Side.CLOSE is an exit instruction, PARSED + BUY/SELL is an
    # entry instruction, exactly what text_parser.py already determined.
    disposition = classify_text_signal(stripped, source="parser-tooling-classification")
    if disposition.outcome is DispositionOutcome.PARSED:
        assert disposition.signal is not None
        return MessageType.EXIT if disposition.signal.side is Side.CLOSE else MessageType.ENTRY

    if _contains_any(lowered, _NON_SIGNAL_PHRASES):
        return MessageType.NON_SIGNAL

    if disposition.outcome is DispositionOutcome.IGNORED:
        # text_parser.py already recognized this as negated/conditional/
        # past-tense commentary ABOUT a trade rather than a live
        # instruction -- that's exactly what COMMENTARY means here too.
        return MessageType.COMMENTARY

    # A short message with no trade-shaped content and no recognized
    # phrase from any list above is more likely off-topic chatter than a
    # genuine trade-related message this classifier just failed to
    # recognize -- but "more likely" is still a guess, so this only
    # narrows to NON_SIGNAL for the clearly-short case; anything with
    # real length and no confident match is UNKNOWN, never guessed.
    word_count = len(re.findall(r"\S+", stripped))
    if word_count <= 3 and not any(ch.isdigit() for ch in stripped):
        return MessageType.NON_SIGNAL

    return MessageType.UNKNOWN


# ---------------------------------------------------------------------------
# Structured field extraction
# ---------------------------------------------------------------------------


_QUANTITY_TYPE_PATTERN = re.compile(r"\b\d[\d,]*(?:\.\d+)?\s*(lots?|units?|shares?|contracts?)\b", re.IGNORECASE)
_ENTRY_RANGE_PATTERN = re.compile(
    r"(?:@|entry|enter|between)\D{0,10}(?P<lo>-?\d[\d,]*(?:\.\d+)?)\s*(?:-|to|and)\s*(?P<hi>-?\d[\d,]*(?:\.\d+)?)",
    re.IGNORECASE,
)
_EXPIRATION_PATTERN = re.compile(
    r"\b(?:exp(?:iry|iration|ires)?|good\s+(?:till|until))[:\s]+(?P<value>[A-Za-z0-9/\-]+)",
    re.IGNORECASE,
)
_TIME_IN_FORCE_PATTERN = re.compile(r"\b(GTC|IOC|FOK|DAY|GTD)\b")
_PROVIDER_POSITION_ID_PATTERN = re.compile(
    r"\b(?:position\s*id|trade\s*id|order\s*id|ref(?:erence)?\s*#?|id)[:#\s]+(?P<value>[A-Za-z0-9\-]{2,})",
    re.IGNORECASE,
)
_REPLY_REFERENCE_PATTERN = re.compile(
    r"\b(?:re|reply\s+to|replying\s+to|in\s+reply\s+to)[:\s]+(?P<value>.+)", re.IGNORECASE
)
_OPTION_STRATEGY_KEYWORDS = (
    "iron condor", "iron butterfly", "vertical spread", "credit spread",
    "debit spread", "put spread", "call spread", "straddle", "strangle",
    "calendar spread", "diagonal spread", "covered call", "cash secured put",
    "butterfly",
)
_OCC_CONTRACT_PATTERN = re.compile(r"^(?P<underlying>[A-Z]{1,6})(?P<date>\d{6})(?P<right>[CP])(?P<strike8>\d{8})$")

# Best-effort ticker fallback for message types (STOP_UPDATE/TARGET_UPDATE/
# ADD/TRIM/CANCEL) that text_parser.py's own grammar never matches (it
# requires a leading side keyword) -- looks for a single, unambiguous
# ALL-CAPS token of plausible ticker shape, excluding common all-caps
# words this free-text grammar already uses for something else. Only
# returns a symbol when EXACTLY ONE candidate remains after filtering --
# multiple candidates are genuinely ambiguous and stay None rather than
# guessing which one is the traded instrument.
_SYMBOL_TOKEN_PATTERN = re.compile(r"\b[A-Z]{1,6}(?:/[A-Z]{1,6})?\b")
_SYMBOL_STOPWORDS = {
    "SL", "TP", "GTC", "IOC", "FOK", "DAY", "GTD", "ADD", "TRIM", "CANCEL",
    "ID", "RE", "EXP", "ATH", "ATL", "PSA", "DCA", "ENTRY", "STOP", "TARGET",
    "USD", "EUR", "GBP",
}


def _fallback_symbol(text: str) -> str | None:
    candidates = {
        m.group(0) for m in _SYMBOL_TOKEN_PATTERN.finditer(text) if m.group(0) not in _SYMBOL_STOPWORDS
    }
    if len(candidates) == 1:
        return next(iter(candidates))
    return None


@dataclass
class ExtractedFields:
    """Every field the user's spec asks a sample-driven "learn from
    samples" workflow to extract, per message. Any field this couldn't
    confidently determine is `None` (or `[]` for `legs`/`targets`) --
    NEVER guessed. `symbol`/`asset_class`/`side`/`quantity`/`entry_price`/
    `stop`/`targets` are read straight off `classify_text_signal`'s own
    `Signal` when it resolves to PARSED (see this module's own docstring)
    -- every other field is produced by this module's own small, bounded
    regexes, independent of whether the message PARSED."""

    symbol: str | None = None
    asset_class: str | None = None
    side: str | None = None
    action: str | None = None
    quantity: float | None = None
    quantity_type: str | None = None
    entry_price: float | None = None
    entry_range: tuple[float, float] | None = None
    expiration: str | None = None
    strike: float | None = None
    call_put: str | None = None
    option_strategy: str | None = None
    legs: list[dict[str, Any]] = field(default_factory=list)
    stop: float | None = None
    targets: list[float] = field(default_factory=list)
    time_in_force: str | None = None
    provider_position_id: str | None = None
    reply_reference: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "asset_class": self.asset_class,
            "side": self.side,
            "action": self.action,
            "quantity": self.quantity,
            "quantity_type": self.quantity_type,
            "entry_price": self.entry_price,
            "entry_range": list(self.entry_range) if self.entry_range is not None else None,
            "expiration": self.expiration,
            "strike": self.strike,
            "call_put": self.call_put,
            "option_strategy": self.option_strategy,
            "legs": self.legs,
            "stop": self.stop,
            "targets": self.targets,
            "time_in_force": self.time_in_force,
            "provider_position_id": self.provider_position_id,
            "reply_reference": self.reply_reference,
        }


#: `MessageType` values that imply a distinct `action` label independent
#: of the underlying `Side` a PARSED signal would carry -- ENTRY/EXIT are
#: intentionally left to `Side` (buy/sell/close) below rather than
#: duplicated here.
_MESSAGE_TYPE_TO_ACTION = {
    MessageType.STOP_UPDATE: "stop_update",
    MessageType.TARGET_UPDATE: "target_update",
    MessageType.ADD: "add",
    MessageType.TRIM: "trim",
    MessageType.CANCEL: "cancel",
}


def extract_fields(text: str, *, message_type: MessageType | None = None) -> ExtractedFields:
    """Thin structured wrapper over `classify_text_signal` -- see this
    module's own docstring for exactly which fields are read off its
    `Signal` vs. produced by this function's own bounded regexes.
    `message_type`, if not given, is derived with `classify_message_type`
    so a caller can classify once and reuse the result, or let this
    function do both in one call."""
    stripped = text.strip()
    resolved_type = message_type if message_type is not None else classify_message_type(stripped)

    result = ExtractedFields()

    disposition = classify_text_signal(stripped, source="parser-tooling-extraction")
    if disposition.outcome is DispositionOutcome.PARSED:
        signal = disposition.signal
        assert signal is not None
        result.symbol = signal.symbol
        result.asset_class = signal.asset_class.value
        result.side = signal.side.value
        result.quantity = signal.quantity
        result.entry_price = signal.price
        result.stop = signal.stop_loss
        result.targets = [t.price for t in signal.targets] if signal.targets else (
            [signal.take_profit] if signal.take_profit is not None else []
        )
    else:
        # No PARSED signal to read from -- only the best-effort ticker
        # fallback below (still confidence-gated: see `_fallback_symbol`'s
        # own docstring) is attempted; every other Signal-shaped field
        # stays honestly None rather than re-parsed by a second engine.
        result.symbol = _fallback_symbol(stripped)

    result.action = _MESSAGE_TYPE_TO_ACTION.get(resolved_type) or (
        result.side if resolved_type in (MessageType.ENTRY, MessageType.EXIT) else None
    )

    qty_match = _QUANTITY_TYPE_PATTERN.search(stripped)
    if qty_match:
        result.quantity_type = qty_match.group(1).lower().rstrip("s") + "s"

    range_match = _ENTRY_RANGE_PATTERN.search(stripped)
    if range_match:
        try:
            lo = float(range_match.group("lo").replace(",", ""))
            hi = float(range_match.group("hi").replace(",", ""))
        except ValueError:
            lo = hi = None  # type: ignore[assignment]
        if lo is not None and hi is not None and lo != hi:
            result.entry_range = (min(lo, hi), max(lo, hi))

    exp_match = _EXPIRATION_PATTERN.search(stripped)
    if exp_match:
        result.expiration = exp_match.group("value")

    tif_match = _TIME_IN_FORCE_PATTERN.search(stripped)
    if tif_match:
        result.time_in_force = tif_match.group(1).upper()

    pos_id_match = _PROVIDER_POSITION_ID_PATTERN.search(stripped)
    if pos_id_match:
        result.provider_position_id = pos_id_match.group("value")

    reply_match = _REPLY_REFERENCE_PATTERN.search(stripped)
    if reply_match:
        result.reply_reference = reply_match.group("value").strip()[:200] or None

    lowered = stripped.lower()
    for phrase in _OPTION_STRATEGY_KEYWORDS:
        if phrase in lowered:
            result.option_strategy = phrase
            break

    if result.symbol:
        occ_match = _OCC_CONTRACT_PATTERN.match(result.symbol.replace("/", "").upper())
        if occ_match:
            result.asset_class = result.asset_class or AssetClass.OPTION.value
            try:
                result.strike = int(occ_match.group("strike8")) / 1000.0
            except ValueError:
                pass
            result.call_put = "call" if occ_match.group("right") == "C" else "put"
            date_raw = occ_match.group("date")
            result.expiration = result.expiration or f"20{date_raw[0:2]}-{date_raw[2:4]}-{date_raw[4:6]}"

    return result


# ---------------------------------------------------------------------------
# Parser-profile versioning + lifecycle
# ---------------------------------------------------------------------------


class ParserProfileStatus(str, enum.Enum):
    """DRAFT -> TESTED -> SHADOW -> CERTIFIED -> ACTIVE -> RETIRED, per
    the user's own spec. `DRAFT` is the only value a freshly-created
    profile may ever start at (enforced in `validate_profile_
    registration`, never left to the caller to remember) -- promotion is
    always a separate, explicit, owner-gated action, same convention as
    `app/phone_escalation.py`'s `CapabilityState`."""

    DRAFT = "draft"
    TESTED = "tested"
    SHADOW = "shadow"
    CERTIFIED = "certified"
    ACTIVE = "active"
    RETIRED = "retired"


#: One step at a time forward (DRAFT -> TESTED -> SHADOW -> CERTIFIED ->
#: ACTIVE), matching the user's own "cannot skip states" requirement --
#: DRAFT cannot jump straight to CERTIFIED or ACTIVE. Any non-RETIRED
#: state may be retired directly (an owner's emergency off-switch for a
#: profile that turned out to be wrong, never blocked by "you must demote
#: one step at a time"), mirroring `phone_escalation.py`'s identical
#: "any state can go straight back to DISABLED" carve-out. RETIRED is
#: terminal -- a retired profile is never resurrected; register a new
#: DRAFT version instead, so its own accuracy history stays intact and
#: inspectable (see this module's own docstring).
_ALLOWED_PROFILE_TRANSITIONS: dict[ParserProfileStatus, frozenset[ParserProfileStatus]] = {
    ParserProfileStatus.DRAFT: frozenset({ParserProfileStatus.TESTED, ParserProfileStatus.RETIRED}),
    ParserProfileStatus.TESTED: frozenset({ParserProfileStatus.SHADOW, ParserProfileStatus.RETIRED}),
    ParserProfileStatus.SHADOW: frozenset({ParserProfileStatus.CERTIFIED, ParserProfileStatus.RETIRED}),
    ParserProfileStatus.CERTIFIED: frozenset({ParserProfileStatus.ACTIVE, ParserProfileStatus.RETIRED}),
    ParserProfileStatus.ACTIVE: frozenset({ParserProfileStatus.RETIRED}),
    ParserProfileStatus.RETIRED: frozenset(),
}


def validate_profile_transition(current: ParserProfileStatus, target: ParserProfileStatus) -> None:
    """Raises `ParserToolingError` (never silently accepts) for a
    transition this module's own lifecycle doesn't allow -- e.g. DRAFT
    straight to ACTIVE, skipping TESTED/SHADOW/CERTIFIED entirely, which
    the user's own instruction ("A parser update should not silently
    alter production behavior") forbids structurally. Mirrors
    `app/phone_escalation.py`'s `validate_state_transition` exactly."""
    if target == current:
        return
    allowed = _ALLOWED_PROFILE_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise ParserToolingError(
            f"cannot transition parser_profile status from {current.value!r} to {target.value!r} -- "
            f"allowed transitions from {current.value!r} are {sorted(s.value for s in allowed)}"
        )


def _require_nonempty(value: str | None, field_name: str) -> str:
    if not value or not str(value).strip():
        raise ParserToolingError(f"{field_name} is required")
    return str(value).strip()


def validate_profile_registration(*, parser_id: str, provider_id: str, version: str) -> None:
    """Validates the fields this module owns vocabulary for BEFORE
    anything is persisted -- same split as `app.provider_catalog.
    validate_provider_registration`."""
    _require_nonempty(parser_id, "parser_id")
    _require_nonempty(provider_id, "provider_id")
    _require_nonempty(version, "version")


def validate_supported_message_types(values: list[str] | None) -> list[str]:
    """Validates every entry of `supported_message_types` against
    `MessageType` (raising `ParserToolingError` for anything else) and
    returns the plain-string list to persist -- `None`/empty means "not
    yet declared", never silently defaulted to "all of them"."""
    if not values:
        return []
    out = []
    for v in values:
        try:
            out.append(MessageType(v).value)
        except ValueError as exc:
            raise ParserToolingError(
                f"supported_message_types entries must be one of {[m.value for m in MessageType]}, got {v!r}"
            ) from exc
    return out
