# Release Evidence Report
Generated: 2026-10-02T23:25:48.577411+00:00
Git Commit: `c775d9bd951b02801b9187c62557bef219998e72`

## Critical Statement
**This report does not certify live readiness or profitability.** Software correctness evidence is separate from profitability evidence. This is a technical summary of test execution and code coverage, not a trading authorization or risk certification.

## Execution Summary
Tests executed: 13
- Passed: 13
- Failed: 0
- Skipped: 0
- Error: 0

Scenarios executed: 12
- PASS: 12
- FAIL: 0
- NOT_RUN: 0

## Scenario Status Counts

- NOT_IMPLEMENTED: 280
- TESTED_SIMULATOR: 12

## Integrity Hashes

Code (SHA256 over app/**/*.py):
```
5ef796fcaba56edcf76e8bf6912a5153ec88d7228aeedab6d1e518f038775768
```

Configuration (SHA256 over pyproject.toml + pytest.ini + requirements.txt):
```
cb358f0f957101699f7df3b03e1619b28636fa813e93791eefb5a543f8d981a0
```

Data (SHA256 over generated/*.jsonl + SCENARIO_CATALOG.json):
```
b1887cd16afd1c4e80bd1aa5f4f3c6992d672a7f9f9a798c609657df24c9f590
```

## Finite-Domain Coverage

Test suite (TST-*) scenarios: 2

## Mutation Testing

Mutants killed: 37
Mutants survived: 0

## Financial Risks

- Profitability claims are NOT included in this report — correctness and profitability are separate
- Edge cases in margin calculation at broker-specific decimal precision
- Settlement risk and T+1 availability not modeled in simulator
- FX rates and corporate actions assumed static, not sourced from live feeds
- Correlated asset default scenarios not covered

## External Blockers

- Broker paper/live credentials not in CI — live evidence requires manual broker integration
- Live account restrictions and approval workflows not tested in CI
- Regulatory compliance (PDT, wash sale, tax loss harvesting) requires broker confirmation

## Profitability Evidence

**None.** Profitability claims are kept separate from correctness evidence. This report covers test execution and code correctness only.

---

*This is evidence of test infrastructure execution in an isolated non-live environment. It does not constitute authorization to trade, deploy to live accounts, or make changes to production configuration.*
