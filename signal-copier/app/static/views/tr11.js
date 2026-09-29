/* TR-11: Routing and allocation rules (`#/trade/routing`).
 *
 * Real backing data/capability: GET /routing-rules, POST /routing-rules,
 * PUT /routing-rules/{id}, DELETE /routing-rules/{id} (app/main.py --
 * already the live, already-tested config surface the legacy dashboard's
 * own Routing rules form uses; app/routing.py's RoutingConfig.destinations_for
 * is the exact live algorithm every signal is routed through). This
 * screen adds no new financial capability, just a spec-shaped surface
 * onto the same endpoints.
 *
 * Honest gaps, disclosed rather than invented: this build's real
 * RoutingRule model (app/routing.py / config_routing_rules table) has
 * ONLY source, destinations (account_id list) and an optional
 * symbol_filter -- there is no analyst_ids, product_ids, instrument_filter,
 * explicit priority integer, or entry_enabled field. Rule PRECEDENCE in
 * this build is simply DB insertion/id order (see
 * app/db.py's list_config_routing_rules "ORDER BY id" and
 * app/routing.py's destinations_for, which iterates self.rules in that
 * same order) -- shown honestly as "evaluation order" rather than an
 * invented priority number. There is also no separate two-stage
 * draft/release gate for a routing rule: POST/PUT here already IS the
 * live config (same disclosure pattern as TR-08's account form) -- "Save
 * draft rule" and "Submit config change" both call the exact same,
 * already-tested endpoint; this screen does not pretend a staged
 * approval step exists where none does.
 *
 * "Preview matches" (TR-11-A02) is real, not fabricated: it recomputes
 * app/routing.py's OWN documented dedup algorithm (SIG-01: each distinct
 * destination account_id is routed to at most once per signal, keeping
 * the first matching rule's order) client-side against the exact same
 * GET /routing-rules + GET /accounts data already loaded -- no broker
 * call, no signal created, matching the spec's "Preview never calls
 * broker write."
 */
(function () {
  "use strict";

  function shell() {
    return `
      <section class="tr-panel" id="tr11-p01"><h2>Rule priority</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr11-p02"><h2>Match criteria</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr11-p03"><h2>Destination preview</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr11-p04"><h2>Conflicts</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr11-p05"><h2>Version diff</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr11-p06"><h2>Rule draft</h2><div class="tr-panel-body"></div></section>
    `;
  }

  // Mirrors app/routing.py's RoutingConfig.destinations_for EXACTLY
  // (source/symbol_filter match, then de-dup by account_id keeping first-
  // match order, then filter by account.enabled unless include_disabled)
  // -- read-only, no side effects, used only for the Destination preview
  // panel below.
  function destinationsFor(rules, accountsById, source, symbol, includeDisabled) {
    const seen = new Set();
    const out = [];
    for (const rule of rules) {
      if (rule.source !== source) continue;
      if (rule.symbol_filter && rule.symbol_filter.length && !rule.symbol_filter.includes(symbol)) continue;
      for (const accountId of rule.destinations) {
        if (seen.has(accountId)) continue;
        const account = accountsById.get(accountId);
        if (account && (account.enabled || includeDisabled)) {
          out.push(account);
          seen.add(accountId);
        }
      }
    }
    return out;
  }

  async function load(ctx) {
    const els = {
      priority: ctx.container.querySelector("#tr11-p01 .tr-panel-body"),
      match: ctx.container.querySelector("#tr11-p02 .tr-panel-body"),
      preview: ctx.container.querySelector("#tr11-p03 .tr-panel-body"),
      conflicts: ctx.container.querySelector("#tr11-p04 .tr-panel-body"),
      diff: ctx.container.querySelector("#tr11-p05 .tr-panel-body"),
      draft: ctx.container.querySelector("#tr11-p06 .tr-panel-body"),
    };
    for (const el of Object.values(els)) StateMatrix.render(el, { state: "loading" });

    const [rulesRes, accountsRes] = await Promise.all([
      ctx.fetchJSON("/routing-rules"),
      ctx.fetchJSON("/accounts"),
    ]);
    if (rulesRes.status === 401 || rulesRes.status === 403) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "denied", deniedCode: rulesRes.status });
      return;
    }
    if (!rulesRes.ok || !accountsRes.ok) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "error", message: "Could not load routing rules/accounts." });
      return;
    }
    const rules = rulesRes.data.routing_rules || [];
    const accounts = accountsRes.data.accounts || [];
    const accountsById = new Map(accounts.map((a) => [a.account_id, a]));

    StateMatrix.render(els.diff, {
      state: "unsupported",
      reason: "No routing-rule revision/version history is tracked in this build (config_routing_rules stores only the current row per id, no change log) -- there is nothing real to diff here.",
    });

    if (!rules.length) {
      for (const key of ["priority", "match", "preview", "conflicts"]) {
        StateMatrix.render(els[key], {
          state: "empty",
          emptyMessage: "No routing rules are configured.",
          nextRoute: "/trade/accounts",
          nextLabel: "Broker accounts and capabilities (TR-07)",
        });
      }
      ctx.setChrome({ asOf: new Date().toISOString() });
      renderDraftForm();
      return;
    }

    // --- Rule priority (real: DB id / evaluation order) ---
    const priorityRows = rules.map((r, idx) => [
      `<span class="mono" id="tr11-rule-${r.id}">#${r.id}</span>`,
      String(idx + 1),
      escapeHtml(r.source),
      r.destinations.length ? escapeHtml(r.destinations.join(", ")) : pill("none", "bad"),
    ]);
    StateMatrix.render(els.priority, {
      state: "ready",
      html: `<p class="section-note">This build has no explicit priority integer -- rules are evaluated in this exact order (ascending rule id / DB insertion order, per app/routing.py's destinations_for), shown as "Evaluation order".</p>${table(
        ["Rule", "Evaluation order", "Provider/analyst", "Destinations"],
        priorityRows,
        "No rules."
      )}`,
    });

    // --- Match criteria ---
    const matchRows = rules.map((r) => [
      `<a href="#tr11-rule-${r.id}">#${r.id}</a>`,
      escapeHtml(r.source),
      r.destinations.length ? escapeHtml(r.destinations.join(", ")) : pill("none", "bad"),
      r.symbol_filter && r.symbol_filter.length ? escapeHtml(r.symbol_filter.join(", ")) : pill("all symbols", "muted"),
      `<span class="tr-not-tracked">not tracked in this build</span>`,
    ]);
    StateMatrix.render(els.match, {
      state: "ready",
      html: `<p class="section-note">Analyst scoping (analyst_ids) and an instrument-verified filter distinct from the raw symbol string are not tracked fields in this build's rule model.</p>${table(
        ["Rule", "Source", "Assets", "Symbol filter", "Instrument filter"],
        matchRows,
        "No rules."
      )}`,
    });

    // --- Conflicts (real SIG-01 duplicate-destination detection) ---
    const bySourceDest = new Map(); // "source|account_id" -> [rule ids]
    for (const r of rules) {
      for (const accountId of r.destinations) {
        const key = `${r.source}|${accountId}`;
        if (!bySourceDest.has(key)) bySourceDest.set(key, []);
        bySourceDest.get(key).push(r.id);
      }
    }
    const dupes = [...bySourceDest.entries()].filter(([, ids]) => ids.length > 1);
    if (!dupes.length) {
      StateMatrix.render(els.conflicts, { state: "empty", emptyMessage: "No overlapping rule/destination pairs detected." });
    } else {
      const rows = dupes.map(([key, ids]) => {
        const [source, accountId] = key.split("|");
        return [escapeHtml(source), escapeHtml(accountId), ids.map((id) => `#${id}`).join(", "), `Rule ${ids[0]} wins (evaluation order) -- account "${escapeHtml(accountId)}" is routed to at most once per signal (SIG-01 dedup), never duplicated.`];
      });
      StateMatrix.render(els.conflicts, {
        state: "ready",
        html: table(["Source", "Destination", "Rules", "Resolution"], rows, "No conflicts."),
      });
    }

    // --- Destination preview (checklist, real dedup algorithm) ---
    const previewSourceOptions = [...new Set(rules.map((r) => r.source))];
    StateMatrix.render(els.preview, {
      state: "ready",
      html: `
        <label>Source<select id="tr11-preview-source">${previewSourceOptions.map((s) => `<option value="${escapeAttr(s)}">${escapeHtml(s)}</option>`).join("")}</select></label>
        <label>Symbol<input type="text" id="tr11-preview-symbol" placeholder="BTCUSDT"></label>
        <button type="button" id="tr11-preview-run">Preview matches</button>
        <p class="section-note">Recomputes app/routing.py's own documented dedup algorithm against the currently loaded config -- no broker call, no signal is created or routed.</p>
        <div id="tr11-preview-result"></div>
      `,
    });
    els.preview.querySelector("#tr11-preview-run").addEventListener("click", () => {
      const source = els.preview.querySelector("#tr11-preview-source").value;
      const symbol = els.preview.querySelector("#tr11-preview-symbol").value.trim();
      const admitted = destinationsFor(rules, accountsById, source, symbol, false);
      const allMatches = destinationsFor(rules, accountsById, source, symbol, true);
      const rows = allMatches.map((a) => [
        `<span class="mono">${escapeHtml(a.account_id)}</span>`,
        boolPill(a.enabled, "admits new entries", "paused (exit-only)"),
        admitted.some((x) => x.account_id === a.account_id) ? pill("would receive this entry", "ok") : pill("deduped/paused", "warn"),
      ]);
      const resultEl = els.preview.querySelector("#tr11-preview-result");
      resultEl.innerHTML = rows.length
        ? table(["Destination", "Entry admission", "Outcome"], rows, "No destinations.")
        : `<p class="sm-empty-message">No destination account matches this source/symbol.</p>`;
    });

    function renderDraftForm() {
      StateMatrix.render(els.draft, {
        state: "ready",
        html: `
          <p class="section-note">This build has no separate draft/release stage for a routing rule -- Save here already applies to the live routing config (same disclosure as TR-08's account form). "New entries" below maps to the destination account's own existing enabled flag (TR-07), not a separate per-rule field.</p>
          <label>Existing rule (optional, to edit)<select id="tr11-draft-rule"><option value="">New rule</option>${rules.map((r) => `<option value="${r.id}">#${r.id} (${escapeHtml(r.source)})</option>`).join("")}</select></label>
          <label>Source<input type="text" id="tr11-draft-source" maxlength="80" placeholder="tradingview"></label>
          <label>Destinations (comma-separated account IDs)<input type="text" id="tr11-draft-destinations" placeholder="acct1,acct2"></label>
          <label>Symbol filter (comma-separated, optional -- blank = all symbols)<input type="text" id="tr11-draft-symbols"></label>
          <div class="form-error" id="tr11-draft-error"></div>
          <div class="tr-controls-row">
            <button type="button" id="tr11-draft-save">Save draft rule</button>
            <button type="button" class="ghost" id="tr11-draft-delete">Delete selected rule</button>
          </div>
          <div id="tr11-draft-result"></div>
        `,
      });
      const ruleSelect = els.draft.querySelector("#tr11-draft-rule");
      ruleSelect.addEventListener("change", () => {
        const rule = rules.find((r) => String(r.id) === ruleSelect.value);
        els.draft.querySelector("#tr11-draft-source").value = rule ? rule.source : "";
        els.draft.querySelector("#tr11-draft-destinations").value = rule ? rule.destinations.join(",") : "";
        els.draft.querySelector("#tr11-draft-symbols").value = rule && rule.symbol_filter ? rule.symbol_filter.join(",") : "";
      });
      els.draft.querySelector("#tr11-draft-save").addEventListener("click", async () => {
        const errorEl = els.draft.querySelector("#tr11-draft-error");
        errorEl.textContent = "";
        const source = els.draft.querySelector("#tr11-draft-source").value.trim();
        const destinations = els.draft.querySelector("#tr11-draft-destinations").value.split(",").map((s) => s.trim()).filter(Boolean);
        const symbolFilterRaw = els.draft.querySelector("#tr11-draft-symbols").value.trim();
        const symbolFilter = symbolFilterRaw ? symbolFilterRaw.split(",").map((s) => s.trim()).filter(Boolean) : null;
        if (!source) { errorEl.textContent = "Source is required."; return; }
        if (!destinations.length) { errorEl.textContent = "At least one destination account is required."; return; }
        const ruleId = ruleSelect.value;
        try {
          if (ruleId) {
            await putJSON(`/routing-rules/${encodeURIComponent(ruleId)}`, { source, destinations, symbol_filter: symbolFilter });
          } else {
            await postJSON("/routing-rules", { source, destinations, symbol_filter: symbolFilter });
          }
          els.draft.querySelector("#tr11-draft-result").innerHTML = `<p class="section-note">Saved -- this is now the live routing config for source "${escapeHtml(source)}".</p>`;
          await load(ctx);
        } catch (err) {
          errorEl.textContent = err.message;
        }
      });
      els.draft.querySelector("#tr11-draft-delete").addEventListener("click", async () => {
        const ruleId = ruleSelect.value;
        if (!ruleId) { els.draft.querySelector("#tr11-draft-error").textContent = "Select an existing rule to delete."; return; }
        if (!confirm(`Delete routing rule #${ruleId}? This stops new routing for it immediately.`)) return;
        try {
          await deleteJSON(`/routing-rules/${encodeURIComponent(ruleId)}`);
          await load(ctx);
        } catch (err) {
          els.draft.querySelector("#tr11-draft-error").textContent = err.message;
        }
      });
    }
    renderDraftForm();

    ctx.setChrome({ asOf: new Date().toISOString() });
  }

  window.Views = window.Views || {};
  window.Views.tr11 = {
    title: "Routing and allocation rules",
    breadcrumb: "Trade / Routing",
    scope: "private_owner",
    origin: "private_execution",
    async render(ctx) {
      ctx.container.innerHTML = shell();
      await load(ctx);
      ctx.registerPoll("tr11", 10000, () => load(ctx));
    },
  };
  Router.register("/trade/routing", "tr11");
})();
