"""add issued_tokens/revoked_tokens -- Bearer-token JWT revocation

Revision ID: c1d2e3f4a5b6
Revises: f4b2c8e0a913
Create Date: 2026-09-29 00:00:00.000000

Backs the real DB-backed denylist described in
app/models/token_revocation.py's own module docstring: a cookie-based
web session was always revocable (`web_sessions` row deletion), but a
Bearer-token JWT (app/services/auth.py's `issue_token`/`decode_token`)
was cryptographically self-contained and stayed valid until natural
expiry, with no way to revoke one early. `revoked_tokens` is that
denylist, keyed by each JWT's own `jti` claim; `issued_tokens` is the
companion table that makes "log out everywhere" possible by recording
each `jti` at issuance.

Neither table is tenant-scoped via `app/db.py`'s row-level-security
policy -- same reasoning `user_identities`/`web_sessions`/`auth_tokens`
already document (a1b2c3d4e5f6): a token is looked up by its own `jti`
before any tenant scope is otherwise established.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c1d2e3f4a5b6'
down_revision: str | None = 'f4b2c8e0a913'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'issued_tokens',
        sa.Column('jti', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('role', sa.String(), nullable=False),
        sa.Column('issued_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('jti'),
    )
    op.create_index('ix_issued_tokens_tenant_id', 'issued_tokens', ['tenant_id'])
    op.create_index('ix_issued_tokens_user_id', 'issued_tokens', ['user_id'])
    op.create_index('ix_issued_tokens_expires_at', 'issued_tokens', ['expires_at'])

    op.create_table(
        'revoked_tokens',
        sa.Column('jti', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=False),
        # Copied from the token's own `exp` claim at revoke time -- lets
        # a future pruning job drop rows for tokens that would already
        # have expired naturally, without re-decoding anything. That
        # pruning job is not created by this migration, only the column
        # it would key off of.
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('jti'),
    )
    op.create_index('ix_revoked_tokens_tenant_id', 'revoked_tokens', ['tenant_id'])
    op.create_index('ix_revoked_tokens_expires_at', 'revoked_tokens', ['expires_at'])


def downgrade() -> None:
    op.drop_index('ix_revoked_tokens_expires_at', table_name='revoked_tokens')
    op.drop_index('ix_revoked_tokens_tenant_id', table_name='revoked_tokens')
    op.drop_table('revoked_tokens')
    op.drop_index('ix_issued_tokens_expires_at', table_name='issued_tokens')
    op.drop_index('ix_issued_tokens_user_id', table_name='issued_tokens')
    op.drop_index('ix_issued_tokens_tenant_id', table_name='issued_tokens')
    op.drop_table('issued_tokens')
