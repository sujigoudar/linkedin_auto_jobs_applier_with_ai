"""CP-045 "Single publishing mode" enforcement -- see
app/models/publisher_writer_claim.py's docstring for the exact spec
language this implements.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.publisher_writer_claim import PublisherWriterClaim


class WriterAlreadyClaimedError(Exception):
    pass


def claim_writer(session: Session, *, channel: str, external_strategy_id: str, writer_identity: str) -> None:
    """Claim (channel, external_strategy_id) for `writer_identity`.
    Idempotent when the SAME writer re-claims what it already holds
    (a restart/retry is not a conflict); raises when a DIFFERENT writer
    already holds the claim -- this is the "exactly one publication
    authority" rule, and it is checked against the actual current row,
    never assumed from an in-memory cache."""
    existing = session.get(PublisherWriterClaim, (channel, external_strategy_id))
    if existing is not None:
        if existing.writer_identity != writer_identity:
            raise WriterAlreadyClaimedError(
                f"{channel!r}/{external_strategy_id!r} is already claimed by {existing.writer_identity!r}, "
                f"not {writer_identity!r} -- exactly one publication authority is allowed per strategy/channel"
            )
        return

    session.add(
        PublisherWriterClaim(channel=channel, external_strategy_id=external_strategy_id, writer_identity=writer_identity)
    )
    session.flush()


def release_writer(session: Session, *, channel: str, external_strategy_id: str, writer_identity: str) -> None:
    """Release a claim -- only the holder itself may release it; a
    caller that never held the claim cannot free it out from under
    whoever does."""
    existing = session.get(PublisherWriterClaim, (channel, external_strategy_id))
    if existing is None or existing.writer_identity != writer_identity:
        raise WriterAlreadyClaimedError(
            f"{writer_identity!r} does not hold the claim on {channel!r}/{external_strategy_id!r}, and cannot release it"
        )
    session.delete(existing)
    session.flush()
