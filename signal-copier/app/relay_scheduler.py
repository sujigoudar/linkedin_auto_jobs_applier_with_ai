"""Scheduled polling of app/relay_worker.py's own `run_once`.

A background loop (started in app/main.py's `lifespan`, same pattern as
`OrderReconciler`/`PriceMonitor`/`ProviderScout`) that periodically
forwards undelivered export_events to the commercial ingress --
INTEGRATION_DECISION.md S11's own "configurable initial one-second
outbox poll ... for non-live testing." Only starts when
`config.RELAY_INGRESS_URL` is actually set (same "pull-based sources
only start if fully configured" convention app/main.py already applies
to TelegramSource/DiscordSource/etc.) -- an unconfigured relay is a
silent no-op scheduler, never a startup failure, since exporting to a
commercial platform is optional for a signal-copier deployment that
doesn't have one.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.db import SignalStore
from app.relay_worker import RelayIngestResult, run_once

logger = logging.getLogger(__name__)


class RelayScheduler:
    def __init__(self, store: SignalStore, *, interval_seconds: float = 1.0):
        self.store = store
        self.interval_seconds = interval_seconds
        self._task: asyncio.Task | None = None
        #: Same contract as OrderReconciler.last_success_at/PriceMonitor's
        #: identical field -- surfaced by app/main.py's /health.
        self.last_success_at: datetime | None = None

    async def start(self) -> None:
        # OPS-02: idempotent, same fix/reasoning as OrderReconciler.start().
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.interval_seconds)
            try:
                result = await asyncio.to_thread(run_once, self.store)
                self._log_result(result)
                self.last_success_at = datetime.now(timezone.utc)
            except asyncio.CancelledError:
                raise  # see PriceMonitor._loop's identical comment
            except Exception:  # noqa: BLE001 - one bad pass must not kill the loop
                logger.exception("error during relay poll pass")

    @staticmethod
    def _log_result(result: RelayIngestResult) -> None:
        if result.unregistered_stream_event_ids:
            logger.warning(
                "relay pass: %d unregistered-stream event(s) parked for retry: %s",
                len(result.unregistered_stream_event_ids), result.unregistered_stream_event_ids,
            )
        if result.integrity_error_event_ids:
            logger.error(
                "relay pass: %d integrity-error event(s) parked, needs operator investigation: %s",
                len(result.integrity_error_event_ids), result.integrity_error_event_ids,
            )
