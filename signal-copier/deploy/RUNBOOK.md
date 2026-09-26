# Promotion runbook: guarded active/passive, human-executed

This is a procedure a human runs, deliberately, end to end. Nothing in this
repository auto-promotes the standby. Automatic site promotion stays
disabled until every precondition in "Automatic promotion eligibility" below
is independently true and released — that has not happened, and enabling it
is a separate, explicit decision, not a default.

## What "redundant" means here, precisely

- Exactly **one** process may hold broker write credentials for a given
  brokerage account at any time — across both sites, across every adapter
  that can reach that same account (e.g. the same Alpaca account is never
  configured on both sites' `config/accounts.yaml` simultaneously).
- The standby is provisioned, boots, and serves `GET /health` — nothing
  more. `STANDBY_MODE=true` (see `app/config.py`, wired into
  `app/main.py`'s `_standby_read_only_gate` middleware and its `lifespan`)
  means it refuses every non-GET request with 503 and never starts signal
  ingestion, `OrderReconciler`, or `PriceMonitor` — independent of whether
  its `config/accounts.yaml` even has real broker credentials in it (it
  shouldn't).
- A missing heartbeat, an expired DNS TTL, or a database lease is **not**
  fencing. None of those prove the old process stopped running or that its
  broker session was revoked. Only a confirmed provider-level stop of the
  tagged instance, or a confirmed revocation of that account's brokerage
  API key/session, counts.

## Before promotion: four things must be independently true

1. **The old writer cannot write.** Stop the active site's
   `signal-copier.service` and *confirm* it's gone (`systemctl status`
   over SSH, or the cloud provider's own instance-state API — not just "I
   sent a stop request"). If SSH is unreachable, use the cloud console's
   stop/terminate action and confirm the instance state, not a ping.
   If neither is confirmable, treat the account's brokerage API
   key/session as still possibly live and revoke or rotate it at the
   broker before proceeding — do not promote against an unconfirmed old
   writer.

2. **Outstanding broker effects are reconciled.** Before this service
   accepted its last request, some orders may have been submitted to a
   broker whose response the app never received (see README's "A known
   remaining gap: an ambiguous submission outcome" — this is exactly that
   gap, now forced by the incident rather than a rare timeout). For every
   account this deployment manages:
   - Pull the broker's own order history / open orders directly (not
     through this app) for at least the last hour before the incident.
   - Cross-reference against `orders` (this app's own record) and
     `lifecycle_state` (`PositionLifecycleManager`'s persisted
     `PendingEntry`/`PendingExit`) from the restored database (step 3).
   - Any broker-side order/fill with no corresponding local record is an
     unresolved intent: resolve it manually (cancel at the broker, or
     reconcile the position) before the new site starts managing that
     symbol — do not let it start "fresh" over unknown broker state.

3. **Recovered state is usable, not just non-empty.** Restore the
   Litestream replica into a **new path** (never overwrite anything):
   ```
   litestream restore -o /var/lib/signal-copier/restored.db \
     -config /etc/signal-copier/litestream.yml \
     /var/lib/signal-copier/signal_copier.db
   ```
   Then, before pointing the app at it:
   - Open it and check `sqlite3 restored.db "PRAGMA integrity_check;"`.
   - Check every `lifecycle_state` row's `pending_entry`/`pending_exit` —
     these carry the *only* record of "an order is in flight and this
     symbol is not yet safely protected." A restore that's missing recent
     writes (Litestream's RPO window) may be missing exactly these rows —
     if it is, that symbol's protection state is unknown, not "assumed
     flat/protected," and needs the manual broker-side check in step 2
     before any new order for it.
   - `config/providers.yaml`/`config/routing.yaml`/`config_accounts` rows
     (source→account routing, provider/analyst overrides) must also be
     present and current — a restore that reconstructs positions but not
     which analyst/source owns them is incomplete, not merely "positions
     only" acceptable.

4. **The new site is actually eligible.** Confirm on the *standby's own
   host*, not by assumption:
   - Broker API reachability for every configured account (network egress,
     not just DNS resolution — an IPv6-only host reaching an IPv4-only
     broker API will look "connected" at the TCP layer to some brokers and
     fail at others).
   - `OWNER_PASSWORD`/`SESSION_SECRET` are set to fresh values (a restore
     should force re-login — see step 5) and the `sessions` table from the
     restore is treated as revoked, not trusted.
   - Real resource headroom: an OCI A1 2-OCPU/12GB standby is not
     equivalent to whatever ran on Hostinger if that host was sized larger
     — check this before assuming identical behavior, not after a slow
     reconciliation loop starts missing its own interval.

## Promotion steps

1. Complete the four checks above. If any is unresolved, stop and escalate
   to a human decision — do not promote on a partial answer.
2. On the new site: copy the restored, verified database into place, set
   real broker credentials in `/etc/signal-copier/active.env` (not
   `standby.env`), unset `STANDBY_MODE` (or set it to `false`).
3. `systemctl disable signal-copier-standby-status.service && systemctl stop signal-copier-standby-status.service`
   then `systemctl enable --now signal-copier.service`.
4. Watch the new process's own startup: `restore_from_store()` rebuilding
   `PositionLifecycleManager`'s in-memory state, then its first
   `OrderReconciler`/`PriceMonitor` cycle. Check `GET /health` shows
   `database_ok`, `price_monitor_ok`, `reconciler_ok` all true within a few
   cycles — not just process-liveness `status: ok`.
5. Revoke every session in the restored `sessions` table (or just confirm
   they're older than `SESSION_TTL_SECONDS` and let them lapse) — a
   restored session must not let an old browser tab keep acting as the
   owner without a fresh login.
6. Reconnect pull-based sources (Telegram/Discord/Slack/Twitter/Rithmic/
   MetaApi) with their own resume cursors — do not replay historical
   messages as if they were new signals.
7. Update DNS / owner bookmark to the new site's address. Confirm the OLD
   site's address (if it's ever reachable again — see "old node return"
   below) returns an error for every mutating route, not a stale UI.
8. Only now tell the owner the promoted site is live. Keep observing for
   at least the recovery-objective window before considering the incident
   closed.

## Old node returning after promotion

If the original host comes back (reboot, network partition healed,
auto-restart), it must **not** resume as a writer. Since `signal-copier.service`
was disabled (not left running) as part of fencing in step 1, a reboot
alone won't re-enable it — but explicitly confirm the unit is `disabled`,
not merely `inactive`, on that host before considering it safe to leave
running (an `inactive` unit can still be started by a leftover cron job,
watchdog, or a human who forgets the incident). Delete or rotate that
host's broker credentials if there's any doubt.

## Failback

Failback (returning to the original site once it's healthy) is a **new**
promotion, following this same runbook in the other direction — never a
"just turn the old one back on" shortcut. It needs the same four
preconditions, freshly re-checked, because state has moved on since the
incident.

## Automatic promotion eligibility (currently: not met, not enabled)

All of the following must be true and explicitly released before automatic
promotion is even considered, per `docs/03_REDUNDANCY_AND_DATA.md` from the
reviewed finalization package (a reasonable bar, adopted here without
inflating it):

- [ ] A tested, independently-enforceable fencing mechanism (confirmed
      cloud-provider stop/terminate, or confirmed brokerage credential
      revocation) — not a heartbeat/DNS/lease.
- [ ] A single serialized promotion authority (so two near-simultaneous
      triggers can't both promote).
- [ ] Measured, acceptable Litestream RPO for every managed account, with
      no unresolved pending-entry/pending-exit loss window.
- [ ] Complete broker order-history readback wired into the promotion path
      (today: manual, per step 2 above).
- [ ] Tested partition/crash/fence-refusal/old-node-return scenarios, not
      just the happy path.

None of these are implemented as automation today. Until they are, every
promotion is the manual procedure above — this is the deliberate baseline,
not a placeholder for something more automatic coming later without review.
