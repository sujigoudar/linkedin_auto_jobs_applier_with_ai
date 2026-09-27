# Resuming the dashboard build

Read `ops/dashboard_state.json` first. This file is the human-readable
companion.

## What this is

A 66-screen dashboard design package (`signal_platform_dashboard_v2`,
extracted alongside `spec/` in this same directory tree during the
session that started this work) across four interfaces: private
signal-trading console, commercial admin/research console, customer
portal, and public website. Verified by direct inspection: the
package's own claimed counts (66 screens, 41 forms, 284 fields, 170
actions, 1270 test cases) all check out against `catalog/*.json`.

This is a specification/design handoff, not implemented application
code -- exactly like the `spec/` package that drove Phases 00-12 of the
commercial platform itself. The same standing practice applies: build
one real, bounded, tested vertical slice at a time; never claim a
screen is "done" without real DB-backed queries/commands and passing
tests; never fabricate data to make an empty state look populated.

## Where things stand

The first vertical slice (AD-07 Products admin screen + PU-02 public
catalog) is DONE: real model/service/routes/templates/migration, 26
passing tests (16 service-level + 10 HTTP-level), full suite (252
tests)/ruff/mypy all green, and the two most safety-critical invariants
(the stale-revision conflict guard, and the `product_visibility` RLS
policy hiding cross-tenant unpublished drafts) were each load-bearing
verified by temporarily breaking them and confirming the relevant test
failed, then restoring. See `dashboard_state.json`'s `current_slice.
what_was_built` for the exact file list. All other 64 screens are not
started -- pick the next one following the steps below.

## How to pick the next screen

1. Read `signal_platform_dashboard_v2/catalog/screens.json` for the
   full list and `signal_platform_dashboard_v2/catalog/phases.json` for
   the package's own suggested build order.
2. Read that screen's `signal_platform_dashboard_v2/screens/<ID>.md` in
   full -- it has the exact layout, table columns, action bindings,
   form fields, all 12 state obligations, and the panel/component
   contracts.
3. Check what backend already exists (models in `app/models/`, services
   in `app/services/`) before writing anything new -- reuse, don't
   duplicate.
4. Build: model/migration (if needed) -> query/command service -> a
   real HTTP route -> a real (minimal, semantic-HTML) template -> real
   tests covering at minimum the empty state, the create/save/reload
   path if the screen has one, and cross-tenant denial.
5. Load-bearing-verify the one or two most safety-critical checks
   (break it, confirm a test fails, restore) before committing.
6. Update `ops/dashboard_state.json`'s `screens_done` list and this
   file, then commit and push.

## Conventions to keep following

- FastAPI + Jinja2 + the existing Chart.js/Tabulator conventions from
  `signal-copier`'s dashboard -- no new frontend framework.
- Every screen's queries/commands are tenant-scoped via the existing
  RLS mechanism (`app/db.py`'s `set_tenant_scope`) -- never trust a
  caller-supplied tenant_id.
- A cross-tenant object access returns 404 ("scoped not-found"), never
  403 (which would leak that the object exists).
- Real empty states are not a UI blocker to defer past -- "no products
  exist yet" is a valid, testable, real state.
- Draft/save/preview/confirm are genuinely separate operations; preview
  never has a side effect.
