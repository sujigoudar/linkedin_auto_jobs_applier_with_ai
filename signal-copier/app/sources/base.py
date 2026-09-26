"""The interface every signal source must implement.

A source's only job is: get a Signal out of whatever the platform sends
(HTTP webhook, bot message, socket callback, polled API) and call
`self.on_signal(signal)`. Everything downstream — routing, sizing,
execution — is handled by the engine.
"""
from __future__ import annotations

import abc
from typing import Awaitable, Callable

from app.models import Signal

SignalHandler = Callable[[Signal], Awaitable[None]]


class SourceAdapter(abc.ABC):
    #: Must match the `source` field used in routing.yaml rules.
    name: str

    def __init__(self, on_signal: SignalHandler):
        self.on_signal = on_signal

    @abc.abstractmethod
    async def start(self) -> None:
        """Begin listening/polling. Push-style adapters (webhook) may no-op here;
        pull-style adapters (Telegram/Discord bots, polling APIs) start their
        listen loop as a background task."""

    async def stop(self) -> None:
        """Override to release connections/tasks started in `start`."""
        return None
