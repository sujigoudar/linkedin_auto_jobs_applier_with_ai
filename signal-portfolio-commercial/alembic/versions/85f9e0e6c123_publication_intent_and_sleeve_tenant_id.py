"""backstop tenant_id + RLS on publication_intents / portfolio_version_sleeves

Revision ID: 85f9e0e6c123
Revises: f4a2590f17e2
Create Date: 2026-09-29 12:00:00.000000

Closes a disclosed, lower-severity audit gap: `PublicationIntent`
(app/models/publication.py) and `portfolio_version_sleeves`
(app/models/portfolio_version.py) carry no `tenant_id` column and no
RLS policy of their own, unlike every sibling tenant-scoped table
(ADR-0001). Today both are correctly and consistently scoped via an
inner join through `PortfolioVersion` (which IS RLS-protected)
everywhere they're queried -- app/services/publication_admin.py,
customer_alerts.py, operations_overview.py, portfolio_rights.py -- so
there is no currently-exploitable leak. But that join discipline is an
application-code convention, not a database-enforced one: a future
query that reads either table without going through it would not be
caught.

This migration is purely additive/backstop:

- Adds `tenant_id` (String, matching the exact type/nullability/index
  convention every other tenant-scoped table uses, e.g.
  `PortfolioVersion.tenant_id` itself -- no FK to `tenants`, same as
  `sleeves`, `managed_programs`, `research_runs`, etc.) to both tables,
  nullable at first.
- Backfills every existing row's `tenant_id` from its own
  `PortfolioVersion.tenant_id`, via the exact same join path the
  application code already uses to scope these tables correctly --
  `publication_intents.portfolio_version_id` /
  `portfolio_version_sleeves.portfolio_version_id` ->
  `portfolio_versions.portfolio_version_id` ->
  `portfolio_versions.tenant_id`. `portfolio_version_sleeves` is
  append-only (04c418cbb547's `append_only_guard` trigger), so its own
  backfill UPDATE briefly disables that one trigger and re-enables it
  immediately after -- see the inline comment at that statement.
- Only then makes the column NOT NULL -- the standard safe two-step
  migration for adding a NOT NULL column to a populated table (add
  nullable, backfill, alter to NOT NULL), since altering straight to
  NOT NULL on a populated table would fail against any pre-existing
  row.
- Enables the exact same `tenant_isolation` RLS policy
  (`app/db.py::_apply_row_level_security`) on both tables that every
  other tenant-scoped table gets, and adds them to
  `app.db._TENANT_SCOPED_TABLES` (see that module) so a fresh schema
  built from scratch (tests/conftest.py's `db_session` fixture) matches
  this migration's own effect.

Deliberately does NOT touch `publication_admin.py`/`customer_alerts.py`
/`operations_overview.py`/`portfolio_rights.py`'s existing join-based
query logic -- those queries already scope correctly; this migration
only adds a database-level backstop behind them. `PortfolioVersionSleeve`
inserts (app/services/candidate_comparison.py) and every test fixture
that builds a `PublicationIntent`/`PortfolioVersionSleeve` row directly
are updated in this same change to pass `tenant_id`, now that it is a
required column.

Applies row-level security in the SAME transaction this migration's own
`add_column`/backfill runs in (via `op.get_bind()`, not a fresh engine/
connection) -- same reasoning as `04c418cbb547`, `a5581417bf7f`, etc.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

from app.db import _apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = '85f9e0e6c123'
down_revision: str | None = 'f4a2590f17e2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Only the two tables THIS revision adds tenant_id/RLS to -- never the
#: live, ever-growing `app.db._TENANT_SCOPED_TABLES`, per
#: `04c418cbb547`'s own precedent on why that breaks a fresh `alembic
#: upgrade head` replay.
_TABLES_AT_THIS_REVISION: tuple[str, ...] = ("publication_intents", "portfolio_version_sleeves")


def upgrade() -> None:
    connection = op.get_bind()

    # Step 1: add the column nullable -- a populated table cannot take a
    # NOT NULL column in one step.
    op.add_column('publication_intents', sa.Column('tenant_id', sa.String(), nullable=True))
    op.add_column('portfolio_version_sleeves', sa.Column('tenant_id', sa.String(), nullable=True))

    # Step 2: backfill from PortfolioVersion.tenant_id via the exact
    # join path the application already uses to scope these tables.
    connection.execute(
        text(
            "UPDATE publication_intents "
            "SET tenant_id = portfolio_versions.tenant_id "
            "FROM portfolio_versions "
            "WHERE portfolio_versions.portfolio_version_id = publication_intents.portfolio_version_id"
        )
    )
    # `portfolio_version_sleeves` is append-only (04c418cbb547's
    # `append_only_guard` trigger rejects any UPDATE, full stop, with no
    # exception for this migration) -- disable that ONE trigger for the
    # length of this one backfill UPDATE, then immediately re-enable it.
    # This is not a weakening of the append-only guarantee: no
    # application code runs between disable and re-enable, this
    # transaction either commits with the trigger back on or rolls back
    # entirely (DDL is transactional here), and the backfill itself is a
    # one-time historical correction, not an ordinary write this guard is
    # meant to catch.
    connection.execute(text("ALTER TABLE portfolio_version_sleeves DISABLE TRIGGER append_only_guard"))
    connection.execute(
        text(
            "UPDATE portfolio_version_sleeves "
            "SET tenant_id = portfolio_versions.tenant_id "
            "FROM portfolio_versions "
            "WHERE portfolio_versions.portfolio_version_id = portfolio_version_sleeves.portfolio_version_id"
        )
    )
    connection.execute(text("ALTER TABLE portfolio_version_sleeves ENABLE TRIGGER append_only_guard"))

    # Step 3: now that every existing row has a real value, enforce
    # NOT NULL going forward.
    op.alter_column('publication_intents', 'tenant_id', existing_type=sa.String(), nullable=False)
    op.alter_column('portfolio_version_sleeves', 'tenant_id', existing_type=sa.String(), nullable=False)

    op.create_index(
        op.f('ix_publication_intents_tenant_id'), 'publication_intents', ['tenant_id'], unique=False
    )
    op.create_index(
        op.f('ix_portfolio_version_sleeves_tenant_id'), 'portfolio_version_sleeves', ['tenant_id'], unique=False
    )

    # Step 4: the same RLS backstop every other tenant-scoped table gets.
    _apply_row_level_security(connection, _TABLES_AT_THIS_REVISION)


def downgrade() -> None:
    raise NotImplementedError(
        "downgrading row-level security / a tenant_id backstop is never a safe automatic "
        "operation -- it would silently weaken a live database's isolation guarantees "
        "(same policy as 04c418cbb547's own downgrade())"
    )
