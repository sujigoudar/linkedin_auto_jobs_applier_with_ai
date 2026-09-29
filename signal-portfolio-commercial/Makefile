# Makefile for signal-portfolio-commercial
#
# Every target below shells out to the EXACT command this app's CI runs
# (.github/workflows/signal-portfolio-commercial-ci.yml), so a local
# `make <target>` reproduces what CI checks -- no drift between "what
# a human/agent runs locally" and "what actually gates a merge".
#
# Built per docs/agents/TOKEN_COST_AUDIT.md finding #1 (Phase 5/13 of
# the Claude Code development-efficiency initiative): before this file
# existed, every dispatched agent re-typed these commands from scratch,
# each a few hundred tokens of near-identical shell instruction with a
# real risk of getting a flag slightly wrong.
#
# Run from within signal-portfolio-commercial/ (these targets assume
# that as the working directory, matching CI's
# `defaults.run.working-directory: signal-portfolio-commercial`).

.PHONY: lint typecheck test test-migrations security audit verify docs-check

# --- lint --------------------------------------------------------------
# Exact CI invocation. Rule selection (F = pyflakes, B = bugbear) and the
# B008 ignore (FastAPI's Depends()-as-default-argument idiom) live in
# pyproject.toml's [tool.ruff.lint] -- not repeated here as CLI flags so
# this can never drift from what `ruff check .` actually resolves to.
lint:
	ruff check .

# --- typecheck -----------------------------------------------------------
# This app's CI already runs mypy against the FULL app/ package (unlike
# signal-copier, whose CI mypy check is scoped to a hardcoded file list --
# see docs/agents/SKILL_BASELINE.md). One target, no "full" variant needed.
typecheck:
	mypy app --ignore-missing-imports

# --- test ----------------------------------------------------------------
# NOTE: tests/conftest.py starts a real, disposable local PostgreSQL 16
# cluster for the test session (tables are dropped/recreated per test
# function). This is not a mock or SQLite fallback -- some of this
# package's own column types (e.g. RightsGrant.uses' ARRAY(String)) are
# genuinely Postgres-specific. Expect this to take real wall-clock time
# (cluster init + the suite itself) -- that wait is expected, not a hang.
# Requires postgresql-16 server binaries; tests using postgres_engine
# skip (not fail) if genuinely unavailable.
test:
	pytest tests/ -q

# --- test-migrations -------------------------------------------------------
# Separate from `make test`'s own disposable per-test schema (conftest.py
# never runs Alembic -- it uses Base.metadata.create_all directly). This
# starts ANOTHER disposable Postgres 16 cluster on port 55433 and runs the
# actual `alembic upgrade head` a real deployment would run, so a broken
# migration (e.g. the transaction-visibility bug the RLS/append-only
# revision hit during development) fails here instead of only surfacing at
# deploy time. Exact mechanism copied from the CI workflow step
# "Verify Alembic migrations apply cleanly".
test-migrations:
	set -e; \
	export PATH="/usr/lib/postgresql/16/bin:$$PATH"; \
	PGDATA=$$(mktemp -d); \
	PORT=55433; \
	initdb --auth=trust -D "$$PGDATA"; \
	pg_ctl -D "$$PGDATA" -o "-p $$PORT -k $$PGDATA -c listen_addresses=127.0.0.1" -l "$$PGDATA/server.log" -w start; \
	createdb -h 127.0.0.1 -p $$PORT -U "$$(whoami)" commercial_migration_check; \
	ALEMBIC_DATABASE_URL="postgresql+psycopg://$$(whoami)@127.0.0.1:$$PORT/commercial_migration_check" python -m alembic upgrade head; \
	pg_ctl -D "$$PGDATA" -m fast stop; \
	rm -rf "$$PGDATA"

# --- security --------------------------------------------------------------
# NOTE: this app's CI currently has NO pip-audit step at all (a gap noted
# in docs/agents/TOKEN_COST_AUDIT.md / an earlier audit finding). This
# target does NOT close that CI gap -- it's an on-demand local check only.
# Whether to add pip-audit to CI is a separate decision left for human
# sign-off, deliberately not made here.
security:
	pip-audit -r requirements.txt

# --- audit / verify ----------------------------------------------------
# Full local battery, in the same order CI's own steps run in (lint,
# typecheck, test) plus the migration check and the on-demand security
# scan. `verify` is an alias for `audit` -- same target, two names,
# because both are used interchangeably across agent prompts/docs.
audit: lint typecheck test test-migrations security

verify: audit

# --- docs-check ----------------------------------------------------------
# Confirms docs/manifest.yaml's categories/docs/root_docs lists are both
# exhaustive (every real file under the declared paths is listed) and
# accurate (every listed file actually exists) -- the same
# manifest-completeness invariant signal-copier's own docs-check target
# enforces, adapted here for this app's actual schema (categories -> docs
# list + a top-level root_docs list), which is NOT the same shape as
# signal-copier's (documents/paths) -- verified against the real file,
# not assumed.
docs-check:
	python3 scripts/check_docs_manifest.py
