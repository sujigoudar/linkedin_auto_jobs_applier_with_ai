"""Add backtest_runs.capital_contention_json (B7, real cross-signal
capital-sharing overlay result)

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-29

Nullable, additive column -- see app/db.py's SCHEMA comment on the
`backtest_runs` table and app/backtest/replay.py's `CapitalContentionReport`
for exactly what this column holds (the real, persisted result of
`BacktestEngine.run_with_capital_contention`: `status` is `"not_tracked"`,
never a fabricated ceiling, whenever the run's account has no real
`max_notional_exposure` configured). `NULL` only for a run persisted
before this column existed. A fresh database gets it straight from
app/db.py's SCHEMA string (SignalStore's own bootstrap, not this file --
see 0001's docstring on why that's the real source of truth for every
SignalStore-created database). This revision only matters for someone
provisioning a database purely through the Alembic CLI, or an operator
running `alembic upgrade head` by hand against an existing deployment
whose `alembic_version` table was already stamped at 0009.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("backtest_runs", sa.Column("capital_contention_json", sa.Text(), nullable=True))


def downgrade() -> None:
    raise NotImplementedError("downgrade is not supported -- see 0001's identical precedent")
