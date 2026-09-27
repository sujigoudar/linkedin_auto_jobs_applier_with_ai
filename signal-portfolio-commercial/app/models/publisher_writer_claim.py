"""CP-045 "Single publishing mode": docs/05's "Never publish the same
strategy through both API and broker/account-transmit concurrently.
Allow exactly one publication authority per external strategy/account,
including aliases." and docs/06's "One publication owner per strategy/
account/channel, enforced by durable claims and actual fencing."

`PublisherWriterClaim` is that durable claim: a real, unique
(channel, external_strategy_id) row naming which writer_identity
currently owns publishing to it. The uniqueness is enforced by the
database (the primary key), not by an application-level check that a
second, concurrent process could race past.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PublisherWriterClaim(Base):
    __tablename__ = "publisher_writer_claims"

    channel: Mapped[str] = mapped_column(String, primary_key=True)
    external_strategy_id: Mapped[str] = mapped_column(String, primary_key=True)
    writer_identity: Mapped[str] = mapped_column(String, nullable=False)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
