# signal-copier — operating rules

Read README.md first. Before changing a subsystem, read the relevant doc
under docs/ (see docs/manifest.yaml for what to read when).

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
1. Investigate relevant code before modifying it — this codebase's own
   module docstrings (app/engine.py, app/lifecycle/manager.py,
   app/capital_allocator.py, app/command_ledger.py, app/writer_lease.py,
   app/qualification.py) are often the most precise spec available;
   read them, don't guess.
2. Do not weaken or delete tests to make a change pass.
3. Do not change public contracts (HTTP routes in app/main.py, the
   Signal/OrderResult/AccountBalance models in app/models.py, the
   BrokerAdapter/SourceAdapter interfaces, exported signal_platform_contracts
   event envelopes) without updating their specification and the callers
   that depend on them (including signal-portfolio-commercial).
4. Run the required verification before declaring work complete: `ruff
   check .`, the CI-scoped `mypy` command (see
   .github/workflows/signal-copier-ci.yml), and the full `pytest -q` suite.
5. Update documentation affected by the change, including README.md and
   the relevant docs/ file.
6. Record significant architectural decisions as ADRs (docs/adr/).
7. Update CHANGELOG.md for externally meaningful changes.
8. Keep docs/state/PROGRESS.md current during long-running work.
9. Ask before destructive or irreversible operations (schema drops, force
   pushes, deleting a broker credential, promoting a writer lease against
   a live account).
10. Git is the authoritative history of code changes.
11. This is financial/trading software: never fabricate a metric or
    status. Use the CapabilityState/CapabilityIntrospection pattern (a
    computed `has_*_capability` property, or an honest `None`/
    "not_tracked"/"insufficient_data") when real data isn't available.
    Fail closed on missing risk inputs — see app/capital_allocator.py and
    app/engine.py's `_reconcile_before_plain_close` for the pattern this
    codebase already follows: reject rather than guess.
