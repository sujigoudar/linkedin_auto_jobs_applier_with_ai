# Dashboard design and implementation package v2

For the existing Signal Copier and Signal Portfolio Commercial applications. Read-only baseline inspected: `2de2d7e2522af2554d438de580a5e0635e23899b`. This package does not modify either application.

1. Open `SCREEN_ATLAS.html` locally to inspect all 66 screen layouts, configuration fields, states, permissions and API bindings. It is an offline design reference, not the application or a source of performance data.
2. Open Claude Code in the genuine current repository. Put this directory under documentation/specification storage, not over app files. Paste `MASTER_PROMPT.md`.
3. Merge the namespaced `.claude/skills/build-signal-dashboards` and reviewer file only after inspecting existing instructions; never replace the whole `.claude` directory. The master prompt works without installing a skill.
4. Follow the 12 phases. Implement real query and command paths against legitimate empty datasets and private drafts immediately. Missing real released portfolios is not a UI blocker.
5. Bind and execute every selected case through the actual apps. `tests/test_pack.py` and the atlas tests validate only this delivery,not production behavior.

Important files: `screens/*.md`, `catalog/forms.json`, `catalog/actions.json`, `catalog/api_contracts.json`, `catalog/subaction_permissions.json`, `catalog/metrics.json`, `catalog/cp_traceability.json`, `catalog/journeys.json`, `catalog/test_cases.json` and `docs/00...15`.

Design counts: 66 screens; 41 forms; 284 individual form fields; 170 visible action bindings; 280 proposed/current API bindings; 1270 test specifications; 9 browser/viewport projects. These are design/testing obligations,not delivered application features or passed application tests.

Existing privacy,execution safety,source rights,merchant/platform/legal and owner release gates remain. No real orders,charges,publication,cloud purchases or production resets are authorized by installing this package.
