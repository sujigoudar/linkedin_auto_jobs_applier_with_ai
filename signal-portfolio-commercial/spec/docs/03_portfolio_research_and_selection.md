# Signal portfolio engine

## Units of combination

Do not combine raw buy messages into a single unbounded feed. Normalize each licensed component into a sleeve with provider, analyst, strategy/horizon, asset/product, parser version, execution policy, cost model, capacity, risk unit and history origin. Maintain its independent virtual book even when the account holds the same instrument through several sleeves. An opposite signal does not automatically liquidate another sleeve. Net only at a deliberately approved execution layer while preserving both owners and conservative stress exposure.

A portfolio version contains an ordered eligible-universe snapshot, selected sleeves, basis-point capital weights plus cash, risk-policy references, asset/channel allowlists, portfolio/cluster concentration limits, target volatility/stress constraints, maximum subscriber capacity, research cutoff, report IDs, consent/disclosure version, deployment artifact and released selector envelope. Every component membership or weight change creates a new immutable version. Historical membership is never overwritten.

## First product templates

Create these DRAFT, NOT_OFFERED templates; attach real providers only after rights and data qualification:

P01 US Equity Intraday: compatible intraday equity sleeves, no inherited overnight permission; portfolio-level order deadlines preserved.
P02 Equity Swing: compatible swing sleeves with explicitly released overnight/gap-risk policy.
P03 FX Diversified: licensed trend, mean-reversion or other demonstrably complementary FX sleeves, exact broker/product units and rollover costs.
P04 Crypto Diversified: compatible spot/derivative sleeves separated by product; never label market-neutral unless measured exposures and stress evidence support that claim.
P05 Multi-Asset Allocation: qualified constituent portfolios plus cash, only on channels/accounts able to implement the actual composition. No synthetic promise that one account supports every asset.

Do not force three different named providers into a portfolio merely for branding. Two analysts from one upstream source may be more dependent than their names suggest. A portfolio may remain unpublished because no credible complementary set exists.

## Data admission

Use complete authorized history ranges, retaining original message revisions, timestamps and all candidate signals, including rejected, unfilled, canceled, open, revised and failed trades. Preserve provider publication, local receipt and information-availability times separately. For every point, distinguish a known flat sleeve from an unavailable/missing sleeve. Only known-flat returns can be zero; missing history must not improve covariance artificially. Align different session calendars on a declared common valuation clock, including stale-market policies and cash/funding effects.

Record actual, forward-paper, platform-model and reconstructed histories separately. No 'all actual' classification merely because an input price came from a real feed. Portfolio replay must simulate shared capital, outstanding orders, margin, late entry, stops, target/stop replacement latency, fees, spreads, borrowing/funding, currency and platform subscriptions. Replay combined decisions rather than summing independent sleeves that each assumed the whole account was available.

## Complementarity measures

Compute eligible pairwise and cluster statistics on common usable periods: return correlation with count/interval, downside correlation, overlapping drawdown intervals, simultaneous direction/underlying/sector/factor exposure, concurrent capital demand, holding-time overlap, turnover, liquidity usage, spread sensitivity and performance by predeclared regime. Flag provider cross-posts as shared provenance rather than fabricated independent opportunities. Retain missingness and confidence intervals. Low historical correlation is not proof of a hedge or stable independence.

Use data cutoffs at the actual decision time. Regime labels must be available causally; never label a crash regime using future peak-to-trough information and then 'predict' it. Portfolio comparisons include adverse co-loss scenarios even when ordinary covariance is low.

## Candidate family and finite evaluation

Research defaults, editable only through a new research-config version: 2–5 sleeves per candidate; no more than 12 eligible sleeves in one approved exhaustive run; 15% fixed cash during initial comparisons; maximum 35% capital weight per sleeve and 50% per correlated cluster; no borrowed capital by default in research. These are deliberately conservative study settings, not changes to the owner's existing financial limits or a live recommendation. Lower limits from an actual account/product always prevail. Two-sleeve candidates may retain additional cash because 2×35% cannot invest 85%.

Enumerate every subset within the approved run universe and size bound, and every declared recipe: equal sleeve capital, inverse-volatility risk proxy, hierarchical risk parity, and constrained minimum-CVaR. Include existing single-sleeve strategies and current production portfolio as benchmarks. Each recipe implements the same cash/concentration/cost/capacity constraints or is recorded INFEASIBLE; do not silently drop awkward subsets. Equal weights are a baseline, not evidence of equal monetary risk. If the approved run would exceed the compute budget, do not randomly sample and call it exhaustive: schedule all deterministic shards or obtain approval for a separately named smaller universe. Every candidate receives a terminal outcome and evidence reference.

Optimization can use the existing SciPy/scikit-learn/skfolio components. Do not install Optuna/River/FinRL. Pin method, hyperparameters, seed, solver tolerances and deterministic tie breaks. Use shrinkage/covariance handling appropriate to sample size, but never generate fabricated returns. Negative sleeve capital weights and leverage are disabled by default; this does not prevent a legitimately short-trading sleeve within its own approved risk limits.

## Evaluation and selection

Chronological outer walk-forward assessment: default rolling training window 252 common valuation sessions and test window63; minimum executable screening of126 usable sessions and30 closed lifecycles per sleeve; otherwise INSUFFICIENT_EVIDENCE with continued forward shadow collection. These counts are screening conventions, not statistical guarantees. The final last20% of available time is held out from selection. Purge crossing trade horizons and embargo at least the maximum relevant holding period plus data-availability lag; compute the exact boundary from sleeve policy. With inadequate data for this structure, no commercial economic-evidence approval is issued.

Within training only, compare recipe parameters and estimator choices. Apply the predeclared multiple-comparison method with the existing arch tooling, using dependence-aware block resampling. Freeze loss series, block method/length, confidence, and number of candidates tried. Keep untouched holdout and forward-shadow results separate. Rerunning until a favorable holdout appears consumes that holdout; it cannot retain its untouched label.

Rank by feasibility and drawdown/stress/capacity first, then Pareto tradeoffs among net growth, tail risk, turnover and execution quality. Do not label one maximum historical Sharpe portfolio 'best forever'. Selection requires a documented comparison to simpler baselines and component sleeves, stability across folds, realistic fee sensitivity and practical minimum account sizes. Failed criteria keep the baseline or cash; do not loosen them automatically.

## Dynamic allocation

Runtime adaptation selects only recipes/weights inside a previously released bounded envelope with exact model hash, inputs, effective times, risk maxima and expiry. Default recomputation is weekly for NEW admissions, not during every quote. Regime changes may reduce allowed new exposure immediately through existing released defensive rules; they may not widen a stop or increase hard limits. Offline research can run daily without changing production weights. Missing inputs select the declared baseline or hold the affected sleeve allocation as cash. Never renormalize missing sleeves upward implicitly.

Open trades retain their original source lineage and management-policy version. A rebalance that changes current positions is a distinct approved transition with costs, quantity commitments and consent; changing a weight document alone does not close/reopen positions. Material changes require customer notice/new mandate where the existing mandate does not cover them.

Outputs: candidate registry, dataset manifest, complete trial enumeration, correlation/co-drawdown charts, capacity stress, fold/holdout/shadow metrics, selected/rejected reasons, publication compatibility, rights intersection and release card. Sources: SRC20, SRC28–SRC31; methodology above is a proposed design, not observed economic superiority.
