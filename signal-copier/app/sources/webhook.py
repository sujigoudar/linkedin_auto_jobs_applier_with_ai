"""Generic JSON / TradingView webhook source.

This is the one fully working ingestion path — point a TradingView alert,
a custom script, or `curl` at POST /webhook/{source_name} and it produces a
normalized Signal. Every other push-based source (Slack, Discord, a
NinjaTrader addon posting HTTP callbacks) can reuse this same parser by
matching its JSON shape, or subclass it for a different shape.

Expected JSON body:
    {
        "symbol": "BTCUSDT",
        "side": "buy",              # buy | sell | close
        "quantity": 0.01,           # optional
        "price": 65000.0,           # optional, informational
        "price_low": 64800.0,       # optional -- entry range lower bound, alongside/instead of "price"
        "price_high": 65200.0,      # optional -- entry range upper bound
        "entry_order_type": "limit",# optional -- market | limit | stop
        "stop_loss": 63000.0,       # optional
        "take_profit": 70000.0,     # optional -- single/primary target (back-compat)
        "targets": [                # optional -- ordered multiple profit targets (TP1/TP2/TP3...)
            {"price": 68000.0, "fraction": 0.5, "label": "TP1"},
            {"price": 70000.0, "fraction": 0.5, "label": "TP2"},
        ],
        "asset_class": "crypto",    # optional, defaults to crypto
        "analyst": "alice",         # optional -- who/what posted this, for app/providers.py overrides
        "message_id": "alert-123",  # optional -- this alert's own native id, for dedup/replay detection
        "intent": "entry_long",     # optional -- entry_long | entry_short | sell | exit | reduce | stop_update | target_update | cancel | add
        "reduce_fraction": 0.5      # optional -- fraction (0 < x <= 1) of position to reduce, for reduce intent
    }

When both "take_profit" and "targets" are given, both are used as-is (no
cross-validation that they agree). When only "targets" is given, the
first target's price becomes the `Signal.take_profit` back-compat value
(see `Signal.take_profit`'s own docstring) so an existing consumer that
only reads that single field still gets a usable number.
"""
from __future__ import annotations

import math
from typing import Any

from app.errors import SignalValidationError
from app.models import AssetClass, Intent, ProfitTarget, Signal, SourceEvent, SourceEventKind, Side
from app.sources.base import SourceAdapter

__all__ = ["WebhookSource", "SignalValidationError"]

#: This adapter's own exact interpretation implementation -- bump this
#: whenever `parse()`'s field-by-field reading of the JSON body changes in
#: a way that would matter to a consumer replaying a past raw payload.
PARSER_VERSION = "webhook-json-v1"


class WebhookSource(SourceAdapter):
    name = "webhook"

    async def start(self) -> None:
        # Push-based: signals arrive via `ingest()`, called from the FastAPI route.
        return None

    async def ingest(self, payload: dict[str, Any], *, source_override: str | None = None) -> Signal:
        signal = self.parse(payload, source_override=source_override)
        await self.on_signal(signal)
        # RISK-01/dedup: a genuine provider `message_id` (an alert's own
        # idempotency key, when the caller's JSON body actually includes
        # one -- see `parse()`) makes this ORIGINAL source-ledger row a
        # real, replay-detectable event rather than an untraceable "some
        # webhook fired". A no-op unless a caller wired `on_source_event`
        # (see `SourceAdapter.__init__`'s own docstring).
        await self._emit_source_event(
            SourceEvent(
                source=signal.source,
                kind=SourceEventKind.ORIGINAL,
                channel_id=signal.channel_id,
                message_id=signal.message_id,
                provider_timestamp=None,  # a webhook body's fields are read below; see parse()
                local_receipt_timestamp=signal.received_at,
                signal=signal,
                raw_source_event=payload,
            )
        )
        return signal

    def parse(self, payload: dict[str, Any], *, source_override: str | None = None) -> Signal:
        symbol = payload.get("symbol")
        side = payload.get("side")
        if not symbol:
            raise SignalValidationError("missing 'symbol'")
        if not side:
            raise SignalValidationError("missing 'side'")

        try:
            side_enum = Side(str(side).lower())
        except ValueError as exc:
            raise SignalValidationError(f"invalid side '{side}'") from exc

        asset_class_raw = payload.get("asset_class", "crypto")
        try:
            asset_class = AssetClass(str(asset_class_raw).lower())
        except ValueError as exc:
            raise SignalValidationError(f"invalid asset_class '{asset_class_raw}'") from exc

        analyst = payload.get("analyst")
        targets = _parse_targets(payload.get("targets"))
        take_profit = _optional_positive_float(payload.get("take_profit"), field="take_profit")
        if take_profit is None and targets:
            # Backward-compatible primary/first-target convention -- see
            # `Signal.take_profit`'s own docstring: an existing consumer
            # that only ever reads `take_profit` still gets a usable
            # value when the source instead gave an ordered `targets`
            # list and no separate bare `take_profit`.
            take_profit = targets[0].price

        # Provider message identity: only set when the caller's own JSON
        # body actually carries one -- a bare TradingView alert (no
        # "message_id"/"alert_id"/"id" field) has no native message
        # identity to report, and this adapter never invents one (see
        # `Signal.message_id`'s own docstring).
        message_id = payload.get("message_id") or payload.get("alert_id") or payload.get("id")

        # Parse optional intent field
        intent_raw = payload.get("intent")
        intent = None
        if intent_raw is not None:
            try:
                intent = Intent(str(intent_raw).lower())
            except ValueError as exc:
                raise SignalValidationError(f"invalid intent '{intent_raw}'") from exc

        # Parse optional reduce_fraction field
        reduce_fraction = _optional_positive_float(payload.get("reduce_fraction"), field="reduce_fraction")
        if reduce_fraction is not None and reduce_fraction > 1.0:
            raise SignalValidationError(f"reduce_fraction must be <= 1.0, got {reduce_fraction}")

        return Signal(
            source=source_override or self.name,
            symbol=str(symbol),
            side=side_enum,
            asset_class=asset_class,
            analyst=str(analyst) if analyst is not None else None,
            quantity=_optional_positive_float(payload.get("quantity"), field="quantity"),
            price=_optional_positive_float(payload.get("price"), field="price"),
            price_low=_optional_positive_float(payload.get("price_low"), field="price_low"),
            price_high=_optional_positive_float(payload.get("price_high"), field="price_high"),
            stop_loss=_optional_positive_float(payload.get("stop_loss"), field="stop_loss"),
            take_profit=take_profit,
            targets=targets,
            entry_order_type=payload.get("entry_order_type"),
            channel_id=source_override or self.name,
            message_id=str(message_id) if message_id is not None else None,
            intent=intent,
            reduce_fraction=reduce_fraction,
            parser_version=PARSER_VERSION,
            raw=payload,
            raw_source_event=payload,
        )


def _parse_targets(raw_targets: Any) -> list[ProfitTarget]:
    """`payload["targets"]`: an ordered list of `{"price": ..., "quantity":
    ..., "fraction": ..., "label": ...}` objects -- the JSON-body
    equivalent of `signal_platform_contracts.payloads.ProfitTargetPayload`.
    `None`/absent stays the existing empty-list default (RISK-02: a
    malformed body must 400, not 500 -- anything that isn't a list of
    dict-shaped levels with a valid `price` raises SignalValidationError,
    same discipline as every other field this parser reads)."""
    if raw_targets is None:
        return []
    if not isinstance(raw_targets, list):
        raise SignalValidationError("'targets' must be a list of target levels")
    parsed: list[ProfitTarget] = []
    for index, level in enumerate(raw_targets):
        if not isinstance(level, dict):
            raise SignalValidationError(f"'targets[{index}]' must be an object")
        price = _optional_positive_float(level.get("price"), field=f"targets[{index}].price")
        if price is None:
            raise SignalValidationError(f"'targets[{index}]' is missing 'price'")
        quantity = _optional_positive_float(level.get("quantity"), field=f"targets[{index}].quantity")
        fraction = level.get("fraction")
        if fraction is not None:
            fraction = _optional_positive_float(fraction, field=f"targets[{index}].fraction")
            if fraction is not None and fraction > 1:
                raise SignalValidationError(f"'targets[{index}].fraction' must be <= 1, got {fraction!r}")
        label = level.get("label")
        parsed.append(
            ProfitTarget(
                price=price,
                quantity=quantity,
                fraction=fraction,
                label=str(label) if label is not None else None,
            )
        )
    return parsed


def _optional_positive_float(value: Any, *, field: str) -> float | None:
    """Parse an optional financial field strictly: reject `None` (pass
    through unset), booleans (`True`/`False` are not quantities even though
    Python's `float()` happily coerces them to 1.0/0.0), non-finite values
    (NaN/inf, which JSON itself cannot encode but a permissive body could
    still smuggle through as a Python object), and anything <= 0 -- a
    quantity, price, or stop/target level of zero or less is never a valid
    trade instruction. Anything that fails to convert to a float at all
    raises SignalValidationError instead of an unhandled ValueError/TypeError
    (RISK-01: malformed bodies must 400, not 500)."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise SignalValidationError(f"'{field}' must be a number, not a boolean")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise SignalValidationError(f"'{field}' is not a valid number: {value!r}") from exc
    if not math.isfinite(parsed):
        raise SignalValidationError(f"'{field}' must be a finite number, got {parsed}")
    if parsed <= 0:
        raise SignalValidationError(f"'{field}' must be greater than zero, got {parsed}")
    return parsed
