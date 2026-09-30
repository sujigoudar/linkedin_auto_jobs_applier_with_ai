"""Track 17: the provider onboarding SCORECARD -- the user's own words:
"NOT a trading-quality score -- operational readiness... The UI should
tell the operator exactly what remains."

`compute_scorecard` is a pure, read-time computation over
`app.certification`'s own real check rows and `shadow_mode_results` --
there is no stored scorecard row anywhere in `app/db.py`'s schema, by
design (a stored summary is exactly the "driftable summary" this
track's own guardrail forbids: "This must be a derived/computed view,
never a stored, driftable summary -- always freshly computed from the
underlying checks at read time"). Every percentage this module returns
is `count(real PASS rows) / count(real applicable rows)` for a real,
existing set of `certification_checks` rows -- never a fabricated
number. Where there is genuinely nothing yet to compute a percentage
from (no scopes registered for this provider at all, or a category with
zero applicable checks), this returns `None` with a `"insufficient_data"`
note, never `0%` standing in for "unknown" and never `100%` standing in
for "nothing to fail yet" -- both would misrepresent readiness as
either worse or better than the honest truth, which is "not yet
measured".
"""
from __future__ import annotations

from typing import Any

from app.certification import CheckStatus, SCORECARD_CATEGORIES


def _category_completion(checks: list[dict], check_names: tuple) -> dict[str, Any]:
    names = {c.value for c in check_names}
    applicable = [row for row in checks if row["check_name"] in names]
    if not applicable:
        return {"percent": None, "note": "insufficient_data", "applicable_count": 0, "pass_count": 0}
    passed = sum(1 for row in applicable if row["status"] == CheckStatus.PASS.value)
    return {
        "percent": round(100.0 * passed / len(applicable), 1),
        "note": "",
        "applicable_count": len(applicable),
        "pass_count": passed,
    }


def _outstanding_from_checks(checks: list[dict]) -> list[dict]:
    """Every real, currently-not-PASS check row, named specifically --
    "which specific check is still NOT_RUN/FAIL" per this track's own
    brief. Never a vague "some checks incomplete" summary."""
    outstanding = []
    for row in checks:
        if row["status"] != CheckStatus.PASS.value:
            outstanding.append(
                {
                    "scope": {
                        "provider_id": row["provider_id"],
                        "source_id": row["source_id"],
                        "asset_class": row["asset_class"],
                        "account_route": row["account_route"],
                    },
                    "check_name": row["check_name"],
                    "status": row["status"],
                    "detail": row.get("detail") or "",
                }
            )
    return outstanding


def _parser_outstanding_note(checks: list[dict]) -> str | None:
    """The user's own example -- "Outstanding: 2 UNKNOWN message
    formats, 1 stale-exit scenario" -- names SPECIFIC unresolved parser
    formats. This codebase has no such per-format breakdown until Track
    15 (parser tooling) lands (see `app.certification_evidence.
    parser_check_evidence`'s own docstring) -- this returns that honest
    gap as a note, never a fabricated count of "2 UNKNOWN formats" this
    build has no real data for."""
    parser_rows = [row for row in checks if row["check_name"] == "parser"]
    if not parser_rows:
        return None
    if all(row["status"] == CheckStatus.PASS.value for row in parser_rows):
        return None
    return (
        "parser check has no per-message-format UNKNOWN breakdown available in this build -- Track 15 "
        "(parser accuracy tooling) is not yet integrated (see app/certification_evidence.py::parser_check); "
        "record a manual owner attestation with the specific formats you verified instead"
    )


def compute_scorecard(store: Any, provider_id: str) -> dict[str, Any]:
    """Computes the full onboarding scorecard for `provider_id` from
    every `certification_checks` row currently registered for it (across
    every source/asset_class/account_route scope this provider has any
    checks for) plus its `shadow_mode_results` coverage. Returns an
    honest `insufficient_data` shape when NOTHING has been registered
    for this provider yet -- never a fabricated 0%/100% scorecard for a
    provider nobody has started certifying."""
    checks = store.list_certification_checks(provider_id=provider_id)
    sources = store.list_sources(provider_id=provider_id)
    shadow_results = store.list_shadow_mode_results(provider_id=provider_id)

    if not checks:
        return {
            "provider_id": provider_id,
            "categories": {
                name: {"percent": None, "note": "insufficient_data", "applicable_count": 0, "pass_count": 0}
                for name in SCORECARD_CATEGORIES
            },
            "outstanding": [],
            "note": "insufficient_data: no certification checks have been registered for this provider yet "
            "-- call ensure_certification_checks / GET a scope's checks to bootstrap them",
        }

    categories: dict[str, Any] = {
        name: _category_completion(checks, check_names) for name, check_names in SCORECARD_CATEGORIES.items()
    }

    # Shadow tests: NOT a check-name category (shadow mode has no
    # PASS/FAIL of its own -- see app/shadow_mode.py) -- this is real
    # coverage: what fraction of this provider's registered sources have
    # at least one recorded shadow_mode_results row. insufficient_data
    # when the provider has no sources registered at all (nothing to
    # measure coverage over).
    if sources:
        sources_with_shadow = {r["account_id"] for r in shadow_results}  # best-effort: distinct accounts covered
        distinct_signals_shadowed = {r["signal_id"] for r in shadow_results}
        categories["shadow_tests"] = {
            "percent": None if not shadow_results else min(100.0, 100.0 * len(distinct_signals_shadowed) / max(1, len(sources))),
            "note": "insufficient_data: no shadow_mode_results recorded yet for this provider" if not shadow_results else "",
            "applicable_count": len(sources),
            "pass_count": len(sources_with_shadow),
            "shadow_result_count": len(shadow_results),
        }
    else:
        categories["shadow_tests"] = {"percent": None, "note": "insufficient_data", "applicable_count": 0, "pass_count": 0}

    outstanding = _outstanding_from_checks(checks)
    parser_note = _parser_outstanding_note(checks)
    if parser_note:
        outstanding.append({"scope": {"provider_id": provider_id}, "check_name": "parser", "status": "NOTE", "detail": parser_note})

    return {
        "provider_id": provider_id,
        "categories": categories,
        "outstanding": outstanding,
        "note": "",
    }
