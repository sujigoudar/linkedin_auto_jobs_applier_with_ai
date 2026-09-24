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
        "asset_class": "crypto"     # optional, defaults to crypto
    }
"""
from __future__ import annotations

from typing import Any

from app.models import AssetClass, Signal, Side
from app.sources.base import SourceAdapter


class SignalValidationError(ValueError):
    pass


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

        return Signal(
            source=source_override or self.name,
            symbol=str(symbol),
            side=side_enum,
            asset_class=asset_class,
            quantity=_optional_float(payload.get("quantity")),
            price=_optional_float(payload.get("price")),
            stop_loss=_optional_float(payload.get("stop_loss")),
            take_profit=_optional_float(payload.get("take_profit")),
            raw=payload,
        )


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None
