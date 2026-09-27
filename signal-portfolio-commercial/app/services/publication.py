"""Idempotent enqueue and state-transition enforcement for
PublicationIntent, per spec/docs/05_publication_and_copy_lifecycle.md's
"State and idempotency" section: "Same key/same body joins one
operation; same key/different body conflicts... Unknown outcomes cannot
be blindly retried even if a later subscription event changes."
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.publication import PublicationIntent, PublicationState

State = PublicationState


class IdempotencyConflictError(Exception):
    """Raised when a caller reuses an `idempotency_key` with a different
    `body_hash` -- the two are not the same operation and must never be
    silently merged or silently allowed to overwrite one another."""


def enqueue_intent(session: Session, intent: PublicationIntent) -> PublicationIntent:
    """Insert `intent`, or return the existing row if this exact
    (idempotency_key, body_hash) pair was already enqueued -- "same key/
    same body joins one operation." Raises `IdempotencyConflictError`
    for "same key/different body" rather than silently picking one."""
    existing = session.scalars(
        select(PublicationIntent).where(PublicationIntent.idempotency_key == intent.idempotency_key)
    ).first()

    if existing is not None:
        if existing.body_hash == intent.body_hash:
            return existing
        raise IdempotencyConflictError(
            f"idempotency_key {intent.idempotency_key!r} was already used with a different body "
            f"(existing body_hash={existing.body_hash!r}, new body_hash={intent.body_hash!r})"
        )

    session.add(intent)
    session.flush()
    return intent


#: The documented lifecycle (docs/05: "Publication states: DRAFT,
#: ELIGIBLE, QUEUED, SENDING, ACKNOWLEDGED, UNKNOWN, REJECTED,
#: RECONCILING, SUPERSEDED, TERMINAL"). UNKNOWN ("possibly accepted, not
#: confirmed") can only proceed to RECONCILING -- never straight to
#: ACKNOWLEDGED or REJECTED, since that would be exactly the "blindly
#: retried" behavior the spec forbids. TERMINAL and SUPERSEDED are
#: absorbing: nothing transitions out of them.
_VALID_TRANSITIONS: dict[State, frozenset[State]] = {
    State.DRAFT: frozenset({State.ELIGIBLE, State.SUPERSEDED}),
    State.ELIGIBLE: frozenset({State.QUEUED, State.SUPERSEDED}),
    State.QUEUED: frozenset({State.SENDING, State.SUPERSEDED}),
    State.SENDING: frozenset({State.ACKNOWLEDGED, State.UNKNOWN, State.REJECTED}),
    State.ACKNOWLEDGED: frozenset({State.TERMINAL}),
    State.UNKNOWN: frozenset({State.RECONCILING}),
    State.RECONCILING: frozenset({State.ACKNOWLEDGED, State.REJECTED, State.UNKNOWN}),
    State.REJECTED: frozenset({State.TERMINAL}),
    State.SUPERSEDED: frozenset(),
    State.TERMINAL: frozenset(),
}


class InvalidPublicationTransitionError(Exception):
    pass


def transition(session: Session, intent: PublicationIntent, new_state: PublicationState) -> PublicationIntent:
    """Move `intent` to `new_state`, or raise if that's not a state this
    lifecycle allows from its current state -- fail closed: an
    unrecognized/unlisted transition is rejected, not permitted by
    default."""
    allowed = _VALID_TRANSITIONS.get(intent.state, frozenset())
    if new_state not in allowed:
        raise InvalidPublicationTransitionError(
            f"cannot transition a {intent.state.value!r} publication intent to {new_state.value!r} "
            f"(allowed: {sorted(s.value for s in allowed)})"
        )
    intent.state = new_state
    session.flush()
    return intent
