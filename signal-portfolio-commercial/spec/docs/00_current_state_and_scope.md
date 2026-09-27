# Scope, actual baseline and the commercialization change

This package is a commercial extension contract, not a new trading engine and not a certification of the existing application. The inspected PR head was `cdbcd1eca999adfaabea76fffda856852f3ecd08` in `sujigoudar/linkedin_auto_jobs_applier_with_ai`, subdirectory `signal-copier/`. Confirm the current revision and preserve later fixes before implementing anything. Only the PR metadata, current dashboard source, economics source and JEV README were inspected for this task. This is not a new end-to-end audit. No broker, payment, source-history or customer workflow was exercised.

The requested new product combines licensed provider/analyst strategies into versioned signal portfolios; researches complementary combinations; sells alert/research access; publishes approved strategies through external copying platforms; and reserves a future broker-managed PAMM/MAM extension. Collective2 is the intended platform name in the request. MAM is the multi-account-management term used here. PAMM and MAM are not interchangeable with ordinary copy subscriptions.

## Existing UI and economics

The current dashboard has owner login, account/provider/analyst/routing controls, account economics, managed-lifecycle coverage, recent signals/orders and local Chart.js/Tabulator assets. Preserve working controls and safe rendering. It is an owner console, not yet the customer storefront described here.

`app/economics.py` calculates average-cost realized P&L from filled order rows. Its docstring explicitly excludes fees and unrealized market marking. Its `completed_trade_win_rate` implementation counts profitable reducing fills, not independently completed trade episodes. Preserve compatibility temporarily, add a correctly named `closing_fill_win_rate`, and deprecate the misleading alias through a versioned API change. Commercial reporting requires actual executions/corrections, fees, product multipliers, cash flows and marks, plus completed-lifecycle metrics and explicit provenance. Do not relabel current gross outputs as net results or a portfolio track record.

The screenshots are visual references only: price panels, equity lines, monitoring, execution timeline and a tablet display. The screenshots' $68-to-$750K and 48-hour claims are unverified. Do not use them as marketing, expected returns, acceptance criteria or evidence that JEV is needed. The unrelated advertisement is out of scope.

## Scope boundary

Previously the application served only the owner's accounts. This request authorizes designing and implementing new customer tenancy, commercialization and managed-account interfaces in non-live environments. It does not itself supply rights to resell a paid provider's signals, customer trading authorization, investment-management registration, a payment processor approval, platform acceptance or custody authority.

No other-account credentials or money are required to implement the deterministic core, customer sandbox, billing test mode, protocol simulators or portfolio research on authorized/synthetic data. Missing rights block the affected publication, not independent coding work. Preserve all previous financial safety requirements, execution repairs and audit cases. An inherited failing risk or fill test is not excused because this work is 'only marketing'.

The implementation is staged: licensed alert products and truthful reports first; approved Collective2 publication next; eToro account/program adapters and other approved copying channels separately; broker-managed PAMM/MAM after its own legal and operational approvals. Customer self-directed copying and discretionary managed accounts have distinct scopes, consent and release records.

## Vocabulary

A provider supplies source instructions. An analyst is a person/model/style within a provider. A sleeve is one qualified analyst/strategy/policy combination. A portfolio version allocates risk/capital across sleeves and cash. A product sells access to one or more portfolio versions. A publication sends a canonical portfolio instruction to a channel. A follower allocation binds a customer's selected account, portfolio, risk limits and version. A venue execution establishes actual financial effect. These identities must not be collapsed into a symbol or an email address.
