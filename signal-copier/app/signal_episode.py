"""SignalEpisode: correlates a sequence of independent provider messages
("BUY GOLD 2350", "SL 2342", "move SL to entry", "close half") into ONE
underlying trade idea from one analyst, instead of treating each inbound
message as an unrelated event.

## What this is, and what it is NOT

This is a PARSING/CLASSIFICATION layer. A `SignalEpisode` records intent
HISTORY -- what an analyst said, in order, about one trade idea. It does
NOT execute anything and it is not a new authority over broker actions:
`app/lifecycle/manager.py`'s `PositionLifecycleManager` and
`app/lifecycle/close_arbiter.py`'s `CloseArbiter` remain the sole path to
real broker commands (see `app/signal_commands.py` for how a classified
revision reaches that engine, when it has a backing capability at all).

## Correlation identity

Every message is classified into a `RevisionEventType` (see
`classify_revision_event` below) and, if it is meant to revise an EXISTING
trade rather than start a new one, correlated against this episode's own
store using the identity actually available on `app/models.py`'s `Signal`:

    provider   = signal.source   (which channel/integration this came from)
    analyst    = signal.analyst  (who, within that source, posted it)
    instrument = signal.symbol, when the message states one

`signal.analyst` is populated by the SOURCE adapter, and how trustworthy it
is varies by source -- see each source's own module docstring:

    - Telegram (`app/sources/telegram.py`): the bot API's own
      `update.effective_user.username`/`full_name` -- platform-verified,
      the poster genuinely is who they claim to be.
    - Webhook (`app/sources/webhook.py`): a plain `"analyst"` field the
      JSON body itself declares -- NOT authenticated by anything here; a
      caller can claim to be any analyst. Real, available signal (the spec
      language this module follows), but not identity-verified the way
      Telegram's is. This is a disclosed limitation, not a defect
      introduced here.

A source that captures NO analyst identity at all (`signal.analyst is
None`) cannot correlate reliably -- every message from it always becomes a
fresh `NEW_ENTRY` episode rather than guessing which open episode (if any)
it might continue. Only `provider` (a fixed value per source instance)
would be left to key off, which is nowhere near enough to distinguish two
different analysts' trades in the same channel.

## AMBIGUOUS: never auto-correlate

When a revision message could plausibly belong to more than one open
episode for the same (provider, analyst) -- e.g. the analyst has GOLD and
EURUSD both open and posts "move sl to breakeven" with no symbol -- this
never guesses. It classifies the message's disposition as AMBIGUOUS and
returns it uncorrelated (`episode is None`, `candidate_episode_ids` lists
what it could have meant) for the caller to log/surface. The same applies
when there are NO open candidates at all for an otherwise-correlatable
revision: there is nothing to revise, so guessing which past episode it
might be about (or silently starting a new one under a revision label)
would misrepresent what the analyst actually said.
"""
from __future__ import annotations

import enum
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.models import Side, Signal
from app.sources.text_parser import DispositionOutcome, classify_text_signal


class RevisionEventType(str, enum.Enum):
    NEW_ENTRY = "new_entry"
    ADD_ENTRY = "add_entry"
    CHANGE_STOP = "change_stop"
    ADD_TARGET = "add_target"
    CHANGE_TARGET = "change_target"
    CANCEL_ENTRY = "cancel_entry"
    CLOSE_PERCENT = "close_percent"
    CLOSE_ALL = "close_all"
    MOVE_TO_BREAKEVEN = "move_to_breakeven"
    TRAIL = "trail"
    COMMENTARY = "commentary"
    NO_ACTION = "no_action"
    AMBIGUOUS = "ambiguous"


#: Event types that describe a trade already open -- these are the ones
#: correlation applies to. NEW_ENTRY always opens a fresh episode.
#: COMMENTARY/NO_ACTION never touch episode state (see `ingest`'s docstring).
_REVISION_EVENT_TYPES = frozenset(
    {
        RevisionEventType.ADD_ENTRY,
        RevisionEventType.CHANGE_STOP,
        RevisionEventType.ADD_TARGET,
        RevisionEventType.CHANGE_TARGET,
        RevisionEventType.CANCEL_ENTRY,
        RevisionEventType.CLOSE_PERCENT,
        RevisionEventType.CLOSE_ALL,
        RevisionEventType.MOVE_TO_BREAKEVEN,
        RevisionEventType.TRAIL,
    }
)


@dataclass
class RevisionClassification:
    text: str
    event_type: RevisionEventType
    symbol: str | None = None
    side: Side | None = None
    price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    close_fraction: float | None = None
    stop_target: str | float | None = None  # "breakeven" or a specific price
    detail: str | None = None


# --- free-text classification -------------------------------------------------
#
# Heuristic, best-effort -- same posture as app/sources/text_parser.py's own
# grammar: a bounded set of real-world phrasings a manual trade-call channel
# actually uses, not an NLP model. Nothing here is confident enough to
# invent a symbol/price it doesn't see; unmatched text is COMMENTARY, never
# guessed into a specific revision.

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

_TRAIL_PATTERN = re.compile(r"\btrail(?:ing)?\b", re.IGNORECASE)

_NO_ACTION_PATTERN = re.compile(
    r"^\s*(?:hold(?:ing)?|no\s+change|stay(?:ing)?\s+put|do\s+nothing|sit\s+tight)\s*[.!]?\s*$", re.IGNORECASE
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

_SYMBOL_HINT_PATTERN = re.compile(r"\b(?P<symbol>[A-Z]{2,10}(?:USDT|USD|EUR|GBP|JPY)?)\b")


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


def _guess_symbol(text: str, *, known_symbols: frozenset[str] = frozenset()) -> str | None:
    """Only ever returns a symbol this message EXPLICITLY names against a
    caller-supplied set of symbols actually worth considering (e.g. the
    analyst's currently-open episodes) -- never a bare guess from shape
    alone (that's `app/sources/text_parser.py`'s `_infer_asset_class`'s
    job for a NEW_ENTRY, a different question: "what kind of instrument is
    this" vs. here, "which of these SPECIFIC open trades is this about")."""
    if not known_symbols:
        return None
    for match in _SYMBOL_HINT_PATTERN.finditer(text.upper()):
        candidate = match.group("symbol")
        if candidate in known_symbols:
            return candidate
    return None


def classify_revision_event(
    text: str, *, known_symbols: frozenset[str] = frozenset()
) -> RevisionClassification:
    """Classify one message's revision-event type. `known_symbols` narrows
    which symbol (if any) a message that doesn't carry its own trade grammar
    (e.g. "close half") can be said to explicitly name -- normally the set
    of symbols the analyst currently has open episodes in; pass an empty
    set if the caller doesn't have that context (the message will then
    simply carry no `symbol`, which is fine: correlation falls back to
    "the analyst's one open episode" and is AMBIGUOUS if there's more than
    one)."""
    stripped = text.strip()
    if not stripped:
        return RevisionClassification(text=text, event_type=RevisionEventType.COMMENTARY, detail="empty message")

    symbol = _guess_symbol(stripped, known_symbols=known_symbols)

    # NEW_ENTRY always wins outright: a message that actually opens with a
    # real side keyword (buy/sell/long/short) is a fresh trade idea, full
    # stop. It is decided FIRST and unconditionally, before any of the
    # revision-specific patterns below get a look -- a fresh entry's own
    # "SL 90"/"TP 100" text would otherwise collide with the standalone
    # CHANGE_STOP/ADD_TARGET patterns (those exist for a message that is
    # ONLY a stop/target revision, not an entry that happens to state one).
    disposition = classify_text_signal(stripped, source="signal_episode")
    if disposition.outcome is DispositionOutcome.PARSED:
        assert disposition.signal is not None
        if disposition.signal.side is not Side.CLOSE:
            return RevisionClassification(
                text=text,
                event_type=RevisionEventType.NEW_ENTRY,
                symbol=disposition.signal.symbol,
                side=disposition.signal.side,
                price=disposition.signal.price,
                stop_loss=disposition.signal.stop_loss,
                take_profit=disposition.signal.take_profit,
            )
    elif disposition.outcome is DispositionOutcome.AMBIGUOUS:
        return RevisionClassification(text=text, event_type=RevisionEventType.AMBIGUOUS, detail=disposition.detail)

    # Either the shared grammar didn't produce a fresh entry at all, or it
    # produced `side == CLOSE` -- which is itself ambiguous: the shared
    # grammar (app/sources/text_parser.py) accepts "close <anything>" as a
    # full close of an instrument literally named <anything>, with no
    # notion that "half"/"all"/a percent is a FRACTION, not a symbol. The
    # revision-specific patterns below are checked first for exactly that
    # reason -- "close half"/"close all" are real revision commands, never
    # a literal close of a symbol called "HALF"/"ALL".
    if _CANCEL_PATTERN.search(stripped):
        return RevisionClassification(text=text, event_type=RevisionEventType.CANCEL_ENTRY, symbol=symbol)

    if _BREAKEVEN_PATTERN.search(stripped):
        return RevisionClassification(
            text=text, event_type=RevisionEventType.MOVE_TO_BREAKEVEN, symbol=symbol, stop_target="breakeven"
        )

    frac_match = _CLOSE_FRACTION_PATTERN.search(stripped)
    if frac_match:
        fraction = _extract_fraction(frac_match.group("amount"))
        if fraction is not None:
            return RevisionClassification(
                text=text, event_type=RevisionEventType.CLOSE_PERCENT, symbol=symbol, close_fraction=fraction
            )

    if _CLOSE_ALL_PATTERN.search(stripped):
        return RevisionClassification(text=text, event_type=RevisionEventType.CLOSE_ALL, symbol=symbol)

    if _TRAIL_PATTERN.search(stripped):
        return RevisionClassification(text=text, event_type=RevisionEventType.TRAIL, symbol=symbol)

    change_target_match = _CHANGE_TARGET_PATTERN.search(stripped)
    if change_target_match:
        price = change_target_match.group("price") or change_target_match.group("price2")
        return RevisionClassification(
            text=text,
            event_type=RevisionEventType.CHANGE_TARGET,
            symbol=symbol,
            take_profit=float(price) if price else None,
        )

    add_target_match = _ADD_TARGET_PATTERN.search(stripped)
    if add_target_match:
        price = add_target_match.group("price") or add_target_match.group("price2")
        return RevisionClassification(
            text=text,
            event_type=RevisionEventType.ADD_TARGET,
            symbol=symbol,
            take_profit=float(price) if price else None,
        )

    add_entry_match = _ADD_ENTRY_PATTERN.search(stripped)
    if add_entry_match:
        price = add_entry_match.group("price")
        return RevisionClassification(
            text=text, event_type=RevisionEventType.ADD_ENTRY, symbol=symbol, price=float(price) if price else None
        )

    stop_match = _STOP_PATTERN.search(stripped)
    if stop_match:
        return RevisionClassification(
            text=text,
            event_type=RevisionEventType.CHANGE_STOP,
            symbol=symbol,
            stop_loss=float(stop_match.group("price")),
            stop_target=float(stop_match.group("price")),
        )

    if _NO_ACTION_PATTERN.match(stripped):
        return RevisionClassification(text=text, event_type=RevisionEventType.NO_ACTION, symbol=symbol)

    # Nothing revision-specific matched. If the shared grammar DID parse
    # this as `side == CLOSE` (checked above), it's a genuine close of a
    # real instrument name ("close AAPL") -- no command pattern claimed it
    # as a fraction/all/etc, so trust the shared grammar's own symbol.
    if disposition.outcome is DispositionOutcome.PARSED:
        assert disposition.signal is not None
        return RevisionClassification(
            text=text, event_type=RevisionEventType.CLOSE_ALL, symbol=disposition.signal.symbol
        )

    return RevisionClassification(text=text, event_type=RevisionEventType.COMMENTARY, symbol=symbol)


# --- SignalEpisode domain model ------------------------------------------------


class EpisodeLifecycleState(str, enum.Enum):
    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"


@dataclass
class SignalEpisode:
    provider: str
    analyst: Optional[str]
    instrument: str
    direction: Side
    episode_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source_message_ids: list[str] = field(default_factory=list)
    revision: int = 0
    lifecycle_state: EpisodeLifecycleState = EpisodeLifecycleState.OPEN
    #: The resolved current plan -- a plain JSON-able dict, deliberately
    #: loose-shaped (this is a history/intent record, not itself something
    #: an executor reads -- see this module's docstring). Always reflects
    #: the LATEST unambiguous revision applied.
    current_intent: dict[str, Any] = field(default_factory=dict)
    entry_plan: list[dict[str, Any]] = field(default_factory=list)
    stop_plan: Optional[float] = None
    targets: list[dict[str, Any]] = field(default_factory=list)

    def apply_revision(self, classification: RevisionClassification, *, message_id: str) -> None:
        """Mutate this episode's plan/history for an ALREADY-CORRELATED
        revision -- the caller (`EpisodeCorrelator.ingest`) is responsible
        for deciding this classification belongs to this specific episode
        (never called for an AMBIGUOUS or uncorrelated disposition)."""
        self.source_message_ids.append(message_id)
        self.revision += 1
        self.updated_at = datetime.now(timezone.utc)
        event = classification.event_type

        if event is RevisionEventType.ADD_ENTRY:
            self.entry_plan.append({"price": classification.price, "text": classification.text})
            self.current_intent["last_entry_add"] = classification.price
        elif event is RevisionEventType.CHANGE_STOP:
            self.stop_plan = classification.stop_loss
            self.current_intent["stop"] = classification.stop_loss
        elif event is RevisionEventType.MOVE_TO_BREAKEVEN:
            self.current_intent["stop"] = "breakeven"
        elif event is RevisionEventType.ADD_TARGET:
            self.targets.append({"price": classification.take_profit, "text": classification.text})
            self.current_intent["targets"] = list(self.targets)
        elif event is RevisionEventType.CHANGE_TARGET:
            if self.targets:
                self.targets[-1] = {"price": classification.take_profit, "text": classification.text}
            else:
                self.targets.append({"price": classification.take_profit, "text": classification.text})
            self.current_intent["targets"] = list(self.targets)
        elif event is RevisionEventType.CANCEL_ENTRY:
            self.lifecycle_state = EpisodeLifecycleState.CANCELLED
            self.current_intent["status"] = "cancelled"
        elif event is RevisionEventType.CLOSE_PERCENT:
            self.current_intent["last_close_fraction"] = classification.close_fraction
        elif event is RevisionEventType.CLOSE_ALL:
            self.lifecycle_state = EpisodeLifecycleState.CLOSED
            self.current_intent["status"] = "closed"
        elif event is RevisionEventType.TRAIL:
            self.current_intent["trailing"] = True


@dataclass
class CorrelationResult:
    classification: RevisionClassification
    episode: Optional[SignalEpisode]
    created_new: bool
    #: Every open episode that was a plausible match when the outcome is
    #: AMBIGUOUS (or empty candidates) -- for logging/surfacing, never
    #: auto-picked from.
    candidate_episode_ids: list[str] = field(default_factory=list)
    #: Why correlation didn't happen, when `episode is None` and this
    #: wasn't a NEW_ENTRY/non-revision event -- e.g. "no identity", "no
    #: open episode to revise", "N plausible open episodes".
    reason: str | None = None


class EpisodeStore:
    """In-memory episode store, keyed the way correlation actually needs to
    query it: (provider, analyst). A caller wanting durable persistence
    wraps this with `app/db.py`'s `SignalStore.save_episode`/
    `load_open_episodes` (see `EpisodeCorrelator`'s optional `store`
    parameter) -- this class itself has no I/O."""

    def __init__(self) -> None:
        self._episodes: dict[str, SignalEpisode] = {}

    def get(self, episode_id: str) -> SignalEpisode | None:
        return self._episodes.get(episode_id)

    def put(self, episode: SignalEpisode) -> None:
        self._episodes[episode.episode_id] = episode

    def open_episodes_for(self, provider: str, analyst: str | None, instrument: str | None = None) -> list[SignalEpisode]:
        return [
            ep
            for ep in self._episodes.values()
            if ep.lifecycle_state is EpisodeLifecycleState.OPEN
            and ep.provider == provider
            and ep.analyst == analyst
            and (instrument is None or ep.instrument == instrument)
        ]

    def all_open(self) -> list[SignalEpisode]:
        return [ep for ep in self._episodes.values() if ep.lifecycle_state is EpisodeLifecycleState.OPEN]


class EpisodeCorrelator:
    """Classifies + correlates one inbound `Signal`'s underlying text
    against open `SignalEpisode`s for its (provider, analyst).

    `sqlite_store`, if given, is `app/db.py`'s `SignalStore` -- every
    create/update is persisted through it immediately (same
    "persist-on-every-transition" posture as `PositionLifecycleManager`);
    without one, episodes live only in the in-memory `EpisodeStore` (fine
    for tests, not for a real deployment)."""

    def __init__(self, store: EpisodeStore | None = None, sqlite_store: Any = None) -> None:
        self.store = store or EpisodeStore()
        self.sqlite_store = sqlite_store
        if sqlite_store is not None:
            for row in sqlite_store.load_open_episodes():
                self.store.put(row)

    def _persist(self, episode: SignalEpisode) -> None:
        self.store.put(episode)
        if self.sqlite_store is not None:
            self.sqlite_store.save_episode(episode)

    def ingest(self, signal: Signal, *, text: str | None = None) -> CorrelationResult:
        """`text` is the raw message text to classify -- defaults to
        `signal.raw.get("text")` (what `app/sources/text_parser.py`-based
        sources already stash there), since a `Signal` itself carries no
        free-text field. Returns a `CorrelationResult` describing what was
        classified and, when applicable, which episode it was applied to."""
        message_text = text if text is not None else str(signal.raw.get("text", ""))

        has_identity = bool(signal.source) and signal.analyst is not None and signal.analyst != ""
        if not has_identity:
            # Fallback per this module's docstring: a source with no real
            # analyst identity can never correlate reliably -- every
            # message becomes its own fresh episode rather than guessing.
            classification = classify_revision_event(message_text)
            episode = self._open_new_episode(signal, classification, source_text=message_text)
            return CorrelationResult(classification=classification, episode=episode, created_new=True)

        open_for_analyst = self.store.open_episodes_for(signal.source, signal.analyst)
        known_symbols = frozenset(ep.instrument for ep in open_for_analyst)
        classification = classify_revision_event(message_text, known_symbols=known_symbols)

        if classification.event_type is RevisionEventType.NEW_ENTRY:
            episode = self._open_new_episode(signal, classification, source_text=message_text)
            return CorrelationResult(classification=classification, episode=episode, created_new=True)

        if classification.event_type not in _REVISION_EVENT_TYPES:
            # COMMENTARY / NO_ACTION / AMBIGUOUS (from the shared grammar
            # itself, e.g. two side keywords in one message) never touch
            # episode state.
            return CorrelationResult(classification=classification, episode=None, created_new=False)

        candidates = open_for_analyst
        if classification.symbol is not None:
            candidates = [ep for ep in candidates if ep.instrument == classification.symbol]

        if len(candidates) == 1:
            episode = candidates[0]
            episode.apply_revision(classification, message_id=signal.id)
            self._persist(episode)
            return CorrelationResult(classification=classification, episode=episode, created_new=False)

        if len(candidates) == 0:
            return CorrelationResult(
                classification=RevisionClassification(
                    text=message_text,
                    event_type=RevisionEventType.AMBIGUOUS,
                    symbol=classification.symbol,
                    detail=f"no open episode to revise for provider={signal.source} analyst={signal.analyst}",
                ),
                episode=None,
                created_new=False,
                candidate_episode_ids=[],
                reason="no open episode to revise",
            )

        # More than one plausible open episode and the message didn't name
        # a symbol that narrows it down -- genuinely ambiguous. Never guess.
        return CorrelationResult(
            classification=RevisionClassification(
                text=message_text,
                event_type=RevisionEventType.AMBIGUOUS,
                detail=f"{len(candidates)} plausible open episodes for provider={signal.source} analyst={signal.analyst}",
            ),
            episode=None,
            created_new=False,
            candidate_episode_ids=[ep.episode_id for ep in candidates],
            reason=f"{len(candidates)} plausible open episodes",
        )

    def _open_new_episode(
        self, signal: Signal, classification: RevisionClassification, *, source_text: str
    ) -> SignalEpisode:
        direction = classification.side if classification.side is not None else signal.side
        episode = SignalEpisode(
            provider=signal.source,
            analyst=signal.analyst,
            instrument=classification.symbol or signal.symbol,
            direction=direction,
            source_message_ids=[signal.id],
            revision=1,
            current_intent={
                "entry_price": classification.price if classification.price is not None else signal.price,
                "stop": classification.stop_loss if classification.stop_loss is not None else signal.stop_loss,
                "targets": (
                    [{"price": classification.take_profit}]
                    if classification.take_profit is not None
                    else ([{"price": signal.take_profit}] if signal.take_profit is not None else [])
                ),
            },
            entry_plan=[{"price": classification.price if classification.price is not None else signal.price, "text": source_text}],
            stop_plan=classification.stop_loss if classification.stop_loss is not None else signal.stop_loss,
            targets=(
                [{"price": classification.take_profit}]
                if classification.take_profit is not None
                else ([{"price": signal.take_profit}] if signal.take_profit is not None else [])
            ),
        )
        self._persist(episode)
        return episode
