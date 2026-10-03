"""Typed payloads -- one model per `EventType` that this slice actually
implements (see envelope.py's own `IMPLEMENTED_EVENT_TYPES`). A payload
model's `.model_dump(mode="json")` is exactly what goes in an
`EventEnvelope.payload`, and `signal_platform_contracts.envelope.
compute_payload_hash` is computed over that same dump -- never a
different serialization of the same data, or the hash would mean
nothing.
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from signal_platform_contracts.identity import (
    InstrumentIdentity,
    PrivateAccountIdentity,
    SourceIdentity,
)
from signal_platform_contracts.money import Money

_KNOWN_SIDES = ("buy", "sell")


def _known_side(value: str) -> str:
    if value not in _KNOWN_SIDES:
        raise ValueError(f"{value!r} is not a known side (expected one of {_KNOWN_SIDES})")
    return value


_KNOWN_ENTRY_ORDER_TYPES = ("market", "limit", "stop")
_KNOWN_MANAGEMENT_HORIZONS = ("intraday", "swing", "deadline", "session")
_KNOWN_OPTION_RIGHTS = ("call", "put")
_KNOWN_CRYPTO_DERIVATIVE_KINDS = ("spot", "perpetual", "future")


def _known_member(value: str, known: tuple[str, ...], field_name: str) -> str:
    if value not in known:
        raise ValueError(f"{field_name} {value!r} is not one of {known}")
    return value


def _known_option_right(value: str) -> str:
    return _known_member(value, _KNOWN_OPTION_RIGHTS, "right")


def _known_crypto_derivative_kind(value: str) -> str:
    return _known_member(value, _KNOWN_CRYPTO_DERIVATIVE_KINDS, "instrument_kind")


class ProfitTargetPayload(BaseModel):
    """One level of an ORDERED profit-target collection --
    `SourceReceiptPayload.targets`'s own list order IS this target's
    ordinal (its position among the source's own TP1/TP2/TP3.../TP_n),
    never re-derived from `price` (a source can genuinely post targets
    out of price order for a SELL vs BUY side, and re-sorting would
    silently misrepresent which one the source actually called "first").
    `quantity`/`fraction` are each optional and independent -- a source
    that only ever says "TP2 110" with no split has neither, and that is
    a real, honest absence, not something this model invents a default
    for (an even split across every target is a ROUTING/sizing decision,
    not a fact this source-side receipt payload asserts)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    price: Money
    #: An absolute quantity to close at this level, when the source (or
    #: the private engine's own sizing) actually specified one.
    quantity: Money | None = None
    #: A fraction (0, 1] of the ORIGINAL planned quantity to close at this
    #: level -- matches this codebase's own existing
    #: `app/lifecycle/models.py` `Target.reduce_fraction` convention.
    fraction: Money | None = None
    #: The source's own label for this level when it gave one verbatim
    #: (e.g. "TP1", "T2") -- never fabricated when the source didn't
    #: distinguish levels by name.
    label: str | None = None

    @field_validator("fraction")
    @classmethod
    def _fraction_in_unit_range(cls, value):
        if value is not None and not (0 < value <= 1):
            raise ValueError(f"fraction must be in (0, 1], got {value!r}")
        return value


class OptionContractDetails(BaseModel):
    """`SourceReceiptPayload.option` -- only set when `instrument.market_type`
    is genuinely an option. Every field here is required (an option
    contract this codebase can't fully identify -- underlying, expiry,
    strike, right, multiplier -- is not yet a real, tradeable identity),
    matching `InstrumentIdentity`'s own "no field this contract boundary
    can't verify" discipline."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    underlying: str
    #: ISO-8601 date string (e.g. "2026-09-18") -- an option's expiry is a
    #: calendar date, not a timestamp; keeping it a plain validated string
    #: (rather than `datetime`) avoids smuggling in a spurious time-of-day
    #: or timezone this contract never actually has.
    expiry: str
    strike: Money
    right: str
    multiplier: Money
    #: What actually settles at expiry/exercise (e.g. "100 shares AAPL",
    #: "cash") -- `None` when the source/engine hasn't resolved it, never
    #: assumed to be "100 shares of the underlying" by default (that's
    #: true for most US equity options but not universal).
    deliverable: str | None = None

    _validate_right = field_validator("right")(_known_option_right)


class FutureContractDetails(BaseModel):
    """`SourceReceiptPayload.future` -- only set when `instrument.market_type`
    is genuinely a future."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The contract root/series symbol (e.g. "ES", "CL"), distinct from
    #: `InstrumentIdentity.instrument_id` which may already carry a
    #: specific expiry-qualified symbol (e.g. "ESZ25") -- this is always
    #: just the root.
    root: str
    expiry: str
    multiplier: Money
    venue: str


class FxContractDetails(BaseModel):
    """`SourceReceiptPayload.fx` -- only set when `instrument.market_type`
    is genuinely forex. `unit` names this pair's own quantity convention
    (e.g. "standard_lot_100000", "micro_lot_1000", "units") -- distinct
    from `InstrumentIdentity.quantity_convention`, which is this
    contract's own generic field shared by every asset class; `unit` is
    the FX-specific detail a receiver needs to convert a bare lot count
    into base-currency units without guessing which lot convention the
    source meant."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_currency: str
    quote_currency: str
    unit: str


class CryptoDerivativeDetails(BaseModel):
    """`SourceReceiptPayload.crypto_derivative` -- only set when this is
    genuinely a crypto DERIVATIVE (perpetual/future), never for crypto
    spot (which needs no such detail at all -- leave this whole field
    `None` rather than setting `instrument_kind="spot"` redundantly)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    instrument_kind: str
    #: `None` when the source/engine hasn't resolved these -- never
    #: assumed to equal the quote currency by default (a USD-margined and
    #: a coin-margined perpetual on the same symbol are different
    #: products).
    margin_currency: str | None = None
    settlement_currency: str | None = None

    _validate_instrument_kind = field_validator("instrument_kind")(_known_crypto_derivative_kind)


class SourceReceiptPayload(BaseModel):
    """`EventType.SOURCE_RECEIPT` -- a source instruction as originally
    received, before any private engine acted on it. This is a
    recommendation, never a claim of execution (INTEGRATION_DECISION.md
    S7: "SOURCE records what was recommended; it does not claim an
    execution").

    Additive, backward-compatible fields (a release review found the
    original shape -- source/analyst/symbol/side/asset class/quantity/
    price/one stop/one take-profit/received time/raw payload --
    "substantially simpler than the provider universe you intend to
    support"): `targets` represents multiple profit targets (TradingView/
    TradeAlgo/BuyAlerts-style TP1/TP2/TP3...) as an ORDERED collection
    instead of forcing everything through the single `take_profit` field;
    `price_low`/`price_high` represent an entry RANGE alongside (not
    instead of -- `price` stays valid as the single/primary/informational
    price, unset for a genuine range-only instruction) a single `price`;
    `entry_order_type`/`entry_expiration` make the entry order's own type
    and time/session/condition boundary explicit; `option`/`future`/`fx`/
    `crypto_derivative` are the contract-type-specific details, each
    `None` unless that asset class actually applies (never populated for
    the wrong asset class, never forced onto every signal regardless of
    asset class). Every one of these is optional with a `None`/empty
    default so an existing single-target/single-price producer's payload
    is unchanged unless it explicitly sets one."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: SourceIdentity
    instrument: InstrumentIdentity
    side: str
    quantity: Money | None = None
    price: Money | None = None
    stop_loss: Money | None = None
    take_profit: Money | None = None
    #: Ordered profit-target collection -- see `ProfitTargetPayload`'s own
    #: docstring. Empty (the default) means "no distinct multi-target
    #: structure was given" -- a single `take_profit` may still be set
    #: independently and is NOT required to duplicate `targets[0]`.
    targets: list[ProfitTargetPayload] = Field(default_factory=list)
    #: An entry price RANGE's lower/upper bound, when the source gave a
    #: range instead of (or alongside) a single `price`.
    price_low: Money | None = None
    price_high: Money | None = None
    #: "market" / "limit" / "stop" -- `None` means not specified (this
    #: contract makes no assumption about what that implies; a consumer
    #: that needs a default applies its OWN documented default, not one
    #: silently baked in here).
    entry_order_type: str | None = None
    #: When this entry order expires (a time, a session boundary, or a
    #: condition) -- carried as a timezone-aware timestamp when the source
    #: gave one; `None` means no expiration was given (not "good till
    #: cancelled" -- this contract does not assert that either).
    entry_expiration: datetime | None = None
    #: "intraday" / "swing" / "deadline" / "session" -- the source's own
    #: stated management horizon, when given. Never inferred.
    management_horizon: str | None = None
    #: A confidence/strategy tag, set ONLY when the source actually
    #: supplied or qualified one -- never fabricated to fill the field.
    confidence: str | None = None
    option: OptionContractDetails | None = None
    future: FutureContractDetails | None = None
    fx: FxContractDetails | None = None
    crypto_derivative: CryptoDerivativeDetails | None = None
    #: The immutable ORIGINAL source event exactly as received (e.g. the
    #: raw provider message/update object, serialized) -- distinct from
    #: every interpreted field above, which is this parser's own reading
    #: of that event, not the event itself. Governed by the same S8
    #: export-grant discipline as `raw_text_excluded` below: `None` by
    #: default (excluded), a later slice's relay must refuse to export it
    #: unless the export grant explicitly covers it.
    raw_source_event: dict[str, Any] | None = None
    #: S8: "Exclude ... private source text ... unless the precise
    #: workflow needs and authorizes them." Defaults to excluded; a later
    #: slice's relay must refuse to export the raw source text unless this
    #: is explicitly False AND the export grant covers it.
    raw_text_excluded: bool = True

    _validate_side = field_validator("side")(_known_side)

    @field_validator("entry_order_type")
    @classmethod
    def _validate_entry_order_type(cls, value):
        if value is not None:
            _known_member(value, _KNOWN_ENTRY_ORDER_TYPES, "entry_order_type")
        return value

    @field_validator("management_horizon")
    @classmethod
    def _validate_management_horizon(cls, value):
        if value is not None:
            _known_member(value, _KNOWN_MANAGEMENT_HORIZONS, "management_horizon")
        return value

    @field_validator("entry_expiration")
    @classmethod
    def _entry_expiration_tz_aware(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("entry_expiration must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _price_range_ordered(self) -> SourceReceiptPayload:
        if self.price_low is not None and self.price_high is not None and self.price_low > self.price_high:
            raise ValueError(f"price_low ({self.price_low}) must not exceed price_high ({self.price_high})")
        return self


#: The real, distinct routing/admission/fill outcome states signal-copier's
#: own `app/engine.py` actually produces for one destination account (or,
#: for the first two, for a signal with no destination account reached at
#: all) -- see that module's own `_export_source_outcome` docstring for
#: exactly which code path produces each one. This is NOT
#: INT-027's own requested taxonomy verbatim (admitted/rejected/unfilled/
#: canceled/loss/commentary) -- "canceled" has no real code path in this
#: engine (no cancellation logic exists anywhere in app/engine.py or its
#: broker adapters), and "loss"/"commentary" are not routing outcomes at
#: all (a loss is a P&L fact computed later from closed positions, not a
#: state this engine's routing/admission step ever reports) -- inventing
#: either here would be exactly the fabrication this contract package's
#: own tests (INT-035) and this integration's own standing rules forbid.
_KNOWN_ROUTING_OUTCOMES = (
    "not_routed",  # no destination account configured for this source/symbol at all
    "disabled_by_settings",  # a destination account/provider/analyst override skipped this entry
    "admitted_filled",  # admitted (INT-027's own "admitted") and the broker confirmed a fill
    "admitted_unfilled",  # admitted but only PENDING so far (INT-027's own "unfilled")
    "rejected",  # a real, checked refusal: routing/asset-class/risk/capital/broker rejection
    "error",  # an operational failure (no broker adapter, broker call raised/errored)
    "eligible_not_selected",  # eligible for routing but not selected in replicate-mode fan-out (WP-38)
    "skipped",  # signal processing skipped this account (e.g., held-out stale signal, WP-38)
)


def _known_routing_outcome(value: str) -> str:
    if value not in _KNOWN_ROUTING_OUTCOMES:
        raise ValueError(f"{value!r} is not a known routing outcome (expected one of {_KNOWN_ROUTING_OUTCOMES})")
    return value


class RoutingAdmissionOutcomePayload(BaseModel):
    """`EventType.ROUTING_ADMISSION_OUTCOME` -- the real routing/admission/
    fill outcome for one `SOURCE_RECEIPT`, exported as a separate,
    correlated follow-up event once that outcome is actually known
    (INTEGRATION_ACCEPTANCE_CASES.json INT-027 "All permitted source
    outcomes reach research": a `SOURCE_RECEIPT` alone never encoded
    WHICH routing outcome produced it -- this payload is exactly that
    missing distinction). Mirrors `FeePayload`'s own late-arriving-
    correlated-update idiom, but correlates by `originating_source_event_id`
    (the originating `SOURCE_RECEIPT`'s own `EventEnvelope.event_id`)
    rather than by an execution's `(broker, broker_order_id)` identity --
    a routing outcome exists before any broker order necessarily does
    (e.g. `rejected`/`error`/`not_routed` never reach a broker at all).

    `account` is `None` exactly when no destination account was ever
    reached for this signal (`not_routed`) -- never fabricated. `outcome`
    is one of `_KNOWN_ROUTING_OUTCOMES` above; see that tuple's own
    comment for the two INT-027-requested categories this engine
    genuinely cannot produce."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    originating_source_event_id: str
    outcome: str
    account: PrivateAccountIdentity | None = None
    broker: str | None = None
    #: The real, underlying `OrderStatus` value (signal-copier's own
    #: `app/models.py`) when an order was actually attempted -- `None`
    #: for `not_routed`/`disabled_by_settings`, where no order ever was.
    order_status: str | None = None
    #: The engine's own real rejection/error message, carried verbatim
    #: (never re-worded or summarized) when one exists.
    message: str | None = None

    _validate_outcome = field_validator("outcome")(_known_routing_outcome)


class FeePayload(BaseModel):
    """`EventType.FEE` -- a confirmed fee for a specific already-applied
    execution, arriving separately from the fill itself
    (INTEGRATION_DECISION.md S7: fees are frequently not known at fill
    time). Correlates to that execution by the SAME (account, instrument,
    broker, broker_order_id) identity its own `ExecutionAppliedPayload`
    carried -- there is no other shared key between the two payloads.
    `fee` is required here (never `None`): this event exists specifically
    to move a fee from "not yet known" to "confirmed", so an unset value
    would defeat its own purpose; a genuinely zero fee is expressed as
    `Money("0")`, not by omitting the event."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    account: PrivateAccountIdentity
    instrument: InstrumentIdentity
    broker: str
    broker_order_id: str
    fee: Money


class PositionSnapshotEntry(BaseModel):
    """One instrument's own net position AS OF a `PositionSnapshotPayload`'s
    own `cutoff_sequence` -- the accumulated result of every fill up to
    and including that sequence, not a fill itself. `side` is the
    position's own direction (never a transaction side): `quantity` is
    always positive, `average_cost` is the position's own volume-
    weighted entry price at the cutoff."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    instrument: InstrumentIdentity
    side: str
    quantity: Money
    average_cost: Money

    _validate_side = field_validator("side")(_known_side)


class PositionSnapshotPayload(BaseModel):
    """`EventType.POSITION_SNAPSHOT` -- S6's own "Snapshot plus deltas":
    one PAGE of a coherent bootstrap snapshot, S9/S6's own "Use snapshot
    cutoff, immutable manifest and delta deduplication"
    (INTEGRATION_ACCEPTANCE_CASES.json INT-008 "Snapshot and delta
    overlap", INT-009 "Interrupted bootstrap resumes").

    `manifest_id` is the SAME across every page of one snapshot attempt
    -- a receiver groups pages by it, never by anything else (never
    inferred from arrival order or timing). `cutoff_sequence` and
    `page_count` MUST also be identical across every page sharing a
    `manifest_id` -- a page claiming a different value for either is a
    corrupted/conflicting manifest, never silently reconciled by
    trusting the latest one. `page_index` is 0-based and every value in
    `range(page_count)` must appear exactly once before the manifest is
    complete -- a receiver never marks a manifest complete from a
    partial set (INT-009's own "Partial snapshot labeled complete",
    prohibited).

    Each page is still its own real `EventEnvelope` with its own
    `export_sequence` (sequential, within the SAME `producer_generation`
    the deltas immediately following the snapshot also use) and its own
    `event_id` -- so page redelivery is exactly as idempotent as every
    other event type here, no separate dedup mechanism needed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest_id: str
    cutoff_sequence: int
    page_index: int
    page_count: int
    positions: list[PositionSnapshotEntry]

    @field_validator("cutoff_sequence", "page_index", "page_count")
    @classmethod
    def _non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("must not be negative")
        return value

    @model_validator(mode="after")
    def _page_index_within_page_count(self) -> PositionSnapshotPayload:
        if self.page_count < 1:
            raise ValueError("page_count must be at least 1")
        if self.page_index >= self.page_count:
            raise ValueError(f"page_index {self.page_index} is out of range for page_count {self.page_count}")
        return self


class ExecutionAppliedPayload(BaseModel):
    """`EventType.EXECUTION_APPLIED` -- a fill the private engine's own
    broker adapter actually confirmed. `fee: None` means "not yet known",
    never "zero" (INTEGRATION_DECISION.md S7: "Importing a zero default is
    not proof of a verified zero fee")."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    account: PrivateAccountIdentity
    instrument: InstrumentIdentity
    side: str
    filled_quantity: Money
    filled_price: Money
    fee: Money | None = None
    broker: str
    broker_order_id: str
    #: Links back to the SourceReceiptPayload's own source_event_id that
    #: caused this fill, when that provenance is known.
    originating_source_event_id: str | None = None
    #: The `SourceIdentity.analyst_id` that originated this fill, when
    #: known -- carried directly on the fill itself (not re-derived by
    #: joining `originating_source_event_id` back to a separate
    #: SOURCE_RECEIPT event, which may not even be exported on the same
    #: stream or may arrive out of order) so a receiver can attribute
    #: P&L per analyst without depending on that join succeeding.
    #: `None` means "not attributed to a specific analyst" -- grouped as
    #: its own bucket by a receiver (INTEGRATION_ACCEPTANCE_CASES.json
    #: INT-026 "Analyst allocation survives shared symbol"), never
    #: folded into some other analyst's numbers.
    originating_analyst_id: str | None = None

    _validate_side = field_validator("side")(_known_side)


class SourceEventKind(str, enum.Enum):
    """`EventType.SOURCE_EVENT`'s own taxonomy -- the source ledger this
    task's own ingestion review asked for: "the source ledger should
    capture: SOURCE_EVENT { original, edit, delete/retract, reply, cancel,
    close, add, target update, stop update }". One row per real,
    native-provider-identified moment in a source message's life, never a
    re-interpretation invented by this codebase -- an adapter emits
    exactly the kind the provider's own event shape actually reports
    (a genuine edit callback -> EDIT, a genuine delete callback ->
    DELETE, ...), never a guessed kind for content it can't distinguish
    from plain new-message text."""

    #: A brand-new message/instruction, never seen before (this codebase's
    #: existing, only-implemented case before this taxonomy existed).
    ORIGINAL = "original"
    #: The provider reports this message was edited in place -- same
    #: native message id, new content. `SourceEventPayload.source.
    #: revision_id`/`original_source_event_id` carry the revision chain.
    EDIT = "edit"
    #: The provider reports this message was deleted/retracted. No new
    #: instruction content -- `signal` is `None`.
    DELETE = "delete"
    #: A reply to another message (`source.parent_event_id` names it) --
    #: may or may not itself carry a new instruction; `signal` is set only
    #: when it does.
    REPLY = "reply"
    #: An explicit "cancel the pending entry" instruction referring to an
    #: earlier ORIGINAL/ADD -- `signal` is `None`; `source.parent_event_id`
    #: names the entry being cancelled.
    CANCEL = "cancel"
    #: An explicit "close now" instruction -- episode/allocation-aware
    #: exit, not a fresh entry. `signal` is `None` unless the source also
    #: gave exit-specific parameters (e.g. a partial-close fraction).
    CLOSE = "close"
    #: An add-on entry for the SAME strategy episode as an earlier
    #: ORIGINAL, posted as its own separate execution intent --
    #: `source.parent_event_id` names that earlier episode.
    ADD = "add"
    #: A target add/update/remove for an existing position --
    #: `signal.targets` carries the revised ordered target collection.
    TARGET_UPDATE = "target_update"
    #: A stop revision (including an explicit move-to-breakeven) for an
    #: existing position -- `signal.stop_loss` carries the revised stop.
    STOP_UPDATE = "stop_update"


class SourceEventPayload(BaseModel):
    """`EventType.SOURCE_EVENT` -- one row of the source ledger described
    above. True deduplication and edit/cancel/reply awareness both depend
    on `source.source_channel_id` + `source.source_event_id` (this
    event's own native provider/channel/message identity) being real and
    stable across redelivery -- see `SourceIdentity`'s own docstring.

    `provider_timestamp` is when the PROVIDER says this event happened
    (e.g. a Telegram message's own `date`/`edit_date`); `local_receipt_
    timestamp` is when THIS SERVICE actually received/processed it --
    kept as two distinct fields on the payload itself (not left to be
    inferred from `EventEnvelope.event_time`/`receipt_time`, which a
    caller could set inconsistently) so "did Telegram reconnect and
    replay the same message" and "how stale was this by the time we saw
    it" are both answerable directly from the payload alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: SourceEventKind
    source: SourceIdentity
    provider_timestamp: datetime
    local_receipt_timestamp: datetime
    #: The instrument this event is about, when known -- `None` for a
    #: DELETE/CANCEL that doesn't restate the instrument, or when the
    #: provider event alone can't identify one (e.g. a bare "close now"
    #: reply with no instrument named in the reply itself).
    instrument: InstrumentIdentity | None = None
    #: The interpreted instruction this event carries, when it carries
    #: one (ORIGINAL/EDIT/ADD/TARGET_UPDATE/STOP_UPDATE typically do;
    #: DELETE/CANCEL typically don't -- see `SourceEventKind`'s own
    #: per-member docstring). `None` is a real, honest absence, not an
    #: omission to fill in later.
    signal: SourceReceiptPayload | None = None
    #: Free-text reason/detail the source gave for a CANCEL/CLOSE/DELETE,
    #: when it gave one verbatim. Never fabricated.
    reason: str | None = None
    #: The immutable raw provider event exactly as received -- see
    #: `SourceReceiptPayload.raw_source_event`'s own docstring; same S8
    #: export-grant discipline applies.
    raw_source_event: dict[str, Any] | None = None

    @field_validator("provider_timestamp", "local_receipt_timestamp")
    @classmethod
    def _tz_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("must be timezone-aware")
        return value
