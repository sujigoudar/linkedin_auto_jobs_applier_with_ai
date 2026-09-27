# Broker-managed PAMM and MAM extension

Implement the domain and read-only simulator now. Real managed-account activation remains separately blocked until exact broker agreements, account capabilities, entity/legal status, permitted investors/geographies, client mandates and fee authority exist. Do not collect trading capital through Stripe, create an unlicensed wallet/pool, or treat an internal allocation table as a broker PAMM account. Customers fund the approved broker directly through its authorized process.

## Distinct models

MAM allocates orders across separately identified managed accounts under permitted manager authority. PAMM commonly allocates participation/economic results according to a broker's percentage/unit mechanism. Broker-specific contracts can differ. The integration stores an exact `AllocationProgram` with authority, eligible accounts, dealing schedule, allocation math, margin conventions, fees, correction handling and authoritative statement source. No generic 'PAMM=true' checkbox qualifies it.

A signal copier can generate desired trades for a managed program, but the broker's account hierarchy, fill allocation and official NAV/unit statements are authoritative. CopyFactory-style individual copying is not automatically native PAMM. Read back accounts, participation, pending capital flows, order allocations, fills and fees. If the broker has no supported API for a required action, expose the read-only/manual-approved workflow and block automation rather than simulate a successful live effect.

## Fair MAM allocation

Freeze participating accounts, eligibility, available margin and the chosen proportional/risk allocation before submission. The approved recipe determines whether an omnibus or per-account order is permitted. Prohibit assigning good fills to one client and poor fills to another after execution. Partial fills allocate according to a precommitted rule, stable tie breaks and legal lot sizes; unallocatable remainder is explicit. Track rejected/ineligible subaccounts independently. A client below minimum quantity is not rounded up beyond its mandate. A customer deposit cannot retroactively enter an earlier allocation cohort.

Default simulator uses largest-remainder allocation of integer units with deterministic account-ID tie break, constrained by pre-approved maximum allocations. Production uses the actual contractual broker rule instead. Record every rounding residue and prove total allocated units equals actual allocatable fills. Rate/fill fairness analysis is reported across clients without exposing one client's identity to another.

## PAMM/unit accounting

Record valuation timestamp, units outstanding, subscription/redemption requests, dealing cutoffs, official NAV per unit, cashflows and fees. Requests after a cutoff enter the next permitted dealing event. Deposit buys units at the approved dealing NAV after its status is confirmed; withdrawals consume units under the same documented convention. Do not treat a pending transfer as invested capital or give a late deposit earlier gains. Broker-restated NAV causes a versioned correction, not silent overwrite.

Performance fee calculations are disabled by default. For an approved high-water-mark program, define unit/account HWM, crystallization calendar, hurdle, equalization/series treatment, cashflow adjustment, loss carryforward, currency, fee base and correction rules. For the simple test-only no-cashflow interval: fee = positive part of (pre-fee NAV − previous fee-adjusted HWM) × approved rate, new HWM = max(previous HWM, post-fee NAV). Deposits/withdrawals require unit-series/equalization or exact broker convention; this simple formula must reject those inputs rather than generate a wrong fee. Trading losses carry forward; a new month does not reset a high-water mark. A customer's fee may differ from another due to subscription timing and equalization. Never charge a generic SaaS invoice based on an estimated pooled profit.

## Managed-account customer experience

Separate application/signatures, broker onboarding/KYC, mandate, reporting, participation/capital-flow status and fee statement. KYC completion remains the broker's verified record where the broker owns it; the SaaS does not store identity documents unnecessarily. Show money movement only as a broker-hosted action/link and observed status. Warn that local request acceptance is not broker settlement. Support mandate termination, successor manager, account closure and incident handoff under the actual agreement. Fees, costs and risk disclosures use approved versions and evidence.

## Exit and incident authority

Subscription cancellation does not revoke a broker mandate; mandate revocation does not authorize further discretionary trading. Establish a precise state transition and broker acknowledgment. A broken master connection may require suspend-new, preserve native protection and contact authorized operators, not unconditional liquidation of all investors. Reconciliation must handle allocation rejects, trade busts, forced broker liquidations, account restrictions and customer withdrawals during open positions. No auto-hedge or exercise action outside the approved program. Source: SRC16 and applicable legal review SRC09–SRC11.
