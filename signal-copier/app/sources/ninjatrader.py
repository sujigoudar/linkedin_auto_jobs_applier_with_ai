"""NinjaTrader as a signal SOURCE, via a NinjaScript AddOn/Indicator that
POSTs fill events to this service's `/ninjatrader/webhook` route.

## Why this used to be a stub, and what changed

NinjaTrader's ATI (Automated Trading Interface) has no fill-event stream
to read out of at all -- its only read-out paths are polling the
"outgoing" state files (which forum reports say only carry coarse states
like WORKING/FLAT, not a fill stream) or `NinjaTrader.Client.dll` query
methods. Every open-source NinjaTrader BRIDGE previously found (TradeRouter,
ninja-webhook, tv-ninjatrader-bridge) is one-way (external signal ->
NinjaTrader order too), so none of those help either.

What DOES exist, verified against real, currently public source (fetched
and read directly, not guessed from a description): Apex-Logics/TradVue's
`ninjatrader/TradVueAutoJournal.cs` and Shadowscr-7/tradingadmin's
`TradeMonitor.cs`. Both are NT8 **Indicators** (not Strategies) that
subscribe to `Account.ExecutionUpdate` at the account level and POST each
fill's JSON via `HttpClient.PostAsync`. The Indicator-not-Strategy choice
matters: a Strategy's `OnExecutionUpdate` only sees orders IT placed, so
it never fires for a fill from ATM/SuperDOM/manual trading/another
strategy -- only the account-level event sees every fill regardless of
origin. Neither repository has a LICENSE file, so `ninjascript/
SignalCopierAutoJournal.cs` (this repo) is written fresh, modeled on that
same verified `Account.ExecutionUpdate` -> `HttpClient.PostAsync`
pattern, not copied.

**Disclosed, unverified:** that C# file has not been compiled or run
against a real or Sim101 NinjaTrader 8 install -- there is no NinjaTrader
or Windows in this development environment to do so. Treat it as a
reviewed reference implementation, not a tested one, until someone
actually imports and runs it in NinjaTrader (Sim101 first, before any
live account). This module's own Python side (`parse()` below and the
FastAPI route in app/main.py) IS real, tested code -- only the C# side
carries that caveat.

## Payload shape

Verified from TradVueAutoJournal.cs's own `BuildPayload`/JSON template
(field names, exact casing, and value semantics all read directly from
its source, not inferred):

    {"ticker": "AAPL", "action": "entry"|"exit", "direction": "Long"|"Short",
     "price": 100.25, "entry_price": ..., "exit_price": ..., "qty": 10,
     "pnl": 0.0, "asset_class": "Stock"|"Futures"|"Forex",
     "order_id": "...", "time": "...", "source": "ninjatrader"}

`direction` describes the POSITION's side, not the raw buy/sell of this
fill: for `action="entry"`, `direction="Long"` means this fill opened or
added to a long (a BUY) and `"Short"` means it opened/added to a short (a
SELL). For `action="exit"`, this is mapped to `Side.CLOSE` regardless of
`direction` -- this project's engine resolves a CLOSE against the
account's own tracked position (see app/engine.py's `_resolve_close`),
not the reported `qty`, so a PARTIAL exit is intentionally not modeled
here (the same simplification app/sources/mt4_mt5.py's MetaApiSource and
app/sources/rithmic.py already make for their own copy-source exit fills:
DEAL_ENTRY_OUT/an exit fill always maps to a full CLOSE, never a
partial). `entry_price`/`exit_price`/`pnl`/`order_id`/`time` are retained
verbatim in `Signal.raw` for observability but don't drive engine
behavior.
"""
from __future__ import annotations

import math

from app.errors import SignalValidationError
from app.models import AssetClass, Intent, Side, Signal
from app.sources.base import SourceAdapter

#: Exact strings TradVueAutoJournal.cs's `assetClass` local variable can
#: hold (its InstrumentType branches: Future -> "Futures", Forex ->
#: "Forex", everything else -> "Stock", including plain equities and
#: anything NinjaTrader doesn't specifically recognize as one of the
#: other two) -- verified against that source, not guessed.
_ASSET_CLASS_MAP = {
    "stock": AssetClass.EQUITY,
    "futures": AssetClass.FUTURE,
    "forex": AssetClass.FOREX,
}


class NinjaTraderSource(SourceAdapter):
    name = "ninjatrader"

    def __init__(self, on_signal):
        super().__init__(on_signal)

    async def start(self) -> None:
        # Push-based: signals arrive via the /ninjatrader/webhook FastAPI
        # route in app/main.py, which validates the shared secret and
        # calls ingest().
        return None

    def parse(self, payload: dict) -> Signal:
        symbol = payload.get("ticker")
        action = payload.get("action")
        direction = payload.get("direction")
        qty = payload.get("qty")
        price = payload.get("price")
        raw_asset_class = payload.get("asset_class")

        if not symbol or not isinstance(symbol, str):
            raise SignalValidationError("missing or invalid 'ticker'")
        if action not in ("entry", "exit"):
            raise SignalValidationError(f"unrecognized action {action!r} (expected 'entry' or 'exit')")
        if direction not in ("Long", "Short"):
            raise SignalValidationError(f"unrecognized direction {direction!r} (expected 'Long' or 'Short')")
        if isinstance(qty, bool) or not isinstance(qty, (int, float)) or qty <= 0:
            raise SignalValidationError(f"invalid 'qty': {qty!r}")
        if (
            isinstance(price, bool)
            or not isinstance(price, (int, float))
            or not math.isfinite(price)
            or price <= 0
        ):
            raise SignalValidationError(f"invalid 'price': {price!r}")
        asset_class = _ASSET_CLASS_MAP.get(str(raw_asset_class).lower()) if raw_asset_class else None
        if asset_class is None:
            raise SignalValidationError(f"unrecognized asset_class {raw_asset_class!r}")

        side = Side.CLOSE if action == "exit" else (Side.BUY if direction == "Long" else Side.SELL)
        # A-07: Mark exits with Intent.EXIT
        intent = Intent.EXIT if action == "exit" else None

        return Signal(
            source=self.name,
            symbol=symbol,
            side=side,
            asset_class=asset_class,
            quantity=float(qty),
            price=float(price),
            intent=intent,
            raw=payload,
        )

    async def ingest(self, payload: dict) -> Signal:
        signal = self.parse(payload)
        await self.on_signal(signal)
        return signal
