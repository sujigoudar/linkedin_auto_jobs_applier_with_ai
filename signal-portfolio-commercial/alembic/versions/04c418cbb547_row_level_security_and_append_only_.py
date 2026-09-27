"""row level security and append only triggers

Revision ID: 04c418cbb547
Revises: f1cbfcc4819c
Create Date: 2026-09-27 18:18:13.392655

Applies the exact same row-level-security and append-only-trigger setup
app/db.py's `enable_row_level_security`/`enforce_append_only` apply in
tests -- reusing that module's connection-level helpers rather than
duplicating their SQL, so a real deployment's schema and the test
schema are provably the same thing, not two hand-maintained copies that
can drift apart. Uses `op.get_bind()` (Alembic's OWN connection, still
inside the transaction the previous "initial schema" revision's table
creation ran in) rather than opening a fresh connection/engine -- a new
connection would run in a separate transaction and couldn't see those
just-created, not-yet-committed tables.

Passes an explicit, frozen snapshot of table names to
`_apply_row_level_security` -- the exact six tables the "initial
schema" revision this one follows actually created -- rather than
`app.db`'s own live `_TENANT_SCOPED_TABLES` constant. That constant has
grown since (e.g. `release_reviews`, added by a later revision); this
revision replays from a point in history before that table existed, so
defaulting to the current, ever-growing tuple would reference a table
that doesn't exist yet. Caught for real: a fresh `alembic upgrade head`
run failed with `UndefinedTable: relation "release_reviews" does not
exist` the moment that table was added to the live constant without
this revision being pinned to its own historical snapshot.

There is no reverse migration for these: an append-only table becoming
mutable, or a tenant table losing row-level security, is never a safe
"undo" -- `downgrade()` deliberately raises rather than silently
weakening a live database's isolation guarantees.
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from app.db import _apply_append_only, _apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = '04c418cbb547'
down_revision: str | None = 'f1cbfcc4819c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Frozen at the tables the "initial schema" revision (f1cbfcc4819c)
#: actually created -- never `app.db._TENANT_SCOPED_TABLES`, which is
#: live code that keeps growing; see this module's own docstring.
_TABLES_AT_THIS_REVISION: tuple[str, ...] = (
    "memberships", "customer_profiles", "ledger_entries", "sleeves", "subscriptions", "portfolio_versions",
)


def upgrade() -> None:
    connection = op.get_bind()
    _apply_row_level_security(connection, _TABLES_AT_THIS_REVISION)
    _apply_append_only(connection)


def downgrade() -> None:
    raise NotImplementedError(
        "downgrading row-level security / append-only enforcement is never a safe automatic "
        "operation -- it would silently weaken a live database's isolation guarantees"
    )
