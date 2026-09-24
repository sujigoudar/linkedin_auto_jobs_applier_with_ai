"""Twitter/X signal source — STUB.

To implement:
    pip install tweepy
    1. Get X API v2 access (a paid tier is required for filtered/real-time
       stream access as of the current API pricing — check current terms).
    2. Use tweepy's `StreamingClient` filtered to the target account(s), or
       poll `get_users_tweets` on an interval if streaming access isn't
       available.
    3. In `start()`, run the stream/poll loop as a background asyncio task,
       parse each tweet's text with `self.parse()`, and await
       `self.on_signal(signal)` on a successful parse.
    4. `parse()` needs real logic tailored to the specific account's tweet
       format — this is the least reliable source (free text, no schema),
       so expect a lot of false negatives/positives without a fairly
       specific parser or an LLM-based extractor.
"""
from __future__ import annotations

from app.models import Signal
from app.sources.base import SourceAdapter


class TwitterSource(SourceAdapter):
    name = "twitter"

    def __init__(self, on_signal, bearer_token: str | None = None, usernames: list[str] | None = None):
        super().__init__(on_signal)
        self.bearer_token = bearer_token
        self.usernames = usernames or []

    async def start(self) -> None:
        raise NotImplementedError(
            "TwitterSource is a stub. See module docstring: set up X API v2 streaming/polling "
            "access with tweepy, and implement parse()."
        )

    def parse(self, tweet_text: str) -> Signal:
        raise NotImplementedError("Implement tweet text parsing for your target account's format.")
