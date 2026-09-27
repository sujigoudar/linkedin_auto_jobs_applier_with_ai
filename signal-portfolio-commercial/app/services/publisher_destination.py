"""AD-09 "Publisher channels and strategies" -- the real save/list
service backing F-PUBLISHER. See dashboard_spec/screens/AD-09.md for
the full screen contract this implements a bounded slice of.

Only `local_simulation` is accepted. "Collective2 tests are not assumed
sandbox" (AD-09's own acceptance text) means `external_test` is not a
safe default either -- and this environment has no real Collective2/
eToro/CopyFactory credentials or sandbox access at all (see
app/services/public_site.py's own compatibility directory), so
`external_test`/`demo`/`live` are all a real, named
EXTERNAL_ENVIRONMENT_NOT_AUTHORIZED blocker, never silently accepted.

"One approved path per external account/strategy; No double publisher"
(AD-09's own field help text) reuses the EXISTING, already-tested
`claim_writer` mechanism (app/services/publisher_writer_claim.py) --
the real single-publication-authority guard this screen must enforce
is the same guard the actual publication pipeline already depends on,
not a separate, parallel check that could drift out of sync with it.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.publisher_destination import (
    Platform,
    PublicationMode,
    PublisherDestination,
    PublisherEnvironment,
)
from app.services.publisher_writer_claim import WriterAlreadyClaimedError, claim_writer

_ONLY_AUTHORIZED_ENVIRONMENT = PublisherEnvironment.LOCAL_SIMULATION


class InvalidPublisherDestinationError(Exception):
    pass


def list_publisher_destinations(session: Session, *, tenant_id: str) -> list[PublisherDestination]:
    return list(
        session.scalars(
            select(PublisherDestination)
            .where(PublisherDestination.tenant_id == tenant_id)
            .order_by(PublisherDestination.created_at.desc())
        ).all()
    )


def create_publisher_destination(
    session: Session,
    *,
    tenant_id: str,
    platform: str,
    external_strategy_id: str,
    environment: str,
    credential_ref: str | None,
    capability_manifest_id: str | None,
    publication_mode: str,
) -> PublisherDestination:
    try:
        platform_enum = Platform(platform)
    except ValueError as exc:
        raise InvalidPublisherDestinationError(f"{platform!r} is not an implemented adapter") from exc
    try:
        environment_enum = PublisherEnvironment(environment)
    except ValueError as exc:
        raise InvalidPublisherDestinationError(f"{environment!r} is not a known mode") from exc
    try:
        publication_mode_enum = PublicationMode(publication_mode)
    except ValueError as exc:
        raise InvalidPublisherDestinationError(f"{publication_mode!r} is not a known publication mode") from exc
    if not external_strategy_id or not external_strategy_id.strip():
        raise InvalidPublisherDestinationError("external_strategy_id is required")

    if environment_enum != _ONLY_AUTHORIZED_ENVIRONMENT:
        raise InvalidPublisherDestinationError(
            f"EXTERNAL_ENVIRONMENT_NOT_AUTHORIZED: no real {platform!r} sandbox or live credentials exist in "
            f"this environment -- only {_ONLY_AUTHORIZED_ENVIRONMENT.value!r} can be saved"
        )

    try:
        claim_writer(
            session, channel=platform_enum.value, external_strategy_id=external_strategy_id, writer_identity=tenant_id
        )
    except WriterAlreadyClaimedError as exc:
        raise InvalidPublisherDestinationError(str(exc)) from exc

    destination = PublisherDestination(
        tenant_id=tenant_id,
        platform=platform_enum,
        external_strategy_id=external_strategy_id,
        environment=environment_enum,
        credential_ref=credential_ref or None,
        capability_manifest_id=capability_manifest_id or None,
        publication_mode=publication_mode_enum,
    )
    session.add(destination)
    session.flush()
    return destination
