# signal-portfolio-commercial — operating rules

Read README.md first. Before changing a subsystem, read the relevant doc under docs/ (see docs/manifest.yaml for what to read when).

Architecture: docs/architecture/
Design: docs/design/
Database: docs/database/
Security: docs/security/
Standards: docs/standards/
Testing: docs/testing/
Operations: docs/operations/
Agent procedures: docs/agents/
Current state: docs/state/
Decisions: docs/adr/

Hard rules:
1. Investigate relevant code before modifying it.
2. Do not weaken or delete tests to make a change pass.
3. Do not change public contracts (API routes, event/inbox schemas) without updating their specification.
4. Run the required verification (ruff, mypy, full pytest suite against a real disposable Postgres cluster, alembic upgrade head) before declaring work complete.
5. Update documentation affected by the change.
6. Record significant architectural decisions as ADRs (docs/adr/).
7. Update CHANGELOG.md for externally meaningful changes.
8. Keep docs/state/PROGRESS.md current during long-running work.
9. Ask before destructive or irreversible operations.
10. Git is the authoritative history of code changes.
11. This is multi-tenant financial software: RLS (row-level security) boundaries and the relay_role's read-only/no-command-authority scope are load-bearing — never weaken them without an explicit, reviewed reason.
