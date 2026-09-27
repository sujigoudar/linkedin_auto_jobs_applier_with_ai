# Gaps the UI implementation must close

1.Circular data dependency:missing portfolios must not prevent Product drafts,empty read models or screens. Implement actual domain slice first.
2.Interface/backend mismatch:existing three routes do not support customer configuration or admin work. Wire current services and new persisted models;do not point UI at nonexistent URLs.
3.API prefix drift:old design/api/commercial/v1 differs from current/api/v1;one canonical new namespace and mapping,not competing side-effect aliases.
4.Token/session gap:JWT /me alone is not browser login;implement verified BFF sessions,CSRF,expiry,revocation and per-action role/object authorization.
5.Privilege confusion:private_owner,commercial owner and customer are separate authorities. No cross-origin private account proxy.
6.Raw claims versus applied behavior:Stripe processed receipt,new role,connected account or successful webhook are not paid entitlement,live mandate or executed fill.
7.Drafts versus published projections:public query must never expose raw draft product,rights contract,source text or unapproved price/performance.
8.Metrics:fees,marks,origin,episode definition,cashflow and currency quality must travel through API,chart,table and exports. No beautiful false curve.
9.Data races:abort plus generation checks stop late accountA data painting under accountB. Stable snapshots prevent pagination duplicates and cohort drift.
10.Actions:preview/confirm/operation roles must preserve idempotency,scope,version and unknown outcomes. No optimistic financial success or general automatic retries.
11.Customization:layout changes cannot conceal safety/risk/cost information,change hard limits or execute user HTML/JS/code.
12.Integration capability screens must distinguish configured/entitled/tested/deployed/released;unavailable positive feature is not complete because denial works.
13.Social copying:join new-only by default;sync old trades separately;pause,billing cancel,revocation and handoff retain open obligations.
14.PAMM/MAM:broker-native programs have exact NAV/fee/dealing rules. No SaaS custody or automatically guessed accounting.
15.Accessibility:actual browser/table/canvas behavior needs verification;templates and screenshot aesthetics do not establish usable forms.
16.Permissions in worker paths:queue/export/research jobs revalidate their principal/service scope;no background tenant leakage.
17.Evidence:actual queries and side effects tested. Screenshots alone,helper tests,syntax pass and this atlas are not application acceptance.
18.Deployment:inactive private/public/staff origins,secure cookies,cache isolation and no live service authority in test environments.

All are design/implementation obligations,not a fresh claim that every corresponding code defect was executed in this delivery. Current code can have advanced;inspect HEAD and preserve working integrations. Where original commercial legal/platform owner cards remain missing,keep effects disabled and finish all independent local screen work.
