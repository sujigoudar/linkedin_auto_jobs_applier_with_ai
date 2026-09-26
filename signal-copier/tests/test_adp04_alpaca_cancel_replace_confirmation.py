"""ADP-04: DELETE returning 204 only means Alpaca's API accepted the cancel
REQUEST, not that the order is actually, finally cancelled -- it can sit in
`pending_cancel` and still execute before the venue tears it down.
Similarly, a PATCH replace returning HTTP 200 only means the API layer
accepted the request; the response body's own `status` can still be
`rejected`. Both were previously treated as unconditional success.

Reproduces the audit's exact two cases
(test_adapter_research_audit::test_alpaca_cancel_acceptance_is_not_final_cancellation
and ::test_alpaca_replacement_rejection_preserved).
"""
import httpx
import pytest

from app.brokers.alpaca import AlpacaBroker
from app.models import DestinationAccount, OrderStatus


@pytest.fixture(autouse=True)
def _alpaca_credentials(monkeypatch):
    monkeypatch.setenv("ALPACA_A_API_KEY", "test")
    monkeypatch.setenv("ALPACA_A_API_SECRET", "test")


@pytest.mark.asyncio
async def test_audits_exact_case_cancel_204_but_still_pending_returns_false():
    broker = AlpacaBroker()
    await broker._client.aclose()

    def transport(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            return httpx.Response(204, request=request)
        return httpx.Response(200, json={"id": "1", "status": "pending_cancel", "filled_qty": "0"}, request=request)

    broker._client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    try:
        assert await broker.cancel_order(DestinationAccount("a", "alpaca"), "1") is False
    finally:
        await broker.close()


@pytest.mark.asyncio
async def test_cancel_confirmed_when_venue_reports_a_terminal_cancelled_status():
    broker = AlpacaBroker()
    await broker._client.aclose()

    def transport(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            return httpx.Response(204, request=request)
        return httpx.Response(200, json={"id": "1", "status": "canceled", "filled_qty": "0"}, request=request)

    broker._client = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    try:
        assert await broker.cancel_order(DestinationAccount("a", "alpaca"), "1") is True
    finally:
        await broker.close()


@pytest.mark.asyncio
async def test_audits_exact_case_replace_rejected_in_response_body_is_reported():
    broker = AlpacaBroker()
    await broker._client.aclose()
    broker._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"id": "new", "status": "rejected"}, request=r))
    )
    try:
        result = await broker.replace_stop_quantity(DestinationAccount("a", "alpaca"), "old", 20, 95)
        assert result.status == OrderStatus.REJECTED
    finally:
        await broker.close()


@pytest.mark.asyncio
async def test_replace_still_reports_pending_when_actually_accepted():
    broker = AlpacaBroker()
    await broker._client.aclose()
    broker._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"id": "new", "status": "new"}, request=r))
    )
    try:
        result = await broker.replace_stop_quantity(DestinationAccount("a", "alpaca"), "old", 20, 95)
        assert result.status == OrderStatus.PENDING
        assert result.broker_order_id == "new"
    finally:
        await broker.close()
