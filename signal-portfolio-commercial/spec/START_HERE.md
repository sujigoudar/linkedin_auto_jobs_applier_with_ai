# Start the commercial build

1. Keep the real application checkout open in Claude Code. Extract this directory beside it or under a clearly identified documentation path, not over application files.
2. Paste MASTER_PROMPT.md. It references this package's local paths; state the extraction path once if it is not beneath the checkout.
3. For project skills, review and merge the six named `.claude/skills/` subdirectories into the application's existing project skill directory without overwriting unrelated settings. The main manual skill is `/portfolio-commercial-build`. Merely extracting an external documentation directory does not automatically register it as an application skill.
4. Claude follows the13 phase files, preserving existing code and state. Existing financial audit blockers remain mandatory. The commercial publication, live charges and managed-account modes start disabled.
5. Owner supplies login/consent and the six approval cards only when the actual integration/release needs them. Do not paste passwords, customer documents, platform keys or signed contracts into public source control.

## Package verification (offline, not application verification)

Use a separate Python environment. Install the minimal reference/test requirements from requirements-reference.txt. Run `python -m pytest -q tests` and `python tools/validate_pack.py` from this directory. These tests check reference invariants and package consistency. They DO NOT execute the commercial app or connect to any platform. All catalog application cases are NOT_RUN until implemented in the actual application.

Read docs/12_validation_and_acceptance.md for the full execution/evidence model. A complete set of result-format checks still requires independent validation of runner identity and broker/processor observations. Never use a forged result JSON to claim readiness.

## Deliverables

Actual backend/database/auth/customer UI, portfolio lab and verified metrics; qualified platform adapters; isolated billing and managed-account tests; source/rights/contract registry; required public policies and approved disclosure workflow; complete tests/evidence; inactive deployment/runbooks; and exact owner/platform/legal blockers. This package is a build contract plus executable reference checks, not that deployed application.
