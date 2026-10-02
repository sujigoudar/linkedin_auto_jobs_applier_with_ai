# Signal Copier Workflow Contract — October 2, 2026

## Start here

Give Claude Code this directory and paste `CLAUDE_CODE_START_PROMPT.md` into the existing application workspace. The work is an in-place implementation and full non-live validation, not permission to trade or change live risk.

`WORKFLOW_SPECIFICATION.md` is the principal engineering contract. It covers source parsing and mixed assets, unknown providers, one-account routing, backed portfolios/sleeves, capital reservations, modified Kelly, product-specific sizing, margin, entries, fills, protection, exits, pyramiding, runners, ownership overlap, exceptional events, operations, UI, research and release evidence.

`SCENARIO_CATALOG.md` is the readable Given/When/Then inventory. `SCENARIO_CATALOG.json` is the machine-readable version: **292 named requirements in 21 categories, all NOT_RUN against the application.**

`generated/` contains **9,228 finite test-input vectors**: 4,608 restricted admission combinations, 4,500 synthetic linear-sizing combinations, and 120 event-order vectors. The first two have restricted independent oracles. The event-order vectors need concrete application lifecycle/reference-ledger oracles. These counts are not a claim of exhaustive coverage of an unlimited external world or a substitute for expanding the models from the actual application inventory.

## Utilities

Use Python 3.10 or later; utilities use the standard library only. These commands make no broker calls themselves. An imported application adapter must be isolated independently; the runner is not a security sandbox.

```bash
python build_catalog.py
python generate_cases.py
python -m unittest -v test_package_helpers
```

Those commands build/check this specification package only. They do not test the signal-copier repository, its deployed code, or any brokerage account.

The following command **must fail with exit code 2** until a real application test adapter is implemented:

```bash
python run_contracts.py
```

Bind the two restricted planning suites only after implementing an actual adapter invoking the application in non-live disposable fixtures:

```bash
python run_contracts.py --adapter tests.workflow_adapter:run_case --isolated-non-live
```

Adapter input is `{id, suite, inputs}` without expected answers. Return `{actual, implementation_paths, evidence}`. `actual` must contain exact observable fields expected by the restricted suite. Admission is a planning-only fixture: it must make zero broker calls even when admission is allowed. Normalize blocking reasons in the defined deterministic order. Sizing fixtures use integer cents, whole units and all unlisted constraints slack; they do not model real margin/options/stress. Never build an adapter that merely recomputes the expected oracle instead of invoking the actual application. The named scenarios, race models and full integration tests are additional required work.

This command **must fail** for the distributed untested catalog:

```bash
python validate_evidence.py SCENARIO_CATALOG.json --evidence-root .
```

For actual release evidence, fill code/test/evidence references and hashes only from executed application tests. Evidence validation checks structure and file presence, not the truth of broker behavior. The complete release gate also requires domain coverage, real test results, reviewed applicability, invariant/mutation evidence and exact route qualification.

## Evidence in this package

`PACKAGE_CHECK_RESULTS.json` and `PACKAGE_HELPER_TEST_OUTPUT.txt` report only local generator/catalog/helper checks and negative controls. They explicitly leave all application scenarios NOT_RUN. No current code checkout, account, live fill, deployed configuration, or profitability was validated by this package.

Current live limits, account identity and deployed wiring must be read and reconciled in Claude Code. Historical v3.4 numerical defaults are references, not automatic live settings. The latest October 2 SQLite/private HTML-JS/direct-adapter decisions take precedence over older Supabase/Dash/SignalStack architecture language.
