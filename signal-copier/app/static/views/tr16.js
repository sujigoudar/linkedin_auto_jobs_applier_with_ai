/* TR-16: Private settings, site role and recovery (`#/trade/system`).
 *
 * Real backing data:
 *   - GET /health (public liveness/readiness -- see app/main.py's own
 *     docstring: `*_ok` false both for a stuck worker and before its
 *     first pass after startup, never hardcoded).
 *   - GET /system/info (NEW this batch, owner-gated): only non-secret
 *     operational facts, checked field-by-field against app/config.py's
 *     `_Settings` -- standby_mode (this instance's real writer/standby
 *     role, app/main.py's `_standby_read_only_gate`), relay
 *     environment/evidence-class labels, background-worker intervals,
 *     whether owner auth is configured and which credential KIND
 *     (hashed vs. plain -- never the credential itself), session TTL,
 *     and this exact database's live Alembic schema version compared to
 *     the deployed code's own migration head (E01 bounded -- see
 *     app/db.py's `alembic_code_head`/`SignalStore.schema_version`).
 *     NEVER returns an API key, token, password, password hash, webhook
 *     secret, or session secret -- see that endpoint's own docstring for
 *     the field-by-field check.
 *
 * Real backup/recovery evidence, honestly bounded: this codebase's actual
 * backup mechanism is Litestream replicating the live SQLite WAL to R2
 * (deploy/litestream/litestream.yml) under a documented, human-executed
 * promotion runbook (deploy/RUNBOOK.md) -- both real files, cited below.
 * Litestream runs as a SEPARATE process this API has no live status/API
 * into, so no last-replicated-at timestamp, RPO number, or backup-object
 * listing is fabricated here -- the one thing this process CAN verify
 * live is its own database's Alembic schema version against the deployed
 * code's migration head (E01's "deployment reproducibility" slice).
 * "Old-writer fencing" and "broker-confirmed position state" (RUNBOOK
 * steps 1 and 4) have no automated check in this build at all (per the
 * RUNBOOK's own "Automatic promotion eligibility: not met") -- shown as
 * real checklist rows requiring manual verification, never a fabricated
 * pass.
 *
 * F-RECOVERY-OPS (TR-16-A02/A03) and TR-16-A01 (Inspect backup) are all
 * `unsupported`: no site/release-manifest/backup-generation registry, no
 * fencing-evidence store, exists anywhere in this codebase. Per the
 * spec's own acceptance note, there is deliberately no automatic-promote
 * button here regardless.
 */
(function () {
  "use strict";

  function unsupportedNote(reason) {
    const el = document.createElement("div");
    StateMatrix.render(el, { state: "unsupported", reason });
    return el.outerHTML;
  }

  function shell() {
    return `
      <section class="tr-panel" id="tr16-p01"><h2>Site role/writer identity</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr16-p02"><h2>Dependencies</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr16-p03"><h2>Backup/restore status</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr16-p04"><h2>Owner access</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr16-p05"><h2>Readiness</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr16-p06"><h2>Recovery checklist</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr16-actions"><h2>Actions</h2><div class="tr-panel-body"></div></section>
    `;
  }

  async function load(ctx) {
    const els = {
      role: ctx.container.querySelector("#tr16-p01 .tr-panel-body"),
      deps: ctx.container.querySelector("#tr16-p02 .tr-panel-body"),
      backup: ctx.container.querySelector("#tr16-p03 .tr-panel-body"),
      owner: ctx.container.querySelector("#tr16-p04 .tr-panel-body"),
      readiness: ctx.container.querySelector("#tr16-p05 .tr-panel-body"),
      checklist: ctx.container.querySelector("#tr16-p06 .tr-panel-body"),
      actions: ctx.container.querySelector("#tr16-actions .tr-panel-body"),
    };
    for (const el of Object.values(els)) StateMatrix.render(el, { state: "loading" });

    const [infoRes, healthRes] = await Promise.all([
      ctx.fetchJSON("/system/info"),
      ctx.fetchJSON("/health"),
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

    // --- Dependencies (reuses the same public GET /health this app's own login screen checks) ---
    const depRows = [
      ["Database", health ? boolPill(health.database_ok) : pill("unknown", "muted")],
      ["Price monitor worker", health ? boolPill(health.price_monitor_ok) : pill("unknown", "muted")],
      ["Order reconciler worker", health ? boolPill(health.reconciler_ok) : pill("unknown", "muted")],
      ["Provider scout worker (informational only)", health ? boolPill(health.provider_scout_ok) : pill("unknown", "muted")],
      ["Relay export worker", health && health.relay_ok !== null && health.relay_ok !== undefined ? boolPill(health.relay_ok) : pill(info.relay_ingress_configured ? "unknown" : "not configured (RELAY_INGRESS_URL unset)", "muted")],
    ];
    StateMatrix.render(els.deps, {
      state: "ready",
      html: `<p class="section-note">Real background-worker liveness (GET /health) -- each flag is false both for a genuinely stuck worker and before its first pass after startup, never hardcoded true.</p>${table(["Component", "Status"], depRows, "No dependency data.")}`,
    });

    // --- Backup/restore status: checklist, do NOT collapse into one badge ---
    const schemaMatch = info.schema_version && info.schema_head && info.schema_version === info.schema_head;
    const backupChecklist = [
      [
        "Database schema reproducibility",
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
    ];
    StateMatrix.render(els.backup, {
      state: "ready",
      html: `<p class="section-note">Each condition is evaluated independently -- payment/connection/rights/trading-authority are never collapsed into one active badge (there is no payment/rights concept in this private owner-only build to begin with).</p>${table(
        ["Condition", "Actual outcome", "Reason code", "Evidence", "Blocker / next step"],
        backupChecklist,
        "No conditions."
      )}`,
    });

    // --- Owner access ---
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

    // --- Readiness ---
    const ready = !info.standby_mode && health && health.status === "ok";
    StateMatrix.render(els.readiness, {
      state: "ready",
      html: `
        <p><strong>${ready ? pill("ready to serve as active writer", "ok") : pill("not ready / standby", "warn")}</strong></p>
        <p class="section-note">Per deploy/RUNBOOK.md's real promotion runbook, four things must be independently true before a standby is ever promoted -- none of this is automatic in this build (see "Automatic promotion eligibility: not met" in that file): (1) the old writer is confirmed stopped/fenced against its broker, (2) the recovered database is verified usable (integrity check + recent PendingEntry/PendingExit review), (3) the new site's own credentials are freshly set (never trusted from a restore), (4) real resource headroom is confirmed. See Recovery checklist below for what this process can and cannot verify live.</p>
      `,
    });

    // --- Recovery checklist (table component; columns per this screen's
    // own table contract: Component, Mode, Last usable progress, Evidence, Blocker) ---
    const checklistRows = [
      [
        "Database schema",
        "verify (live, this process)",
        schemaMatch ? "at head" : `stamped ${escapeHtml(info.schema_version || "unknown")}, code expects ${escapeHtml(info.schema_head || "unknown")}`,
        "SignalStore.schema_version() vs. alembic_code_head() (app/db.py)",
        schemaMatch ? "none" : "migration pending",
      ],
      [
        "Off-site backup replication",
        "manual (external Litestream process)",
        "not observable from this API",
        "deploy/litestream/litestream.yml",
        "no live status/API into litestream from this process",
      ],
      [
        "Old-writer fencing",
        "manual (human-executed runbook)",
        "not tracked",
        "deploy/RUNBOOK.md “Before promotion” checklist, step 1",
        "no automated fencing/epoch mechanism exists in this build",
      ],
      [
        "Broker-confirmed position state",
        "manual",
        "not tracked",
        "deploy/RUNBOOK.md step 1",
        "no live broker-position readback is exposed by any GET endpoint in this build (same gap as Reconciliation and trading incidents, TR-13's broker/store comparison)",
      ],
    ];
    StateMatrix.render(els.checklist, {
      state: "ready",
      html: table(["Component", "Mode", "Last usable progress", "Evidence", "Blocker"], checklistRows, "No verified deployment or recovery evidence is recorded."),
    });

    // --- Actions: no backing capability for any of the 3 ---
    StateMatrix.render(els.actions, {
      state: "unsupported",
      reason: "TR-16-A01 (Inspect backup), TR-16-A02 (Open non-live restore job) and TR-16-A03 (Prepare promotion review) have no backing capability: this build has no site/release-manifest/backup-generation registry and no fencing-evidence store anywhere in app/db.py. Per this screen's own acceptance note there is deliberately no automatic-promote control regardless -- promotion stays the manual, human-executed deploy/RUNBOOK.md procedure, never a GUI action.",
    });

    ctx.setChrome({ asOf: new Date().toISOString() });
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
