/* TR-16: Private settings, site role and recovery (`#/trade/system`).
 *
 * Redesigned as an operational-readiness console (design review, 2026-09):
 * a single top rollup (ACTIVE / STANDBY / DEGRADED / NOT READY), a
 * per-subsystem table (Application, database, broker connectivity,
 * signal ingestion, reconciliation, protection, backup, restore,
 * fencing, deployment), and a guided recovery runbook with explicit
 * gates -- rather than a flat list of implementation notes. Every status
 * on this screen still traces to a real, already-existing signal; the
 * standing rule from the earlier phases of this screen is unchanged and
 * strengthened: never render a fabricated "ok" for a subsystem this
 * build cannot actually check.
 *
 * P0-8 (external release audit, 2026-09): "'Reachable' must not mean
 * 'ready.'" Several earlier screenshots showed service/broker
 * reachability alongside unknown balance, unknown buying power, missing
 * provider worker, unavailable price monitoring, no rights/qualification
 * data -- all folded into one green/red rollup. This batch adds
 * GET /system/readiness (owner-gated) and a new "Readiness dimensions"
 * panel that renders six independently-computed dimensions as their own
 * labeled rows, ALWAYS visible (even when the rollup above reads ACTIVE):
 *   - liveness: is this process/its database probe responding at all
 *     (heartbeat-level -- NOT the same as any account's data being fresh).
 *   - data_readiness: per-account LIVE broker balance/buying-power read
 *     this cycle -- can be `unknown` even while liveness is `up`.
 *   - market_data_readiness: PriceMonitor's own real freshness -- separate
 *     from "is the broker connection reachable at all."
 *   - trading_authority: whether this process holds a valid writer lease.
 *     PLACEHOLDER pending the P0-6 fencing/lease work -- see
 *     app/main.py's system_readiness docstring for the exact follow-up.
 *   - protection_readiness: managed-lifecycle stop/target confirmation
 *     state, AND whether that confirmed state is still current (fresh
 *     reconciler cross-check), not just confirmed once in the past.
 *   - release_status: qualification/release-approval state. PLACEHOLDER
 *     pending the P0-7 qualification-taxonomy work.
 * The existing ACTIVE/STANDBY/DEGRADED/NOT READY rollup is now computed
 * SERVER-SIDE from exactly these six dimensions (app/main.py's
 * `_compute_readiness_rollup`) -- this module no longer derives its own
 * rollup from raw /health+/system/info fields, so the rollup and the
 * dimension rows underneath it can never disagree. See `renderDimensions`
 * below for how each dimension is rendered independently, and this
 * file's git history for the previous JS-side `computeRollup` this
 * replaces.
 *
 * Real backing data (all read-only GETs):
 *   - GET /health (public): `database_ok`/`price_monitor_ok`/
 *     `reconciler_ok`/`provider_scout_ok`/`equity_snapshotter_ok`/
 *     `relay_ok` and the pre-aggregated `status` ("ok" iff database_ok
 *     AND price_monitor_ok AND reconciler_ok -- see app/main.py's own
 *     `health()`). `*_ok` is false both for a stuck worker and before its
 *     first pass after startup, never hardcoded.
 *   - GET /health also carries INT-040's own real storage-ceiling check:
 *     `outbox_backlog_ok`/`outbox_backlog_bytes`/`outbox_backlog_row_count`/
 *     `outbox_backlog_ceiling_bytes` -- a live `SUM(LENGTH(envelope_json))`
 *     over undelivered `export_events` rows (app/db.py's
 *     `SignalStore.export_outbox_backlog`) compared against the
 *     configurable `EXPORT_OUTBOX_SIZE_CEILING_BYTES`. Never an estimate,
 *     never a fabricated pass.
 *   - GET /system/info (owner-gated): `standby_mode` (this instance's
 *     real writer/standby role), relay labels/config, and this exact
 *     database's live Alembic schema version vs. the deployed code's own
 *     migration head (`schema_version`/`schema_head` -- E01's real
 *     "deployment reproducibility" signal).
 *   - GET /positions (owner-gated): `stop_gap_count` (a real, derived
 *     aggregate -- every open managed-lifecycle position whose
 *     `stop_status` isn't `stop_confirmed`, computed server-side off the
 *     same per-position field, app/main.py's `list_positions`) and each
 *     lifecycle's own `halted` flag (app/lifecycle/close_arbiter.py) --
 *     this is the real "protection-confirmation state from stop/target
 *     events" this screen's Protection row uses.
 *   - GET /metrics (owner-gated, Prometheus text; fetched and parsed
 *     client-side here for exactly two already-computed real numbers:
 *     `signal_copier_price_observation_age_seconds` and
 *     `signal_copier_reconciler_cycle_age_seconds`, app/metrics.py --
 *     these are the only two real "seconds since last successful pass"
 *     ages this codebase computes anywhere, so Broker connectivity and
 *     Reconciliation below can show a genuine age/last-successful-check
 *     time instead of just a boolean. A metrics fetch failing (e.g. a
 *     build with no lifecycle_manager) degrades those two rows back to
 *     boolean-only, never a fabricated age.
 *
 *   - GET /system/readiness (owner-gated, new this batch): the six
 *     dimensions above, plus the `rollup` this module now renders
 *     verbatim -- see app/main.py's own docstring for exactly which real
 *     signal backs each one.
 *
 * Rollup logic (explicit, computed server-side ONLY from the six real
 * dimensions above -- see app/main.py's `_compute_readiness_rollup`; kept
 * here for quick reference):
 *   1. STANDBY   -- info.standby_mode is true. A standby deliberately
 *      does not run signal ingestion/reconciliation/price polling at all
 *      (app/main.py's `lifespan`), so its workers' own `_ok` flags are
 *      not evidence of a problem here -- role is checked first.
 *   2. NOT READY -- GET /health was unreachable, OR (not standby and)
 *      health.status !== "ok" -- i.e. this app's own critical rollup
 *      (database_ok AND price_monitor_ok AND reconciler_ok) says no.
 *   3. DEGRADED  -- otherwise, if any INFORMATIONAL-only worker is not
 *      fresh (provider_scout_ok, equity_snapshotter_ok, or relay_ok when
 *      a relay is actually configured) OR the export outbox backlog is
 *      over its configured ceiling (outbox_backlog_ok === false).
 *      INT-040's own explicit, stated decision: an over-ceiling outbox
 *      backlog does NOT belong in the critical NOT READY gate above,
 *      because it does not itself mean this instance's open positions
 *      are unprotected right now (database_ok/price_monitor_ok/
 *      reconciler_ok already cover that) -- it is the same kind of
 *      slower-building, export-reliability risk relay_ok already
 *      represents (indeed the outbox and the relay are the same pipe:
 *      a long relay outage is exactly what grows this backlog), so it
 *      is surfaced the same way: real, never silent, but folded into
 *      DEGRADED rather than forcing a healthy, fully-protected instance
 *      to read NOT READY over a storage/export concern.
 *   4. ACTIVE    -- otherwise.
 * This deliberately does NOT fold "fencing" into this rollup: fencing
 * has no automated, continuously-computed real signal anywhere in this
 * build (see the Fencing row and the runbook below) -- it is a one-time
 * precondition for a promotion/recovery event, not a steady-state
 * health signal, so wiring it into the every-30s rollup would force
 * either a fabricated "fencing_ok" flag (dishonest) or a permanent
 * "NOT READY" on an otherwise perfectly healthy running instance
 * (misleading). Fencing instead gates the recovery runbook below,
 * which is where the design review's "automatic promotion should stay
 * disabled until fencing is genuinely established" requirement actually
 * applies -- see the runbook's own note: this build has no automatic
 * promotion capability AT ALL (no endpoint, no button, anywhere), so
 * that requirement is currently moot, not merely satisfied by a
 * disabled control.
 *
 * Honestly bounded, never fabricated:
 *   - Backup: Litestream replicating the live SQLite WAL to R2
 *     (deploy/litestream/litestream.yml) runs as a SEPARATE process this
 *     API has no live status/API into -- rendered `not_tracked`, never a
 *     fabricated "last backup at ...".
 *   - Restore: deploy/RUNBOOK.md step 3's `litestream restore` +
 *     `PRAGMA integrity_check` is a real, documented, human-executed
 *     procedure with no live status this process can read -- rendered
 *     `not_tracked`.
 *   - Fencing: "old writer cannot write" / "broker-confirmed position
 *     state" (RUNBOOK.md's "Before promotion" steps 1-2) have no
 *     automated check anywhere in this build -- rendered `not_tracked`;
 *     the runbook below still uses this honestly (a manual attestation
 *     checkbox, never a fabricated pass) as the actual gate in front of
 *     every later step.
 *   - Deployment: unlike backup/restore, this DOES have a real live
 *     check -- schema_version === schema_head (above). E11's "deployment
 *     reproducibility" slice is real; a live "did this exact deploy also
 *     replace/verify infra" check is not, and isn't claimed here.
 *
 * The guided recovery runbook below is built around RUNBOOK.md's own
 * "Before promotion: four things must be independently true" -- this
 * build has NO real promotion action to gate at all (no endpoint, no
 * button -- confirmed against app/main.py and TR-13's own "no manual
 * reconciliation-trigger endpoint" finding, tr13.js), so rather than
 * fabricate a promotion workflow that doesn't exist, the runbook is
 * built as sequential, explicitly gated CONFIRMATIONS: each gate's
 * checkbox is disabled until the previous gate is confirmed, and gate 3
 * additionally requires two real, live checks (schema-at-head AND
 * database_ok) to be true before it can even be checked -- never a
 * checkbox you can tick past a real red signal. Confirmation state is
 * held in this module's own memory only (no fencing-evidence store
 * exists anywhere in app/db.py to persist it in) -- explicitly labeled
 * as such, never presented as a durable record. The final row
 * (Promotion) is permanently `unsupported`, regardless of how many
 * gates above are confirmed: promotion stays deploy/RUNBOOK.md's manual,
 * human-executed procedure, never an in-app action.
 */
(function () {
  "use strict";

  function capSlot(id) {
    return `<span class="cap-state-slot" id="${id}"></span>`;
  }
  function mountCapStates(root, specs) {
    for (const [id, opts] of specs) {
      const el = root.querySelector(`#${id}`);
      if (el) Components.renderCapabilityState(el, opts);
    }
  }

  // --- /metrics (owner-gated Prometheus text) is fetched directly here
  // (not via ctx.fetchJSON, which assumes a JSON body) -- same-origin
  // fetch, so the existing owner session cookie is sent automatically,
  // same trust boundary as every other owner-gated GET this screen
  // already reads. A failure (401 if the session lapsed mid-poll, or any
  // network error) degrades silently to `null` -- callers must treat
  // that as "age not available," never as a zero/fresh age. ---
  async function fetchMetricsText() {
    try {
      const res = await fetch("/metrics");
      if (!res.ok) return null;
      return await res.text();
    } catch (e) {
      return null;
    }
  }

  function parseGaugeValue(text, name) {
    if (!text) return null;
    const m = text.match(new RegExp(`^${name}\\s+([0-9eE+\\-.]+)\\s*$`, "m"));
    if (!m) return null;
    const v = parseFloat(m[1]);
    return Number.isFinite(v) ? v : null;
  }

  function fmtAge(seconds) {
    if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return "not exposed";
    const s = Math.max(0, Math.floor(seconds));
    if (s < 60) return `${s}s ago`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    const d = Math.floor(h / 24);
    return `${d}d ago`;
  }

  function fmtBytes(n) {
    if (n === null || n === undefined || Number.isNaN(n)) return "not exposed";
    if (n < 1024) return `${n} B`;
    const units = ["KB", "MB", "GB", "TB"];
    let v = n;
    let u = -1;
    while (v >= 1024 && u < units.length - 1) {
      v /= 1024;
      u += 1;
    }
    return `${v.toFixed(1)} ${units[u]}`;
  }

  function fmtLastCheck(ageSeconds, fetchedAt) {
    if (ageSeconds === null || ageSeconds === undefined) return "not exposed by this build";
    const t = new Date(fetchedAt.getTime() - ageSeconds * 1000);
    return t.toISOString();
  }

  // --- Readiness dimensions (P0-8): GET /system/readiness computes
  // liveness / data_readiness / market_data_readiness / trading_authority
  // / protection_readiness / release_status independently, plus a
  // `rollup` that folds them into ACTIVE/STANDBY/DEGRADED/NOT READY (see
  // app/main.py's system_readiness/_compute_readiness_rollup docstrings
  // for the exact, explicit gate order). This module no longer computes
  // its own rollup from raw /health+/system/info fields -- the server is
  // the single source of truth for both the rollup AND each dimension, so
  // the console can never show a dimension that disagrees with the
  // rollup that folds it in. ---

  const DIMENSION_TONE = {
    up: "ok", fresh: "ok", current: "ok", held: "ok", approved: "ok", asserted: "ok",
    partial: "warn", stale: "warn", degraded: "warn",
    down: "crit", unknown: "crit", gap: "crit", not_held: "crit", rejected: "crit",
    not_tracked: "neutral",
  };

  function dimensionTone(status) {
    return DIMENSION_TONE[status] || "neutral";
  }

  function renderDimensions(container, readiness, readinessRes) {
    if (!container) return;
    if (!readiness) {
      StateMatrix.render(container, {
        state: "error",
        message: `Could not load GET /system/readiness this cycle${readinessRes && readinessRes.status ? ` (HTTP ${readinessRes.status})` : ""} -- every dimension below, and the rollup above, is therefore NOT READY rather than guessed.`,
      });
      return;
    }
    const dataAccounts = (readiness.data_readiness && readiness.data_readiness.accounts) || [];
    const accountsDetail = dataAccounts.length
      ? `<ul>${dataAccounts
          .map(
            (a) =>
              `<li><span class="mono">${escapeHtml(a.account_id)}</span>: ${pill(a.status, dimensionTone(a.status))} -- ${escapeHtml(a.reason || "")}${
                a.status === "fresh"
                  ? ` (cash=${a.cash === null || a.cash === undefined ? "n/a" : a.cash}, buying_power=${a.buying_power === null || a.buying_power === undefined ? "n/a" : a.buying_power})`
                  : ""
              }</li>`
          )
          .join("")}</ul>`
      : "";
    // Extract workflow data for Reservations and Intents rows
    const workflow = (readiness.workflow) || {};
    const reservationHealth = workflow.reservations || {};
    const intentHealth = workflow.intents || {};
    const staleUnknownHeld = reservationHealth.stale_unknown_held || 0;
    const dispatchingWithoutResponse = intentHealth.dispatching_without_response || 0;

    StateMatrix.render(container, {
      state: "ready",
      html: `
        <p class="section-note">Each row below is computed and rendered independently -- a service can be reachable (Liveness = up) while Data readiness, Market-data readiness, Trading authority, Protection readiness, Release status, Reservations or Intents is unknown/stale/not held/not tracked, and this table shows BOTH facts rather than collapsing them into the single rollup above. "Reachable" is never rendered as "ready."</p>
        ${table(
          ["Dimension", "Status", "Reason", "Age", "Notes"],
          [
            ["Liveness", pill(readiness.liveness.status, dimensionTone(readiness.liveness.status)), escapeHtml(readiness.liveness.reason || ""), "—", "Is this process/its database probe responding at all (heartbeat-level)."],
            ["Data readiness", pill(readiness.data_readiness.status, dimensionTone(readiness.data_readiness.status)), escapeHtml(readiness.data_readiness.reason || ""), "—", `Per-account live balance/buying-power read. ${accountsDetail}`],
            [
              "Market-data readiness",
              pill(readiness.market_data_readiness.status, dimensionTone(readiness.market_data_readiness.status)),
              escapeHtml(readiness.market_data_readiness.reason || ""),
              readiness.market_data_readiness.age_seconds === null || readiness.market_data_readiness.age_seconds === undefined ? "—" : fmtAge(readiness.market_data_readiness.age_seconds),
              "Is current price flowing for the instruments this account trades (PriceMonitor freshness).",
            ],
            [
              "Trading authority",
              pill(readiness.trading_authority.status, dimensionTone(readiness.trading_authority.status)),
              escapeHtml(readiness.trading_authority.reason || ""),
              "—",
              "Placeholder pending the P0-6 fencing/lease work -- see Reason.",
            ],
            [
              "Protection readiness",
              pill(readiness.protection_readiness.status, dimensionTone(readiness.protection_readiness.status)),
              escapeHtml(readiness.protection_readiness.reason || ""),
              "—",
              "Managed-lifecycle stop/target confirmation state, and whether it's current (reconciler-fresh), not just confirmed once.",
            ],
            [
              "Release status",
              pill(readiness.release_status.status, dimensionTone(readiness.release_status.status)),
              escapeHtml(readiness.release_status.reason || ""),
              "—",
              "Placeholder pending the P0-7 qualification/release-taxonomy work -- see Reason.",
            ],
            [
              "Reservations",
              pill(staleUnknownHeld > 0 ? "degraded" : "current", staleUnknownHeld > 0 ? "bad" : "ok"),
              staleUnknownHeld > 0 ? `${fmtNum(staleUnknownHeld)} budget reservation(s) have UNKNOWN_HELD status older than 15 minutes (stale).` : "All budget reservations are current.",
              "—",
              "Tracks HELD and UNKNOWN_HELD budget reservation states (WC-21). DEGRADED when any stale UNKNOWN_HELD reservation exists.",
            ],
            [
              "Intents",
              pill(dispatchingWithoutResponse > 0 ? "degraded" : "current", dispatchingWithoutResponse > 0 ? "bad" : "ok"),
              dispatchingWithoutResponse > 0 ? `${fmtNum(dispatchingWithoutResponse)} intent(s) dispatched without a recorded response yet.` : "All dispatched intents have recorded responses.",
              "—",
              "Tracks signal execution intent records and outbox delivery state (WC-21). DEGRADED when any dispatched intent lacks a response_recorded_at timestamp.",
            ],
          ],
          "No dimension data."
        )}
      `,
    });
  }

  function shell() {
    return `
      <section class="tr-panel" id="tr16-autonomy"><h2>Ready to run on its own?</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr16-status"><h2>Operational readiness</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr16-dimensions"><h2>Readiness dimensions</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr16-p01"><h2>Site role/writer identity</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr16-p02"><h2>Subsystems</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr16-p03"><h2>Backup/restore/deployment evidence</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr16-p04"><h2>Owner access</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr16-p05"><h2>Promotion readiness</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr16-p06"><h2>Guided recovery runbook</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr16-actions"><h2>Actions</h2><div class="tr-panel-body"></div></section>
    `;
  }

  // Module-scope, in-memory only (see docstring) -- survives across this
  // screen's 30s poll re-renders but NOT a page reload, and is never sent
  // anywhere: there is no fencing-evidence store in this codebase to
  // persist it in, and pretending otherwise would misrepresent a real
  // recovery record.
  const runbookGateState = { g1: false, g2: false, g3: false, g4: false };

  function renderRunbook(container, ctxData) {
    const { schemaMatch, dbOk, authConfigured, pendingCount } = ctxData;
    const gate3Ready = schemaMatch === true && dbOk === true;

    const gates = [
      {
        key: "g1",
        title: "1. Old writer confirmed stopped/fenced",
        enabled: true,
        body: `
          <p class="section-note">No automated fencing/epoch mechanism exists in this build (RUNBOOK.md "Before promotion" step 1) -- this is your own attestation, not a live check. Confirm via <code>systemctl status</code> over SSH or the cloud provider's instance-state API (never just "I sent a stop request"); if neither is confirmable, revoke/rotate the account's brokerage API key/session first.</p>
          ${capSlot("tr16-gate1-cap")}
        `,
      },
      {
        key: "g2",
        title: "2. Outstanding broker effects reconciled",
        enabled: runbookGateState.g1,
        body: `
          <p class="section-note">Pull each account's broker-side order history directly (not through this app) and cross-reference against <code>orders</code>/<code>lifecycle_state</code> (RUNBOOK.md step 2). Real evidence available right now: ${
            pendingCount === null
              ? "could not read GET /positions this cycle."
              : `${fmtNum(pendingCount)} managed-lifecycle position(s) currently have an unresolved pending_entry/pending_exit (GET /positions) -- resolve every one of these, and any broker-side order/fill with no local record, before proceeding.`
          }</p>
        `,
      },
      {
        key: "g3",
        title: "3. Recovered database verified usable",
        enabled: runbookGateState.g2 && gate3Ready,
        blockedReason: runbookGateState.g2 && !gate3Ready
          ? "Blocked by real live checks: this requires BOTH schema-at-head (GET /system/info) AND database_ok (GET /health) to be true before it can be confirmed -- see Subsystems above for which one is currently failing."
          : null,
        body: `
          <p class="section-note">Live prerequisite (not a substitute for actually restoring and inspecting a copy, RUNBOOK.md step 3): Database schema at head = ${schemaMatch === true ? pill("yes", "ok") : pill("no / unknown", "bad")}; database reachable = ${dbOk === true ? pill("yes", "ok") : pill("no / unknown", "bad")}. Before checking this box you must still separately run <code>litestream restore</code> into a NEW path, <code>PRAGMA integrity_check</code>, and review every restored <code>lifecycle_state</code> row's pending_entry/pending_exit and routing config -- none of that is observable from this running process.</p>
        `,
      },
      {
        key: "g4",
        title: "4. New site actually eligible",
        enabled: runbookGateState.g3,
        body: `
          <p class="section-note">Real, partial evidence: owner auth is ${authConfigured ? pill("configured", "ok") : pill("not configured", "bad")} on THIS instance (GET /system/info) -- confirm fresh OWNER_PASSWORD/SESSION_SECRET values are set on the new site, not carried over from a restore. Broker API reachability (real network egress, not just DNS) and real resource headroom have no live check from this screen -- verify both manually on the standby's own host (RUNBOOK.md step 4).</p>
        `,
      },
    ];

    const rows = gates
      .map((g) => {
        const checked = runbookGateState[g.key];
        const disabled = !g.enabled;
        return `
          <li class="tr16-gate${disabled ? " tr16-gate-disabled" : ""}">
            <label>
              <input type="checkbox" class="tr16-gate-checkbox" data-gate="${g.key}" ${checked ? "checked" : ""} ${disabled ? "disabled" : ""} />
              <strong>${g.title}</strong>
            </label>
            ${disabled && g.blockedReason ? `<p class="section-note">${escapeHtml(g.blockedReason)}</p>` : ""}
            ${disabled && !g.blockedReason ? `<p class="section-note">Blocked: confirm the previous gate first.</p>` : ""}
            ${g.body}
          </li>`;
      })
      .join("");

    container.innerHTML = `
      <p class="section-note">Mirrors deploy/RUNBOOK.md's own "Before promotion: four things must be independently true" -- Database schema reproducibility is checked live above (gate 3); every other gate is either a manual, human attestation this app cannot verify remotely, or is blocked outright by a real red signal. Confirmations here are held only in this browser tab's memory (no fencing-evidence store exists anywhere in this codebase) -- they are an operator aid, never a persisted recovery record.</p>
      <ol class="tr16-runbook">${rows}</ol>
    `;
    mountCapStates(container, [
      [
        "tr16-gate1-cap",
        {
          status: "not_tracked",
          reason: "No automated fencing/epoch mechanism exists in this build -- see the Fencing row in Subsystems above.",
        },
      ],
    ]);

    container.querySelectorAll(".tr16-gate-checkbox").forEach((el) => {
      el.addEventListener("change", (e) => {
        const key = e.target.getAttribute("data-gate");
        runbookGateState[key] = e.target.checked;
        // Unchecking an earlier gate must also revoke every later one --
        // otherwise a later checkbox could stay "confirmed" against a
        // precondition that's since been withdrawn.
        if (!e.target.checked) {
          if (key === "g1") { runbookGateState.g2 = false; runbookGateState.g3 = false; runbookGateState.g4 = false; }
          if (key === "g2") { runbookGateState.g3 = false; runbookGateState.g4 = false; }
          if (key === "g3") { runbookGateState.g4 = false; }
        }
        renderRunbook(container, ctxData);
      });
    });
  }

  function renderAutonomy(container, readiness) {
    if (!container) return;
    if (!readiness || !readiness.accounts) {
      StateMatrix.render(container, {
        state: "error",
        message: "Could not load per-account readiness data.",
      });
      return;
    }

    const accounts = readiness.accounts || [];
    if (accounts.length === 0) {
      StateMatrix.render(container, {
        state: "empty",
        message: "No accounts configured.",
      });
      return;
    }

    // Collect all blocked items across all accounts
    const blockedItems = [];
    accounts.forEach((acc) => {
      const blocked = (acc.items || []).filter((item) => item.status === "blocked");
      blocked.forEach((item) => {
        blockedItems.push({
          account_id: acc.account_id,
          key: item.key,
          reason: item.reason,
          fix_route: item.fix_route,
        });
      });
    });

    // Status: green if no blocked items, red otherwise
    const isReady = blockedItems.length === 0;
    const statusColor = isReady ? "ok" : "crit";
    const statusLabel = isReady ? "Ready to run on its own" : `${blockedItems.length} blocker(s)`;

    let html = `
      <div style="margin-bottom: 1rem;">
        ${pill(statusLabel, statusColor)}
        ${isReady ? "<p>All configured accounts are ready for autonomous trading.</p>" : ""}
      </div>
    `;

    if (blockedItems.length > 0) {
      html += `<p class="section-note">The following items must be addressed before autonomous trading:</p><ul>`;
      blockedItems.forEach((item) => {
        const fixText = item.fix_route ? ` <a href="${item.fix_route}" class="fix-link">Fix</a>` : "";
        html += `<li><strong>${escapeHtml(item.account_id)}</strong>: ${escapeHtml(item.key)} — ${escapeHtml(item.reason)}${fixText}</li>`;
      });
      html += "</ul>";
    }

    // Detail table for all accounts
    html += `<p class="section-note" style="margin-top: 1rem;">Per-account readiness checklist:</p>`;
    const rows = accounts.map((acc) => {
      const blockedCount = (acc.items || []).filter((item) => item.status === "blocked").length;
      const notTrackedCount = (acc.items || []).filter((item) => item.status === "not_tracked").length;
      const okCount = (acc.items || []).filter((item) => item.status === "ok").length;
      const statusPill = blockedCount > 0 ? pill(`${blockedCount} blocked`, "crit") :
                        notTrackedCount > 0 ? pill(`all ok / ${notTrackedCount} not tracked`, "warn") :
                        pill("all ok", "ok");

      const detailItems = (acc.items || [])
        .map((item) => {
          const tone = item.status === "ok" ? "ok" : item.status === "blocked" ? "crit" : "neutral";
          const fixLink = item.fix_route ? ` <a href="${item.fix_route}" class="fix-link">Fix</a>` : "";
          return `${escapeHtml(item.key)}: ${pill(item.status, tone)} ${escapeHtml(item.reason)}${fixLink}`;
        })
        .join("<br>");

      return [
        escapeHtml(acc.account_id),
        statusPill,
        detailItems,
      ];
    });

    html += table(
      ["Account", "Status", "Items"],
      rows,
      "No accounts."
    );

    StateMatrix.render(container, {
      state: "ready",
      html: html,
    });
  }

  async function load(ctx) {
    const els = {
      autonomy: ctx.container.querySelector("#tr16-autonomy .tr-panel-body"),
      status: ctx.container.querySelector("#tr16-status .tr-panel-body"),
      dimensions: ctx.container.querySelector("#tr16-dimensions .tr-panel-body"),
      role: ctx.container.querySelector("#tr16-p01 .tr-panel-body"),
      subsystems: ctx.container.querySelector("#tr16-p02 .tr-panel-body"),
      backup: ctx.container.querySelector("#tr16-p03 .tr-panel-body"),
      owner: ctx.container.querySelector("#tr16-p04 .tr-panel-body"),
      readiness: ctx.container.querySelector("#tr16-p05 .tr-panel-body"),
      runbook: ctx.container.querySelector("#tr16-p06 .tr-panel-body"),
      actions: ctx.container.querySelector("#tr16-actions .tr-panel-body"),
    };
    for (const el of Object.values(els)) StateMatrix.render(el, { state: "loading" });

    const [infoRes, healthRes, positionsRes, metricsText, readinessRes] = await Promise.all([
      ctx.fetchJSON("/system/info"),
      ctx.fetchJSON("/health"),
      ctx.fetchJSON("/positions"),
      fetchMetricsText(),
      ctx.fetchJSON("/system/readiness"),
    ]);
    if (infoRes.status === 401 || infoRes.status === 403) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "denied", deniedCode: infoRes.status });
      return;
    }
    if (!infoRes.ok) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "error", message: "Could not load system info." });
      return;
    }
    const info = infoRes.data;
    const health = healthRes.ok ? healthRes.data : null;
    const fetchedAt = new Date();
    const lifecycles = (positionsRes.ok && positionsRes.data && positionsRes.data.managed_lifecycles) || [];
    const stopGapCount = positionsRes.ok && positionsRes.data ? positionsRes.data.stop_gap_count : null;
    const haltedCount = lifecycles.filter((l) => l.halted).length;
    const pendingCount = positionsRes.ok ? lifecycles.filter((l) => l.pending_exit || l.pending_entry).length : null;
    const priceAgeSeconds = parseGaugeValue(metricsText, "signal_copier_price_observation_age_seconds");
    const reconcilerAgeSeconds = parseGaugeValue(metricsText, "signal_copier_reconciler_cycle_age_seconds");
    const schemaMatch = Boolean(info.schema_version && info.schema_head && info.schema_version === info.schema_head);

    // --- Readiness dimensions (P0-8, GET /system/readiness): liveness,
    // data readiness, market-data readiness, trading authority, protection
    // readiness and release status are each computed independently
    // server-side -- see app/main.py's system_readiness docstring. A
    // failure to even reach this endpoint is itself treated as NOT READY
    // (fail closed), never silently falling back to the old health-only
    // rollup, which is exactly the "reachable was treated as ready" gap
    // this batch closes. ---
    const readiness = readinessRes.ok ? readinessRes.data : null;
    const rollup = readiness
      ? readiness.rollup
      : {
          label: "NOT READY",
          tone: "crit",
          reason: "GET /system/readiness was unreachable this cycle -- the independent readiness dimensions (liveness/data/market-data/trading authority/protection/release) could not be computed, so this cannot honestly report anything but NOT READY.",
        };

    // --- Operational readiness rollup (top) ---
    const statusHost = document.createElement("div");
    Components.renderKPIBand(statusHost, {
      items: [
        { label: "Status", value: rollup.label, tone: rollup.tone, sublabel: rollup.reason },
        { label: "Protection deficit", value: stopGapCount === null ? "Unknown" : fmtNum(stopGapCount), tone: stopGapCount === null ? "neutral" : stopGapCount > 0 ? "crit" : "ok", sublabel: "open positions with an unconfirmed stop (GET /positions)" },
        { label: "Halted positions", value: fmtNum(haltedCount), tone: haltedCount > 0 ? "crit" : "ok", sublabel: "close-arbiter halts (GET /positions)" },
        { label: "Deployment", value: schemaMatch ? "At head" : "Mismatch", tone: schemaMatch ? "ok" : "bad", sublabel: "DB schema vs. code migration head (GET /system/info)" },
      ],
    });
    els.status.removeAttribute("aria-busy");
    els.status.innerHTML = "";
    els.status.appendChild(statusHost);
    els.status.insertAdjacentHTML(
      "beforeend",
      `<p class="section-note">This is a ROLLUP of the six independent readiness dimensions in the panel below -- STANDBY, then NOT READY, then DEGRADED, then ACTIVE (see GET /system/readiness / app/main.py's <code>_compute_readiness_rollup</code>). A subsystem being reachable does NOT by itself mean ACTIVE: every dimension below stays visible in its own row even when this rollup reads ACTIVE, and an unconfirmed stop or an absent trading authority forces NOT READY here regardless of how healthy the others look.</p>`
    );

    // --- Autonomy checklist (top panel) ---
    renderAutonomy(els.autonomy, readiness);

    // --- Readiness dimensions: each rendered as its own labeled row, never
    // folded into the single rollup above -- this is the direct fix for
    // the external release audit's "reachable alongside unknown balance/
    // buying power/no rights data must never render as if everything is
    // fine." A dimension can show a real, independent status even while
    // every OTHER dimension (including the rollup) looks fine. ---
    renderDimensions(els.dimensions, readiness, readinessRes);

    // --- Site role/writer identity ---
    StateMatrix.render(els.role, {
      state: "ready",
      html: `
        <ul>
          <li>Role: ${info.standby_mode ? pill("standby (read-only -- STANDBY_MODE=true)", "warn") : pill("active writer", "ok")}</li>
          <li>Relay environment label: <span class="mono">${escapeHtml(info.relay_environment)}</span></li>
          <li>Relay evidence class: <span class="mono">${escapeHtml(info.relay_evidence_class)}</span></li>
          <li>Relay ingress configured: ${boolPill(info.relay_ingress_configured)}</li>
        </ul>
        <p class="section-note">Real, from app/main.py's own STANDBY_MODE gate (_standby_read_only_gate): while standby, this process serves only GET/HEAD/OPTIONS -- no signal ingestion, no background reconciliation/price polling, no financial command can reach the engine, regardless of any individual route's own logic.</p>
      `,
    });

    // --- Subsystems: Application / database / broker connectivity /
    // signal ingestion / reconciliation / protection / backup / restore /
    // fencing / deployment -- each mapped to a real check, or rendered
    // not_tracked with an honest reason, never a fabricated pass. ---
    const rows = [
      [
        "Application",
        boolPill(healthRes.ok),
        healthRes.ok ? fmtLastCheck(0, fetchedAt) : "not exposed",
        healthRes.ok ? "live (checked this cycle)" : "unknown",
        `GET /health responding at all (this cycle's own status field: ${escapeHtml(String(health ? health.status : "unreachable"))})`,
        healthRes.ok ? "none" : "process is not responding -- check it's running and the network path to it",
      ],
      [
        "Database",
        health ? boolPill(health.database_ok) : pill("unknown", "muted"),
        health ? fmtLastCheck(0, fetchedAt) : "not exposed",
        health ? "live (checked this cycle)" : "unknown",
        "GET /health's database_ok -- a live store.get_position() probe run in-request, app/main.py",
        !health ? "cannot verify -- GET /health was unreachable this cycle" : health.database_ok ? "none" : "database file unreachable/locked -- check disk, permissions, and app logs",
      ],
      [
        "Broker connectivity",
        health ? boolPill(health.price_monitor_ok) : pill("unknown", "muted"),
        fmtLastCheck(priceAgeSeconds, fetchedAt),
        fmtAge(priceAgeSeconds),
        "GET /health's price_monitor_ok (freshness) + GET /metrics' signal_copier_price_observation_age_seconds (real age) -- PriceMonitor's broker.get_last_price() polling, app/pricing.py",
        !health ? "cannot verify -- GET /health was unreachable this cycle" : health.price_monitor_ok ? "none" : "check broker API credentials/network egress for every configured account",
      ],
      [
        "Signal ingestion",
        boolPill(healthRes.ok),
        healthRes.ok ? fmtLastCheck(0, fetchedAt) : "not exposed",
        healthRes.ok ? "live (checked this cycle)" : "unknown",
        "Inferred from GET /health responding (webhook/pull-source ingestion runs in-process); this build has no dedicated per-source ingestion-freshness flag -- webhook ingestion is synchronous per request, and pull sources (Telegram/Discord/MetaApi/Rithmic/…) surface no last_success_at anywhere",
        "none (no dedicated signal exists to remediate against)",
      ],
      [
        "Reconciliation",
        health ? boolPill(health.reconciler_ok) : pill("unknown", "muted"),
        fmtLastCheck(reconcilerAgeSeconds, fetchedAt),
        fmtAge(reconcilerAgeSeconds),
        "GET /health's reconciler_ok (freshness) + GET /metrics' signal_copier_reconciler_cycle_age_seconds (real age) -- OrderReconciler's background pass, app/reconciliation.py",
        !health ? "cannot verify -- GET /health was unreachable this cycle" : health.reconciler_ok ? "none" : "no manual trigger exists (fixed-interval background loop) -- restart the process if stuck past several intervals",
      ],
      [
        "Protection",
        stopGapCount === null ? pill("unknown", "muted") : stopGapCount > 0 ? pill(`${fmtNum(stopGapCount)} uncovered`, "bad") : pill("all confirmed", "ok"),
        positionsRes.ok ? fmtLastCheck(0, fetchedAt) : "not exposed",
        positionsRes.ok ? "live (checked this cycle)" : "unknown",
        "GET /positions' stop_gap_count (server-computed off each lifecycle's real stop_status, app/main.py) + halted flags (app/lifecycle/close_arbiter.py)",
        stopGapCount === null ? "cannot verify -- GET /positions was unreachable this cycle" : stopGapCount > 0 || haltedCount > 0 ? "see Reconciliation and trading incidents (#/trade/incidents) for which position and why" : "none",
      ],
      [
        "Storage (export outbox)",
        health && health.outbox_backlog_ok !== undefined
          ? (health.outbox_backlog_ok ? pill("under ceiling", "ok") : pill("over ceiling", "bad"))
          : pill("unknown", "muted"),
        health ? fmtLastCheck(0, fetchedAt) : "not exposed",
        health ? "live (checked this cycle)" : "unknown",
        `GET /health's outbox_backlog_ok (INT-040) -- real SUM(LENGTH(envelope_json)) over undelivered app/db.py export_events rows, app/db.py's SignalStore.export_outbox_backlog: ${
          health && health.outbox_backlog_bytes !== null && health.outbox_backlog_bytes !== undefined
            ? `${fmtBytes(health.outbox_backlog_bytes)} across ${fmtNum(health.outbox_backlog_row_count)} undelivered row(s), ceiling ${fmtBytes(health.outbox_backlog_ceiling_bytes)} (EXPORT_OUTBOX_SIZE_CEILING_BYTES)`
            : "not exposed this cycle"
        }`,
        !health
          ? "cannot verify -- GET /health was unreachable this cycle"
          : health.outbox_backlog_ok
          ? "none"
          : "the commercial ingress has been unreachable long enough to build a real backlog past its configured ceiling -- restore RELAY_INGRESS_URL connectivity, or raise EXPORT_OUTBOX_SIZE_CEILING_BYTES only after a deliberate operator review of real disk headroom. Rows are never pruned or truncated automatically.",
      ],
      [
        "Backup",
        capSlot("tr16-sub-backup"),
        "—",
        "—",
        "deploy/litestream/litestream.yml, deploy/RUNBOOK.md",
        "none actionable from here -- Litestream is a separate process",
      ],
      [
        "Restore",
        capSlot("tr16-sub-restore"),
        "—",
        "—",
        "deploy/RUNBOOK.md step 3 (litestream restore + PRAGMA integrity_check)",
        "run the documented manual restore procedure; see the runbook below",
      ],
      [
        "Fencing",
        capSlot("tr16-sub-fencing"),
        "—",
        "—",
        "deploy/RUNBOOK.md \"Before promotion\" step 1; \"Automatic promotion eligibility: not met\"",
        "manual confirmation only -- see gate 1 of the runbook below",
      ],
      [
        "Deployment",
        schemaMatch ? pill("verified match", "ok") : pill("mismatch or unknown", "bad"),
        fmtLastCheck(0, fetchedAt),
        "live (checked this cycle)",
        `SignalStore.schema_version() vs. alembic_code_head() (app/db.py) -- live: version=${escapeHtml(info.schema_version || "unknown")}, code head=${escapeHtml(info.schema_head || "unknown")}`,
        schemaMatch ? "none" : "run `alembic upgrade head` against this database before trusting a restore built from this release",
      ],
    ];
    StateMatrix.render(els.subsystems, {
      state: "ready",
      html: `<p class="section-note">Each subsystem below is evaluated independently off a real, already-existing signal -- never collapsed into one badge and never a fabricated "ok" for Backup/Restore/Fencing, which this build genuinely cannot check live (shown as "Not tracked").</p>${table(
        ["Subsystem", "Status", "Last successful check", "Age", "Evidence", "Remediation"],
        rows,
        "No subsystem data."
      )}`,
    });
    mountCapStates(els.subsystems, [
      ["tr16-sub-backup", { status: "not_tracked", reason: "Litestream replicates the live SQLite WAL to R2 as a SEPARATE process this API has no live status/API into -- no last-replicated-at timestamp or RPO number is fabricated here." }],
      ["tr16-sub-restore", { status: "not_tracked", reason: "The real restore procedure (litestream restore into a new path, then PRAGMA integrity_check + a manual lifecycle_state/routing review) is documented and human-executed, with no live status this process can read.", remediation: "Follow deploy/RUNBOOK.md step 3 exactly; never overwrite the live database with an unverified restore." }],
      ["tr16-sub-fencing", { status: "not_tracked", reason: "No automated fencing/epoch mechanism exists in this build -- a missing heartbeat, an expired DNS TTL, or a database lease is explicitly NOT fencing per RUNBOOK.md. Only a confirmed provider-level stop or a confirmed brokerage credential revocation counts, and neither is checked live here." }],
    ]);

    // --- Backup/restore/deployment evidence (expanded detail) ---
    StateMatrix.render(els.backup, {
      state: "ready",
      html: `<p class="section-note">Expanded evidence for the three Subsystems rows above that this process cannot verify live end to end, plus the one it can (Deployment/schema).</p>${table(
        ["Condition", "Actual outcome", "Reason code", "Evidence", "Blocker / next step"],
        [
          [
            "Database schema reproducibility (Deployment)",
            schemaMatch ? pill("verified match", "ok") : pill("mismatch or unknown", "bad"),
            schemaMatch ? "SCHEMA_AT_HEAD" : "SCHEMA_MISMATCH",
            `live: version=${escapeHtml(info.schema_version || "unknown")}, code head=${escapeHtml(info.schema_head || "unknown")}`,
            schemaMatch ? "none" : "run `alembic upgrade head` against this database before trusting a restore built from this release",
          ],
          [
            "Off-site data backup (Litestream → R2)",
            pill("real mechanism, no live status from this API", "warn"),
            "NOT_OBSERVABLE_FROM_THIS_PROCESS",
            "deploy/litestream/litestream.yml, deploy/RUNBOOK.md",
            "Litestream runs as a separate process/binary with no status API wired into this service -- no last-replicated-at timestamp or RPO number is fabricated here",
          ],
          [
            "Restore procedure",
            pill("real, manual, no live status", "warn"),
            "NOT_OBSERVABLE_FROM_THIS_PROCESS",
            "deploy/RUNBOOK.md step 3",
            "litestream restore into a NEW path, PRAGMA integrity_check, review every restored lifecycle_state row's pending_entry/pending_exit -- none of this is observable from this running process",
          ],
        ],
        "No conditions."
      )}`,
    });

    // --- Owner access (unchanged) ---
    StateMatrix.render(els.owner, {
      state: "ready",
      html: `
        <ul>
          <li>Auth configured: ${boolPill(info.auth_configured)}</li>
          <li>Credential kind: <span class="mono">${escapeHtml(info.owner_credential_kind)}</span>${info.owner_credential_kind.startsWith("plain") ? ` ${pill("consider OWNER_PASSWORD_HASH instead", "warn")}` : ""}</li>
          <li>Session TTL: ${fmtNum(info.session_ttl_seconds)}s</li>
          <li>Force secure cookies: ${boolPill(info.force_secure_cookies)}</li>
        </ul>
        <p class="section-note">This build has exactly one owner credential (app/auth.py) -- no multi-user/role registry exists to list here. Only booleans about the credential are shown; the credential's own value is never read by this endpoint (see GET /system/info's own docstring).</p>
      `,
    });

    // --- Promotion readiness ---
    StateMatrix.render(els.readiness, {
      state: "ready",
      html: `
        <p><strong>${rollup.label === "ACTIVE" ? pill("running normally as active writer", "ok") : pill(rollup.label, rollup.tone === "crit" ? "bad" : rollup.tone === "warn" ? "warn" : "muted")}</strong></p>
        <p class="section-note">Per deploy/RUNBOOK.md, four things must be independently true before a standby is ever promoted -- none of this is automatic in this build ("Automatic promotion eligibility: not met" in that file), and this build has no automatic-promotion capability at all to gate (no endpoint, no button -- confirmed against app/main.py). "Automatic promotion should stay disabled until fencing is genuinely established" is therefore currently moot rather than merely satisfied: there is nothing to accidentally auto-enable. See the guided runbook below for the real, gated, human-executed path.</p>
      `,
    });

    // --- Guided recovery runbook (explicit gates) ---
    els.runbook.removeAttribute("aria-busy");
    renderRunbook(els.runbook, {
      schemaMatch,
      dbOk: health ? health.database_ok : null,
      authConfigured: info.auth_configured,
      pendingCount,
    });

    // --- Actions: no backing capability for any of the 3 ---
    els.actions.removeAttribute("aria-busy");
    Components.renderCapabilityState(els.actions, {
      status: "unsupported",
      reason: "TR-16-A01 (Inspect backup), TR-16-A02 (Open non-live restore job) and TR-16-A03 (Prepare promotion review) have no backing capability: this build has no site/release-manifest/backup-generation registry and no fencing-evidence store anywhere in app/db.py. There is also no promotion endpoint of any kind in app/main.py.",
      remediation: "Promotion stays the manual, human-executed deploy/RUNBOOK.md procedure, never a GUI action -- per this screen's own acceptance note there is deliberately no automatic-promote control regardless, and the guided runbook above never enables one.",
    });

    ctx.setChrome({ asOf: fetchedAt.toISOString() });
  }

  window.Views = window.Views || {};
  window.Views.tr16 = {
    title: "Private settings, site role and recovery",
    breadcrumb: "Trade / System",
    scope: "private_owner",
    origin: "private_execution",
    async render(ctx) {
      ctx.container.innerHTML = shell();
      await load(ctx);
      ctx.registerPoll("tr16", 30000, () => load(ctx));
    },
  };
  Router.register("/trade/system", "tr16");
})();
