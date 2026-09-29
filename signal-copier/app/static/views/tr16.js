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
 * Real backing data (all read-only GETs, no new endpoint added):
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
 * Rollup logic (explicit, computed ONLY from the real fields above --
 * see `computeRollup`):
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

  // --- Rollup: ACTIVE / STANDBY / DEGRADED / NOT READY -- see this
  // file's own module docstring for the exact, explicit logic and why
  // fencing is deliberately NOT folded in here. ---
  function computeRollup(info, health, healthOk) {
    if (!healthOk || !health) {
      return { label: "NOT READY", tone: "crit", reason: "GET /health was unreachable this cycle -- nothing below can be verified live." };
    }
    if (info.standby_mode) {
      return { label: "STANDBY", tone: "neutral", reason: "STANDBY_MODE=true -- this instance deliberately does not ingest signals, reconcile orders, or poll prices (app/main.py's lifespan/_standby_read_only_gate)." };
    }
    if (health.status !== "ok") {
      return {
        label: "NOT READY",
        tone: "crit",
        reason: `GET /health reports status="${health.status}" -- at least one of database_ok/price_monitor_ok/reconciler_ok is false.`,
      };
    }
    const relayDown = info.relay_ingress_configured && health.relay_ok === false;
    const outboxOverCeiling = health.outbox_backlog_ok === false;
    if (!health.provider_scout_ok || !health.equity_snapshotter_ok || relayDown || outboxOverCeiling) {
      return {
        label: "DEGRADED",
        tone: "warn",
        reason: outboxOverCeiling
          ? "The private export outbox backlog is over its configured storage ceiling -- see the Storage row below. Position protection itself is unaffected; this is an export/storage-reliability risk, not silently ignored."
          : "Every subsystem position-protection depends on is fresh, but an informational-only worker (provider scout, equity snapshotter, or the configured relay export) is not.",
      };
    }
    return { label: "ACTIVE", tone: "ok", reason: "This is the active writer, and every subsystem GET /health tracks is fresh." };
  }

  function shell() {
    return `
      <section class="tr-panel" id="tr16-status"><h2>Operational readiness</h2><div class="tr-panel-body"></div></section>
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

  async function load(ctx) {
    const els = {
      status: ctx.container.querySelector("#tr16-status .tr-panel-body"),
      role: ctx.container.querySelector("#tr16-p01 .tr-panel-body"),
      subsystems: ctx.container.querySelector("#tr16-p02 .tr-panel-body"),
      backup: ctx.container.querySelector("#tr16-p03 .tr-panel-body"),
      owner: ctx.container.querySelector("#tr16-p04 .tr-panel-body"),
      readiness: ctx.container.querySelector("#tr16-p05 .tr-panel-body"),
      runbook: ctx.container.querySelector("#tr16-p06 .tr-panel-body"),
      actions: ctx.container.querySelector("#tr16-actions .tr-panel-body"),
    };
    for (const el of Object.values(els)) StateMatrix.render(el, { state: "loading" });

    const [infoRes, healthRes, positionsRes, metricsText] = await Promise.all([
      ctx.fetchJSON("/system/info"),
      ctx.fetchJSON("/health"),
      ctx.fetchJSON("/positions"),
      fetchMetricsText(),
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

    // --- Operational readiness rollup (top) ---
    const rollup = computeRollup(info, health, healthRes.ok);
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
      `<p class="section-note">Computed strictly from GET /health and GET /system/info's own real fields -- see this file's module docstring for the exact rollup order (STANDBY, then NOT READY, then DEGRADED, then ACTIVE). Fencing is deliberately not part of this rollup; it gates the recovery runbook below instead.</p>`
    );

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
