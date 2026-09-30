"""The interface every signal source must implement.

A source's only job is: get a Signal out of whatever the platform sends
(HTTP webhook, bot message, socket callback, polled API) and call
`self.on_signal(signal)`. Everything downstream — routing, sizing,
execution — is handled by the engine.
"""
from __future__ import annotations

import abc
from typing import Any, Awaitable, Callable

from app.models import Signal, SourceEvent

#: Every adapter calls `await self.on_signal(signal)` and discards whatever
#: comes back -- in practice always `SignalCopierEngine.handle_signal`,
#: which returns `list[OrderResult]` (used by the direct HTTP webhook route,
#: not by any adapter). `Awaitable[Any]` reflects that the return value is
#: part of no adapter's contract, not a hidden assumption that it's None.
SignalHandler = Callable[[Signal], Awaitable[Any]]

#: Optional companion to `SignalHandler`: an adapter that has been wired to
#: recognize edit/delete/reply/cancel/close/add/target_update/stop_update
#: moments (not only "new message") calls `await self._emit_source_event(...)`
#: for each one -- see `SourceEvent`'s own docstring. Every adapter this task
#: didn't touch, and every caller that hasn't opted into the source ledger
#: yet, simply never sets this (it stays `None`), so nothing here is a
#: breaking change to an existing adapter/caller.
SourceEventHandler = Callable[[SourceEvent], Awaitable[Any]]


class SourceAdapter(abc.ABC):
    #: Must match the `source` field used in routing.yaml rules.
    name: str

    def __init__(self, on_signal: SignalHandler, *, on_source_event: SourceEventHandler | None = None):
        self.on_signal = on_signal
        #: See `SourceEventHandler`'s own docstring -- `None` (the default)
        #: means this adapter instance has no source-ledger sink wired;
        #: `_emit_source_event` below is then a pure no-op.
        self.on_source_event = on_source_event

    @abc.abstractmethod
    async def start(self) -> None:
        """Begin listening/polling. Push-style adapters (webhook) may no-op here;
        pull-style adapters (Telegram/Discord bots, polling APIs) start their
        listen loop as a background task."""

    async def stop(self) -> None:
        """Override to release connections/tasks started in `start`."""
        return None

    async def _emit_source_event(self, event: SourceEvent) -> None:
        """Best-effort dispatch of one source-ledger row to whatever the
        caller wired via `on_source_event` -- a real no-op (not an error)
        when nothing is wired, so this is always safe for a source adapter
        to call regardless of how it was constructed. Never blocks or
        replaces `on_signal`: a source that also has real, tradeable
        content still calls `on_signal` separately (see e.g.
        `TelegramSource.handle_update`)."""
        if self.on_source_event is not None:
            await self.on_source_event(event)
