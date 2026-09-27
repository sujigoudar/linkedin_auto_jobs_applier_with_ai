# Exact action dispatch and editor modes

The UI uses the immutable action ID in `catalog/actions.json`. It must not infer a backend method by interpreting a button's label at runtime. The handler registry allowlists screen, form, action, phase, object type, role and effect. Shared visual components do not share authority.

A navigation action resolves a known screen and exact selected object. A read action retrieves its named projection. A form action opens the named editor mode; opening it has no effect. A command action uses the exact separately cataloged API and receives a durable operation. No arbitrary reflection, JavaScript handler supplied by the server, or raw SQL is permitted.

The form workflow request envelope is:

- `form_id`: an allowlisted form used by the current screen.
- `ui_action_id`: an action registered for that screen.
- `target_object_id`: required when revising or revoking a specific record; not a caller-selected tenant.
- `expected_revision`: existing record version, or absent for a creation that will receive a server ID.
- `fields`: exactly the form's typed properties. Partial drafts may omit required-ready fields.
- `editor_mode`: the explicit permitted create/edit/revoke/import/verify/test/review mode from the action registry.

Save creates a versioned draft or commits a strictly cosmetic preference. Validation evaluates all current field and domain requirements. Preview receives draft identity/revision and requested action, then returns a version-bound summary and blockers. Confirmation receives only the valid preview identity and idempotency key; it must not accept edited financial fields that bypass the preview. The server rechecks rights, account, tenant, version and authority before effect.

Destructive or externally effectful labels always open a preview first. “Revoke key,” “revoke membership,” “pause entries,” “change plan,” and “request handoff” do not directly mutate just because the user clicked a row action. A canceled dialog has no effect. Password/verification forms are the exception to persisted drafts: they submit to the verified identity workflow without storing raw credentials.

## Source and delivery editor modes

Source setup supports separate `save_draft`, `import_history` and `validate_parser` operations. Importing history creates a read-only resumable job; it cannot enable live admissions. Parser validation creates an assessment linked to exact event/parser versions, not another source event. Activating a source requires its separate reviewed configuration action.

Delivery preferences support separate `save_preferences`, `request_verification` and `send_test` operations. A test message is visibly labeled and cannot be parsed into the live financial ingress. A verified target does not grant trading authority.

## API credentials and staff access

Creating a scoped API key verifies the permitted read/delivery scopes and expiry, reveals the secret once, and stores only the required protected form. Revocation binds the existing key ID and version. Staff invitation, grant update and revocation likewise bind the existing membership or verified invitation identity. Last-owner protection and immediate session invalidation are server rules. An admin role never becomes private-owner authority.

## Private policy editor

TR-12 has subnavigation: Overview, Sizing, Initial stop, Targets, Trailing, Holding and Review. The active editor is one of F-POLICY, F-SIZING-RECIPE, F-STOP-RECIPE, F-TARGET-RECIPE, F-TRAIL-RECIPE or F-HOLD-RECIPE. Switching tabs preserves explicitly saved drafts and warns before discarding unsaved changes.

Recipe drafts have their own immutable version identities. A top-level policy references exact child versions; changing one creates a new proposed policy version. “Save policy draft” applies to the active editor and does not release it. “Preview on signal” resolves the whole proposed policy against the selected original signal and exact product/account facts. “Request review” packages all changed versions and relevant tests. Existing-position policy changes require their separate scoped action, not a side effect of releasing future-entry defaults.

Method/recipe selectors use server-returned compatible schemas and bounds. A user may not supply a new calculation name, multiplier, price basis or arbitrary formula. Missing price, risk, position or entitlement inputs appear as blockers rather than guessed defaults. Cosmetic layout changes cannot conceal these blockers.

## Exports versus execution

“Export broker-bound instructions” generates a scoped document/job. It does not submit those instructions. “Prepare release card” generates evidence and proposed authority for review; it does not promote a site. “Reconcile unknown” requests authoritative readback; it is not a generic retry button. The result screen labels each of these outcomes distinctly.
