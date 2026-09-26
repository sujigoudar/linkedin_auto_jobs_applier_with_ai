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
from app.models import AssetClass, Signal, Side

_SIDE_ALIASES = {
    "buy": Side.BUY,
    "long": Side.BUY,
    "sell": Side.SELL,
    "short": Side.SELL,
    "close": Side.CLOSE,
    "exit": Side.CLOSE,
}

_PATTERN = re.compile(
    r"""
    (?P<side>buy|sell|long|short|close|exit)\s+
    (?P<symbol>[A-Za-z0-9/.\-]+)
    (?:\s+(?P<quantity>-?\d+(?:\.\d+)?)\s*(?:lots?|units?|shares?)?)?
    (?:\s*@\s*(?P<price>-?\d+(?:\.\d+)?))?
    (?:.*?\bSL[:=]?\s*(?P<sl>-?\d+(?:\.\d+)?))?
    (?:.*?\bTP[:=]?\s*(?P<tp>-?\d+(?:\.\d+)?))?
    """,
    re.IGNORECASE | re.VERBOSE | re.DOTALL,
)

# SIG-02: more than one side keyword, or more than one TP level, in the same
# message means this is either a compound instruction (two distinct trades
# in one message -- "BUY AAPL 10 and SELL MSFT 5") or a multi-target
# instruction this single-TP grammar can't faithfully represent ("TP1 105
# TP2 110") -- either way, picking just the first one and silently
# discarding the rest would trade on less than what the message actually
# said. Refuse rather than guess.
_SIDE_WORD_PATTERN = re.compile(r"\b(?:buy|sell|long|short|close|exit)\b", re.IGNORECASE)
_TP_LEVEL_PATTERN = re.compile(r"\bTP\d*\b", re.IGNORECASE)

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
    if len(_TP_LEVEL_PATTERN.findall(stripped)) > 1:
        return MessageDisposition(
            text=text,
            outcome=DispositionOutcome.AMBIGUOUS,
            detail="more than one take-profit level -- no way to represent a multi-target exit",
        )

    for field in ("quantity", "price", "sl", "tp"):
        raw = match.group(field)
        if raw is not None and raw.startswith("-"):
            return MessageDisposition(
                text=text,
                outcome=DispositionOutcome.MISSING_DATA,
                detail=f"'{field}' must not be negative, got {raw!r}",
            )

    side = _SIDE_ALIASES[match.group("side").lower()]
    symbol = match.group("symbol").upper()

    inferred_asset_class = _infer_asset_class(symbol)
    resolved_asset_class = (
        inferred_asset_class if inferred_asset_class is not None else asset_class
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
        take_profit=_optional_float(match.group("tp")),
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
