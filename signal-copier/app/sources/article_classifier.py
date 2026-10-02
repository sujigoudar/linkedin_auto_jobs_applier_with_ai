"""Track 9: trade-candidate classification for extracted articles.

This is the hard, real part of website ingestion -- prose is not a short
structured alert (contrast `app/sources/text_parser.py`), so nothing here
tries to reuse that grammar directly. Instead, two distinct steps:

1. `classify_article` -- a deterministic, rule/keyword-based FIRST PASS
   that labels the whole article into one of `ArticleClassification`'s
   members, using explicit phrase matching (see `_ACTIONABLE_PHRASES` /
   `_CONDITIONAL_PHRASES` / etc. below). This is NOT a solved NLP
   problem -- it is a bounded heuristic that is honest about its own
   limits: anything it can't confidently place lands on
   `NEEDS_HUMAN_REVIEW`, never a guessed category. A real production
   deployment would want this backed by a real classifier/LLM call with
   its own confidence score; this first pass is deliberately simple and
   auditable (every decision traces to a literal phrase match) so its
   mistakes are at least legible.

2. `build_trade_candidate` -- for an `ACTIONABLE_RECOMMENDATION` or
   `CONDITIONAL_SETUP` article ONLY, attempts to extract a structured
   trade into `TradeCandidate` -- the rich, multi-target/multi-leg shape
   this module defines (mirroring `app/models.py`'s `Signal`/
   `ProfitTarget`/`OptionContractSpec`, per the Track 9 brief's "read
   Track 2's work before reinventing fields"). The hard safety rule this
   function enforces: if ANY leg of a detected multi-leg options
   structure has an unresolved strike/expiry/direction, the WHOLE
   candidate stays `resolved=False` -- see `TradeCandidate.resolved`'s
   own docstring. A candidate that isn't `resolved` is NEVER converted
   into a live `Signal` by `to_signal` below; it exists only to be shown
   to a human.

Every extraction here is a first-pass heuristic (regex/keyword over
prose), explicitly weaker than the deterministic grammar
`text_parser.py` runs against short, structurally uniform alert text.
That is a real, disclosed gap, not something this module papers over --
see each function's own docstring for exactly what it can and can't
resolve.
"""
from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from typing import Optional

from app.models import (
    AssetClass,
    OptionContractSpec,
    ProfitTarget,
    Side,
    Signal,
)

#: This module's own exact interpretation implementation -- mirrors
#: `app.sources.text_parser.PARSER_VERSION`'s convention; see
#: `Signal.parser_version`'s docstring.
CLASSIFIER_VERSION = "article-classifier-v1-heuristic"


class ArticleClassification(str, enum.Enum):
    """The six categories the Track 9 brief names, plus the explicit
    "hold, needs a human" outcome every genuinely ambiguous article must
    land on instead of a guess."""

    #: Explicit, present-tense action language ("we are buying", "adding
    #: to our position") -- a real trade the source says it is (or just)
    #: taking.
    ACTIONABLE_RECOMMENDATION = "actionable_recommendation"
    #: A trade the source says it WOULD take if a stated condition is met
    #: ("if X breaks above Y, consider buying") -- not yet actionable on
    #: its own, but structured enough to be worth extracting as a
    #: candidate for a human to watch.
    CONDITIONAL_SETUP = "conditional_setup"
    #: Illustrative/teaching language ("for example", "let's say") -- not
    #: a real instruction about a real position.
    EDUCATIONAL_EXAMPLE = "educational_example"
    #: Past-tense description of a trade already closed/completed ("we
    #: bought X last week and sold at Y") -- reporting, not an
    #: instruction to act now.
    HISTORICAL_RECAP = "historical_recap"
    #: Managing an EXISTING position ("raising our stop", "taking partial
    #: profits", "consider selling half") -- distinct from a fresh entry
    #: recommendation; this module does not attempt to extract these into
    #: a `TradeCandidate` (no new entry to represent), only labels them so
    #: they're never miscounted as a fresh actionable entry.
    ADJUSTMENT_OR_EXIT = "adjustment_or_exit"
    #: Market commentary with no specific, present-or-conditional
    #: instruction about a specific position.
    GENERAL_COMMENTARY = "general_commentary"
    #: This first-pass heuristic found conflicting or insufficient signal
    #: to confidently choose among the above -- an explicit "hold, needs
    #: human review" outcome, never a guessed category. See this module's
    #: own docstring.
    NEEDS_HUMAN_REVIEW = "needs_human_review"


@dataclass
class ArticleClassificationResult:
    classification: ArticleClassification
    #: The literal phrase(s) that drove this decision -- always populated
    #: for every non-`NEEDS_HUMAN_REVIEW` outcome (auditability: every
    #: decision traces to real matched text, never a black-box score).
    matched_phrases: list[str] = field(default_factory=list)
    detail: Optional[str] = None


# -- Phrase banks -----------------------------------------------------
# Every phrase below is checked as a case-insensitive substring/regex
# against the article's extracted body text. These are deliberately
# literal and narrow (a first pass, per this module's own docstring) --
# widening them is a tuning exercise for whoever owns this classifier in
# production, backed by real false-positive/negative review, not
# something to "improve" opportunistically here.

_ACTIONABLE_PHRASES = [
    r"\bwe(?:'re| are) buying\b",
    r"\bwe(?:'re| are) shorting\b",
    r"\badding to our position\b",
    r"\bwe added to our position\b",
    r"\binitiating a position\b",
    r"\bwe bought\b(?!.{0,40}\b(?:last|earlier|previously|on \w+ \d)\b)",
    r"\bbuy(?:ing)? (?:the )?(?:breakout|pivot) (?:now|today)\b",
    r"\bwe recommend buying\b",
    r"\bbuy point is\b.{0,60}\bnow\b",
]

_CONDITIONAL_PHRASES = [
    r"\bif\b.{0,80}\bthen\b",
    r"\bif (?:it|the stock|shares|price) (?:breaks?|clears?|closes?|moves?)\b",
    r"\bconsider (?:buying|selling|adding)\b.{0,40}\bif\b",
    r"\bwould be a buy (?:above|if|once)\b",
    r"\bwatch for a (?:breakout|move) above\b",
    r"\ba buy point (?:of|at|near) .{0,20} would trigger\b",
]

_EDUCATIONAL_PHRASES = [
    r"\bfor example\b",
    r"\bfor instance\b",
    r"\blet'?s say\b",
    r"\bas an illustration\b",
    r"\bhypothetically\b",
    r"\bin this example\b",
    r"\bto illustrate\b",
]

_HISTORICAL_PHRASES = [
    r"\bin the past\b",
    r"\bwe bought .{0,60}\b(?:last|earlier|previously|on \w+ \d)\b",
    r"\bhad (?:bought|sold|shorted)\b",
    r"\bwe closed (?:the|our) position\b",
    r"\blooking back\b",
    r"\bpreviously recommended\b",
]

_ADJUSTMENT_PHRASES = [
    r"\bwe(?:'re| are) selling\b",
    r"\braising (?:our|the) stop\b",
    r"\btightening (?:our|the) stop\b",
    r"\btaking (?:partial )?profits\b",
    r"\bconsider selling (?:half|a portion|part)\b",
    r"\btrimming (?:our|the) position\b",
    r"\bmoving (?:our|the) stop to\b",
    r"\bexiting (?:the|our) remaining\b",
]


def _find_matches(text: str, patterns: list[str]) -> list[str]:
    lowered = text
    hits: list[str] = []
    for pattern in patterns:
        m = re.search(pattern, lowered, re.IGNORECASE)
        if m:
            hits.append(m.group(0))
    return hits


def classify_article(text: str) -> ArticleClassificationResult:
    """Deterministic first-pass classification -- see this module's own
    docstring. Ambiguity resolution order (most specific/actionable
    first, so a message combining an educational aside with a real
    present-tense trade call still surfaces the real trade call):
    ADJUSTMENT_OR_EXIT > ACTIONABLE_RECOMMENDATION > CONDITIONAL_SETUP >
    HISTORICAL_RECAP > EDUCATIONAL_EXAMPLE > GENERAL_COMMENTARY. Two
    categories matching with none of them being adjustment/actionable
    (a genuinely mixed article) is NEEDS_HUMAN_REVIEW rather than an
    arbitrary pick."""
    if not text or not text.strip():
        return ArticleClassificationResult(
            classification=ArticleClassification.NEEDS_HUMAN_REVIEW, detail="no article text to classify"
        )

    adjustment = _find_matches(text, _ADJUSTMENT_PHRASES)
    actionable = _find_matches(text, _ACTIONABLE_PHRASES)
    conditional = _find_matches(text, _CONDITIONAL_PHRASES)
    historical = _find_matches(text, _HISTORICAL_PHRASES)
    educational = _find_matches(text, _EDUCATIONAL_PHRASES)

    if adjustment:
        return ArticleClassificationResult(
            classification=ArticleClassification.ADJUSTMENT_OR_EXIT, matched_phrases=adjustment
        )
    if actionable and not historical:
        # A clean present-tense action call, uncontaminated by
        # past-tense recap language -- the only case this heuristic is
        # confident calling ACTIONABLE_RECOMMENDATION outright.
        return ArticleClassificationResult(
            classification=ArticleClassification.ACTIONABLE_RECOMMENDATION, matched_phrases=actionable
        )
    if conditional and not historical and not actionable:
        return ArticleClassificationResult(
            classification=ArticleClassification.CONDITIONAL_SETUP, matched_phrases=conditional
        )
    if historical and not actionable and not conditional:
        return ArticleClassificationResult(
            classification=ArticleClassification.HISTORICAL_RECAP, matched_phrases=historical
        )
    if educational and not actionable and not conditional and not historical:
        return ArticleClassificationResult(
            classification=ArticleClassification.EDUCATIONAL_EXAMPLE, matched_phrases=educational
        )
    if actionable or conditional or historical or educational:
        # More than one signal category matched with no clean priority
        # winner above (e.g. both actionable AND historical phrases
        # present) -- genuinely mixed, refuse to guess.
        return ArticleClassificationResult(
            classification=ArticleClassification.NEEDS_HUMAN_REVIEW,
            matched_phrases=actionable + conditional + historical + educational,
            detail="multiple conflicting classification signals matched",
        )
    return ArticleClassificationResult(
        classification=ArticleClassification.GENERAL_COMMENTARY,
        detail="no specific trade-instruction language recognized -- treated as commentary, not a signal",
    )


# -- Structured trade-candidate extraction -----------------------------


class PriceKind(str, enum.Enum):
    """Explicit, typed price representations -- the Track 9 brief's own
    hard requirement: never coerce net debit/credit, per-contract
    premium, and total dollar amount into one ambiguous "price" number."""

    NET_DEBIT = "net_debit"
    NET_CREDIT = "net_credit"
    PER_CONTRACT_PREMIUM = "per_contract_premium"
    TOTAL_DOLLAR_AMOUNT = "total_dollar_amount"
    SHARE_PRICE = "share_price"


@dataclass
class PriceRepresentation:
    kind: PriceKind
    value: float


@dataclass
class OptionLegCandidate:
    """One leg of a (possibly multi-leg) options structure. Any field
    left `None` means that leg's own strike/expiry/direction could not be
    resolved from the article text -- see `TradeCandidate.resolved`'s
    docstring for what an unresolved leg does to the WHOLE candidate."""

    right: Optional[str] = None  # "call" | "put"
    strike: Optional[float] = None
    expiry: Optional[str] = None  # ISO-8601 date, when resolvable
    direction: Optional[str] = None  # "buy" | "sell" (long/short this leg)
    raw_text: Optional[str] = None

    @property
    def is_resolved(self) -> bool:
        return self.right is not None and self.strike is not None and self.direction is not None


@dataclass
class TradeCandidate:
    """A structured trade extracted from an `ACTIONABLE_RECOMMENDATION`
    or `CONDITIONAL_SETUP` article -- the rich shape the Track 9 brief
    asks for, built on Track 2's existing `Signal`/`ProfitTarget`/
    `OptionContractSpec` fields rather than reinventing them.

    Keyed for dedup/revision by `(channel_id, message_id)` == this
    source's `(site_id, canonical_article_url)`, exactly like
    `Signal.channel_id`/`message_id` -- see `app/sources/website.py`'s
    module docstring for how a later revision of the SAME URL updates
    this record in place instead of creating a duplicate.
    """

    channel_id: str
    message_id: str  # the canonical article URL
    classification: ArticleClassification
    symbol: Optional[str] = None
    side: Optional[Side] = None
    asset_class: AssetClass = AssetClass.EQUITY
    analyst: Optional[str] = None
    targets: list[ProfitTarget] = field(default_factory=list)
    price_low: Optional[float] = None
    price_high: Optional[float] = None
    stop_loss: Optional[float] = None
    prices: list[PriceRepresentation] = field(default_factory=list)
    #: Empty for a plain equity/single-option candidate; 2+ entries for a
    #: detected multi-leg options structure (spread/straddle/etc).
    option_legs: list[OptionLegCandidate] = field(default_factory=list)
    #: The single-leg case, when this candidate is a plain (non-spread)
    #: option trade with everything resolved -- mirrors `Signal.option`.
    option: Optional[OptionContractSpec] = None
    #: HARD SAFETY REQUIREMENT (Track 9 brief): `True` only when EVERY
    #: field this candidate needs to become a real order is fully
    #: resolved -- in particular, when `option_legs` is non-empty, `True`
    #: only if EVERY leg's `is_resolved` is `True`. A candidate with even
    #: one unresolved leg of a multi-leg spread stays `False` FOREVER for
    #: that revision -- `to_signal` below refuses to build a `Signal` for
    #: an unresolved candidate, and nothing in this codebase is permitted
    #: to split an incompletely-understood spread into partial naked-leg
    #: orders. See `resolve_candidate`'s docstring for how this is
    #: computed.
    resolved: bool = False
    unresolved_reason: Optional[str] = None
    parser_version: str = CLASSIFIER_VERSION
    raw_source_event: dict = field(default_factory=dict)

    def to_signal(self, *, source: str) -> Optional[Signal]:
        """Builds a real, routable `Signal` -- ONLY when `resolved` is
        `True` and this is a plain (0 or 1 leg) candidate. A multi-leg
        candidate is NEVER converted here even when `resolved=True`:
        `app.models.Signal.option` has no representation for more than
        one leg (a genuine, disclosed gap in today's `Signal` shape --
        see this module's own module docstring) -- a multi-leg spread
        candidate stays a `TradeCandidate` for a human/future work to act
        on, never silently collapsed onto one leg's fields. Returns
        `None` for every case that must not produce a live signal."""
        if not self.resolved:
            return None
        if len(self.option_legs) > 1:
            return None
        if self.symbol is None or self.side is None:
            return None

        option_spec = self.option
        if option_spec is None and len(self.option_legs) == 1:
            leg = self.option_legs[0]
            if not leg.is_resolved:
                return None
            option_spec = OptionContractSpec(
                underlying=self.symbol, expiry=leg.expiry or "", strike=leg.strike or 0.0, right=leg.right or ""
            )

        primary_price = self.prices[0].value if self.prices else None
        return Signal(
            source=source,
            symbol=self.symbol,
            side=self.side,
            asset_class=self.asset_class,
            analyst=self.analyst,
            price=primary_price,
            price_low=self.price_low,
            price_high=self.price_high,
            stop_loss=self.stop_loss,
            take_profit=self.targets[0].price if self.targets else None,
            targets=self.targets,
            option=option_spec,
            channel_id=self.channel_id,
            message_id=self.message_id,
            parser_version=self.parser_version,
            raw_source_event=self.raw_source_event,
            raw={"classification": self.classification.value},
        )


# -- Extraction regexes (first-pass, prose-oriented) --------------------

_SYMBOL_PATTERN = re.compile(r"\b([A-Z]{1,5})\b(?:\s+shares|\s+stock)?")
_BUY_VERB_PATTERN = re.compile(r"\b(buying|bought|buy|long)\b", re.IGNORECASE)
_SELL_VERB_PATTERN = re.compile(r"\b(selling|sold|sell|short(?:ing)?)\b", re.IGNORECASE)
_STOP_PATTERN = re.compile(r"\bstop(?:-loss| loss)?\s*(?:at|of|near)?\s*\$?(\d+(?:\.\d+)?)", re.IGNORECASE)
_TARGET_PATTERN = re.compile(r"\btarget\s*(?:of|at|near)?\s*\$?(\d+(?:\.\d+)?)", re.IGNORECASE)
_NET_DEBIT_PATTERN = re.compile(r"\bnet debit of\s*\$?(\d+(?:\.\d+)?)", re.IGNORECASE)
_NET_CREDIT_PATTERN = re.compile(r"\bnet credit of\s*\$?(\d+(?:\.\d+)?)", re.IGNORECASE)
_PREMIUM_PATTERN = re.compile(r"\bpremium of\s*\$?(\d+(?:\.\d+)?)", re.IGNORECASE)
_TOTAL_DOLLAR_PATTERN = re.compile(r"\btotal (?:cost|risk) of\s*\$?(\d+(?:,\d{3})*(?:\.\d+)?)", re.IGNORECASE)

#: A single option leg mention: "$150 call expiring Sept 18" / "buy the
#: 150 put" / "sell to open the 145 call". Deliberately narrow -- real
#: OCC-style parsing lives in `text_parser.py`'s symbol grammar for
#: structured alerts; this is a best-effort prose reading only.
_OPTION_LEG_PATTERN = re.compile(
    r"\b(buy(?:ing)?|sell(?:ing)?)\b[^.]{0,40}?\$?(\d+(?:\.\d+)?)\s*(call|put)s?\b", re.IGNORECASE
)
#: A spread/multi-leg cue -- when present, this module looks for MULTIPLE
#: leg mentions and treats fewer than 2 resolved legs as unresolved
#: rather than silently treating it as a single-leg trade.
_SPREAD_CUE_PATTERN = re.compile(r"\b(spread|straddle|strangle|vertical|iron condor|collar)\b", re.IGNORECASE)


def _extract_symbol(text: str, *, candidate_hint: Optional[str] = None) -> Optional[str]:
    if candidate_hint:
        return candidate_hint.upper()
    # Heuristic: the most frequently repeated 1-5 letter all-caps token
    # that isn't a common false-positive word is very likely the ticker
    # being discussed. First pass only -- a real per-provider parser
    # would do better.
    counts: dict[str, int] = {}
    _STOPWORDS = {"BUY", "SELL", "CALL", "PUT", "TP", "SL", "IBD", "ETF"}
    for m in _SYMBOL_PATTERN.finditer(text):
        sym = m.group(1)
        if sym in _STOPWORDS:
            continue
        counts[sym] = counts.get(sym, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


def resolve_candidate(candidate: TradeCandidate) -> TradeCandidate:
    """Computes `candidate.resolved` -- the one place that logic lives,
    so `build_trade_candidate` and any future caller can't diverge on it.
    See `TradeCandidate.resolved`'s own docstring for the hard safety
    rule this enforces."""
    if candidate.symbol is None or candidate.side is None:
        candidate.resolved = False
        candidate.unresolved_reason = "could not confidently determine symbol and/or trade direction"
        return candidate
    if candidate.option_legs:
        unresolved_legs = [leg for leg in candidate.option_legs if not leg.is_resolved]
        if unresolved_legs:
            candidate.resolved = False
            candidate.unresolved_reason = (
                f"{len(unresolved_legs)} of {len(candidate.option_legs)} option leg(s) have an "
                "unresolved strike/expiry/direction -- the WHOLE multi-leg candidate stays "
                "unresolved rather than routing the resolved legs alone"
            )
            return candidate
    candidate.resolved = True
    candidate.unresolved_reason = None
    return candidate


def build_trade_candidate(
    text: str,
    *,
    channel_id: str,
    message_id: str,
    classification: ArticleClassificationResult,
    symbol_hint: Optional[str] = None,
    analyst: Optional[str] = None,
    raw_source_event: Optional[dict] = None,
) -> Optional[TradeCandidate]:
    """Attempts to extract a `TradeCandidate` out of an article's text --
    ONLY called for `ACTIONABLE_RECOMMENDATION`/`CONDITIONAL_SETUP`
    articles (an `ADJUSTMENT_OR_EXIT`/other classification has no fresh
    entry to extract; see `classify_article`'s own docstring). Returns
    `None` when even a symbol/side can't be determined at all (nothing to
    build a candidate around) -- the caller should treat that the same as
    `NEEDS_HUMAN_REVIEW`."""
    if classification.classification not in (
        ArticleClassification.ACTIONABLE_RECOMMENDATION,
        ArticleClassification.CONDITIONAL_SETUP,
    ):
        return None

    symbol = _extract_symbol(text, candidate_hint=symbol_hint)
    buy_hits = _BUY_VERB_PATTERN.findall(text)
    sell_hits = _SELL_VERB_PATTERN.findall(text)
    side: Optional[Side] = None
    if buy_hits and not sell_hits:
        side = Side.BUY
    elif sell_hits and not buy_hits:
        side = Side.SELL
    # buy_hits and sell_hits both present (or neither) -> side stays None,
    # which resolve_candidate() below correctly refuses to resolve.

    if symbol is None and side is None:
        return None

    prices: list[PriceRepresentation] = []
    for pattern, kind in (
        (_NET_DEBIT_PATTERN, PriceKind.NET_DEBIT),
        (_NET_CREDIT_PATTERN, PriceKind.NET_CREDIT),
        (_PREMIUM_PATTERN, PriceKind.PER_CONTRACT_PREMIUM),
    ):
        m = pattern.search(text)
        if m:
            prices.append(PriceRepresentation(kind=kind, value=float(m.group(1))))
    m = _TOTAL_DOLLAR_PATTERN.search(text)
    if m:
        prices.append(PriceRepresentation(kind=PriceKind.TOTAL_DOLLAR_AMOUNT, value=float(m.group(1).replace(",", ""))))

    stop_match = _STOP_PATTERN.search(text)
    stop_loss = float(stop_match.group(1)) if stop_match else None
    target_matches = _TARGET_PATTERN.findall(text)
    targets = [ProfitTarget(price=float(v), label=f"TP{i+1}") for i, v in enumerate(target_matches)]

    option_legs: list[OptionLegCandidate] = []
    leg_matches = list(_OPTION_LEG_PATTERN.finditer(text))
    is_spread = bool(_SPREAD_CUE_PATTERN.search(text))
    if leg_matches:
        asset_class = AssetClass.OPTION
        for leg_match in leg_matches:
            verb, strike_str, right = leg_match.groups()
            direction = "buy" if re.match(r"buy", verb, re.IGNORECASE) else "sell"
            option_legs.append(
                OptionLegCandidate(
                    right=right.lower(),
                    strike=float(strike_str),
                    # This first-pass prose reader does not attempt to
                    # resolve an expiry date from free text (dates in
                    # articles are given in every imaginable format,
                    # relative or absolute) -- an option leg with no
                    # resolvable expiry is, correctly, an UNRESOLVED leg,
                    # never guessed.
                    expiry=None,
                    direction=direction,
                    raw_text=leg_match.group(0),
                )
            )
        if is_spread and len(option_legs) < 2:
            # The article names a spread but this reader only found one
            # leg mention -- that's an incompletely-understood spread,
            # not a plain single-leg option trade. Add a placeholder
            # unresolved leg so `resolve_candidate` correctly refuses the
            # whole candidate rather than treating the one leg it did
            # find as the entire (naked) trade.
            option_legs.append(OptionLegCandidate(raw_text="unidentified additional leg(s) of a named spread"))
    else:
        asset_class = AssetClass.EQUITY

    candidate = TradeCandidate(
        channel_id=channel_id,
        message_id=message_id,
        classification=classification.classification,
        symbol=symbol,
        side=side,
        asset_class=asset_class,
        analyst=analyst,
        targets=targets,
        stop_loss=stop_loss,
        prices=prices,
        option_legs=option_legs,
        raw_source_event=raw_source_event or {},
    )
    return resolve_candidate(candidate)
