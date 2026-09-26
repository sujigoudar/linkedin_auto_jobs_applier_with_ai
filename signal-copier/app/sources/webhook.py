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
        "stop_loss": 63000.0,       # optional
        "take_profit": 70000.0,     # optional
        "asset_class": "crypto",    # optional, defaults to crypto
        "analyst": "alice"          # optional -- who/what posted this, for app/providers.py overrides
    }
"""
from __future__ import annotations

import math
from typing import Any

from app.errors import SignalValidationError
from app.models import AssetClass, Signal, Side
from app.sources.base import SourceAdapter

__all__ = ["WebhookSource", "SignalValidationError"]


class WebhookSource(SourceAdapter):
    name = "webhook"

    async def start(self) -> None:
        # Push-based: signals arrive via `ingest()`, called from the FastAPI route.
        return None

    async def ingest(self, payload: dict[str, Any], *, source_override: str | None = None) -> Signal:
        signal = self.parse(payload, source_override=source_override)
        await self.on_signal(signal)
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
        return Signal(
            source=source_override or self.name,
            symbol=str(symbol),
            side=side_enum,
            asset_class=asset_class,
            analyst=str(analyst) if analyst is not None else None,
            quantity=_optional_positive_float(payload.get("quantity"), field="quantity"),
            price=_optional_positive_float(payload.get("price"), field="price"),
            stop_loss=_optional_positive_float(payload.get("stop_loss"), field="stop_loss"),
            take_profit=_optional_positive_float(payload.get("take_profit"), field="take_profit"),
            raw=payload,
        )


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
