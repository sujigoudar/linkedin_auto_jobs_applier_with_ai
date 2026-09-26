"""C22: structlog for the core financial path's correlated, structured
logs. Deliberately configured to write through its own dedicated
renderer (PrintLoggerFactory), never by reformatting the existing
stdlib root logger's handlers -- an earlier version of this did that
and corrupted an unrelated test's plain stdlib `caplog` assertions
elsewhere in this same suite.

`structlog.testing.capture_logs()` intentionally bypasses the configured
processor chain (per its own docs), so it can't be used to test
`merge_contextvars`/redaction -- those are tested here against the real
rendered output (via capsys, reading structlog's own PrintLoggerFactory
stdout) and via the processor functions directly.
"""
import pytest
import structlog

from app.brokers.paper import PaperBroker
from app.db import SignalStore
from app.engine import SignalCopierEngine
from app.logging_config import _redact_secrets, bind_signal_context, configure_structlog
from app.models import DestinationAccount, Side, Signal
from app.routing import RoutingConfig, RoutingRule


@pytest.fixture(autouse=True)
def _configured():
    configure_structlog(json_output=False)
    yield
    structlog.reset_defaults()


@pytest.fixture
def store(tmp_path):
    return SignalStore(tmp_path / "test.db")


def test_redact_secrets_processor_masks_secret_shaped_keys():
    event_dict = {"event": "x", "webhook_secret": "super-secret-value", "account_id": "acct1"}

    result = _redact_secrets(None, "info", event_dict)

    assert result["webhook_secret"] == "***redacted***"
    assert result["account_id"] == "acct1"  # non-secret fields untouched


def test_bind_signal_context_sets_and_resets_contextvars():
    assert structlog.contextvars.get_contextvars() == {}
    with bind_signal_context(signal_id="sig-123", source="tradingview", symbol="AAPL", side="buy"):
        assert structlog.contextvars.get_contextvars()["signal_id"] == "sig-123"
        assert structlog.contextvars.get_contextvars()["symbol"] == "AAPL"
    assert structlog.contextvars.get_contextvars() == {}


def test_rendered_output_carries_bound_context_and_redacts_secrets(capsys):
    log = structlog.get_logger("some.module")
    with bind_signal_context(signal_id="sig-123", source="tradingview", symbol="AAPL", side="buy"):
        log.info("something happened", webhook_secret="super-secret-value")

    output = capsys.readouterr().out
    assert "sig-123" in output
    assert "AAPL" in output
    assert "super-secret-value" not in output
    assert "redacted" in output


def test_context_does_not_leak_into_output_after_the_with_block_exits(capsys):
    log = structlog.get_logger("some.module")
    with bind_signal_context(signal_id="sig-123", source="tradingview", symbol="AAPL", side="buy"):
        pass
    log.info("logged after the signal's context has been cleared")

    assert "sig-123" not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_handle_signal_binds_context_for_the_whole_call(store, capsys):
    """End-to-end: engine.handle_signal's own structured_logger calls
    (signal_received/signal_processed) carry the bound correlation
    fields in the real rendered output."""
    broker = PaperBroker()
    account = DestinationAccount(account_id="acct1", broker="paper")
    routing = RoutingConfig(
        rules=[RoutingRule(source="tradingview", destinations=["acct1"])], accounts={"acct1": account}
    )
    engine = SignalCopierEngine(routing=routing, brokers={"paper": broker}, store=store)

    signal = Signal(source="tradingview", symbol="AAPL", side=Side.BUY, quantity=10.0)
    await engine.handle_signal(signal)

    output = capsys.readouterr().out
    assert "signal_received" in output
    assert "signal_processed" in output
    assert signal.id in output
    assert "AAPL" in output
