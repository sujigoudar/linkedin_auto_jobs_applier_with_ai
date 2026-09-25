"""Historical price data for the Signal Backtester (app/backtest/replay.py).

## The honest limitation up front

This project has no market-data vendor connected — no API key, no MCP
tool, no bundled dataset. `PriceHistoryProvider` is the seam a real
vendor (Polygon, Alpaca's own market-data API, ccxt's OHLCV fetch, a
paid data provider) would plug into; `CsvPriceHistoryProvider` is the
only implementation shipped here, reading bars *you* supply from a local
CSV. Backtesting against your own exported/purchased historical data
works today; backtesting against a symbol you haven't sourced data for
does not, and this module won't silently substitute synthetic prices to
make that look like it works.
"""
from __future__ import annotations

import abc
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class HistoricalBar:
    """One OHLCV bar. `high`/`low` are what make backtesting a stop AND a
    target against the same bar genuinely ambiguous — see
    app/backtest/simulator.py's module docstring for why that ambiguity is
    preserved rather than guessed away."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def __post_init__(self) -> None:
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError(
                f"bar at {self.timestamp} has an impossible OHLC relationship "
                f"(open={self.open}, high={self.high}, low={self.low}, close={self.close})"
            )


class PriceHistoryProvider(abc.ABC):
    """The seam a real market-data source plugs into. Every implementation
    must return bars in ascending timestamp order, and must not fabricate
    bars for a period it doesn't actually have data for — the backtester
    treats a gap in coverage as a `DATA_GAP` in its report (see
    app/backtest/replay.py), not as "no signal fired here."""

    @abc.abstractmethod
    def get_bars(self, symbol: str, start: datetime, end: datetime) -> list[HistoricalBar]:
        """Return every bar for `symbol` in [start, end], ascending by time."""


class CsvPriceHistoryProvider(PriceHistoryProvider):
    """Reads bars from a local CSV: columns `timestamp,open,high,low,close`
    and an optional `volume`. `timestamp` must be ISO 8601
    (`2024-01-15T09:30:00+00:00`). One file per symbol — pass a dict
    mapping symbol -> csv path, since a backtest run may cover more than
    one symbol across its replayed signals.

    This is deliberately the simplest possible real implementation: no
    network call, no vendor account, no invented data. Export or buy the
    bars you want to backtest against and point this at the file.
    """

    def __init__(self, csv_paths: dict[str, Path]):
        self._csv_paths = {symbol: Path(path) for symbol, path in csv_paths.items()}
        self._cache: dict[str, list[HistoricalBar]] = {}

    def get_bars(self, symbol: str, start: datetime, end: datetime) -> list[HistoricalBar]:
        bars = self._cache.get(symbol)
        if bars is None:
            path = self._csv_paths.get(symbol)
            if path is None or not path.exists():
                return []
            bars = _load_csv(path)
            self._cache[symbol] = bars
        return [b for b in bars if start <= b.timestamp <= end]


def _load_csv(path: Path) -> list[HistoricalBar]:
    bars: list[HistoricalBar] = []
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            ts = datetime.fromisoformat(row["timestamp"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            bars.append(
                HistoricalBar(
                    timestamp=ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume") or 0.0),
                )
            )
    bars.sort(key=lambda b: b.timestamp)
    return bars
