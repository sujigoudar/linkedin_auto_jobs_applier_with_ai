"""Twitter/X OAuth 2.0 USER-CONTEXT signal source (Track 6).

Additive to `app.sources.twitter.TwitterSource` (the existing app-only
`TWITTER_BEARER_TOKEN` filtered-stream adapter, left completely
untouched by this module -- see that adapter's own docstring). That
bearer token can only ever see PUBLIC tweets matching a filtered-stream
rule; it can never see a protected/private account's tweets, regardless
of whether the account owner follows and was approved by that account.
This adapter instead uses an OAuth 2.0 **user-context** access token
(3-legged OAuth 2.0 with PKCE, the account owner authorizes the app in
their own browser -- see `docs/security/TWITTER_USER_CONTEXT.md` for the
full, owner-run authorization procedure) so API calls are made AS the
authenticated person, seeing exactly what their own timeline/following
view would show them, including protected accounts they follow and were
approved by.

tweepy version/API-shape verification (checked against tweepy 4.17.0,
the version actually installed in this repository's environment, not
assumed from memory -- see this module's own tests, none of which touch
a real Twitter/X connection):

  - `tweepy.Client.__init__` takes `bearer_token=`, not a distinct
    "user context" constructor argument. Reading tweepy's own installed
    source (`Client.request`), when a call is made with the default
    `user_auth=False` it ALWAYS sends `Authorization: Bearer
    {self.bearer_token}` -- there is no code-level distinction between
    an app-only project bearer token and an OAuth 2.0 user-context
    access token; both are simply bearer-style strings. tweepy's own
    documented pattern for OAuth 2.0 User Context is therefore: obtain
    the access token via `tweepy.OAuth2UserHandler` (real-browser 3-legged
    PKCE flow), then construct `tweepy.Client(bearer_token=<that access
    token>)` -- which is exactly what `start()` below does.
    `Client.__init__`'s `access_token`/`access_token_secret` parameters
    are for OAuth 1.0a (`user_auth=True` on a per-call basis), a
    DIFFERENT, older auth scheme this adapter does not use.
  - `tweepy.StreamingClient.__init__` takes only `bearer_token` (no
    OAuth2 user-context constructor shape at all) and X's own filtered
    stream is documented as an App-only (project-level bearer token)
    endpoint -- there is no tweepy or X API mechanism to run the
    real-time filtered stream authenticated as a specific user. This is
    the "verify rather than assume" point 4 of the Track 6 brief calls
    for: real-time delivery via user-context auth is NOT available, so
    this adapter is REST-polling-based (`get_users_tweets` on an
    interval), not stream-based. This is a genuine, disclosed limitation
    of the underlying API/library, not a corner this adapter cut.

Deletion observability (also disclosed, not assumed): X's v2
`get_users_tweets` REST endpoint has no delete-notification mechanism at
all -- a tweet that stops appearing between two polls could be a real
delete, a protection-status change, or simply pagination/rate-limit
timing. This adapter therefore never emits a `SourceEventKind.DELETE` --
an honest, disclosed gap (mirroring `TelegramUserSource`'s own disclosed
private-chat delete gap), never a guessed DELETE event.

Edit observability: X does support Tweet edits (a `edit_history_tweet_
ids` field on a v2 Tweet object, `tweet_fields=["edit_history_tweet_ids"]`)
-- the FIRST id in that list is the tweet's original id, and a tweet with
more than one entry is a later revision of that chain. This adapter reads
that field to populate `Signal.original_message_id`/`revision_id` the
same way `TelegramUserSource`/`SlackUserSource` do for their own
providers' edit mechanisms -- reasoned from X API v2's own published
field reference, not empirically re-confirmed against a live edited
tweet in this environment.

Historical import vs. live admission: mirrors `TelegramUserSource.
import_history` -- a separate, read-only path that only ever calls
`save_historical_signal`, never `on_signal`, and never advances the live
checkpoint (`since_id`).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional, Protocol

from app.errors import SignalValidationError
from app.models import AssetClass, Signal, SourceEvent, SourceEventKind
from app.sources.base import SourceAdapter, SourceEventHandler
from app.sources.text_parser import parse_text_signal
from app.collector_registry import CollectorHealth

logger = logging.getLogger(__name__)

#: See `app.sources.telegram_user.PARSER_VERSION`'s own docstring for why
#: this is distinct from `app.sources.twitter.TwitterSource`'s (unset)
#: parser identity -- a different adapter/transport reading a different
#: event shape (REST polling + `edit_history_tweet_ids`), even though
#: both call the same underlying `parse_text_signal` grammar.
PARSER_VERSION = "twitter-user-text-parser-v1"

#: Point 4b of the Track 6 brief: a reasonable polling fallback interval
#: for the REST-only user-context path -- frequent enough to be useful
#: for a trading signal, conservative enough to comfortably stay under
#: X API v2's own per-user-context-token rate limits for
#: `GET /2/users/:id/tweets` (documented as 900 requests/15 min per user
#: at the App level, far looser than this default needs). Overridable by
#: the caller for a specific account's own latency/rate-limit tradeoff.
DEFAULT_POLL_INTERVAL_SECONDS = 30


class CollectorRegistryPort(Protocol):
    """The narrow slice of `app.db.SignalStore` this adapter needs --
    same structural-typing rationale as
    `app.sources.telegram_user.CollectorRegistryPort`/
    `app.sources.slack_user.CollectorRegistryPort`."""

    def get_pull_collector_checkpoint(self, collector_id: str) -> Optional[str]: ...

    def advance_pull_collector_checkpoint(self, collector_id: str, checkpoint: str) -> None: ...

    def update_pull_collector_health(
        self, collector_id: str, health_state: str, *, detail: str | None = None
    ) -> None: ...

    def record_pull_collector_qualification_evidence(
        self, collector_id: str, *, evidence: dict
    ) -> None: ...


def _tweet_field(tweet: Any, name: str, default: Any = None) -> Any:
    """Works against both a real tweepy `Tweet` object (attribute access)
    and a lightweight fake/dict used in tests (mapping access) -- same
    dual-shape tolerance `_message_text` gives Telethon's `Message` in
    `app.sources.telegram_user`."""
    if isinstance(tweet, dict):
        return tweet.get(name, default)
    return getattr(tweet, name, default)


class TwitterUserSource(SourceAdapter):
    name = "twitter_user"

    def __init__(
        self,
        on_signal,
        *,
        collector_id: str,
        target_user_id: str,
        access_token: str | None = None,
        poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS,
        asset_class: AssetClass = AssetClass.CRYPTO,
        on_source_event: SourceEventHandler | None = None,
        registry: CollectorRegistryPort | None = None,
        save_historical_signal: Callable[[Signal], None] | None = None,
    ):
        super().__init__(on_signal, on_source_event=on_source_event)
        self.collector_id = collector_id
        #: The numeric X/Twitter user id being followed -- NOT the
        #: `@handle` (X's own v2 API is id-keyed; a handle-to-id lookup,
        #: `get_user(username=...)`, is a real caller's own one-time
        #: setup step, kept out of this adapter).
        self.target_user_id = str(target_user_id)
        #: The OAuth 2.0 USER-CONTEXT access token -- read from an env
        #: var by the caller (see docs/security/TWITTER_USER_CONTEXT.md),
        #: never accepted as a literal/prompted value from this class.
        self.access_token = access_token
        self.poll_interval_seconds = poll_interval_seconds
        self.asset_class = asset_class
        self.registry = registry
        self.save_historical_signal = save_historical_signal
        self._client: Any = None
        self._poll_task: Any = None
        self._stopped = False
        if registry is None:
            logger.warning(
                "TwitterUserSource collector_id=%s constructed with no registry -- checkpoint/health/"
                "qualification-evidence will NOT be persisted; this is only appropriate for a test",
                collector_id,
            )

    def parse(self, tweet_text: str, analyst: str | None = None) -> Signal:
        return parse_text_signal(tweet_text, source=self.name, asset_class=self.asset_class, analyst=analyst)

    # -- Checkpoint / health helpers (safe no-ops with no registry) -------

    def _get_checkpoint(self) -> str | None:
        if self.registry is None:
            return None
        return self.registry.get_pull_collector_checkpoint(self.collector_id)

    def _advance_checkpoint(self, tweet_id: str) -> None:
        if self.registry is None:
            return
        self.registry.advance_pull_collector_checkpoint(self.collector_id, tweet_id)

    def _set_health(self, state: CollectorHealth, detail: str | None = None) -> None:
        if self.registry is None:
            return
        self.registry.update_pull_collector_health(self.collector_id, state.value, detail=detail)

    def _record_qualified(self, *, tweet_id: str | None, method: str) -> None:
        if self.registry is None:
            return
        self.registry.record_pull_collector_qualification_evidence(
            self.collector_id, evidence={"observed_tweet_id": tweet_id, "method": method}
        )

    # -- Single-tweet handling (directly testable, no tweepy needed) ------

    async def handle_tweet(self, tweet: Any) -> None:
        """`tweet`: a tweepy v2 `Tweet` object (or, in a test, a plain
        dict/fake object exposing the same fields via `_tweet_field`):
        `id`, `text`, `author_id`, and optionally
        `edit_history_tweet_ids` (a list; see this module's own docstring
        for how a >1-length list is treated as an edit revision)."""
        tweet_id = str(_tweet_field(tweet, "id"))
        text = _tweet_field(tweet, "text", "") or ""
        author_id = _tweet_field(tweet, "author_id")
        edit_history = _tweet_field(tweet, "edit_history_tweet_ids") or []
        edit_history = [str(x) for x in edit_history]
        original_id = edit_history[0] if edit_history else tweet_id
        is_edit_revision = bool(edit_history) and edit_history[-1] == tweet_id and len(edit_history) > 1

        if not text:
            self._set_health(
                CollectorHealth.UNSUPPORTED_FORMAT_ENCOUNTERED,
                detail=f"tweet with no text content (tweet_id={tweet_id})",
            )
            await self._emit_source_event(
                SourceEvent(
                    source=self.name,
                    kind=SourceEventKind.ORIGINAL,
                    channel_id=self.target_user_id,
                    message_id=tweet_id,
                    reason="unsupported_format: tweet has no text",
                    raw_source_event=_raw_dict(tweet),
                )
            )
            return

        try:
            signal = self.parse(text, analyst=str(author_id) if author_id else None)
        except SignalValidationError:
            logger.debug("twitter_user tweet did not parse as a signal: %r", text)
            return

        signal.channel_id = self.target_user_id
        signal.message_id = tweet_id
        signal.parser_version = PARSER_VERSION
        if is_edit_revision:
            signal.revision_id = tweet_id
            signal.original_message_id = original_id

        await self.on_signal(signal)
        self._advance_checkpoint(tweet_id)
        self._record_qualified(tweet_id=tweet_id, method="poll_get_users_tweets")
        await self._emit_source_event(
            SourceEvent(
                source=self.name,
                kind=SourceEventKind.EDIT if is_edit_revision else SourceEventKind.ORIGINAL,
                channel_id=self.target_user_id,
                message_id=tweet_id,
                revision_id=tweet_id if is_edit_revision else None,
                original_message_id=original_id if is_edit_revision else None,
                local_receipt_timestamp=signal.received_at,
                signal=signal,
                raw_source_event=_raw_dict(tweet),
            )
        )

    # -- Polling loop (point 4b: the verified real-time fallback) --------

    async def poll_once(self, client: Any) -> int:
        """One polling cycle: fetch every tweet newer than this
        collector's checkpoint (X's own `since_id` parameter -- the
        NATIVE incremental-fetch mechanism `get_users_tweets` already
        provides, used here directly as the live-admission checkpoint,
        same role `_admits_live`'s message-id comparison plays for
        Telegram/Slack) and dispatch each to `handle_tweet` oldest-first
        (X returns newest-first). Returns how many tweets were
        processed. `client`: a tweepy `Client` (or, in a test, a fake
        exposing `get_users_tweets(id=..., since_id=..., **kwargs)` ->
        an object with a `.data` list)."""
        checkpoint = self._get_checkpoint()
        kwargs: dict[str, Any] = {
            "id": self.target_user_id,
            "tweet_fields": ["author_id", "edit_history_tweet_ids"],
            "user_auth": False,
        }
        if checkpoint is not None:
            kwargs["since_id"] = checkpoint
        try:
            response = client.get_users_tweets(**kwargs)
        except Exception as exc:  # pragma: no cover - real network/API errors, shape varies
            self._set_health(CollectorHealth.NO_CHANNEL_ACCESS, detail=str(exc))
            raise
        tweets = list(getattr(response, "data", None) or [])
        for tweet in reversed(tweets):  # oldest first
            await self.handle_tweet(tweet)
        return len(tweets)

    async def _poll_loop(self) -> None:
        while not self._stopped:
            try:
                await self.poll_once(self._client)
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - already recorded via _set_health in poll_once
                logger.exception("twitter_user collector_id=%s poll cycle failed", self.collector_id)
            await asyncio.sleep(self.poll_interval_seconds)

    # -- Historical import -- read-only, NEVER live-routed -----------------

    async def import_history(
        self,
        tweets: list[Any],
        *,
        batch_label: str,
    ) -> dict:
        """Mirrors `TelegramUserSource.import_history`/`SlackUserSource.
        import_history` exactly: every imported `Signal` is tagged
        `import_batch=batch_label` and handed to `save_historical_signal`
        -- NEVER `self.on_signal`. Never advances the live `since_id`
        checkpoint. `tweets` is whatever the caller's real wiring already
        fetched (e.g. a paginated `get_users_tweets` backfill)."""
        imported: list[dict] = []
        skipped: list[dict] = []
        for tweet in tweets:
            tweet_id = str(_tweet_field(tweet, "id"))
            text = _tweet_field(tweet, "text", "") or ""
            if not text:
                skipped.append({"tweet_id": tweet_id, "outcome": "unsupported_format", "detail": "no text"})
                continue
            author_id = _tweet_field(tweet, "author_id")
            try:
                signal = self.parse(text, analyst=str(author_id) if author_id else None)
            except SignalValidationError as exc:
                skipped.append({"tweet_id": tweet_id, "outcome": "no_match", "detail": str(exc)})
                continue
            signal.channel_id = self.target_user_id
            signal.message_id = tweet_id
            signal.parser_version = PARSER_VERSION
            signal.import_batch = batch_label
            if self.save_historical_signal is not None:
                self.save_historical_signal(signal)
            imported.append({"id": signal.id, "tweet_id": tweet_id, "symbol": signal.symbol})
        return {"batch_label": batch_label, "imported": imported, "skipped": skipped}

    # -- Lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        try:
            import tweepy
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("tweepy is not installed; run `pip install tweepy`") from exc

        if not self.access_token:
            self._set_health(CollectorHealth.MISSING_CREDENTIALS, detail="access_token not configured")
            raise RuntimeError(
                "TwitterUserSource requires access_token -- see docs/security/TWITTER_USER_CONTEXT.md; "
                "this adapter never performs the OAuth authorization flow itself"
            )

        # See this module's own docstring: tweepy has no distinct
        # "OAuth2 user context" constructor arg -- the access token
        # obtained via the owner's own browser-based OAuth2UserHandler
        # flow is passed as `bearer_token`, exactly like an app-only
        # token would be; what differs is which token string this is.
        self._client = tweepy.Client(bearer_token=self.access_token)

        try:
            self._client.get_user(id=self.target_user_id, user_auth=False)
        except Exception as exc:  # pragma: no cover - real network/API errors, shape varies
            self._set_health(CollectorHealth.NO_CHANNEL_ACCESS, detail=str(exc))
            raise

        self._stopped = False
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        self._stopped = True
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass


def _raw_dict(tweet: Any) -> dict:
    if isinstance(tweet, dict):
        return tweet
    data = getattr(tweet, "data", None)
    if isinstance(data, dict):
        return data
    return {
        "id": _tweet_field(tweet, "id"),
        "text": _tweet_field(tweet, "text"),
        "author_id": _tweet_field(tweet, "author_id"),
    }
