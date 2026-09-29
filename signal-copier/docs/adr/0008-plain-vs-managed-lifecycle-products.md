# ADR-0008: Plain/unmanaged and full managed-lifecycle as two distinct, explicitly declared position-management products

Status: Accepted
Date: 2026-09-29

## Context

Not every destination account needs, or can support, the same
position-management sophistication. Some accounts simply want a plain
BUY/SELL/CLOSE order copied against this service's own tracked
position. Others need protect-first entry handling, logical
targets/trailing, MAE/MFE tracking, and a serialized close arbiter to
avoid an oversell when a target and a stop could otherwise both fire
against the same shares (`app/lifecycle/manager.py`'s module
docstring). These are not two configurations of one feature — they are
fundamentally different products with different safety guarantees,
different data availability (MAE/MFE, protection coverage), and
different failure modes.

`app/models.py`'s `ManagementRecipe` docstring states this directly:
"managed and unmanaged positions are fundamentally different products
... and that distinction must be a real, auditable field on the
account, not something a screen or a report has to re-derive by
checking `managed_lifecycle` itself every time." Before this, the
distinction existed only as the `managed_lifecycle` boolean, which
callers had to interpret contextually rather than read as an explicit
product declaration.

## Decision

`ManagementRecipe` is a persisted, two-value enum on
`DestinationAccount`/`config_accounts`:

- `FULL_MANAGED_LIFECYCLE` — entries/exits route through
  `PositionLifecycleManager`: protect-first entry, logical
  targets/trailing, MAE/MFE tracking, a serialized close arbiter. The
  full managed-lifecycle safety product.
- `PLAIN_UNMANAGED` — entries/exits are plain BUY/SELL/CLOSE orders
  against this service's own tracked position: no MAE/MFE, no
  protection coverage, no transfer logic.

`DestinationAccount.__post_init__` fills `management_recipe` from the
existing `managed_lifecycle` boolean whenever it isn't given
explicitly, so every constructed account always carries a real,
non-`None` value — never left as an unauditable inference redone ad
hoc by whatever screen or report happens to read it. An explicit value
that disagrees with `managed_lifecycle` is accepted as-is, not
silently overwritten — that disagreement is itself a real
misconfiguration worth surfacing and auditing, not something this
field quietly resolves on the account's behalf.

This is deliberately kept as a two-value declaration matching the
codebase's existing `managed_lifecycle` terminology, not a new tiered
taxonomy — a broker/route-*capability* qualification taxonomy (what an
adapter can actually verify or do, per route — ADR-0006) is a related
but distinct concept: `management_recipe` is the **account's own**
declared management contract, which a qualification taxonomy may
reference, but never replaces.

A plain account's CLOSE carries its own additional safety requirement:
`DestinationAccount.exclusive_writer_qualified` is an explicit,
narrow, off-by-default operator assertion that nothing else writes to
that broker account's position outside this service. `False` (the
default) is a hard block — an unreconciled, unqualified plain close is
rejected outright rather than proceeding against a possibly-stale
local projection — never a config convenience to flip merely to make a
rejection go away.

## Consequences

- Every account-facing report or screen can read `management_recipe`
  directly as the authoritative declaration, instead of re-deriving it
  from `managed_lifecycle` (or worse, from which code path happens to
  handle that account today).
- The two products carry genuinely different data guarantees: MAE/MFE
  (`position_excursions`), `stop_target_events`, and protection-status
  fields are meaningful only for `FULL_MANAGED_LIFECYCLE` accounts;
  querying them for a `PLAIN_UNMANAGED` account is an honest empty
  result, not a bug.
- A plain account's CLOSE has no tracked lifecycle object linking it
  back to its originating entry fill(s) the way a managed close's
  `family_id` does — `orders.family_id` is `NULL` for a plain close, a
  disclosed, honest gap rather than a fabricated link (see
  `app/db.py`'s `orders.family_id` SCHEMA comment).
- `exclusive_writer_qualified` makes an unreconciled plain-account
  close fail closed by default; enabling it is a real operational
  promise the operator makes about that one account, documented in
  README.md's "Exclusive-writer qualification" section, not a
  low-stakes toggle.
