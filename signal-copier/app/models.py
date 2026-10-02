"""Core domain models shared by every source and broker adapter.

Every adapter — however different its wire format (TradingView JSON, a
Telegram message, an MT4 EA callback) — must produce or consume a `Signal`.
That's the one contract the rest of the engine depends on.
"""
from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


class Side(str, enum.Enum):
    BUY = "buy"
    SELL = "sell"
    CLOSE = "close"


class Intent(str, enum.Enum):
    """Signal intent — why a side was chosen, and what execution behavior is expected.

    ENTRY_LONG: Open a long position
    ENTRY_SHORT: Open a short position (sell to open)
    SELL: Exit a long position
    EXIT: Close all existing positions (intent-agnostic)
    REDUCE: Reduce position size by a fraction (reduce_fraction applies)
    STOP_UPDATE: Update a protective stop on existing position
    TARGET_UPDATE: Update or add a profit target
    CANCEL: Cancel a pending order or reduce an open position
    ADD: Add to an existing position
    """
    ENTRY_LONG = "entry_long"
    ENTRY_SHORT = "entry_short"
    SELL = "sell"
    EXIT = "exit"
    REDUCE = "reduce"
    STOP_UPDATE = "stop_update"
    TARGET_UPDATE = "target_update"
    CANCEL = "cancel"
    ADD = "add"


class AssetClass(str, enum.Enum):
    CRYPTO = "crypto"
    FOREX = "forex"
    EQUITY = "equity"
    OPTION = "option"
    FUTURE = "future"


class EntryOrderType(str, enum.Enum):
    """A release review found the previous, price-only Signal shape didn't
    let a source distinguish a market entry from a resting limit/stop
    order -- see `Signal.entry_order_type`'s own docstring."""

    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


@dataclass
class ProfitTarget:
    """One level of an ORDERED profit-target collection (`Signal.targets`).
    List order IS this target's ordinal (the source's own TP1/TP2/TP3...),
    never re-derived from `price`. `quantity`/`fraction` are each
    independent and optional -- a source that only ever gives a bare price
    per level has neither, and that's a real, honest absence, not
    something this dataclass invents a default for. Mirrors
    `signal_platform_contracts.payloads.ProfitTargetPayload` field-for-
    field, and `app/lifecycle/models.py`'s own `Target.reduce_fraction`
    convention for `fraction`."""

    price: float
    quantity: Optional[float] = None
    #: A fraction (0, 1] of the ORIGINALLY planned quantity to close at
    #: this level.
    fraction: Optional[float] = None
    #: The source's own label for this level, verbatim, when it gave one
    #: (e.g. "TP1") -- never fabricated.
    label: Optional[str] = None


@dataclass
class OptionContractSpec:
    """`Signal.option` -- only set when `Signal.asset_class` is genuinely
    OPTION. See `signal_platform_contracts.payloads.OptionContractDetails`
    for the shared cross-service shape this mirrors."""

    underlying: str
    expiry: str  # ISO-8601 date string, e.g. "2026-09-18"
    strike: float
    right: str  # "call" | "put"
    multiplier: float = 100.0
    deliverable: Optional[str] = None


@dataclass
class FutureContractSpec:
    """`Signal.future` -- only set when `Signal.asset_class` is genuinely
    FUTURE."""

    root: str
    expiry: str
    multiplier: float
    venue: str


@dataclass
class FxContractSpec:
    """`Signal.fx` -- only set when `Signal.asset_class` is genuinely
    FOREX and the source distinguished base/quote/unit convention beyond
    the bare symbol."""

    base_currency: str
    quote_currency: str
    unit: str  # e.g. "standard_lot_100000", "micro_lot_1000", "units"


@dataclass
class CryptoDerivativeSpec:
    """`Signal.crypto_derivative` -- only set when this is genuinely a
    crypto DERIVATIVE (perpetual/future), never for crypto spot."""

    instrument_kind: str  # "spot" | "perpetual" | "future"
    margin_currency: Optional[str] = None
    settlement_currency: Optional[str] = None


@dataclass
class Signal:
    """A normalized trade instruction, independent of where it came from."""

    source: str
    symbol: str
    side: Side
    asset_class: AssetClass = AssetClass.CRYPTO
    #: Who *within* `source` posted this (e.g. one trader in a shared
    #: Discord server) — optional. Used by app/providers.py to resolve
    #: per-analyst settings overrides; a source parser that can identify
    #: the poster sets this, others leave it None and only provider-level
    #: (not analyst-level) overrides apply.
    analyst: Optional[str] = None
    quantity: Optional[float] = None
    #: The single/primary/informational price -- kept exactly as before
    #: for every existing producer. A source that instead gives an entry
    #: RANGE sets `price_low`/`price_high` (below) alongside or instead of
    #: this; `price` is never required to duplicate either bound.
    price: Optional[float] = None
    stop_loss: Optional[float] = None
    #: The single/primary take-profit -- kept exactly as before. A source
    #: with multiple profit targets ALSO populates `targets` (below);
    #: existing consumers that only ever read `take_profit` keep working
    #: unchanged (it's conventionally the first/primary target's price
    #: when `targets` is set, but is never required to be).
    take_profit: Optional[float] = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    raw: dict[str, Any] = field(default_factory=dict)
    #: E02 (bounded, history-import workflow): `None` for every signal that
    #: arrived through a live transport (webhook/bot) -- this codebase's
    #: existing, only signal-creation path (`app/db.py`'s `save_signal`) --
    #: set to an owner-chosen/auto-generated batch label ONLY when a signal
    #: was instead created by the owner-gated batch-classify-and-import
    #: review workflow (`POST /sources/{source}/import-signals`) out of a
    #: pasted historical message that `classify_batch` resolved to PARSED.
    #: This is the one, cheap, honest distinguishing field between a
    #: backfilled and a live-received Signal row -- an additive column
    #: (see `app/db.py`'s `_COLUMN_MIGRATIONS`), not a second schema.
    import_batch: Optional[str] = None

    #: Track 29: this signal's own Track 14 provider-catalog `sources.id`
    #: (see `app/provider_catalog.py`'s module docstring) -- the specific
    #: transport this signal arrived through, when that's known. Bridges
    #: to `signal_platform_contracts.identity.SourceIdentity.
    #: source_catalog_id` 1:1 in `app/export_events.py`. `None` (the
    #: default) for every adapter not wired to the Track 14 catalog --
    #: never fabricated or derived from `channel_id`.
    source_catalog_id: Optional[str] = None

    # -- Multi-provider representability (release review: the previous
    # shape -- source/analyst/symbol/side/asset class/quantity/price/one
    # stop/one take-profit/received time/raw payload -- was "substantially
    # simpler than the provider universe you intend to support"). Every
    # field below is optional/empty-default so an existing single-target/
    # single-price producer is completely unaffected unless it explicitly
    # sets one. Mirrors signal_platform_contracts.payloads.
    # SourceReceiptPayload's own identically-named additions field for
    # field -- see app/export_events.py's `build_source_receipt_envelope`
    # for where the two are bridged.

    #: Ordered profit-target collection (TP1/TP2/TP3...) -- see
    #: `ProfitTarget`'s own docstring. Empty (the default) means no
    #: distinct multi-target structure was given.
    targets: list["ProfitTarget"] = field(default_factory=list)
    #: An entry price RANGE's lower/upper bound, when the source gave a
    #: range instead of (or alongside) a single `price`.
    price_low: Optional[float] = None
    price_high: Optional[float] = None
    #: "market" (the default/unspecified case) / "limit" / "stop".
    entry_order_type: Optional[EntryOrderType] = None
    #: When this entry order expires (a time, a session boundary, or a
    #: condition) -- `None` means no expiration was given.
    entry_expiration: Optional[datetime] = None
    #: "intraday" / "swing" / "deadline" / "session" -- the source's own
    #: stated management horizon. Never inferred.
    management_horizon: Optional[str] = None
    #: A confidence/strategy tag, set ONLY when the source actually
    #: supplied or qualified one -- never fabricated.
    confidence: Optional[str] = None
    #: Signal intent — why this side was chosen and what execution behavior
    #: is expected. `None` means intent will be derived from `side` in __post_init__.
    intent: Optional[Intent] = None
    #: Fraction (0 < x <= 1) of current position to reduce when intent is REDUCE.
    #: E.g., 0.5 for half, 0.25 for trim 25%. `None` for non-reduce intents.
    reduce_fraction: Optional[float] = None
    option: Optional["OptionContractSpec"] = None
    future: Optional["FutureContractSpec"] = None
    fx: Optional["FxContractSpec"] = None
    crypto_derivative: Optional["CryptoDerivativeSpec"] = None

    # -- Provider message identity / revision / provenance --------------

    #: This provider's own native channel identifier (e.g. a Discord/
    #: Telegram channel id, a webhook source's configured name) -- `None`
    #: when the adapter hasn't wired real channel identity yet (the
    #: existing, unchanged default for every adapter this task didn't
    #: touch).
    channel_id: Optional[str] = None
    #: This provider's own native message/event identifier -- together
    #: with `channel_id`, the real dedup/correlation key a provider
    #: reconnect-and-replay, edit, or delete needs. `None` for the same
    #: reason as `channel_id`.
    message_id: Optional[str] = None
    #: This SPECIFIC revision's own native id (e.g. an edited message's
    #: new edit marker) -- `None` for an original message/no revision.
    revision_id: Optional[str] = None
    #: The FIRST message's own `message_id` this revision chain traces
    #: back to -- `None` when this Signal IS the original, or isn't part
    #: of a revision chain.
    original_message_id: Optional[str] = None
    #: The exact parser/interpretation implementation that produced this
    #: Signal (e.g. a version string for `app/sources/text_parser.py`'s
    #: grammar) -- `None` when the producing adapter hasn't been wired to
    #: report one.
    parser_version: Optional[str] = None
    #: The immutable ORIGINAL source event exactly as received (e.g. the
    #: raw Discord/Telegram message object, serialized) -- distinct from
    #: `raw` above, which for most existing adapters already holds
    #: exactly this (e.g. `app/sources/webhook.py`'s raw JSON body) but
    #: for a text-message adapter has historically held only `{"text":
    #: ...}`, not the provider's own full event envelope. `None` when not
    #: populated.
    raw_source_event: Optional[dict[str, Any]] = None

    # -- Track 16: canonical signal lifecycle timeline -----------------
    # The user's own spec, verbatim: "Do not reduce freshness to
    # received_at. Store: source created time, source modified time,
    # first observed time, received time, parsed time, decision time."
    # `received_at` above already covers the last of those; these five
    # are honestly `None` (never guessed/backfilled from received_at)
    # for every adapter that hasn't been wired to report a real value.
    # See app/signal_freshness.py's own module docstring for how these
    # feed freshness evaluation, and app/db.py's `get_signal_lifecycle`
    # for how they feed the lifecycle timeline read.

    #: When the PROVIDER itself says this event happened (e.g. a
    #: Telegram message's own `date`, a webhook payload's own
    #: `created_at`) -- distinct from `received_at` (when THIS SERVICE
    #: saw it). Mirrors `SourceEvent.provider_timestamp`.
    source_created_at: Optional[datetime] = None
    #: When the provider says this event was last EDITED/updated, if it
    #: reports one distinct from its creation time -- `None` for an
    #: original, never-edited message, or a source that doesn't report
    #: edit timestamps at all.
    source_modified_at: Optional[datetime] = None
    #: When THIS DEPLOYMENT first became aware of the underlying real-
    #: world event this signal describes -- distinct from `received_at`
    #: (when this specific parsed Signal was produced), for the case
    #: where an earlier, less-complete sighting (e.g. a bare pointer
    #: notification later followed by its full content) preceded it.
    #: `None` when there was no earlier sighting to distinguish this
    #: from `received_at`.
    first_observed_at: Optional[datetime] = None
    #: When this raw event finished being PARSED into this Signal's own
    #: structured fields -- `None` for a source whose adapter doesn't
    #: report a distinct parse timestamp from receipt.
    parsed_at: Optional[datetime] = None
    #: When app/engine.py finished making its routing/admission DECISION
    #: for this signal (set by the engine itself, not by any adapter --
    #: see `app/db.py`'s `save_signal` call site in `_handle_signal`).
    #: `None` until that decision has actually been made.
    decision_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if isinstance(self.side, str):
            self.side = Side(self.side.lower())
        if isinstance(self.asset_class, str):
            self.asset_class = AssetClass(self.asset_class.lower())
        if isinstance(self.entry_order_type, str):
            self.entry_order_type = EntryOrderType(self.entry_order_type.lower())
        if isinstance(self.intent, str):
            self.intent = Intent(self.intent.lower())
        # Derive intent from side if not explicitly set
        if self.intent is None:
            if self.side == Side.BUY:
                self.intent = Intent.ENTRY_LONG
            elif self.side == Side.SELL:
                self.intent = Intent.SELL
            elif self.side == Side.CLOSE:
                self.intent = Intent.EXIT


class SourceEventKind(str, enum.Enum):
    """The source ledger this codebase's own ingestion review asked for:
    "the source ledger should capture: SOURCE_EVENT { original, edit,
    delete/retract, reply, cancel, close, add, target update, stop
    update }". Mirrors
    `signal_platform_contracts.payloads.SourceEventKind` member for
    member -- see that enum's own per-member docstring for what each one
    means and when `SourceEvent.signal` is/isn't expected to be set. An
    adapter emits exactly the kind the provider's own event shape
    actually reports, never a guessed kind for content it can't
    distinguish from plain new-message text."""

    ORIGINAL = "original"
    EDIT = "edit"
    DELETE = "delete"
    REPLY = "reply"
    CANCEL = "cancel"
    CLOSE = "close"
    ADD = "add"
    TARGET_UPDATE = "target_update"
    STOP_UPDATE = "stop_update"


@dataclass
class SourceEvent:
    """One row of the source ledger -- see `SourceEventKind`'s own
    docstring. True deduplication and edit/cancel/reply awareness both
    depend on `channel_id`/`message_id` (this event's own native
    provider/channel/message identity) being real and stable across
    redelivery.

    `provider_timestamp` is when the PROVIDER says this event happened
    (e.g. a Telegram message's own `date`/`edit_date`); `local_receipt_
    timestamp` is when THIS SERVICE actually received/processed it --
    kept as two distinct fields (not left to be inferred from `Signal.
    received_at` alone) so "did the provider reconnect and replay the
    same message" and "how stale was this by the time we saw it" are
    both answerable directly."""

    source: str
    kind: SourceEventKind
    channel_id: Optional[str] = None
    message_id: Optional[str] = None
    revision_id: Optional[str] = None
    original_message_id: Optional[str] = None
    parent_message_id: Optional[str] = None
    #: `None` when the provider event itself carries no distinct
    #: timestamp of its own (rare, but honest) -- never fabricated to
    #: equal `local_receipt_timestamp`.
    provider_timestamp: Optional[datetime] = None
    local_receipt_timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    #: The interpreted instruction this event carries, when it carries
    #: one (see `SourceEventKind`'s own per-member docstring for which
    #: kinds typically do). `None` is a real, honest absence.
    signal: Optional["Signal"] = None
    #: Free-text reason/detail the source gave for a CANCEL/CLOSE/DELETE,
    #: when it gave one verbatim. Never fabricated.
    reason: Optional[str] = None
    #: The immutable raw provider event exactly as received.
    raw_source_event: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    FILLED = "filled"
    REJECTED = "rejected"
    ERROR = "error"


@dataclass
class OrderResult:
    """What a broker adapter hands back after trying to execute a signal."""

    account_id: str
    status: OrderStatus
    signal_id: str
    broker_order_id: Optional[str] = None
    filled_quantity: Optional[float] = None
    filled_price: Optional[float] = None
    message: str = ""
    executed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    #: Broker commission/fee for this order, in account currency.
    #: None when this broker doesn't report fees or this order wasn't filled.
    fee: Optional[float] = None
    #: Currency of fee (ISO 4217 code, e.g., USD).
    #: Only set when fee is not None.
    fee_currency: Optional[str] = None
    #: Difference between expected (signal.price * filled_quantity) and
    #: actual fill value (* quantity). Positive = slippage against the trade.
    #: None when this broker doesn't report slippage data.
    slippage: Optional[float] = None
    #: E-11: Currency in which filled_price is quoted for this order
    #: (ISO 4217 code, e.g., 'USD', 'JPY', 'EUR', 'BTC'). NULL when the
    #: adapter doesn't report it or this order wasn't filled. Never guessed
    #: from symbol syntax — must come from the broker or adapter's own
    #: instrumentation data. See app/models.py's DestinationAccount.currency
    #: for the account's base currency (distinct from each order's individual
    #: price currency).
    price_currency: Optional[str] = None


@dataclass
class AccountBalance:
    """A broker's own real-time answer to "what does this account actually
    have" -- see app/brokers/base.py's `get_account_balance` for what this
    is and isn't a substitute for (in particular: `app/capital_allocator.py`
    deliberately does NOT read this; it enforces a ceiling against this
    service's own confirmed-fill replay, never a live broker balance read).

    Every field is `None`, not 0.0, when this broker/account genuinely
    didn't report it -- a spot/cash account with no margin concept at all
    (e.g. Alpaca's non-margin accounts) reports `maintenance_margin=None`,
    never an invented 0.0 that would look identical to "no margin used
    right now" for one that DOES have margin.
    """

    account_id: str
    #: Free cash, in the account's base currency.
    cash: Optional[float] = None
    #: Total account value (cash + market value of open positions).
    equity: Optional[float] = None
    #: What Alpaca calls "buying power" -- how much more could be
    #: deployed right now, already netting out any margin in use. Not a
    #: universal concept (e.g. ccxt spot has no single account-wide
    #: figure like this — see CCXTBroker's docstring on why it doesn't
    #: implement this method at all).
    buying_power: Optional[float] = None
    #: Margin currently held against open positions, if this is a margin
    #: account and the broker reports it.
    maintenance_margin: Optional[float] = None
    #: E-11: Base currency for this account (ISO 4217 code, e.g., 'USD',
    #: 'EUR', 'JPY'). The currency in which cash, equity, buying_power,
    #: and maintenance_margin are expressed. NULL when the broker doesn't
    #: report it. See DestinationAccount.currency for the operator's
    #: configuration of the same fact.
    currency: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "account_id": self.account_id,
            "cash": self.cash,
            "equity": self.equity,
            "buying_power": self.buying_power,
            "maintenance_margin": self.maintenance_margin,
            "currency": self.currency,
        }


@dataclass(frozen=True)
class QuantityBreakdown:
    """TRK-Q1: the explicit, distinct-field quantity model for one
    order/position event -- formalizing (never renaming) the seven
    genuinely different quantities a release review named as the single
    most important architectural principle for this system: "requested
    quantity != reserved quantity != acknowledged quantity != cumulative
    executed quantity != currently owned quantity != quantity still
    executable != protected quantity."

    This dataclass does not introduce new bookkeeping where this codebase
    already tracks a fact correctly under a different name -- it names and
    collects values that (mostly) already exist scattered across
    app/engine.py's own AUD-01 model, app/lifecycle/models.py's
    `PendingEntry`/`PendingExit`/`StopRecord`, and app/db.py's `orders`
    table, so every caller has ONE place to build/read the full picture
    instead of five ad hoc local variables. See `app/quantity.py`'s
    `build_quantity_breakdown` for how these are actually assembled from
    real order/position/protection state, and each field below for exactly
    which existing concept it corresponds to (never invented independently
    of one).

    Every field is `None` (never a fabricated 0.0) when this specific
    event/order/position genuinely has nothing real to report for it --
    e.g. a REJECTED order was never acknowledged and has nothing still
    executable; a plain (non-managed_lifecycle) account has no
    `protected_quantity` concept at all, since this codebase's protective-
    stop machinery is a managed-lifecycle-only concern (see
    app/lifecycle/manager.py's module docstring)."""

    #: What was actually asked for -- app/engine.py's own `requested_quantity`
    #: (the `quantity` argument every `BrokerAdapter.place_order` call is
    #: made with), `PendingEntry.requested_quantity`/`PendingExit
    #: .requested_quantity`.
    requested_quantity: float
    #: Capital admission has set this many units of capacity aside for this
    #: order and NOT yet released it -- `PendingEntry.reserved_quantity`
    #: (units) / `PendingEntry.reserved_notional`,
    #: `CapitalAllocator.pending_reservation` (notional; this field is the
    #: same fact in units, when a per-order quantity reservation is
    #: tracked). Distinct from `requested_quantity`: a partially-resolved
    #: entry's reservation can already have been narrowed down (see
    #: `PositionLifecycleManager.resolve_pending_entry`) while
    #: `requested_quantity` still reports the original ask. `None` when
    #: nothing in this deployment reserves capital for this event at all
    #: (e.g. no notional/risk gate configured for the account).
    reserved_quantity: float | None = None
    #: The broker/venue has ACCEPTED an order at this size -- true the
    #: instant a real `broker_order_id` exists (FILLED, or PENDING with a
    #: real id to poll -- see app/command_ledger.py's `SUBMITTED_UNCONFIRMED`/
    #: `CONFIRMED` states, which mark exactly this same acceptance), even
    #: before any of it has actually executed. Distinct from
    #: `cumulative_executed_quantity`: an order can be fully acknowledged
    #: (accepted resting at the venue) while 0 units have executed so far.
    #: `0.0` (not `None`) for a definite non-acceptance (REJECTED, or no
    #: broker_order_id at all -- app/command_ledger.py's
    #: `UNKNOWN_AMBIGUOUS`); `None` only when this event hasn't reached the
    #: broker at all yet (e.g. a pre-submission intent record).
    acknowledged_quantity: float | None = None
    #: The broker's own reported cumulative filled quantity, exactly as
    #: given -- app/engine.py's AUD-01 `confirmed_cumulative_fill` /
    #: `orders.confirmed_cumulative_fill`. Never guessed, never defaulted
    #: to `requested_quantity`.
    cumulative_executed_quantity: float | None = None
    #: This service's own current belief about what's actually owned right
    #: now, updated ONLY from confirmed fills/exits -- AUD-01's
    #: `actual_remaining_ownership` (== `positions.net_quantity` /
    #: `PositionLifecycle.confirmed_owned_quantity` for a managed position).
    currently_owned_quantity: float | None = None
    #: How much of `requested_quantity` could STILL be confirmed by the
    #: broker later -- AUD-01's `outstanding_possible_fill`
    #: (`requested_quantity - confirmed_cumulative_fill` while PENDING,
    #: `0.0` once terminal) / `PendingEntry.unresolved_remainder` /
    #: `PendingExit.unresolved_remainder`. Genuine uncertain exposure --
    #: never silently treated as already-owned, never silently treated as
    #: zero while still open.
    quantity_still_executable: float | None = None
    #: How much of the position currently sits behind a broker-CONFIRMED
    #: protective stop -- `StopRecord.protected_quantity` /
    #: `PositionLifecycle.covered_quantity` (`0.0`, not the stop's own
    #: `protected_quantity` value, whenever `StopRecord.status !=
    #: STOP_CONFIRMED` -- an unconfirmed/pending stop protects nothing yet).
    #: `None` for a plain (non-managed_lifecycle) account, which has no
    #: protective-stop concept in this codebase's execution path at all.
    protected_quantity: float | None = None


class ManagementRecipe(str, enum.Enum):
    """P0-5: an account's explicit, PERSISTED declaration of which safety
    product it actually is -- managed and unmanaged positions are
    fundamentally different products (MAE/MFE, protection coverage,
    transfer logic, and the rest of app/lifecycle/manager.py's machinery
    exist for one of them and not the other), and that distinction must
    be a real, auditable field on the account, not something a screen or
    a report has to re-derive by checking `managed_lifecycle` itself
    every time.

    This is deliberately a two-value declaration today (matching this
    codebase's own existing `managed_lifecycle` boolean terminology, not
    inventing new tiers): a broker/route-CAPABILITY qualification
    taxonomy (what a broker adapter can actually verify/do) is a related
    but distinct concept that a sibling effort (P0-7) is building for
    broker capabilities specifically -- this field is the ACCOUNT's own
    declared management contract, and P0-7's taxonomy may reference it,
    not replace it.
    """

    #: Entries/exits for this account route through
    #: PositionLifecycleManager -- protect-first, logical targets/
    #: trailing, MAE/MFE tracking, a serialized close arbiter. The full
    #: managed-lifecycle safety product.
    FULL_MANAGED_LIFECYCLE = "full_managed_lifecycle"
    #: Entries/exits for this account are plain BUY/SELL/CLOSE orders
    #: against this service's own tracked position -- no MAE/MFE, no
    #: protection coverage, no transfer logic. See
    #: app/engine.py's module docstring ("Close signals") and
    #: `DestinationAccount.exclusive_writer_qualified` for what a plain
    #: account's CLOSE additionally now requires before it's allowed to
    #: proceed.
    PLAIN_UNMANAGED = "plain_unmanaged"


class CommandType(str, enum.Enum):
    """What kind of durable financial command a `command_ledger` row
    describes -- see app/command_ledger.py's module docstring for exactly
    which real call site in app/engine.py / app/lifecycle/manager.py writes
    each one. `TARGET_CHANGE` is a reserved name: this codebase's logical
    targets (app/lifecycle/models.py's `Target`) are evaluated in-process
    against a price feed and never themselves become a resting broker
    order -- a target SELL fires as a `CLOSE` command and a TIGHTEN_STOP
    target fires as a `STOP_CHANGE` -- so no call site produces a
    `TARGET_CHANGE` row today. Kept in the enum (rather than omitted)
    because a future broker-native take-profit order would be a real,
    distinct command needing this exact type, and the audit this ledger
    responds to names it explicitly."""

    ENTRY = "entry"
    CLOSE = "close"
    STOP_CHANGE = "stop_change"
    TARGET_CHANGE = "target_change"
    REPLACE = "replace"
    CANCEL = "cancel"
    FLATTEN = "flatten"


class UncertaintyState(str, enum.Enum):
    """The lifecycle of one `command_ledger` row's knowledge about whether
    its command actually happened at the broker -- see
    app/command_ledger.py's module docstring for the full state machine.
    `PENDING_SUBMISSION` and `SUBMITTED_UNCONFIRMED` and
    `UNKNOWN_AMBIGUOUS` are all *unresolved* (no `resolved_at`);
    `CONFIRMED` and `REJECTED_CONFIRMED` are terminal."""

    #: Row written and committed BEFORE the broker call -- the pre-effect
    #: durable intent. Never observed after `open_command_ledger_entry`
    #: returns in-process (the very next thing that call site does is
    #: either call the broker or, in a crash/restart, leave the row here
    #: for a restart-recovery reader to find -- see
    #: `SignalStore.list_unresolved_command_ledger_entries`'s docstring).
    PENDING_SUBMISSION = "pending_submission"
    #: The broker call returned a real broker_order_id but no terminal fill/
    #: reject yet (a PENDING OrderResult with an id to poll) -- expected to
    #: resolve later via reconciliation.
    SUBMITTED_UNCONFIRMED = "submitted_unconfirmed"
    #: A definite, broker-confirmed terminal success (FILLED, or a confirmed
    #: cancel/replace).
    CONFIRMED = "confirmed"
    #: A definite, broker-confirmed terminal rejection -- nothing was ever
    #: accepted at the venue.
    REJECTED_CONFIRMED = "rejected_confirmed"
    #: The critical state the audit names: the broker call itself raised,
    #: timed out, or returned PENDING/ERROR with no broker_order_id to poll
    #: -- genuinely unknown whether the request reached (and was accepted
    #: by) the venue before the failure. Never silently dropped or treated
    #: as either a success or a failure; must be resolved only by
    #: independent reconciliation (a broker position/order readback), never
    #: assumed.
    UNKNOWN_AMBIGUOUS = "unknown_ambiguous"


#: Every `UncertaintyState` a row can be resolved into -- `resolved_at` is
#: set iff a row's current state is one of these. Kept next to the enum
#: (not private/scattered across app/db.py) since app/command_ledger.py and
#: SignalStore both need the exact same terminal set.
TERMINAL_UNCERTAINTY_STATES = frozenset({UncertaintyState.CONFIRMED, UncertaintyState.REJECTED_CONFIRMED})


@dataclass
class CommandLedgerEntry:
    """One durable row of the financial command ledger (`command_ledger`
    table) -- see app/command_ledger.py's module docstring for the full
    contract, and `SignalStore.list_unresolved_command_ledger_entries`'s
    docstring for the exact shape a sibling restart-recovery reader (P0-4)
    depends on.
    """

    id: str
    intent_id: str
    idempotency_key: str
    command_type: CommandType
    account_id: str
    environment: str
    request_fingerprint: str
    created_at: datetime
    uncertainty_state: UncertaintyState
    expected_revision: Optional[str] = None
    remote_identifiers: dict[str, Any] = field(default_factory=dict)
    terminal_evidence: dict[str, Any] = field(default_factory=dict)
    resolved_at: Optional[datetime] = None
    #: Not persisted. True only on the entry returned by the
    #: `open_command_ledger_entry` call that INSERTED this row; False when
    #: that call found an existing row for the same idempotency key. Lets a
    #: caller tell "I am the first attempt" from "an earlier attempt (maybe a
    #: crashed or concurrent one) already wrote its intent" even while the
    #: row is still `PENDING_SUBMISSION`.
    newly_opened: bool = False

    @property
    def is_resolved(self) -> bool:
        return self.resolved_at is not None


@dataclass
class DestinationAccount:
    """One account a signal can be routed to, plus how to size the trade."""

    account_id: str
    broker: str
    multiplier: float = 1.0
    fixed_quantity: Optional[float] = None
    symbol_map: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    #: Route entries/exits for this account through
    #: app/lifecycle/manager.py's PositionLifecycleManager (protect-first,
    #: logical targets, a serialized close arbiter) instead of embedding
    #: stop_loss/take_profit directly into the entry order. See
    #: app/lifecycle/manager.py's module docstring for why/when this
    #: matters. Off by default — existing accounts behave exactly as
    #: before unless explicitly opted in.
    managed_lifecycle: bool = False
    #: E03: an opt-in notional-exposure ceiling for this account (None = no
    #: limit, the existing default behavior). See app/capital_allocator.py's
    #: module docstring for exactly what this does and doesn't enforce --
    #: whenever this (or any other gate below) is configured, a signal
    #: with no resolvable price now REJECTS rather than skipping the check.
    max_notional_exposure: Optional[float] = None
    #: E03 (risk-basis sizing): an opt-in ceiling on risk-to-stop as a
    #: percentage (0-1) of this account's real, freshly-fetched equity
    #: (None = not enforced, the default). A new entry whose
    #: |entry_price - stop_loss| * quantity would exceed
    #: `equity * risk_percent_of_equity` is rejected. Fails closed --
    #: rejects, never silently skips -- whenever the admitting signal has
    #: no `stop_loss`, or the account's broker can't report a real
    #: `equity` figure right now. See app/capital_allocator.py's module
    #: docstring and app/engine.py's `_check_risk_basis`.
    risk_percent_of_equity: Optional[float] = None
    #: P0-5: this account's own explicit, persisted management-recipe
    #: declaration -- see `ManagementRecipe`'s own docstring. `None` on
    #: construction means "not explicitly declared"; `__post_init__`
    #: immediately fills it from `managed_lifecycle` so every constructed
    #: `DestinationAccount` always carries a real, non-None value from
    #: here on (never left as an unauditable inference done ad hoc by
    #: whatever screen/report happens to read it). Passing an EXPLICIT
    #: value that disagrees with `managed_lifecycle` is accepted as-is
    #: (not silently overwritten) -- that disagreement is itself a real
    #: misconfiguration worth surfacing/auditing on TR-07, not something
    #: this field quietly resolves on the account's behalf.
    management_recipe: Optional[ManagementRecipe] = None
    #: P0-5: a simple, free-form qualification label for this account's
    #: declared management contract (e.g. "qualified", "unqualified",
    #: "pending_review") -- intentionally NOT an enum yet. P0-7 is
    #: building a fuller broker-capability qualification taxonomy
    #: separately; this field may end up referencing that taxonomy later,
    #: but for now it's this account's own simple, independent label.
    #: `None` (the default) means "not yet declared," never fabricated as
    #: "qualified."
    qualification_level: Optional[str] = None
    #: P0-5: an explicit, narrow, OFF-BY-DEFAULT operator assertion that
    #: NOTHING else writes to this specific broker account's position
    #: outside Signal Copier -- no manual intervention, no other
    #: automated writer, no direct dashboard/API trade at the broker, no
    #: corporate action that changes share count without an offsetting
    #: fill this service sees. See app/engine.py's
    #: `_reconcile_before_plain_close` for exactly what setting this to
    #: True allows: a plain (non-managed_lifecycle) account's CLOSE to
    #: proceed against this service's own locally tracked position alone,
    #: with no live broker-side reconciliation, WHEN the broker adapter
    #: also has no verified position-readback capability at all (see
    #: `BrokerAdapter.has_position_readback_capability`). `False` (the
    #: default) is a hard block, not a silent gap being accepted: an
    #: unreconciled, unqualified plain close is REJECTED outright rather
    #: than proceeding on a possibly-stale local projection. This is a
    #: real operational promise the operator is making about this one
    #: account, not a config convenience to flip to make a rejection go
    #: away -- see README.md's "Exclusive-writer qualification" section
    #: for what it means and its risk before setting it True.
    exclusive_writer_qualified: bool = False
    #: Circuit breaker: maximum acceptable daily loss as percentage of equity
    #: (e.g., 5 for 5%). `None` (the default) means no daily loss limit is
    #: enforced for this account. When set, entries are rejected if today's
    #: P&L loss (realized + unrealized) exceeds this percentage. Closes are
    #: always allowed to hedge/unwind after a breach. See app/daily_loss_limiter.py.
    daily_loss_limit_percent: Optional[float] = None
    #: Minimum equity threshold (in account currency). When set, new entries
    #: are rejected if account equity would fall below this level. `None`
    #: (the default) disables this check. See app/daily_loss_limiter.py.
    min_equity_threshold: Optional[float] = None
    #: E-11/B-10: Base currency for this account (ISO 4217 code, e.g.,
    #: 'USD', 'EUR', 'JPY'). Used for multi-currency support: the currency
    #: in which cash, equity, and P&L are expressed. `None` means not
    #: declared; operators must explicitly configure this when trading
    #: multiple currencies on the same account. See OrderResult.price_currency
    #: for each individual order's price currency (distinct from the account
    #: base currency).
    currency: Optional[str] = None
    #: B-11: an opt-in maximum gross leverage ceiling for this account
    #: (e.g., 1.0 = no leverage, 1.25 = 25% leverage allowed, 2.0 = 200%
    #: leverage allowed). When set, the sum of confirmed, pending, and new
    #: notional exposure is refused if it would exceed
    #: max_gross_leverage × (equity − maintenance_margin). `None` (the
    #: default) means no leverage limit is enforced. See app/capital_allocator.py.
    max_gross_leverage: Optional[float] = None
    #: B-14: Whether this account is allowed to open short positions. When
    #: False (the default for equity/cash accounts), a SELL entry on a flat
    #: account is rejected, and a SELL entry on an existing long is treated
    #: as a close/reduce-only against the tracked long position. When True,
    #: a SELL entry behaves as a new short-side entry (the legacy behavior).
    allow_short: bool = False
    #: WP-38 (G-C-13): the EvidenceClass value (e.g., "INTERNAL_PAPER",
    #: "OBSERVED_OWNER_LIVE") to export for this account's events.
    #: `None` (the default) means use the global config.RELAY_EVIDENCE_CLASS.
    evidence_class: Optional[str] = None
    #: WP-38 (G-C-24): monotonic counter for paper broker order IDs,
    #: persisted per account to remain unique across restarts.
    #: Only used when broker='paper'; None/unused for other brokers.
    paper_order_id_sequence: Optional[int] = None

    def __post_init__(self) -> None:
        if self.management_recipe is None:
            self.management_recipe = (
                ManagementRecipe.FULL_MANAGED_LIFECYCLE
                if self.managed_lifecycle
                else ManagementRecipe.PLAIN_UNMANAGED
            )
        elif isinstance(self.management_recipe, str):
            self.management_recipe = ManagementRecipe(self.management_recipe)
