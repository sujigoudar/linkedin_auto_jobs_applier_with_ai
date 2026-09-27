# Dashboard design v2: delivery and validation

This package expands the actual three-route commercial skeleton and the separate private trading console into a screen-by-screen build contract. It is not an implemented customer site.

Design scope: 66 screens, 340 specified panels, 41 forms, 284 field definitions, 170 visible action bindings, 280 current/proposed API bindings, 52 metric definitions, 108 JSON schemas and 36 journeys. CP-091 through CP-114 are mapped using their exact inherited text.

The 1,270 application test specifications expand to 11,430 baseline browser/viewport instances. Field, mode, role and discovered endpoint variations remain additional explicit obligations. These are NOT_RUN application tests, not new passing implementation evidence.

Executed here: 231 design-package tests passed without skips; 1,860 checks of the offline atlas in system Chromium passed, with no JavaScript errors or external requests in the final run. The atlas checks cover all 66 screens and 12 states at desktop and phone sizes, configuration/contract views and controls. They do not establish actual application, PostgreSQL, auth-provider, Stripe, brokerage or deployment correctness.

The initial browser harness and a search-discovery issue were corrected and retested. Earlier evidence is retained. File navigation is blocked by this preparation container; the exact self-contained HTML was rendered through Playwright set_content with external requests blocked.

Do not publish synthetic financial data. Build actual queryable empty states, persisted drafts and authenticated local workflows, then test populated states against isolated fixtures. Missing external access is not a reason to omit independently buildable screens.
