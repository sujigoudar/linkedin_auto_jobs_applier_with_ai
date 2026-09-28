"""CU-07 "Platform connections" / CU-08 "Connection wizard" -- the real
declare/list/disconnect service backing these screens. See
app/models/platform_connection.py for the full screen contract this
implements a bounded slice of.

`_REVIEWED_PLATFORMS` reuses the exact same reviewed-adapter allowlist
AD-09's publisher destinations and AD-17's integration configurations
already established -- only Collective2/eToro/MetaApi CopyFactory are
real, reviewed adapters in this codebase; every other platform name is
refused (`REVIEWED_PLATFORM_NOT_FOUND`), never silently accepted.
`create_platform_connection` also refuses any environment other than
`local_simulation` (`EXTERNAL_ENVIRONMENT_NOT_AUTHORIZED`) -- no real
OAuth authorization or account-identity readback exists anywhere in
this build.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.platform_connection import PlatformConnection, PlatformConnectionState

#: The same three real, reviewed adapter modules AD-09/AD-17 already
#: established -- never an invented platform name.
_REVIEWED_PLATFORMS: frozenset[str] = frozenset({"collective2", "etoro", "metaapi_copyfactory"})

_ALLOWED_ENVIRONMENT = "local_simulation"


class InvalidPlatformConnectionError(Exception):
    pass


def list_own_platform_connections(session: Session, *, tenant_id: str, user_id: str) -> list[PlatformConnection]:
    return list(
        session.scalars(
            select(PlatformConnection)
            .where(PlatformConnection.tenant_id == tenant_id, PlatformConnection.user_id == user_id)
            .order_by(PlatformConnection.created_at.desc())
        ).all()
    )


def get_own_platform_connection(
    session: Session, connection_id: str, *, tenant_id: str, user_id: str
) -> PlatformConnection | None:
    """Returns None both when the id never existed AND when it belongs
    to another tenant or another user under the same tenant -- a scoped
    not-found, never a 403 that would leak existence."""
    connection = session.get(PlatformConnection, connection_id)
    if connection is None or connection.tenant_id != tenant_id or connection.user_id != user_id:
        return None
    return connection


def create_platform_connection(
    session: Session, *, tenant_id: str, user_id: str, platform: str, environment: str, masked_account_label: str
) -> PlatformConnection:
    if platform not in _REVIEWED_PLATFORMS:
        raise InvalidPlatformConnectionError(f"REVIEWED_PLATFORM_NOT_FOUND: {platform!r} is not a reviewed platform")
    if environment != _ALLOWED_ENVIRONMENT:
        raise InvalidPlatformConnectionError(
            f"EXTERNAL_ENVIRONMENT_NOT_AUTHORIZED: {environment!r} is not authorized "
            f"-- no real OAuth authorization exists in this build"
        )
    if not masked_account_label or not masked_account_label.strip():
        raise InvalidPlatformConnectionError("masked_account_label is required")

    connection = PlatformConnection(
        tenant_id=tenant_id,
        user_id=user_id,
        platform=platform,
        environment=environment,
        masked_account_label=masked_account_label,
    )
    session.add(connection)
    session.flush()
    return connection


def disconnect_platform_connection(session: Session, connection: PlatformConnection) -> PlatformConnection:
    connection.state = PlatformConnectionState.DISCONNECTED
    connection.updated_at = datetime.now(timezone.utc)
    session.flush()
    return connection
