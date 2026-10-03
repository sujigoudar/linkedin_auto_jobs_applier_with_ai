"""PublicationIntent: the durable, idempotent unit of "send this
instruction to an external channel" -- per spec/docs/05_publication_and_copy_lifecycle.md's
canonical sequence ("...canonical model action -> release/rights/audience
check -> durable publication intent -> destination adapter -> external
acknowledgment/readback...") and mirrors
spec/contracts/PublicationIntent.schema.json field-for-field, not
redesigned.

Two uniqueness constraints, both load-bearing, not incidental:

- `idempotency_key` is globally unique -- "Same key/same body joins one
  operation; same key/different body conflicts" (see
  app/services/publication.py's `enqueue_intent`, which enforces the
  "same key/different body" half; the DB constraint is the backstop for
  the half that must never depend on application code remembering to
  check).
- `(channel, external_strategy_id, portfolio_version_id, action,
  revision)` is unique -- docs/02: "Publisher intents have unique
  (channel, external_strategy, portfolio_version, logical_action,
  revision) keys plus body hash." Two different intents can never claim
  to be the same (channel, strategy, portfolio version, action,
  revision) tuple with different content.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import ARRAY, DateTime, Enum, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Environment(str, enum.Enum):
    LOCAL_SIM = "LOCAL_SIM"
    INTEGRATION_ISOLATED = "INTEGRATION_ISOLATED"
    PLATFORM_DEMO = "PLATFORM_DEMO"
    PRIVATE_SHADOW = "PRIVATE_SHADOW"
    COMMERCIAL_LIVE = "COMMERCIAL_LIVE"


class PublicationAction(str, enum.Enum):
    OPEN = "OPEN"
    ADD = "ADD"
    REDUCE = "REDUCE"
    CLOSE = "CLOSE"
    STOP_UPDATE = "STOP_UPDATE"
    TARGET_UPSERT = "TARGET_UPSERT"
    TARGET_REMOVE = "TARGET_REMOVE"
    TARGET_CLEAR = "TARGET_CLEAR"
    ENTRY_CANCEL = "ENTRY_CANCEL"
    STATUS_CORRECTION = "STATUS_CORRECTION"
    STRATEGY_PAUSE = "STRATEGY_PAUSE"


class PublicationSide(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


class QuantityBasis(str, enum.Enum):
    UNITS = "UNITS"
    CONTRACTS = "CONTRACTS"
    ORIGINAL_FRACTION = "ORIGINAL_FRACTION"
    REMAINING_FRACTION = "REMAINING_FRACTION"
    TARGET_POSITION = "TARGET_POSITION"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class PublicationState(str, enum.Enum):
    DRAFT = "DRAFT"
    ELIGIBLE = "ELIGIBLE"
    QUEUED = "QUEUED"
    SENDING = "SENDING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    UNKNOWN = "UNKNOWN"
    REJECTED = "REJECTED"
    RECONCILING = "RECONCILING"
    SUPERSEDED = "SUPERSEDED"
    TERMINAL = "TERMINAL"


class PublicationIntent(Base):
    __tablename__ = "publication_intents"
    __table_args__ = (
        UniqueConstraint(
            "channel", "external_strategy_id", "portfolio_version_id", "action", "revision",
            name="uq_publication_intent_natural_key",
        ),
    )

    intent_id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    #: A database-level RLS backstop (ADR-0001), not this table's own
    #: scoping mechanism -- every query still scopes correctly via an
    #: inner join through `PortfolioVersion` (which IS tenant-scoped),
    #: e.g. app/services/publication_admin.py, customer_alerts.py,
    #: operations_overview.py. Backfilled from that same join path by
    #: alembic/versions/85f9e0e6c123_publication_intent_and_sleeve_tenant_id.py.
    tenant_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    environment: Mapped[Environment] = mapped_column(Enum(Environment, native_enum=False), nullable=False)
    portfolio_version_id: Mapped[str] = mapped_column(String, nullable=False)
    episode_id: Mapped[str] = mapped_column(String, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[PublicationAction] = mapped_column(Enum(PublicationAction, native_enum=False), nullable=False)
    side: Mapped[PublicationSide] = mapped_column(Enum(PublicationSide, native_enum=False), nullable=False)
    channel: Mapped[str] = mapped_column(String, nullable=False)
    external_strategy_id: Mapped[str] = mapped_column(String, nullable=False)
    instrument_id: Mapped[str] = mapped_column(String, nullable=False)
    #: A decimal-string quantity, or NULL for actions with no quantity
    #: meaning (e.g. STOP_UPDATE) -- kept a string, never Float, per this
    #: build's money/quantity precision rule; None mirrors the schema's
    #: `{"type": "null"}` alternative.
    quantity: Mapped[str | None] = mapped_column(String, nullable=True)
    quantity_basis: Mapped[QuantityBasis] = mapped_column(Enum(QuantityBasis, native_enum=False), nullable=False)
    price_basis: Mapped[str] = mapped_column(String, nullable=False)
    policy_hash: Mapped[str] = mapped_column(String, nullable=False)
    audience_snapshot_hash: Mapped[str] = mapped_column(String, nullable=False)
    source_revision_ids: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    rights_grant_ids: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    body_hash: Mapped[str] = mapped_column(String, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[PublicationState] = mapped_column(
        Enum(PublicationState, native_enum=False), nullable=False, default=PublicationState.DRAFT
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
