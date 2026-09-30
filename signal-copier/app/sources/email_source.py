"""Email signal source (Track 7).

There was no email ingestion adapter in this codebase before this track.
This module is the whole of it: `EmailSource`, a `SourceAdapter`
(`app/sources/base.py`) that reads trading-alert email from an IMAP
mailbox with an app-specific password -- the universal case, works for
any provider that supports IMAP (Gmail, Outlook, a dedicated
signal-forwarding mailbox the owner sets up). Only Python's stdlib
`imaplib`/`email` modules are used -- no new dependency.

A Gmail-API/OAuth connection mode is deliberately NOT implemented here --
see `docs/security/EMAIL_COLLECTOR.md`'s "Gmail API (OAuth) -- not yet
implemented" section and `app.email_collectors.ConnectionMode.GMAIL_API`'s
own docstring. `EmailSource.__init__` raises `NotImplementedError`
immediately if constructed with that mode, rather than silently behaving
like IMAP or half-implementing OAuth.

Polling vs. IMAP IDLE (RFC 2177): IMAP IDLE gives push-like near-real-time
notification without polling, but stdlib `imaplib` does not implement it,
and the well-maintained third-party libraries that do (`imapclient`,
`aioimaplib`) are a NEW dependency -- a Level 2 (`agent_decides_flags_in_
report`) change per `.agent/autonomy.yaml`'s own `requirements.txt`
trigger, and pulling one in JUST for IDLE support risked exactly the
"don't over-engineer this if IDLE support turns out to be fragile"
judgment call the Track 7 brief calls out. This adapter instead polls on
a plain interval (`EmailCollector.poll_interval_seconds`, default 60s) --
the honest latency tradeoff is: a signal can sit in the mailbox up to
`poll_interval_seconds` before this adapter observes it. That is
DISCLOSED, not silent -- see this adapter's own `_poll_once` and the
setup doc's own note on tightening it for a latency-sensitive channel (at
the cost of more frequent IMAP `SELECT`/`SEARCH` round trips against the
provider, which some providers rate-limit).

Message parsing (point 2): an email's subject and body are concatenated
("SUBJECT\\n\\nBODY") into ONE string handed to `parse_text_signal` --
many trading-alert emails put the key instruction in the subject line
(e.g. "BUY BTCUSDT @ 65000") and the qualifying detail (SL/TP) in the
body, or vice versa; `text_parser.py`'s own grammar already tolerates
free text around/between fields (`re.DOTALL`), so one combined parse
finds an instruction spanning both without this adapter having to guess
which one the trader put it in. See `extract_plain_text` for how a
plain-text body, an HTML-only body, or neither, is read.

Identity/dedup (point 3): `Signal.message_id` is the email's own native
`Message-ID` header, used directly -- RFC 5322 guarantees it is globally
unique, so (unlike Telegram/Discord's own integer message ids) this
adapter never needs to invent or namespace one. `channel_id` is
`f"{imap_host}:{imap_folder}"` (the mailbox/folder being watched). An
email is not editable after sending, so a genuinely new email always gets
`revision_id=None`/`original_message_id=None` -- there is no live "this
message was edited in place" concept the way there is for a Telegram/
Slack edit. When a provider instead sends a genuine follow-up
correction/retraction email (a NEW `Message-ID`, but carrying a standard
`In-Reply-To`/`References` header pointing at the original), this adapter
captures that relationship on the emitted `SourceEvent`'s own
`parent_message_id` field (kind=REPLY) -- see `handle_message`'s own
docstring for why this is neither a silent duplicate nor an automatic
edit.

Checkpoint-based historical-import vs. live-admission separation (point
5): exactly the same two-code-path split as
`app.sources.telegram_user.TelegramUserSource` -- `import_history` (read-
only, uses a caller-supplied list of already-fetched `(uid, raw_bytes)`
pairs) NEVER calls `on_signal` and NEVER advances
`checkpoint_uid`; only the live poll loop (`_poll_once` ->
`handle_message`) does either, and only for a UID strictly newer than the
persisted checkpoint (`_admits_live`).

IMAP UID caveat (disclosed, not hidden): IMAP UIDs are only guaranteed
monotonically increasing WITHIN one folder for a given `UIDVALIDITY`
epoch (RFC 3501 SS2.3.1.1) -- if the server ever reports a NEW
`UIDVALIDITY` for the folder (a rare but real IMAP event: folder
recreated, provider-side migration), previously stored UIDs are no longer
meaningful. `_poll_once` guards against this WITHIN one running process
(it baselines `UIDVALIDITY` on its first successful poll and fails closed
-- `CollectorHealth.NO_MAILBOX_ACCESS` -- if a later poll ever sees it
change) but this guard is IN-MEMORY ONLY, not a value persisted to the
`email_collectors` registry across a restart: a process that restarts
after the folder was recreated simply re-baselines from whatever
`UIDVALIDITY` it now observes, same as it always has, and its already-
persisted `checkpoint_uid` could in principle now refer to a different
message than before. This is a known, disclosed gap (documented here
rather than silently assumed away) -- closing it fully would mean adding
a persisted `uidvalidity` column and is left for a future pass if it ever
proves to matter in practice (`UIDVALIDITY` changes are rare enough in
real-world IMAP deployments that most providers document them as
happening effectively never for antique mailboxes).
"""
from __future__ import annotations

import asyncio
import email as email_lib
import email.policy
import email.utils
import imaplib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from html.parser import HTMLParser
from typing import Any, Callable, Optional, Protocol

from app.email_collectors import CollectorHealth, ConnectionMode
from app.errors import SignalValidationError
from app.models import AssetClass, Signal, SourceEvent, SourceEventKind
from app.sources.base import SourceAdapter, SourceEventHandler
from app.sources.text_parser import parse_text_signal

logger = logging.getLogger(__name__)

#: This adapter's own exact interpretation implementation -- see
#: `Signal.parser_version`'s own docstring. Distinct from every other
#: source's own version string: this is a different transport reading a
#: different event shape (subject+body concatenation, RFC 5322 headers),
#: even though it calls the SAME underlying `parse_text_signal` grammar.
PARSER_VERSION = "email-text-parser-v1"


class CollectorRegistryPort(Protocol):
    """The narrow slice of `app.db.SignalStore` this adapter actually
    needs -- named here (rather than importing `SignalStore` directly) so
    a test can hand this adapter a lightweight fake with no real
    database. Mirrors `app.sources.telegram_user.CollectorRegistryPort`'s
    own split for the exact same reason."""

    def get_email_collector_checkpoint(self, collector_id: str) -> Optional[int]: ...

    def advance_email_collector_checkpoint(self, collector_id: str, uid: int) -> None: ...

    def update_email_collector_health(
        self, collector_id: str, health_state: str, *, detail: str | None = None
    ) -> None: ...

    def record_email_collector_qualification_evidence(
        self, collector_id: str, *, evidence: dict, qualified_at: datetime | None = None
    ) -> None: ...


@dataclass
class UnsupportedFormatEvent:
    """What `handle_message` returns (in addition to emitting the
    `UNSUPPORTED_FORMAT_ENCOUNTERED` health state) for a real,
    allow-listed message with no extractable text at all -- e.g. an
    image-only alert with no plain-text part and no HTML part that yields
    any readable text after tag-stripping. Point 2: a distinct, VISIBLE
    outcome, never a silent drop indistinguishable from "not a signal"
    plain-text chatter."""

    channel_id: str
    message_id: Optional[str]
    content_types: list[str] = field(default_factory=list)


@dataclass
class ParsedEmail:
    """The result of reading one raw RFC 5322 message -- kept as a plain,
    directly-constructible dataclass so every handler below (and every
    test) can be exercised with a fake `ParsedEmail` and never needs a
    real IMAP connection or even a real `email.message.EmailMessage`."""

    message_id: Optional[str]
    subject: str
    from_addr: Optional[str]
    text: str
    #: `True` when this message had at least one body part (a payload was
    #: present) but none of it yielded extractable text -- distinguishes
    #: "genuinely empty" from "unsupported format" in `handle_message`.
    had_content: bool
    content_types: list[str] = field(default_factory=list)
    in_reply_to: Optional[str] = None
    #: The full `References` header, split into individual Message-IDs,
    #: oldest first -- RFC 5322's own convention. Used, together with
    #: `in_reply_to`, to find the ORIGINAL message this one traces back
    #: to when a provider sends a correction (see this module's own
    #: docstring).
    references: list[str] = field(default_factory=list)
    provider_date: Optional[datetime] = None


class _HTMLTextExtractor(HTMLParser):
    """A minimal, dependency-free HTML-to-text extractor (stdlib
    `html.parser.HTMLParser` only). This repo's own C14 HTML-sanitization
    work (`app/main.py`'s DOMPurify usage) is a FRONTEND/browser-side
    concern for rendering untrusted HTML safely in the admin UI -- it has
    no server-side Python equivalent to reuse here (grepped for
    `DOMPurify`/`bleach`/`strip_html` across this codebase; nothing
    server-side exists). This class is deliberately narrow: it drops
    `<script>`/`<style>` contents entirely, converts block-level tags to a
    newline boundary (so "<p>BUY</p><p>BTCUSDT</p>" doesn't glue into
    "BUYBTCUSDT"), and unescapes entities via `HTMLParser`'s own built-in
    handling -- it does not attempt full HTML5 parsing correctness, only
    "extract the readable words," which is all `parse_text_signal` needs."""

    _BLOCK_TAGS = {
        "p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "blockquote",
    }
    _SKIP_TAGS = {"script", "style", "head", "title"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data:
            self._parts.append(data)

    def get_text(self) -> str:
        raw = "".join(self._parts)
        # Collapse runs of whitespace (but keep single newlines as the
        # block-boundary signal above intended) without over-engineering
        # real HTML layout back into meaningful prose whitespace.
        lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in raw.split("\n")]
        return "\n".join(line for line in lines if line)


def strip_html_to_text(html: str) -> str:
    """Stdlib-only HTML -> readable text (see `_HTMLTextExtractor`'s own
    docstring for why this is a new, narrow extractor rather than reusing
    the codebase's existing (frontend-only) HTML-sanitization work)."""
    extractor = _HTMLTextExtractor()
    extractor.feed(html)
    extractor.close()
    return extractor.get_text()


def _decode_addr_header(value: str | None) -> Optional[str]:
    if not value:
        return None
    _, addr = email.utils.parseaddr(value)
    return addr or None


def _split_references(value: str | None) -> list[str]:
    if not value:
        return []
    return re.findall(r"<[^<>]+>", value) or value.split()


def extract_plain_text(msg: EmailMessage) -> tuple[str, bool, list[str]]:
    """Point 2: extract plain text when available; if only HTML, strip
    tags. Uses the modern `email.message.EmailMessage`/`policy.default`
    API's own `get_body`, which already walks a multipart MIME tree
    correctly (alternative/mixed/related) and hands back the single best
    part for a given preference order -- no ad hoc manual `walk()` needed.

    Returns `(text, had_content, content_types)`:
      - `text`: the best extracted plain text, `""` if none.
      - `had_content`: `True` if this message had ANY body part at all
        (even if it yielded no usable text) -- lets `handle_message`
        distinguish "no body whatsoever" from "had a body, but it was
        e.g. an image with no caption," both of which are `unsupported_
        format_encountered`, but distinctly logged.
      - `content_types`: every body/attachment content-type actually
        observed, for the health-state detail message.
    """
    content_types: list[str] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        content_types.append(part.get_content_type())

    had_content = bool(content_types)

    plain_part = msg.get_body(preferencelist=("plain",))
    if plain_part is not None:
        try:
            text = plain_part.get_content()
        except Exception:  # noqa: BLE001 - a malformed/undecodable part must not crash ingestion
            text = ""
        if isinstance(text, str) and text.strip():
            return text.strip(), had_content, content_types

    html_part = msg.get_body(preferencelist=("html",))
    if html_part is not None:
        try:
            html_content = html_part.get_content()
        except Exception:  # noqa: BLE001
            html_content = ""
        if isinstance(html_content, str) and html_content.strip():
            text = strip_html_to_text(html_content)
            if text.strip():
                return text.strip(), had_content, content_types

    return "", had_content, content_types


def parse_email_bytes(raw: bytes) -> ParsedEmail:
    """Parse one raw RFC 5322 message (as returned by an IMAP `FETCH
    ... RFC822`) into a `ParsedEmail`. Uses `email.policy.default`, the
    modern, RFC-conformant policy (handles MIME multipart, header
    decoding/unfolding, and `get_body`/`get_content` correctly) -- stdlib
    only, no new dependency."""
    msg = email_lib.message_from_bytes(raw, policy=email.policy.default)
    subject = str(msg.get("Subject", "") or "")
    message_id = msg.get("Message-Id") or msg.get("Message-ID")
    message_id = str(message_id).strip() if message_id else None
    from_addr = _decode_addr_header(str(msg.get("From")) if msg.get("From") else None)
    in_reply_to_raw = msg.get("In-Reply-To")
    in_reply_to = _split_references(str(in_reply_to_raw) if in_reply_to_raw else None)
    references_raw = msg.get("References")
    references = _split_references(str(references_raw) if references_raw else None)
    provider_date = None
    date_header = msg.get("Date")
    if date_header:
        try:
            provider_date = email.utils.parsedate_to_datetime(str(date_header))
        except (TypeError, ValueError):
            provider_date = None

    text, had_content, content_types = extract_plain_text(msg)

    return ParsedEmail(
        message_id=message_id,
        subject=subject.strip(),
        from_addr=from_addr,
        text=text,
        had_content=had_content,
        content_types=content_types,
        in_reply_to=(in_reply_to[0] if in_reply_to else None),
        references=references,
        provider_date=provider_date,
    )


class EmailSource(SourceAdapter):
    name = "email"

    def __init__(
        self,
        on_signal,
        *,
        collector_id: str,
        imap_host: str,
        imap_folder: str,
        sender_allowlist: list[str],
        connection_mode: ConnectionMode | str = ConnectionMode.IMAP,
        imap_port: int = 993,
        username: str | None = None,
        password: str | None = None,
        subject_patterns: list[str] | None = None,
        poll_interval_seconds: int = 60,
        asset_class: AssetClass = AssetClass.CRYPTO,
        on_source_event: SourceEventHandler | None = None,
        registry: CollectorRegistryPort | None = None,
        save_historical_signal: Callable[[Signal], None] | None = None,
    ):
        super().__init__(on_signal, on_source_event=on_source_event)
        mode = ConnectionMode(connection_mode) if isinstance(connection_mode, str) else connection_mode
        if mode is not ConnectionMode.IMAP:
            # See this module's own docstring: Gmail API/OAuth is a
            # documented, flagged follow-up, never a half-implementation
            # silently falling back to IMAP semantics it wasn't built for.
            raise NotImplementedError(
                "EmailSource only implements ConnectionMode.IMAP today -- see "
                "docs/security/EMAIL_COLLECTOR.md's 'Gmail API (OAuth) -- not yet implemented' section"
            )
        self.collector_id = collector_id
        self.imap_host = imap_host
        self.imap_port = imap_port
        self.imap_folder = imap_folder
        self.username = username
        self.password = password
        self.sender_allowlist = {addr.strip().lower() for addr in sender_allowlist if addr.strip()}
        self.subject_patterns = [p.lower() for p in (subject_patterns or [])]
        self.poll_interval_seconds = poll_interval_seconds
        self.asset_class = asset_class
        #: `None` is a supported, honest configuration (e.g. in a unit
        #: test) -- checkpoint/health simply aren't persisted anywhere.
        #: A real deployment always passes the real `SignalStore`.
        self.registry = registry
        self.save_historical_signal = save_historical_signal
        self._conn: imaplib.IMAP4_SSL | None = None
        self._poll_task: asyncio.Task | None = None
        self._stopped = False
        #: The folder's own `UIDVALIDITY` as last observed THIS PROCESS
        #: (not persisted across restarts -- see `_poll_once`'s own
        #: docstring for exactly what this protects against and what it
        #: doesn't).
        self._uidvalidity: int | None = None
        if registry is None:
            logger.warning(
                "EmailSource collector_id=%s constructed with no registry -- checkpoint/health/"
                "qualification-evidence will NOT be persisted; this is only appropriate for a test",
                collector_id,
            )

    @property
    def channel_id(self) -> str:
        return f"{self.imap_host}:{self.imap_folder}"

    def parse(self, subject: str, body: str, analyst: str | None = None) -> Signal:
        """Point 2: subject and body are concatenated into one string
        before parsing -- see this module's own docstring for why."""
        combined = f"{subject}\n\n{body}".strip() if body else subject
        return parse_text_signal(combined, source=self.name, asset_class=self.asset_class, analyst=analyst)

    def _is_admitted_sender(self, from_addr: str | None) -> bool:
        if from_addr is None:
            return False
        if from_addr.lower() not in self.sender_allowlist:
            return False
        if self.subject_patterns:
            return True  # subject check happens where the subject is available (handle_message)
        return True

    def _matches_subject_patterns(self, subject: str) -> bool:
        if not self.subject_patterns:
            return True
        lowered = subject.lower()
        return any(pattern in lowered for pattern in self.subject_patterns)

    # -- Checkpoint / health helpers (safe no-ops with no registry) ------

    def _get_checkpoint(self) -> int | None:
        if self.registry is None:
            return None
        return self.registry.get_email_collector_checkpoint(self.collector_id)

    def _advance_checkpoint(self, uid: int) -> None:
        if self.registry is None:
            return
        self.registry.advance_email_collector_checkpoint(self.collector_id, uid)

    def _set_health(self, state: CollectorHealth, detail: str | None = None) -> None:
        if self.registry is None:
            return
        self.registry.update_email_collector_health(self.collector_id, state.value, detail=detail)

    def _record_qualified(self, *, message_id: str | None, uid: int | None) -> None:
        if self.registry is None:
            return
        self.registry.record_email_collector_qualification_evidence(
            self.collector_id,
            evidence={"observed_message_id": message_id, "observed_uid": uid, "method": "live_poll"},
        )

    def _admits_live(self, uid: int) -> bool:
        """Point 5b: on every poll/reconnect, only a UID strictly NEWER
        than this collector's persisted checkpoint is treated as live and
        passed to `on_signal` -- mirrors
        `TelegramUserSource._admits_live`'s own docstring exactly, with
        IMAP UID in place of a Telegram message id."""
        checkpoint = self._get_checkpoint()
        return checkpoint is None or uid > checkpoint

    # -- Live message handling -------------------------------------------

    async def handle_message(self, uid: int, raw: bytes) -> Optional[UnsupportedFormatEvent]:
        """The live per-message handler -- `uid` is this message's own
        IMAP UID within `imap_folder` (see this module's own docstring
        for the `UIDVALIDITY` caveat), `raw` its raw RFC 5322 bytes
        (whatever a real poll's `FETCH ... RFC822` returned, or a test's
        own fixture bytes -- kept out of any real IMAP call so this is
        directly testable with no mailbox connection, same separation as
        `TelegramUserSource.handle_new_message_event`).

        Sender/subject admission (point 4) happens here, BEFORE any
        parsing: a message from a sender not on `sender_allowlist`, or
        (when `subject_patterns` is configured) whose subject matches
        none of them, is silently ignored -- not a signal, not an
        unsupported-format event, not logged as an error. This is
        ordinary mailbox noise this collector was never asked to watch,
        the same as a Telegram collector configured against one chat
        never processing messages from a different chat it isn't
        subscribed to.
        """
        parsed = parse_email_bytes(raw)
        if not self._is_admitted_sender(parsed.from_addr) or not self._matches_subject_patterns(parsed.subject):
            return None

        channel_id = self.channel_id

        has_media = any(not ct.startswith("text/") for ct in parsed.content_types)
        if not parsed.text and has_media:
            # Point 2: a real attachment/image with no usable text
            # anywhere in the body -- the "image-only alert" case the
            # brief names -- is explicitly classified, never silently
            # dropped. (An email with an EMPTY body and no attachment at
            # all -- just a subject line carrying the whole instruction,
            # a real pattern for terse trading-alert emails -- is not
            # this case; it falls through to the ordinary parse below,
            # which reads the subject alone.)
            content_types = parsed.content_types
            self._set_health(
                CollectorHealth.UNSUPPORTED_FORMAT_ENCOUNTERED,
                detail=(
                    f"message_id={parsed.message_id} from {parsed.from_addr} had no extractable text "
                    f"(content_types={content_types})"
                ),
            )
            await self._emit_source_event(
                SourceEvent(
                    source=self.name,
                    kind=SourceEventKind.ORIGINAL,
                    channel_id=channel_id,
                    message_id=parsed.message_id,
                    provider_timestamp=parsed.provider_date,
                    reason="unsupported_format: no extractable plain text or HTML-derived text",
                    raw_source_event={"message_id": parsed.message_id, "content_types": content_types, "uid": uid},
                )
            )
            return UnsupportedFormatEvent(channel_id=channel_id, message_id=parsed.message_id, content_types=content_types)

        try:
            signal = self.parse(parsed.subject, parsed.text, analyst=self._analyst_for(parsed.from_addr))
        except SignalValidationError:
            logger.debug("email message did not parse as a signal: subject=%r", parsed.subject)
            return None
        except Exception as exc:  # noqa: BLE001 - a genuine parser bug, distinct from "not a signal"
            self._set_health(CollectorHealth.PARSER_FAILURE, detail=f"message_id={parsed.message_id}: {exc}")
            logger.exception("email parser failure for message_id=%s", parsed.message_id)
            return None

        signal.channel_id = channel_id
        signal.message_id = parsed.message_id
        signal.parser_version = PARSER_VERSION
        # Point 3: a genuine new email is never a revision -- revision_id/
        # original_message_id stay None. A correction email (In-Reply-To
        # set) is STILL a fresh, distinguishable Signal (own message_id,
        # own parse) -- the relationship to the original is captured
        # SEPARATELY below via SourceEvent.parent_message_id, never by
        # collapsing this into an EDIT of the original.

        if uid is not None and not self._admits_live(uid):
            logger.info(
                "email collector_id=%s uid=%s at/below checkpoint -- not re-admitted live",
                self.collector_id,
                uid,
            )
            return None

        await self.on_signal(signal)
        if uid is not None:
            self._advance_checkpoint(uid)
        self._record_qualified(message_id=parsed.message_id, uid=uid)

        is_correction = bool(parsed.in_reply_to or parsed.references)
        parent_message_id = parsed.in_reply_to or (parsed.references[0] if parsed.references else None)
        await self._emit_source_event(
            SourceEvent(
                source=self.name,
                kind=SourceEventKind.REPLY if is_correction else SourceEventKind.ORIGINAL,
                channel_id=channel_id,
                message_id=parsed.message_id,
                parent_message_id=parent_message_id if is_correction else None,
                provider_timestamp=parsed.provider_date,
                local_receipt_timestamp=signal.received_at,
                signal=signal,
                raw_source_event={
                    "subject": parsed.subject,
                    "text": parsed.text,
                    "message_id": parsed.message_id,
                    "from": parsed.from_addr,
                    "uid": uid,
                    "in_reply_to": parsed.in_reply_to,
                    "references": parsed.references,
                },
            )
        )
        return None

    def _analyst_for(self, from_addr: str | None) -> str | None:
        return from_addr

    # -- Historical import (point 5a) -- read-only, NEVER live-routed ----

    async def import_history(
        self,
        messages: list[tuple[int, bytes]],
        *,
        batch_label: str,
    ) -> dict:
        """Point 5a: an explicit, owner-triggered, READ-ONLY historical
        import -- `messages` is a list of `(uid, raw_rfc822_bytes)` pairs
        the caller's real polling/fetch code already retrieved (kept out
        of this method so it's directly testable with plain fixture
        bytes, no real IMAP connection needed). Mirrors
        `TelegramUserSource.import_history`'s own contract exactly: every
        imported `Signal` is tagged `import_batch=batch_label` and handed
        to `save_historical_signal` -- NEVER to `self.on_signal` -- and
        this never advances `checkpoint_uid`."""
        imported: list[dict] = []
        skipped: list[dict] = []
        for uid, raw in messages:
            parsed = parse_email_bytes(raw)
            if not self._is_admitted_sender(parsed.from_addr) or not self._matches_subject_patterns(parsed.subject):
                skipped.append({"uid": uid, "message_id": parsed.message_id, "outcome": "not_admitted_sender"})
                continue
            has_media = any(not ct.startswith("text/") for ct in parsed.content_types)
            if not parsed.text and has_media:
                skipped.append({"uid": uid, "message_id": parsed.message_id, "outcome": "unsupported_format"})
                continue
            try:
                signal = self.parse(parsed.subject, parsed.text, analyst=self._analyst_for(parsed.from_addr))
            except SignalValidationError as exc:
                skipped.append({"uid": uid, "message_id": parsed.message_id, "outcome": "no_match", "detail": str(exc)})
                continue
            signal.channel_id = self.channel_id
            signal.message_id = parsed.message_id
            signal.parser_version = PARSER_VERSION
            signal.import_batch = batch_label
            if self.save_historical_signal is not None:
                self.save_historical_signal(signal)
            imported.append({"id": signal.id, "uid": uid, "message_id": parsed.message_id, "symbol": signal.symbol})
        return {"batch_label": batch_label, "imported": imported, "skipped": skipped}

    # -- Live polling loop (IMAP) -----------------------------------------

    def _connect(self) -> imaplib.IMAP4_SSL:
        """Blocking IMAP connect+login+select -- run via
        `asyncio.to_thread` from `start`/`_poll_once`, never on the event
        loop directly (imaplib has no async API)."""
        conn = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
        conn.login(self.username, self.password)
        status, _ = conn.select(self.imap_folder)
        if status != "OK":
            raise imaplib.IMAP4.error(f"could not select folder {self.imap_folder!r}")
        return conn

    def _fetch_new_uids(self, conn: imaplib.IMAP4_SSL, since_uid: int | None) -> list[int]:
        criterion = f"UID {since_uid + 1}:*" if since_uid is not None else "ALL"
        status, data = conn.uid("search", None, criterion)
        if status != "OK" or not data or not data[0]:
            return []
        uids = sorted({int(u) for u in data[0].split()})
        # `UID n:*` includes `n` itself if `n` still exists -- filter it
        # back out rather than relying on the server's own edge behavior.
        if since_uid is not None:
            uids = [u for u in uids if u > since_uid]
        return uids

    def _fetch_raw(self, conn: imaplib.IMAP4_SSL, uid: int) -> bytes | None:
        status, data = conn.uid("fetch", str(uid), "(RFC822)")
        if status != "OK" or not data:
            return None
        for part in data:
            if isinstance(part, tuple) and len(part) == 2:
                return part[1]
        return None

    def _fetch_uidvalidity(self, conn: imaplib.IMAP4_SSL) -> int | None:
        status, data = conn.status(self.imap_folder, "(UIDVALIDITY)")
        if status != "OK" or not data or not data[0]:
            return None
        match = re.search(rb"UIDVALIDITY\s+(\d+)", data[0])
        return int(match.group(1)) if match else None

    async def _poll_once(self) -> None:
        """One IMAP poll: connect, check `UIDVALIDITY` hasn't changed out
        from under this collector's checkpoint SINCE THIS PROCESS STARTED
        (see this module's own docstring for the full caveat -- this is
        an in-process guard only, not a value persisted across restarts,
        so it protects against the folder being recreated WHILE this
        collector is running, not against a change that happened while
        the process was down; a restarted process re-baselines
        `_uidvalidity` from whatever the folder reports then, same as it
        always has), then fetch and handle every UID newer than the
        checkpoint."""
        checkpoint = self._get_checkpoint()
        try:
            conn = await asyncio.to_thread(self._connect)
        except (imaplib.IMAP4.error, OSError) as exc:
            self._set_health(CollectorHealth.NO_MAILBOX_ACCESS, detail=str(exc))
            logger.warning("email collector_id=%s could not connect/select mailbox: %s", self.collector_id, exc)
            return
        try:
            uidvalidity = await asyncio.to_thread(self._fetch_uidvalidity, conn)
            if uidvalidity is not None:
                if self._uidvalidity is None:
                    self._uidvalidity = uidvalidity
                elif uidvalidity != self._uidvalidity:
                    self._set_health(
                        CollectorHealth.NO_MAILBOX_ACCESS,
                        detail=(
                            f"folder {self.imap_folder!r} UIDVALIDITY changed ({self._uidvalidity} -> "
                            f"{uidvalidity}) -- this collector's checkpoint is no longer meaningful; "
                            "failing closed rather than reinterpreting a stale UID against the new epoch"
                        ),
                    )
                    logger.error(
                        "email collector_id=%s UIDVALIDITY changed for folder %s -- refusing to poll "
                        "further this process; re-register or restart to re-baseline",
                        self.collector_id,
                        self.imap_folder,
                    )
                    return
            uids = await asyncio.to_thread(self._fetch_new_uids, conn, checkpoint)
            for uid in uids:
                raw = await asyncio.to_thread(self._fetch_raw, conn, uid)
                if raw is None:
                    continue
                await self.handle_message(uid, raw)
        finally:
            try:
                await asyncio.to_thread(conn.logout)
            except Exception:  # noqa: BLE001 - best-effort cleanup only
                pass

    async def _poll_loop(self) -> None:
        while not self._stopped:
            try:
                await self._poll_once()
            except Exception:  # noqa: BLE001 - one bad poll must not kill the loop
                logger.exception("email collector_id=%s poll iteration failed", self.collector_id)
            await asyncio.sleep(self.poll_interval_seconds)

    # -- Lifecycle --------------------------------------------------------

    async def start(self) -> None:
        if not self.username or not self.password:
            self._set_health(CollectorHealth.MISSING_CREDENTIALS, detail="username/password not fully configured")
            raise RuntimeError(
                "EmailSource requires username and password (an IMAP app password) -- see "
                "docs/security/EMAIL_COLLECTOR.md"
            )
        # Fail fast on an unreachable/misconfigured mailbox, same as
        # TelegramUserSource.start's own get_entity check, before
        # scheduling the background poll loop.
        try:
            conn = await asyncio.to_thread(self._connect)
            await asyncio.to_thread(conn.logout)
        except (imaplib.IMAP4.error, OSError) as exc:
            self._set_health(CollectorHealth.NO_MAILBOX_ACCESS, detail=str(exc))
            raise RuntimeError(f"EmailSource could not connect to {self.imap_host}:{self.imap_port}: {exc}") from exc

        self._stopped = False
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        self._stopped = True
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._poll_task = None
