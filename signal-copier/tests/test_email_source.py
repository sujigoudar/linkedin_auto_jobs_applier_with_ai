"""Tests for EmailSource (app/sources/email_source.py) -- the IMAP-based
email signal source. Mirrors tests/test_telegram_user_source.py's own
pattern (lightweight fake registry, no real IMAP/mailbox connection
needed) plus dedicated coverage for MIME parsing, HTML stripping,
unsupported-format classification, Message-ID-based identity, and the
In-Reply-To/References correction-email relationship."""
from email.message import EmailMessage

import pytest

from app.db import SignalStore
from app.email_collectors import CollectorHealth
from app.models import Side, SourceEventKind
from app.sources.email_source import (
    EmailSource,
    UnsupportedFormatEvent,
    extract_plain_text,
    parse_email_bytes,
    strip_html_to_text,
)


def _make_email(
    *,
    subject: str,
    from_addr: str,
    message_id: str | None = None,
    body: str | None = None,
    html: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    image_only: bool = False,
) -> bytes:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = "watcher@example.com"
    if message_id:
        msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references

    if image_only:
        msg.add_attachment(b"\x89PNG\r\n\x1a\n", maintype="image", subtype="png", filename="alert.png")
    elif body is not None and html is not None:
        msg.set_content(body)
        msg.add_alternative(html, subtype="html")
    elif html is not None:
        msg.set_content(html, subtype="html")
    elif body is not None:
        msg.set_content(body)
    else:
        # A real "subject carries everything" email still has an (empty)
        # text/plain body part -- an EmailMessage with no body part at all
        # (image_only) is the deliberately distinct "no body whatsoever"
        # case exercised separately below.
        msg.set_content("")

    return msg.as_bytes()


class _FakeRegistry:
    def __init__(self, checkpoint=None):
        self.checkpoint = checkpoint
        self.health_calls = []
        self.qualification_calls = []
        self.checkpoint_advances = []

    def get_email_collector_checkpoint(self, collector_id):
        return self.checkpoint

    def advance_email_collector_checkpoint(self, collector_id, uid):
        self.checkpoint = uid
        self.checkpoint_advances.append(uid)

    def update_email_collector_health(self, collector_id, health_state, *, detail=None):
        self.health_calls.append((health_state, detail))

    def record_email_collector_qualification_evidence(self, collector_id, *, evidence, qualified_at=None):
        self.qualification_calls.append(evidence)


def _source(registry=None, sender_allowlist=None, subject_patterns=None):
    received = []
    events = []

    async def on_signal(signal):
        received.append(signal)

    async def on_source_event(event):
        events.append(event)

    src = EmailSource(
        on_signal,
        collector_id="buyalerts",
        imap_host="imap.gmail.com",
        imap_folder="INBOX",
        sender_allowlist=sender_allowlist or ["signals@buyalerts.example"],
        subject_patterns=subject_patterns,
        on_source_event=on_source_event,
        registry=registry,
    )
    return src, received, events


# -- MIME / text extraction --------------------------------------------------


def test_extract_plain_text_prefers_plain_part_when_both_present():
    raw = _make_email(
        subject="s", from_addr="a@example.com", body="BUY BTCUSDT @ 65000", html="<p>BUY BTCUSDT @ 65000</p>"
    )
    parsed = parse_email_bytes(raw)
    assert parsed.text == "BUY BTCUSDT @ 65000"


def test_extract_plain_text_falls_back_to_html_stripped_when_only_html():
    raw = _make_email(subject="s", from_addr="a@example.com", html="<p>BUY <b>BTCUSDT</b></p><p>@ 65000</p>")
    parsed = parse_email_bytes(raw)
    assert "BUY" in parsed.text and "BTCUSDT" in parsed.text and "65000" in parsed.text


def test_strip_html_to_text_drops_script_and_style_and_keeps_block_boundaries():
    html = "<html><head><style>.x{color:red}</style></head><body><p>BUY</p><script>evil()</script><p>BTCUSDT</p></body></html>"
    text = strip_html_to_text(html)
    assert "evil" not in text
    assert "color" not in text
    assert "BUY" in text and "BTCUSDT" in text
    # Block tags keep "BUY" and "BTCUSDT" from gluing into one word --
    # they land on separate lines rather than being concatenated.
    assert "BUYBTCUSDT" not in text
    lines = [line for line in text.split("\n") if line]
    assert "BUY" in lines and "BTCUSDT" in lines


def test_image_only_email_has_no_extractable_text():
    raw = _make_email(subject="Alert", from_addr="a@example.com", image_only=True)
    parsed = parse_email_bytes(raw)
    assert parsed.text == ""
    assert parsed.had_content is True
    assert "image/png" in parsed.content_types


def test_message_id_and_reply_headers_are_read_verbatim():
    raw = _make_email(
        subject="CORRECTION",
        from_addr="a@example.com",
        message_id="<new123@buyalerts.example>",
        body="BUY BTCUSDT @ 64000",
        in_reply_to="<orig123@buyalerts.example>",
        references="<orig123@buyalerts.example>",
    )
    parsed = parse_email_bytes(raw)
    assert parsed.message_id == "<new123@buyalerts.example>"
    assert parsed.in_reply_to == "<orig123@buyalerts.example>"
    assert parsed.references == ["<orig123@buyalerts.example>"]


# -- handle_message: parsing, subject+body combination -----------------------


@pytest.mark.asyncio
async def test_subject_only_signal_is_parsed():
    src, received, _ = _source()
    raw = _make_email(
        subject="BUY BTCUSDT @ 65000",
        from_addr="signals@buyalerts.example",
        message_id="<m1@buyalerts.example>",
        body="thanks for subscribing",
    )
    await src.handle_message(1, raw)
    assert len(received) == 1
    assert received[0].symbol == "BTCUSDT"
    assert received[0].side == Side.BUY
    assert received[0].price == 65000.0


@pytest.mark.asyncio
async def test_signal_spanning_subject_and_body_is_parsed_via_concatenation():
    """Point 2: subject carries the entry, body carries SL/TP -- only
    combining both lets this parse as one instruction."""
    src, received, _ = _source()
    raw = _make_email(
        subject="BUY BTCUSDT",
        from_addr="signals@buyalerts.example",
        message_id="<m2@buyalerts.example>",
        body="@ 65000 SL 64000 TP 67000",
    )
    await src.handle_message(2, raw)
    assert len(received) == 1
    assert received[0].price == 65000.0
    assert received[0].stop_loss == 64000.0
    assert received[0].take_profit == 67000.0


@pytest.mark.asyncio
async def test_message_id_and_channel_id_and_parser_version_are_set():
    src, received, _ = _source()
    raw = _make_email(
        subject="BUY BTCUSDT @ 65000", from_addr="signals@buyalerts.example", message_id="<m3@buyalerts.example>"
    )
    await src.handle_message(3, raw)
    assert received[0].message_id == "<m3@buyalerts.example>"
    assert received[0].channel_id == "imap.gmail.com:INBOX"
    assert received[0].parser_version == "email-text-parser-v1"
    # A genuine new email is never a revision.
    assert received[0].revision_id is None
    assert received[0].original_message_id is None


@pytest.mark.asyncio
async def test_sender_not_on_allowlist_is_silently_ignored():
    src, received, events = _source(sender_allowlist=["signals@buyalerts.example"])
    raw = _make_email(subject="BUY BTCUSDT @ 65000", from_addr="spam@somewhere.example", message_id="<x@x>")
    result = await src.handle_message(1, raw)
    assert received == []
    assert events == []
    assert result is None


@pytest.mark.asyncio
async def test_subject_pattern_filter_narrows_admission_further():
    src, received, _ = _source(subject_patterns=["urgent"])
    raw = _make_email(subject="BUY BTCUSDT @ 65000", from_addr="signals@buyalerts.example", message_id="<x@x>")
    await src.handle_message(1, raw)
    assert received == []


# -- Unsupported format (point 2) --------------------------------------------


@pytest.mark.asyncio
async def test_image_only_email_is_classified_unsupported_not_silently_dropped():
    registry = _FakeRegistry()
    src, received, events = _source(registry)
    raw = _make_email(
        subject="Alert", from_addr="signals@buyalerts.example", message_id="<img1@buyalerts.example>", image_only=True
    )
    result = await src.handle_message(1, raw)
    assert received == []
    assert isinstance(result, UnsupportedFormatEvent)
    assert result.message_id == "<img1@buyalerts.example>"
    assert registry.health_calls[0][0] == CollectorHealth.UNSUPPORTED_FORMAT_ENCOUNTERED.value
    assert len(events) == 1
    assert "unsupported_format" in events[0].reason


@pytest.mark.asyncio
async def test_plain_non_signal_text_is_dropped_not_flagged_unsupported():
    src, received, events = _source()
    raw = _make_email(subject="Weekly newsletter", from_addr="signals@buyalerts.example", body="Thanks for reading!", message_id="<n1@x>")
    result = await src.handle_message(1, raw)
    assert received == []
    assert events == []
    assert result is None


# -- Checkpoint / live admission (point 5b) ----------------------------------


@pytest.mark.asyncio
async def test_uid_at_or_below_checkpoint_is_not_readmitted_live():
    registry = _FakeRegistry(checkpoint=50)
    src, received, _ = _source(registry)
    raw = _make_email(subject="BUY BTCUSDT @ 65000", from_addr="signals@buyalerts.example", message_id="<m@x>")
    await src.handle_message(50, raw)
    assert received == []
    assert registry.checkpoint_advances == []


@pytest.mark.asyncio
async def test_uid_above_checkpoint_is_admitted_and_advances_checkpoint():
    registry = _FakeRegistry(checkpoint=50)
    src, received, _ = _source(registry)
    raw = _make_email(subject="BUY BTCUSDT @ 65000", from_addr="signals@buyalerts.example", message_id="<m@x>")
    await src.handle_message(51, raw)
    assert len(received) == 1
    assert registry.checkpoint_advances == [51]
    assert registry.qualification_calls


@pytest.mark.asyncio
async def test_no_registry_wired_is_a_safe_no_op_for_checkpoint_and_health():
    src, received, _ = _source(registry=None)
    raw = _make_email(subject="BUY BTCUSDT @ 65000", from_addr="signals@buyalerts.example", message_id="<m@x>")
    await src.handle_message(1, raw)
    assert len(received) == 1


# -- Reply/correction-email relationship capture (point 3) -------------------


@pytest.mark.asyncio
async def test_correction_email_is_a_new_signal_with_parent_message_id_captured():
    """A follow-up correction (new Message-ID, In-Reply-To set) is real,
    distinguishable information -- not a silent duplicate, not an
    automatic edit of the original."""
    src, received, events = _source()
    raw = _make_email(
        subject="CORRECTION: BUY BTCUSDT @ 64000",
        from_addr="signals@buyalerts.example",
        message_id="<corrected@buyalerts.example>",
        in_reply_to="<original@buyalerts.example>",
        references="<original@buyalerts.example>",
    )
    await src.handle_message(1, raw)
    assert len(received) == 1
    signal = received[0]
    # Still a fresh, distinguishable signal -- never collapsed into an edit.
    assert signal.message_id == "<corrected@buyalerts.example>"
    assert signal.revision_id is None
    assert signal.original_message_id is None

    assert len(events) == 1
    assert events[0].kind == SourceEventKind.REPLY
    assert events[0].message_id == "<corrected@buyalerts.example>"
    assert events[0].parent_message_id == "<original@buyalerts.example>"


@pytest.mark.asyncio
async def test_ordinary_new_email_emits_original_kind_with_no_parent():
    src, received, events = _source()
    raw = _make_email(
        subject="BUY BTCUSDT @ 65000", from_addr="signals@buyalerts.example", message_id="<fresh@buyalerts.example>"
    )
    await src.handle_message(1, raw)
    assert len(events) == 1
    assert events[0].kind == SourceEventKind.ORIGINAL
    assert events[0].parent_message_id is None


# -- Historical import (point 5a) -- read-only, never live-routed -----------


@pytest.mark.asyncio
async def test_import_history_never_calls_on_signal_and_tags_import_batch():
    saved = []
    received = []

    async def on_signal(signal):
        received.append(signal)

    src = EmailSource(
        on_signal,
        collector_id="buyalerts",
        imap_host="imap.gmail.com",
        imap_folder="INBOX",
        sender_allowlist=["signals@buyalerts.example"],
        save_historical_signal=saved.append,
    )
    messages = [
        (1, _make_email(subject="BUY BTCUSDT @ 65000", from_addr="signals@buyalerts.example", message_id="<h1@x>")),
        (2, _make_email(subject="Weekly digest", from_addr="signals@buyalerts.example", body="just chatting", message_id="<h2@x>")),
        (3, _make_email(subject="Alert", from_addr="signals@buyalerts.example", message_id="<h3@x>", image_only=True)),
        (4, _make_email(subject="BUY ETHUSDT @ 3000", from_addr="spam@somewhere.example", message_id="<h4@x>")),
    ]

    result = await src.import_history(messages, batch_label="email-2024-history")

    assert received == []  # NEVER live-routed
    assert len(saved) == 1
    assert saved[0].import_batch == "email-2024-history"
    assert saved[0].symbol == "BTCUSDT"
    assert len(result["imported"]) == 1
    assert len(result["skipped"]) == 3


@pytest.mark.asyncio
async def test_import_history_never_advances_live_checkpoint():
    registry = _FakeRegistry(checkpoint=None)
    saved = []

    async def on_signal(signal):
        pass

    src = EmailSource(
        on_signal,
        collector_id="buyalerts",
        imap_host="imap.gmail.com",
        imap_folder="INBOX",
        sender_allowlist=["signals@buyalerts.example"],
        registry=registry,
        save_historical_signal=saved.append,
    )
    messages = [(99, _make_email(subject="BUY BTCUSDT @ 65000", from_addr="signals@buyalerts.example", message_id="<h@x>"))]

    await src.import_history(messages, batch_label="backfill")

    assert registry.checkpoint_advances == []
    assert registry.get_email_collector_checkpoint("buyalerts") is None


# -- Message-ID based cross-collector dedup (point 3) ------------------------


@pytest.mark.asyncio
async def test_message_id_dedup_via_signal_store(tmp_path):
    """Two collectors (or one collector's reconnect) observing the SAME
    email (same channel_id/message_id) must canonicalize onto the same
    signal id via SignalStore.find_signal_id_by_provider_identity --
    exactly the Track 5 cross-transport dedup key, reused as-is."""
    store = SignalStore(tmp_path / "test.db")
    src, received, _ = _source()
    raw = _make_email(
        subject="BUY BTCUSDT @ 65000", from_addr="signals@buyalerts.example", message_id="<dup@buyalerts.example>"
    )
    await src.handle_message(1, raw)
    store.save_signal(received[0])

    existing_id = store.find_signal_id_by_provider_identity(
        channel_id="imap.gmail.com:INBOX", message_id="<dup@buyalerts.example>", revision_id=None
    )
    assert existing_id == received[0].id


# -- Gmail API mode is a documented follow-up, not a rushed implementation --


def test_gmail_api_connection_mode_raises_not_implemented():
    async def on_signal(signal):
        pass

    with pytest.raises(NotImplementedError):
        EmailSource(
            on_signal,
            collector_id="x",
            imap_host="unused",
            imap_folder="INBOX",
            sender_allowlist=["a@example.com"],
            connection_mode="gmail_api",
        )
