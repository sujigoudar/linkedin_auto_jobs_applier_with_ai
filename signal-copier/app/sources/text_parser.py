"""Generic free-text signal parser, shared by every text-message source
(Telegram, Discord, Slack, SMS, Twitter/X).

There's no universal signal-channel format, but the vast majority of manual
trade-call channels use some variant of:

    BUY BTCUSDT
    BUY BTCUSDT @ 65000
    SELL EURUSD 0.50 lots SL 1.0950 TP 1.1050
    LONG AAPL 10 @ 190.25 SL 185 TP 200
    close ETHUSDT

This parser handles that family. If a specific channel uses a format this
doesn't cover, write a dedicated parser for it (see each source's
docstring) rather than fighting this regex into something it isn't.
"""
from __future__ import annotations

import enum
import re
from dataclasses import dataclass

from app.errors import SignalValidationError
from app.models import AssetClass, ProfitTarget, Signal, Side

#: This parser's own exact interpretation implementation -- see
#: `Signal.parser_version`'s own docstring. Bump whenever this grammar's
#: field-by-field reading of message text changes in a way that would
#: matter to a consumer replaying a past raw message.
PARSER_VERSION = "text-parser-v2-multi-target"

_SIDE_ALIASES = {
    "buy": Side.BUY,
    "long": Side.BUY,
    "sell": Side.SELL,
    "short": Side.SELL,
    "close": Side.CLOSE,
    "exit": Side.CLOSE,
}

#: SIG-XX (this pass): a bare `-?\d+(?:\.\d+)?` stops matching at the first
#: character it doesn't recognize -- for "1,000" that means it captures
#: only "1" and leaves ",000" dangling in the surrounding text, unmatched
#: and silently discarded. That isn't "1,000" parsed as one lot, it's a
#: 1000x undersized (or, if the trader meant a European decimal comma,
#: differently-wrong) quantity slipping through as a clean PARSED result.
#: Capture the WHOLE digit run (comma included) so classify_text_signal
#: can see the comma and refuse the message outright -- a comma inside a
#: number is inherently ambiguous between a thousands separator ("1,000"
#: = one thousand) and a decimal separator ("1,5" = one and a half) with
#: no locale hint anywhere in this free-text grammar, so guessing either
#: reading risks silently sizing an order 1000x off. Refuse rather than
#: guess, same as every other ambiguity this parser already declines to
#: resolve.
_NUMBER = r"-?\d[\d,]*(?:\.\d+)?"

_PATTERN = re.compile(
    r"""
    (?P<side>buy|sell|long|short|close|exit)\s+
    (?P<symbol>[A-Za-z0-9/.\-]+)
    (?:\s+(?P<quantity>""" + _NUMBER + r""")\s*(?:lots?|units?|shares?)?)?
    (?:\s*@\s*(?P<price>""" + _NUMBER + r"""))?
    (?:.*?\bSL[:=]?\s*(?P<sl>""" + _NUMBER + r"""))?
    (?:.*?\bTP[:=]?\s*(?P<tp>""" + _NUMBER + r"""))?
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)

# SIG-02: more than one side keyword in the same message means this is a
# compound instruction (two distinct trades in one message -- "BUY AAPL 10
# and SELL MSFT 5") -- picking just the first one and silently discarding
# the rest would trade on less than what the message actually said. Refuse
# rather than guess.
_SIDE_WORD_PATTERN = re.compile(r"\b(?:buy|sell|long|short|close|exit)\b", re.IGNORECASE)

#: Every TP mention in the message, each with its own optional level number
#: (bare "TP"/"TP:" has an empty `num`; "TP1", "TP2", ... carry one) and its
#: own value. A multi-target instruction ("TP1 105 TP2 110") is now
#: REPRESENTABLE (see `_resolve_take_profit_targets` below) -- this no
#: longer forces every multi-TP message into AMBIGUOUS, only the genuinely
#: unparseable/inconsistent ones (bare repeated TP with no level numbers to
#: order by, or level numbers that aren't a clean 1..n sequence).
_TP_ENTRY_PATTERN = re.compile(r"\bTP(?P<num>\d*)[:=]?\s*(?P<val>" + _NUMBER + r")", re.IGNORECASE)

# A plain substring match doesn't prove the message is actually giving that
# instruction -- "DO NOT BUY AAPL 10" contains "BUY AAPL 10" too. This is not
# a full parse of meaning (see this module's docstring: dedicated per-source
# parsers exist for that), but a bounded, cheap check that refuses the
# obvious cases of negated or still-conditional commentary rather than
# silently trading on them -- looking at a few words immediately before the
# matched instruction, and anywhere after it, for words that reverse or
# defer it.
_NEGATION_OR_CONDITIONAL_WORDS = {
    "not", "don't", "dont", "doesn't", "doesnt", "didn't", "didnt",
    "won't", "wont", "wouldn't", "wouldnt", "shouldn't", "shouldnt",
    "never", "no", "avoid", "skip", "cancel", "cancelled", "canceled",
    "if", "unless", "maybe", "possibly", "might", "considering", "consider",
    "wait", "waiting", "hold", "holding", "ignore", "disregard",
    # SIG-02: past-tense reporting of someone else's instruction ("Yesterday
    # I said BUY AAPL 10") is a description of a signal, not the signal
    # itself.
    "yesterday", "said",
}
_WORDS_BEFORE_MATCH_TO_CHECK = 4


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z']+", text.lower())


# SIG-03: a trader posting an OCC-style option contract sometimes puts a
# space between the underlying ticker and the date/type/strike code (e.g.
# "BUY AAPL 260918C00200000 1"). Left alone, the main pattern's symbol
# group only captures the ticker ("AAPL") and the option code's leading
# digits get swallowed by the quantity group instead -- "260918" units of
# AAPL is not a real instruction, and treating it as one would size an
# order off a date/strike code rather than what the trader actually typed.
# Splice the two tokens back into one contiguous symbol before the main
# pattern ever runs, so an option contract parses as one instrument with
# its own real quantity after it, same as it would if posted with no
# space at all.
_OPTION_FRAGMENT_AFTER_SYMBOL = re.compile(r"\b(?P<sym>[A-Za-z]{1,6})\s+(?P<optfrag>\d{6}[CP]\d{8})\b")


def _merge_split_option_symbols(text: str) -> str:
    return _OPTION_FRAGMENT_AFTER_SYMBOL.sub(lambda m: f"{m.group('sym')}{m.group('optfrag')}", text)


# SIG-03: every text source defaults to (or is configured with) ONE fixed
# asset_class for every message it ever produces -- fine for a genuinely
# single-market channel, wrong for a "mixed" one where different analysts
# post about different markets. A bare "BUY AAPL 10" in a channel
# configured/defaulted to CRYPTO used to come out tagged CRYPTO regardless,
# which can pass the broker asset-class gate for the WRONG reason (routed
# to a crypto venue that "succeeds" against a similarly-named but wrong
# instrument) rather than being caught. This is a best-effort classifier
# from the symbol's own shape alone -- no market data, no per-venue lookup
# -- confident enough to override an assumed default when the two
# disagree, but never confident enough to invent an asset class for a
# shape it doesn't recognize (see `_infer_asset_class`'s return of None).
_OPTION_SYMBOL_PATTERN = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")

_FX_CURRENCY_CODES = {
    "USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "CNH", "CNY",
    "HKD", "SGD", "SEK", "NOK", "DKK", "MXN", "ZAR", "TRY", "PLN", "HUF",
    "CZK", "THB", "INR",
}

_CRYPTO_QUOTE_SUFFIXES = ("USDT", "USDC", "BUSD", "TUSD", "DAI")
_CRYPTO_BASE_HINTS = {
    "BTC", "ETH", "XRP", "LTC", "BCH", "BNB", "SOL", "ADA", "DOGE", "DOT",
    "MATIC", "AVAX", "LINK", "TRX", "XLM", "ATOM",
}


def _infer_asset_class(symbol: str) -> AssetClass | None:
    """Best-effort instrument classification from the symbol's shape alone.
    Returns None when the shape doesn't confidently match any known
    convention (e.g. a bare 3-letter string could be a stock ticker or half
    of a currency pair) -- the caller must not guess further in that case,
    only fall back to whatever asset_class it already trusted."""
    bare = symbol.replace("/", "").upper()

    if _OPTION_SYMBOL_PATTERN.match(bare):
        return AssetClass.OPTION

    if (
        len(bare) == 6
        and bare[:3] in _FX_CURRENCY_CODES
        and bare[3:] in _FX_CURRENCY_CODES
        and bare[:3] != bare[3:]
    ):
        return AssetClass.FOREX

    if bare.endswith(_CRYPTO_QUOTE_SUFFIXES):
        for suffix in _CRYPTO_QUOTE_SUFFIXES:
            if bare.endswith(suffix) and len(bare) > len(suffix):
                return AssetClass.CRYPTO
    for base in _CRYPTO_BASE_HINTS:
        if bare.startswith(base) and bare[len(base):] in ("USD", "EUR", "GBP", "BTC", "ETH"):
            return AssetClass.CRYPTO

    if bare.isalpha() and 1 <= len(bare) <= 5:
        return AssetClass.EQUITY

    return None


class DispositionOutcome(str, enum.Enum):
    """E02 (bounded, adoption plan): every message this parser looks at
    gets one of these outcomes, not just a pass/fail -- see
    `classify_text_signal`'s docstring. Distinct from `Side` and every
    other enum in this codebase; this classifies the MESSAGE, not a
    resolved trade."""

    #: A real, resolved trade instruction was extracted.
    PARSED = "parsed"
    #: Recognized as negated, conditional, or past-tense commentary about
    #: a trade rather than the trade itself -- correctly not a signal,
    #: not a parser failure.
    IGNORED = "ignored"
    #: Looked like an instruction but couldn't be resolved to exactly one
    #: unambiguous trade (more than one side keyword, more than one
    #: take-profit level).
    AMBIGUOUS = "ambiguous"
    #: An instruction was recognized but a numeric field was structurally
    #: invalid (e.g. a truncated/negative quantity or level) -- distinct
    #: from AMBIGUOUS: the shape is clear, one specific value is not.
    MISSING_DATA = "missing_data"
    #: Nothing this grammar recognizes as any kind of trade instruction.
    NO_MATCH = "no_match"


@dataclass
class MessageDisposition:
    text: str
    outcome: DispositionOutcome
    signal: Signal | None = None
    detail: str | None = None


@dataclass
class _TpLevel:
    """One raw TP mention found by `_TP_ENTRY_PATTERN`, resolved to its
    final target ordinal -- `label` is that ordinal as a string ("1",
    "2", ... or "" for a single unlabeled TP), `value` is its raw
    (comma/zero-unvalidated) numeric text."""

    label: str
    value: str


def _resolve_take_profit_targets(stripped: str) -> str | tuple[str | None, list[_TpLevel]]:
    """Resolves every TP mention in the message to either a genuine
    multi-target ordered collection or a single primary take-profit --
    returning a `str` (the AMBIGUOUS detail message) when the message's
    own TP labeling doesn't unambiguously determine an order.

    - No TP mention: `(None, [])`.
    - Exactly one TP mention (labeled or not): `(that value, [that level])`
      -- unchanged from this parser's previous single-TP behavior.
    - Two or more TP mentions, each carrying a distinct level number that
      forms a clean `1..n` sequence (e.g. "TP1 105 TP2 110", in any word
      order): REPRESENTABLE -- returns them sorted into target order,
      `take_profit` set to TP1's own value (the back-compat primary/
      first-target convention -- see `Signal.take_profit`'s own
      docstring).
    - Two or more TP mentions that are NOT a clean `1..n` sequence (a bare
      repeated "TP" with no level numbers to order by, a repeated same
      number, or numbers that skip/aren't sequential): genuinely
      ambiguous -- this grammar has no reliable way to determine which
      mention is "first", so it refuses rather than guessing, same as
      before this multi-target capability existed."""
    entries = list(_TP_ENTRY_PATTERN.finditer(stripped))
    if not entries:
        return (None, [])
    if len(entries) == 1:
        entry = entries[0]
        level = _TpLevel(label=entry.group("num") or "", value=entry.group("val"))
        return (level.value, [level])

    nums = [entry.group("num") for entry in entries]
    if any(n == "" for n in nums):
        return "more than one take-profit level, but not consistently numbered (TP1, TP2, ...) -- can't determine target order"
    ints = [int(n) for n in nums]
    if len(set(ints)) != len(ints) or sorted(ints) != list(range(1, len(ints) + 1)):
        return "take-profit levels are not a clean TP1, TP2, ... sequence -- can't determine target order"

    ordered = sorted(zip(ints, entries, strict=True), key=lambda pair: pair[0])
    targets_raw = [_TpLevel(label=str(num), value=entry.group("val")) for num, entry in ordered]
    return (targets_raw[0].value, targets_raw)


def classify_text_signal(
    text: str, *, source: str, asset_class: AssetClass = AssetClass.CRYPTO, analyst: str | None = None
) -> MessageDisposition:
    """E02 (bounded): classify one message's disposition -- PARSED,
    IGNORED, AMBIGUOUS, MISSING_DATA, or NO_MATCH -- rather than the
    binary "produced a Signal or raised" this module used to expose. This
    is what a source onboarding/history-review workflow needs (the
    adoption plan's E02 acceptance obligation: "every selected record has
    a parsed/ignored/ambiguous/missing-data disposition") -- reviewing
    WHY a past message never became a trade, not only that it didn't.

    `parse_text_signal` (below) is now a thin wrapper over this that
    preserves its exact previous behavior (raises SignalValidationError
    for every non-PARSED outcome) -- nothing on the live signal-ingestion
    path changed."""
    stripped = _merge_split_option_symbols(text.strip())
    match = _PATTERN.search(stripped)
    if not match:
        return MessageDisposition(text=text, outcome=DispositionOutcome.NO_MATCH, detail="no recognizable trade instruction")

    preceding = _words(stripped[: match.start()])[-_WORDS_BEFORE_MATCH_TO_CHECK:]
    following = _words(stripped[match.end() :])
    if any(w in _NEGATION_OR_CONDITIONAL_WORDS for w in preceding + following):
        return MessageDisposition(
            text=text,
            outcome=DispositionOutcome.IGNORED,
            detail="negated, conditional, or still-pending commentary rather than a trade instruction",
        )

    if len(_SIDE_WORD_PATTERN.findall(stripped)) > 1:
        return MessageDisposition(
            text=text,
            outcome=DispositionOutcome.AMBIGUOUS,
            detail="more than one trade instruction in a single message",
        )

    tp_resolution = _resolve_take_profit_targets(stripped)
    if isinstance(tp_resolution, str):
        # Genuinely unparseable/inconsistent TP labeling -- see
        # `_resolve_take_profit_targets`'s own docstring for exactly which
        # shapes still fall here (bare repeated TP with nothing to order
        # by, or level numbers that aren't a clean 1..n sequence).
        return MessageDisposition(text=text, outcome=DispositionOutcome.AMBIGUOUS, detail=tp_resolution)
    take_profit_raw, targets_raw = tp_resolution

    for field, raw in (
        ("quantity", match.group("quantity")),
        ("price", match.group("price")),
        ("sl", match.group("sl")),
        *((f"tp{level.label}", level.value) for level in targets_raw),
    ):
        if raw is not None and (raw.startswith("-") or float(raw.replace(",", "")) == 0):
            # A quantity, price, or stop/target level of zero (or less) is
            # never a valid trade instruction -- app/sources/webhook.py's
            # JSON ingestion path already enforces exactly this ("must be
            # greater than zero") for the same fields; this text grammar
            # used to let a literal "0" through as a clean PARSED result
            # with no such check.
            return MessageDisposition(
                text=text,
                outcome=DispositionOutcome.MISSING_DATA,
                detail=f"'{field}' must be greater than zero, got {raw!r}",
            )
        if raw is not None and "," in raw:
            # A comma inside a number is ambiguous (thousands separator vs.
            # decimal separator) with no locale hint in free text -- see
            # _NUMBER's comment above. Refuse instead of guessing which
            # reading the trader meant.
            return MessageDisposition(
                text=text,
                outcome=DispositionOutcome.MISSING_DATA,
                detail=f"'{field}' has an ambiguous comma-separated number, got {raw!r}",
            )

    side = _SIDE_ALIASES[match.group("side").lower()]
    symbol = match.group("symbol").upper()

    inferred_asset_class = _infer_asset_class(symbol)
    resolved_asset_class = (
        inferred_asset_class if inferred_asset_class is not None else asset_class
    )

    # Only a GENUINE multi-target message (2+ cleanly-numbered TP levels)
    # populates `targets` -- a single TP mention keeps this parser's
    # previous behavior exactly (`targets` stays empty, `take_profit`
    # alone carries the value).
    targets = (
        [ProfitTarget(price=float(level.value.replace(",", "")), label=f"TP{level.label}") for level in targets_raw]
        if len(targets_raw) > 1
        else []
    )

    signal = Signal(
        source=source,
        symbol=symbol,
        side=side,
        asset_class=resolved_asset_class,
        analyst=analyst,
        quantity=_optional_float(match.group("quantity")),
        price=_optional_float(match.group("price")),
        stop_loss=_optional_float(match.group("sl")),
        take_profit=_optional_float(take_profit_raw),
        targets=targets,
        parser_version=PARSER_VERSION,
        raw={"text": text},
    )
    return MessageDisposition(text=text, outcome=DispositionOutcome.PARSED, signal=signal)


def classify_batch(
    texts: list[str], *, source: str, asset_class: AssetClass = AssetClass.CRYPTO, analyst: str | None = None
) -> list[MessageDisposition]:
    """E02 (bounded): classify a batch of historical messages -- the
    piece of a full source-onboarding/parser-lab workflow this session
    can deliver without a specific source's own history-export API to
    pull from (that part -- "one workflow imports all accessible
    authorized selected history" -- stays a disclosed gap; this is the
    classification step that workflow would call per message)."""
    return [classify_text_signal(text, source=source, asset_class=asset_class, analyst=analyst) for text in texts]


def parse_text_signal(
    text: str, *, source: str, asset_class: AssetClass = AssetClass.CRYPTO, analyst: str | None = None
) -> Signal:
    disposition = classify_text_signal(text, source=source, asset_class=asset_class, analyst=analyst)
    if disposition.outcome is not DispositionOutcome.PARSED:
        raise SignalValidationError(f"{disposition.detail}: {text!r}")
    assert disposition.signal is not None  # PARSED always sets it -- see classify_text_signal
    return disposition.signal


def _optional_float(value: str | None) -> float | None:
    return float(value) if value is not None else None
