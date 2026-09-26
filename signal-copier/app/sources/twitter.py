"""Twitter/X signal source.

Setup:
    pip install tweepy
    1. Get X API v2 access with filtered-stream permission (a paid tier is
       required for this as of X's current API pricing — check their terms,
       since this changes).
    2. Create a rule matching the account(s) you want to copy from, e.g.
       `from:SomeTraderHandle`, via `add_rules` (done once, out of band —
       see tweepy's StreamingClient docs) or pass `rules` here to set them
       on start.
    3. Set TWITTER_BEARER_TOKEN.

tweepy's StreamingClient is synchronous (uses `requests` under the hood),
so it runs in a background thread; matched tweets are handed back to the
asyncio event loop via `asyncio.run_coroutine_threadsafe`.

Message parsing uses the shared free-text parser (app/sources/text_parser.py) —
expect a lot of false negatives/positives on free-text tweets without a
more specific parser tailored to the account's actual posting style.
"""
from __future__ import annotations

import asyncio
import functools
import logging

from app.errors import SignalValidationError
from app.models import AssetClass, Signal
from app.sources.base import SourceAdapter
from app.sources.text_parser import parse_text_signal

logger = logging.getLogger(__name__)

# SIG-05: a bare `stream.delete_rules([rule.id for rule in existing])`
# deleted EVERY rule the bearer token's account had, including ones this
# app never created (another app sharing the same token, or rules the
# owner set up by hand outside this service) -- required_repair calls for
# changing only application-owned source rules. Tagging every rule this
# adapter adds with this constant, and on startup only ever deleting rules
# that carry the SAME tag, is what makes "application-owned" checkable
# rather than assumed.
_MANAGED_RULE_TAG = "signal_copier_managed"


class TwitterSource(SourceAdapter):
    name = "twitter"

    def __init__(
        self,
        on_signal,
        bearer_token: str,
        rules: list[str] | None = None,
        asset_class: AssetClass = AssetClass.CRYPTO,
    ):
        super().__init__(on_signal)
        self.bearer_token = bearer_token
        self.rules = rules or []
        self.asset_class = asset_class
        self._stream = None

    def parse(self, tweet_text: str, analyst: str | None = None) -> Signal:
        return parse_text_signal(tweet_text, source=self.name, asset_class=self.asset_class, analyst=analyst)

    async def start(self) -> None:
        try:
            import tweepy
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("tweepy is not installed; run `pip install tweepy`") from exc

        loop = asyncio.get_running_loop()
        source = self

        class _Stream(tweepy.StreamingClient):
            def on_tweet(self, tweet) -> None:  # noqa: ANN001 - tweepy's own signature
                # tweet_fields=["author_id"] below makes this available -- the
                # numeric account ID, not a @handle (resolving that needs an
                # extra users lookup this doesn't make), but stable and
                # sufficient for app/providers.py's per-analyst overrides.
                try:
                    signal = source.parse(tweet.text, analyst=str(tweet.author_id) if tweet.author_id else None)
                except SignalValidationError:
                    logger.debug("tweet did not parse as a signal: %r", tweet.text)
                    return
                asyncio.run_coroutine_threadsafe(source.on_signal(signal), loop)

        stream = _Stream(self.bearer_token)

        if self.rules:
            existing = stream.get_rules().data or []
            app_owned_rule_ids = [rule.id for rule in existing if getattr(rule, "tag", None) == _MANAGED_RULE_TAG]
            if app_owned_rule_ids:
                stream.delete_rules(app_owned_rule_ids)
            stream.add_rules(
                [tweepy.StreamRule(value=rule, tag=_MANAGED_RULE_TAG) for rule in self.rules]
            )

        self._stream = stream
        # stream.filter() blocks forever running the stream loop, so it's run in a
        # background thread rather than awaited directly (which would hang startup).
        loop.run_in_executor(None, functools.partial(stream.filter, tweet_fields=["author_id"]))

    async def stop(self) -> None:
        if self._stream is not None:
            self._stream.disconnect()
