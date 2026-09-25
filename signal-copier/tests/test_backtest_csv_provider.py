from datetime import datetime, timezone

from app.backtest.models import CsvPriceHistoryProvider


def test_reads_bars_from_csv_within_range(tmp_path):
    path = tmp_path / "AAPL.csv"
    path.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2024-01-01T00:00:00+00:00,100,102,99,101,1000\n"
        "2024-01-02T00:00:00+00:00,101,105,100,104,1200\n"
        "2024-01-10T00:00:00+00:00,104,106,103,105,900\n"
    )
    provider = CsvPriceHistoryProvider({"AAPL": path})

    bars = provider.get_bars(
        "AAPL", datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 3, tzinfo=timezone.utc)
    )

    assert len(bars) == 2
    assert bars[0].close == 101
    assert bars[1].close == 104


def test_missing_symbol_returns_empty_not_an_error(tmp_path):
    provider = CsvPriceHistoryProvider({})
    bars = provider.get_bars(
        "UNKNOWN", datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 3, tzinfo=timezone.utc)
    )
    assert bars == []


def test_missing_file_returns_empty_not_an_error(tmp_path):
    provider = CsvPriceHistoryProvider({"AAPL": tmp_path / "does-not-exist.csv"})
    bars = provider.get_bars(
        "AAPL", datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 3, tzinfo=timezone.utc)
    )
    assert bars == []
