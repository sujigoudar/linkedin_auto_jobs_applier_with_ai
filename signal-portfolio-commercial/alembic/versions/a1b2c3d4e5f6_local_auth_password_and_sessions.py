"""local auth: password/verification on user_identities, auth_tokens, web_sessions

Revision ID: a1b2c3d4e5f6
Revises: d1a5c7e93f02
Create Date: 2026-09-28 20:00:00.000000

Backs ID-01/ID-02/ID-03 (dashboard_spec/screens/) -- a real, working
local sign-in/sign-up/verify/recover system for THIS deployment, the
same LOCAL_SIM-appropriate pattern signal-copier's own app/auth.py
already uses (password hash + server-side revocable session + a
separate CSRF token), not a Supabase Auth integration (a real
production deployment's own external task -- see
app/services/auth.py's own module docstring). Reuses `pwdlib`
(argon2id), already a real dependency of the paired signal-copier
service for the identical reason.

`user_identities` and the two new tables here are deliberately NOT
added to `app/db.py`'s `_TENANT_SCOPED_TABLES` -- an identity/session
row is looked up BEFORE its tenant/role is known (that's what the
lookup determines), so tenant-scoped row-level security can't gate the
lookup itself; same reasoning `user_identities` itself already follows.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: str | None = 'd1a5c7e93f02'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('user_identities', sa.Column('password_hash', sa.String(), nullable=True))
    op.add_column('user_identities', sa.Column('email_verified_at', sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        'auth_tokens',
        sa.Column('token', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column(
            'token_type',
            sa.Enum('EMAIL_VERIFICATION', 'PASSWORD_RESET', name='authtokentype', native_enum=False),
            nullable=False,
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user_identities.user_id']),
        sa.PrimaryKeyConstraint('token'),
    )
    op.create_index('ix_auth_tokens_user_id', 'auth_tokens', ['user_id'])

    op.create_table(
        'web_sessions',
        sa.Column('session_id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=False),
        sa.Column('role', sa.String(), nullable=False),
        sa.Column('csrf_token', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id', 'user_id'], ['memberships.tenant_id', 'memberships.user_id']),
        sa.PrimaryKeyConstraint('session_id'),
    )


def downgrade() -> None:
    op.drop_table('web_sessions')
    op.drop_index('ix_auth_tokens_user_id', table_name='auth_tokens')
    op.drop_table('auth_tokens')
    op.drop_column('user_identities', 'email_verified_at')
    op.drop_column('user_identities', 'password_hash')
