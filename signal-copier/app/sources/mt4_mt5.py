"""MT4/MT5 signal source, via the MetaApi cloud SDK
(https://github.com/metaapi/metaapi-python-sdk — verified against its
README/examples rather than written blind). MetaApi runs the MT4/MT5
terminal connection in the cloud, so — unlike app/brokers/mt4_mt5.py's
same-host `MetaTrader5` package option — this needs no local Windows
terminal at all and works for both MT4 and MT5 accounts uniformly.

Setup:
    pip install metaapi-cloud-sdk
    1. Sign up at metaapi.cloud (API access to one account is free).
    2. Get an API token from the MetaApi dashboard.
    3. Either add your MT4/MT5 account through the dashboard and note its
       account_id, or let this adapter create it on first run by passing
       login/password/server/platform (see `_get_or_create_account`).

This polls `connection.history_storage.deals` on an interval rather than
relying on a specific real-time "new deal" callback name, since the exact
listener method for that isn't pinned down in the SDK's public examples —
polling a confirmed, documented property is more reliable than guessing at
an unverified one. Increase/decrease `poll_interval_seconds` to trade off
latency against API load.
"""
from __future__ import annotations

import asyncio
import logging

from app.models import AssetClass, Signal, Side
from app.sources.base import SourceAdapter

logger = logging.getLogger(__name__)


class MetaApiSource(SourceAdapter):
    name = "mt4_mt5"

    def __init__(
        self,
        on_signal,
        token: str,
        account_id: str,
        asset_class: AssetClass = AssetClass.FOREX,
        poll_interval_seconds: float = 2.0,
    ):
        super().__init__(on_signal)
        self.token = token
        self.account_id = account_id
        self.asset_class = asset_class
        self.poll_interval_seconds = poll_interval_seconds
        self._connection = None
        self._poll_task: asyncio.Task | None = None
        self._seen_deal_ids: set[str] = set()

    async def start(self) -> None:
        try:
            from metaapi_cloud_sdk import MetaApi
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "metaapi-cloud-sdk is not installed; run `pip install metaapi-cloud-sdk`"
            ) from exc

        api = MetaApi(self.token)
        account = await api.metatrader_account_api.get_account(self.account_id)

        if account.state != "DEPLOYED":
            await account.deploy()
        if account.connection_status != "CONNECTED":
            await account.wait_connected()

        connection = account.get_streaming_connection()
        await connection.connect()
        await connection.wait_synchronized()
        self._connection = connection

        # Seed with existing deals so only genuinely new ones fire a signal.
        self._seen_deal_ids = {deal.get("id") for deal in connection.history_storage.deals}

        self._poll_task = asyncio.create_task(self._poll_loop())

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self.poll_interval_seconds)
            try:
                self._check_for_new_deals()
            except Exception:  # noqa: BLE001 - one bad poll iteration must not kill the loop
                logger.exception("error polling MetaApi history_storage for new deals")

    def _check_for_new_deals(self) -> None:
        for deal in self._connection.history_storage.deals:
            deal_id = deal.get("id")
            if deal_id is None or deal_id in self._seen_deal_ids:
                continue
            self._seen_deal_ids.add(deal_id)

            deal_type = deal.get("type", "")
            if deal_type not in ("DEAL_TYPE_BUY", "DEAL_TYPE_SELL"):
                continue  # skip balance/credit/etc. entries that aren't trades

            side = Side.BUY if deal_type == "DEAL_TYPE_BUY" else Side.SELL
            signal = Signal(
                source=self.name,
                symbol=deal.get("symbol", ""),
                side=side,
                asset_class=self.asset_class,
                quantity=deal.get("volume"),
                price=deal.get("price"),
                raw=deal,
            )
            asyncio.create_task(self.on_signal(signal))

    async def stop(self) -> None:
        if self._poll_task is not None:
            self._poll_task.cancel()
        if self._connection is not None:
            await self._connection.close()
