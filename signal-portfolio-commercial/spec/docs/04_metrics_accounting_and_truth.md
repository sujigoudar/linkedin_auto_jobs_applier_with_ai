# Accounting, performance and commercial truth

## Four independent books

Keep source recommendations, canonical portfolio model, platform strategy and actual follower execution books separate. An alert delivered is not an executed trade. C2 strategy model performance is not customer actual performance. The owner's discretionary activity is not automatically the portfolio product. A platform's 'verified' status retains its precise definition and cannot be copied into a generic verified badge.

The execution journal is append-only by identity with correction/reversal events; projections can be rebuilt. Record exact instrument, side, quantity step, execution price, multiplier/point value, currency, fee/funding/borrow cashflows, event time, receipt time, source authority and reconciliation state. Use Decimal or integer minor/tick units at accounting boundaries; do not calculate exact money in browser floating point. A corrected partial execution changes the relevant projections once. Foreign-currency P&L and conversion values retain their sources and conversion convention.

NAV is marked assets plus cash less liabilities using an approved valuation policy. Separate reporting marks from executable bids/asks and stale marks from current marks. No mark or missing opening basis makes the affected amount incomplete; exclude nothing silently from totals. Broker statement reconciliation provides an independent check. Customer reports must disclose unresolved residuals rather than insert balancing gains.

## Metric registry

Each result includes metric ID/version, entity (sleeve/portfolio/platform/follower), units/currency, origin, start/end/as-of, denominator, cost convention, valuation clock, sample count, data-quality mask, uncertainty and computation hash.

Total return: use time-weighted return split at actual external cash flows where sufficient valuations exist. Cash deposits are not profit. Money-weighted return may be separately reported with actual dated flows and solver status. Modified Dietz is explicitly labeled an approximation when used; not silently substituted for TWR.

Net P&L: gross trading result minus verified incurred transaction costs, financing and the declared subscription/platform fee allocation. Never subtract estimated fees twice or label modeled fees actual. Report both strategy economics before a customer subscription and representative all-in customer economics with its exact capital/price tier/cost assumptions. A fixed $99 monthly fee has a materially different return impact on $5k and $50k; display the convention.

Realized/unrealized: determined by the frozen accounting convention and exact lot/allocation identity. Preserve the owner's existing average-cost reporting where needed; new commercial journal must implement its declared lot convention. Tax reports are out of scope unless separately qualified.

Completed-lifecycle win rate: net profitable strategy episodes divided by all closed episodes, counting break-even separately. Partial exits and multiple fills are not separate completed trades. Expose `closing_fill_win_rate` as a separate metric if useful. Unresolved/open trades remain in exposure/NAV, not quietly discarded from performance.

Profit factor: gross profit of qualifying net-completed episodes divided by absolute gross losses, with explicit EMPTY, NO_LOSSES and DEFINED states. No-loss is not a finite number to rank above all other candidates without sample warning.

Drawdown: peak-to-trough of the defined cash-flow-adjusted NAV/return series; use nonnegative magnitudes, record underwater duration and valuation gaps. Chart downsampling must not alter the calculation. A strategy-version change does not reset public historical peaks. Real-time drawdown has a separate resolution/source from daily drawdown.

Sharpe/Sortino: periodic excess returns and compatible risk-free/downside target, declared annualization frequency and autocorrelation caveats. Zero variance or insufficient observations returns undefined, not infinity as a success score. Calmar and CAGR require positive compatible starting values and meaningful intervals; avoid annualizing a few days into promotional performance.

Tail risk: expected shortfall/VaR horizon, probability, sample/tail count, method and uncertainty. Report historical worst outcomes and deterministic scenario stress alongside estimates. Do not confuse historical CVaR with contractual maximum loss.

Trade-level: gross/net R using the ORIGINAL entry-time risk denominator, MAE/MFE from valid owned-position observations, hold duration, fill/rejection rate, average win/loss, win/loss streaks, slippage and costs. No-stop history uses only a predeclared replay fallback; hindsight-selected stops cannot define R.

Operational/replication: signal-to-publication latency, channel delivery lag, platform acknowledgment/fill lag, follower tracking error, missed/canceled/late trades, proportion covered by verified native stops, protection deficits and quota incidents. Negative observations remain visible.

Portfolio: sleeve contributions, common-period correlation/downside correlation, marginal stress/risk, cash utilization, simultaneous exposure, turnover, capacity headroom and rights/data completeness. No double counting constituents in an aggregate portfolio-of-portfolios; traverse lineage once.

## Subscriber fees and attribution

Store processor fees, tax, refunds, platform fees and provider royalties in a BUSINESS ledger distinct from trading accounts. Subscription revenue is not investment P&L. Fee price changes do not rewrite prior periods. Coupons, prorations and annual-prepaid recognition are explicit business events. Investor performance-fee calculations, when broker-managed services are later enabled, derive from broker agreements and actual statements, not generic SaaS invoicing.

## Reports and publication

Public reports are approved immutable snapshots with complete disclosures. Customer reports contain only their data. Operators can inspect the raw lineage and recompute. Export includes metric definitions, data-quality notes and source/contract attribution permitted by rights. No API caller can switch a hypothetical row's origin to actual. Archive corrected reports with old/new hashes and explanation; never erase unfavorable history. Sources: SRC09–SRC11.
