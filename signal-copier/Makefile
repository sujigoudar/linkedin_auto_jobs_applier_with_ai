# signal-copier developer/agent Makefile
#
# Every target below shells out to the EXACT command CI runs (see
# .github/workflows/signal-copier-ci.yml) so a human or an agent can run
# the same check locally before pushing, without re-typing or
# reinventing it from scratch. This closes TOKEN_COST_AUDIT.md finding
# #1 (deterministic-check instructions re-typed in full, per agent).
#
# Uses `python -m <tool>` throughout (not bare tool names) to match how
# this session runs everything -- works whether the tool is on PATH or
# only importable in the current interpreter/venv.
#
# This Makefile intentionally lives at signal-copier/ and does not touch
# signal-portfolio-commercial/ or the unrelated root-level LinkedIn
# auto-applier app.

.PHONY: lint typecheck typecheck-full test test-fast security audit verify docs-check

# C38 -- ruff, exact CI invocation (see pyproject.toml for the scoped
# ruleset: select = ["F", "B"]).
lint:
	python -m ruff check .

# C38 -- mypy, exact CI-scoped 31-file list (see
# .github/workflows/signal-copier-ci.yml). This is what CI actually
# enforces -- it is deliberately narrower than the whole app/ package.
typecheck:
	python -m mypy app/db.py app/engine.py app/reconciliation.py app/lifecycle/manager.py app/lifecycle/models.py \
	     app/routing.py app/risk.py app/models.py app/main.py app/backtest/simulator.py app/sources/webhook.py \
	     app/sources/whatsapp.py app/sources/sms_twilio.py app/sources/ninjatrader.py \
	     app/sources/text_parser.py app/sources/base.py app/brokers/alpaca.py app/brokers/base.py \
	     app/brokers/paper.py app/brokers/ccxt_broker.py app/brokers/signalstack.py app/brokers/ninjatrader.py \
	     app/config.py app/auth.py app/rate_limit.py app/context/sec_edgar.py app/context/fred.py app/context/fx.py \
	     app/capital_allocator.py app/provider_value.py app/provider_scout.py \
	     app/workflow/reasons.py app/workflow/money.py app/workflow/sizing.py \
	     --follow-imports=silent

# NOT run by CI. The full, non-scoped check across the whole app/
# package -- TOKEN_COST_AUDIT.md finding #7 found 18 real errors here
# that the CI-scoped check above misses (the other ~45 files silently
# accumulate un-typechecked drift). This target exists to make that gap
# visible on demand; it does not change what CI enforces, and running it
# is expected to fail today. Do not "fix" this target to pass -- fixing
# the 18 errors is separate follow-up work, not part of this tooling.
typecheck-full:
	python -m mypy app --ignore-missing-imports

# CI's exact test invocation.
test:
	python -m pytest -q

# No custom pytest markers exist in this repo today (verified: only
# @pytest.mark.asyncio / @pytest.mark.parametrize appear, both structural,
# not speed-tier markers) -- there is no slow/fast split worth inventing
# a marker for, so this aliases to the full run rather than fabricating
# a subset. If a genuinely slow test class emerges later, mark it and
# narrow this target then.
test-fast: test

# C36/C37 -- dependency audit, secret scan, static security lint.
# pip-audit and bandit invocations match CI (see
# .github/workflows/signal-copier-ci.yml for the CVE-2026-49265 ignore
# rationale: tweepy pins oauthlib<4, no compatible release exists yet).
# gitleaks matches the CI job's config file (GITLEAKS_CONFIG); CI itself
# runs it via gitleaks/gitleaks-action, which wraps this same `detect`
# invocation against .gitleaks.toml.
security:
	python -m pip_audit -r requirements.txt --ignore-vuln CVE-2026-49265
	gitleaks detect --config .gitleaks.toml --source . --no-git -v
	python -m bandit -r app -ll

# The full local pre-push check: everything CI enforces, plus the
# security suite, in one command. Mirrors the "before you push, prove
# the change is sound" discipline this session has manually enforced in
# every agent dispatch (TOKEN_COST_AUDIT.md finding #1).
audit: lint typecheck test security

# Common alternate spelling for the same thing.
verify: audit

# Confirms docs/manifest.yaml has zero missing/dangling entries against
# the real docs/ tree -- the same cross-check performed by hand earlier
# in this initiative when a manifest gap was found and fixed.
docs-check:
	python scripts/check_docs_manifest.py
