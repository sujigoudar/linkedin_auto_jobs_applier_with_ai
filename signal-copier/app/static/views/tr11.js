/* TR-11: Routing and allocation rules (`#/trade/routing`).
 *
 * Real backing data/capability: GET/POST/PUT/DELETE /routing-rules
 * (app/main.py -- the live, already-tested config surface the legacy
 * dashboard's own Routing rules form uses; app/routing.py's
 * RoutingConfig.evaluate/destinations_for is the exact live algorithm
 * every signal is routed through), GET /providers (provider/analyst
 * settings overrides, app/providers.py), GET /accounts, GET
 * /capital-allocation (app/capital_allocator.py), and two new endpoints
 * this redesign adds:
 *
 *   - POST /routing-rules/simulate -- "if this signal arrived now, what
 *     would happen": calls the SAME real matching function
 *     (RoutingConfig.evaluate), the SAME real provider/analyst admission
 *     resolver (engine._effective_settings), the SAME real broker
 *     asset-class check, and the SAME real capital-reservation admission
 *     gate (engine._try_reserve_capital, reservation released immediately
 *     after so this is a true no-side-effect dry run) that a real signal
 *     goes through at ingestion time -- never a client-side
 *     reimplementation. See that endpoint's own docstring for exactly
 *     what it does and doesn't check.
 *   - POST /routing-rules/position-impact -- "which of my real open
 *     positions were routed under this rule, and would a pending edit to
 *     it change their future entry/exit routing" -- computed from real
 *     open positions (GET-equivalent of store.list_open_positions) and
 *     their real originating signal (store.
 *     list_filled_orders_with_signal_chronological), run through the
 *     same real destinations_for both before and after the edit.
 *
 * Honest gaps, disclosed rather than invented: this build's real
 * RoutingRule model (app/routing.py / config_routing_rules table) has
 * ONLY source, destinations (account_id list) and an optional
 * symbol_filter -- there is no analyst_ids/product_ids field on a rule
 * itself (analyst-level overrides are a SEPARATE provider/analyst
 * settings concept, app/providers.py, that narrows sizing/entry-
 * admission but never which rule matches). There is also no "strategy"
 * concept anywhere in this codebase (confirmed against app/models.py,
 * app/engine.py, app/routing.py) -- the routing graph below is Provider
 * -> Analyst -> Rule -> Account, never a fabricated Strategy tier. Rule
 * PRECEDENCE in this build is simply DB insertion/id order (see
 * app/db.py's list_config_routing_rules "ORDER BY id" and
 * app/routing.py's evaluate, which iterates self.rules in that same
 * order) -- shown honestly as "evaluation order" rather than an invented
 * priority number. There is also no separate two-stage draft/release gate
 * for a routing rule: POST/PUT here already IS the live config (same
 * disclosure pattern as TR-08's account form).
 *
 * Version diff (TR-11-A03): config_routing_rules stores only the current
 * row per id, no change log -- there is no real revision history to
 * diff or roll back to (disclosed honestly below, not fabricated). What
 * IS real and fully buildable without new persistence: a BEFORE/AFTER
 * diff of this rule's own currently-saved values vs the operator's
 * pending, not-yet-saved form edit -- shown here, and required (one
 * "Preview" click) before Save actually applies the change.
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

  function shell() {
    return `
      <section class="tr-panel" id="tr11-p01"><h2>Rule priority</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr11-p02"><h2>Match criteria</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr11-p07"><h2>Routing graph</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr11-p03"><h2>Destination preview</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr11-p08"><h2>Signal simulator -- "if this signal arrived now"</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr11-p04"><h2>Conflicts</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr11-p06"><h2>Rule draft</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr11-p05"><h2>Version diff and position impact</h2><div class="tr-panel-body"></div></section>
    `;
  }

  // Mirrors app/routing.py's RoutingConfig.evaluate/destinations_for
  // EXACTLY (source/symbol_filter match, then de-dup by account_id
  // keeping first-match order, then filter by account.enabled unless
  // include_disabled) -- read-only, no side effects, used only for the
  // cheap client-side "Destination preview" checklist below (a distinct,
  // lighter-weight panel from the real server-side "Signal simulator"
  // panel, which calls POST /routing-rules/simulate and therefore goes
  // through the actual engine code, never this client-side copy).
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
          out.push({ ...account, delivery_mode: rule.delivery_mode || "single" });
          seen.add(accountId);
        }
      }
    }
    return out;
  }

  function fieldsSignature(f) {
    return JSON.stringify(f);
  }

  async function load(ctx) {
    const els = {
      priority: ctx.container.querySelector("#tr11-p01 .tr-panel-body"),
      match: ctx.container.querySelector("#tr11-p02 .tr-panel-body"),
      graph: ctx.container.querySelector("#tr11-p07 .tr-panel-body"),
      preview: ctx.container.querySelector("#tr11-p03 .tr-panel-body"),
      simulator: ctx.container.querySelector("#tr11-p08 .tr-panel-body"),
      conflicts: ctx.container.querySelector("#tr11-p04 .tr-panel-body"),
      diff: ctx.container.querySelector("#tr11-p05 .tr-panel-body"),
      draft: ctx.container.querySelector("#tr11-p06 .tr-panel-body"),
    };
    for (const el of Object.values(els)) StateMatrix.render(el, { state: "loading" });

    const [rulesRes, accountsRes, providersRes] = await Promise.all([
      ctx.fetchJSON("/routing-rules"),
      ctx.fetchJSON("/accounts"),
      ctx.fetchJSON("/providers"),
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
    const providers = providersRes.ok ? providersRes.data.providers || [] : [];
    const accountsById = new Map(accounts.map((a) => [a.account_id, a]));

    if (!rules.length) {
      for (const key of ["priority", "match", "graph", "preview", "simulator", "conflicts"]) {
        StateMatrix.render(els[key], {
          state: "empty",
          emptyMessage: "No routing rules are configured.",
          nextRoute: "/trade/accounts",
          nextLabel: "Broker accounts and capabilities (TR-07)",
        });
      }
      StateMatrix.render(els.diff, {
        state: "empty",
        emptyMessage: "No routing rules to diff -- create one below first.",
      });
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
      (r.delivery_mode || "single") === "replicate"
        ? pill("replicate: every destination gets a copy", "warn")
        : pill("single: ONE account selected, in this order", "ok"),
    ]);
    StateMatrix.render(els.priority, {
      state: "ready",
      html: `<p class="section-note">This build has no explicit priority integer -- rules are evaluated in this exact order (ascending rule id / DB insertion order, per app/routing.py's evaluate/destinations_for), shown as "Evaluation order".</p>${table(
        ["Rule", "Evaluation order", "Provider/analyst", "Destinations", "Delivery mode"],
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
      capSlot(`tr11-cap-instrument-filter-${r.id}`),
    ]);
    StateMatrix.render(els.match, {
      state: "ready",
      html: table(
        ["Rule", "Source", "Assets", "Symbol filter", "Instrument filter"],
        matchRows,
        "No rules."
      ),
    });
    mountCapStates(
      els.match,
      rules.map((r) => [
        `tr11-cap-instrument-filter-${r.id}`,
        {
          status: "not_tracked",
          reason: "An instrument-verified filter distinct from the raw symbol string is not a tracked field in this build's rule model.",
        },
      ])
    );

    // --- Routing graph: Provider -> Analyst -> Rule -> Account (real config) ---
    renderRoutingGraph(els.graph, { rules, accounts, providers, accountsById });

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

    // --- Destination preview (cheap checklist, real dedup algorithm, client-side) ---
    const previewSourceOptions = [...new Set(rules.map((r) => r.source))];
    StateMatrix.render(els.preview, {
      state: "ready",
      html: `
        <label>Source<select id="tr11-preview-source">${previewSourceOptions.map((s) => `<option value="${escapeAttr(s)}">${escapeHtml(s)}</option>`).join("")}</select></label>
        <label>Symbol<input type="text" id="tr11-preview-symbol" placeholder="BTCUSDT"></label>
        <button type="button" id="tr11-preview-run">Preview matches</button>
        <p class="section-note">Recomputes app/routing.py's own documented dedup algorithm against the currently loaded config -- no broker call, no signal is created or routed. For entry admission, capital and asset-class checks, use the Signal simulator panel below (calls the real engine).</p>
        <div id="tr11-preview-result"></div>
      `,
    });
    els.preview.querySelector("#tr11-preview-run").addEventListener("click", () => {
      const source = els.preview.querySelector("#tr11-preview-source").value;
      const symbol = els.preview.querySelector("#tr11-preview-symbol").value.trim();
      const admitted = destinationsFor(rules, accountsById, source, symbol, false);
      const allMatches = destinationsFor(rules, accountsById, source, symbol, true);
      const firstSingle = admitted.find((x) => x.delivery_mode !== "replicate");
      const rows = allMatches.map((a) => {
        const isAdmitted = admitted.some((x) => x.account_id === a.account_id);
        let outcome = pill("deduped/paused", "warn");
        if (isAdmitted && a.delivery_mode === "replicate") outcome = pill("would receive this entry (replicate)", "ok");
        else if (isAdmitted && firstSingle && firstSingle.account_id === a.account_id) outcome = pill("selected (first eligible; capital/health gates run in the simulator)", "ok");
        else if (isAdmitted) outcome = pill("alternative -- not selected unless an earlier account is ineligible", "muted");
        return [
          `<span class="mono">${escapeHtml(a.account_id)}</span>`,
          boolPill(a.enabled, "admits new entries", "paused (exit-only)"),
          outcome,
        ];
      });
      const resultEl = els.preview.querySelector("#tr11-preview-result");
      resultEl.innerHTML = rows.length
        ? table(["Destination", "Entry admission", "Outcome"], rows, "No destinations.")
        : `<p class="sm-empty-message">No destination account matches this source/symbol.</p>`;
    });

    // --- Signal simulator: real dry-run through the real engine ---
    renderSimulator(els.simulator, { previewSourceOptions, ctx });

    function renderDraftForm() {
      StateMatrix.render(els.draft, {
        state: "ready",
        html: `
          <p class="section-note">This build has no separate draft/release stage for a routing rule -- Save here already applies to the live routing config (same disclosure as TR-08's account form). "New entries" below maps to the destination account's own existing enabled flag (TR-07), not a separate per-rule field. Before Save applies, use "Preview change" (or Save itself will run one preview first) to see the version diff and existing-position impact below.</p>
          <label>Existing rule (optional, to edit)<select id="tr11-draft-rule"><option value="">New rule</option>${rules.map((r) => `<option value="${r.id}">#${r.id} (${escapeHtml(r.source)})</option>`).join("")}</select></label>
          <label>Source<input type="text" id="tr11-draft-source" maxlength="80" placeholder="tradingview"></label>
          <label>Destinations (comma-separated account IDs)<input type="text" id="tr11-draft-destinations" placeholder="acct1,acct2"></label>
          <label>Symbol filter (comma-separated, optional -- blank = all symbols)<input type="text" id="tr11-draft-symbols"></label>
          <label>Delivery mode<select id="tr11-draft-mode">
            <option value="single">single -- destinations are alternatives; ONE account is selected per signal (first eligible, in the order listed)</option>
            <option value="replicate">replicate -- every destination receives its own copy (explicit fan-out)</option>
          </select></label>
          <div class="form-error" id="tr11-draft-error"></div>
          <div class="tr-controls-row">
            <button type="button" class="ghost" id="tr11-draft-preview">Preview change (diff + position impact)</button>
            <button type="button" id="tr11-draft-save">Save draft rule</button>
            <button type="button" class="ghost" id="tr11-draft-delete">Delete selected rule</button>
          </div>
          <div id="tr11-draft-result"></div>
        `,
      });

      let lastPreviewedSignature = null;

      function currentFields() {
        const source = els.draft.querySelector("#tr11-draft-source").value.trim();
        const destinations = els.draft.querySelector("#tr11-draft-destinations").value.split(",").map((s) => s.trim()).filter(Boolean);
        const symbolFilterRaw = els.draft.querySelector("#tr11-draft-symbols").value.trim();
        const symbolFilter = symbolFilterRaw ? symbolFilterRaw.split(",").map((s) => s.trim()).filter(Boolean) : null;
        const deliveryMode = els.draft.querySelector("#tr11-draft-mode").value;
        return { source, destinations, symbolFilter, deliveryMode };
      }

      const ruleSelect = els.draft.querySelector("#tr11-draft-rule");
      ruleSelect.addEventListener("change", () => {
        const rule = rules.find((r) => String(r.id) === ruleSelect.value);
        els.draft.querySelector("#tr11-draft-source").value = rule ? rule.source : "";
        els.draft.querySelector("#tr11-draft-destinations").value = rule ? rule.destinations.join(",") : "";
        els.draft.querySelector("#tr11-draft-symbols").value = rule && rule.symbol_filter ? rule.symbol_filter.join(",") : "";
        els.draft.querySelector("#tr11-draft-mode").value = rule ? rule.delivery_mode || "single" : "single";
        renderDiffAndImpact(rule || null, currentFields(), ctx, els.diff);
        lastPreviewedSignature = null;
      });

      async function runPreview() {
        const rule = rules.find((r) => String(r.id) === ruleSelect.value) || null;
        const fields = currentFields();
        await renderDiffAndImpact(rule, fields, ctx, els.diff);
        lastPreviewedSignature = fieldsSignature({ ruleId: ruleSelect.value, ...fields });
      }

      els.draft.querySelector("#tr11-draft-preview").addEventListener("click", () => { runPreview(); });

      els.draft.querySelector("#tr11-draft-save").addEventListener("click", async () => {
        const errorEl = els.draft.querySelector("#tr11-draft-error");
        errorEl.textContent = "";
        const { source, destinations, symbolFilter, deliveryMode } = currentFields();
        if (!source) { errorEl.textContent = "Source is required."; return; }
        if (!destinations.length) { errorEl.textContent = "At least one destination account is required."; return; }
        const ruleId = ruleSelect.value;
        const signature = fieldsSignature({ ruleId, source, destinations, symbolFilter, deliveryMode });
        if (signature !== lastPreviewedSignature) {
          // Before-you-save gate: force a real diff + position-impact
          // preview against these exact pending values before the first
          // Save click actually applies anything -- a second click with
          // the same values goes ahead and saves.
          await runPreview();
          els.draft.querySelector("#tr11-draft-result").innerHTML = `<p class="section-note">Reviewed the version diff and position impact below -- click "Save draft rule" again to apply this change.</p>`;
          return;
        }
        try {
          if (ruleId) {
            await putJSON(`/routing-rules/${encodeURIComponent(ruleId)}`, { source, destinations, symbol_filter: symbolFilter, delivery_mode: deliveryMode });
          } else {
            await postJSON("/routing-rules", { source, destinations, symbol_filter: symbolFilter, delivery_mode: deliveryMode });
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

      StateMatrix.render(els.diff, {
        state: "empty",
        emptyMessage: "Pick a rule (or start a new one) above, then Preview change to see the diff and position impact here.",
      });
    }
    renderDraftForm();

    ctx.setChrome({ asOf: new Date().toISOString() });
  }

  // --- Version diff (real, in-memory form-state diff -- no version
  // persistence in this build, see module docstring) + position impact
  // (real, from POST /routing-rules/position-impact). ---
  async function renderDiffAndImpact(rule, fields, ctx, diffEl) {
    StateMatrix.render(diffEl, { state: "loading" });

    const beforeSource = rule ? rule.source : null;
    const beforeDestinations = rule ? rule.destinations : [];
    const beforeSymbolFilter = rule && rule.symbol_filter ? rule.symbol_filter : null;

    function fmtList(list) {
      return list && list.length ? escapeHtml(list.join(", ")) : pill(rule ? "none" : "n/a (new rule)", "muted");
    }
    function diffRow(label, before, after, changed) {
      return [label, before, after, changed ? pill("changed", "warn") : pill("unchanged", "ok")];
    }
    const sourceChanged = beforeSource !== fields.source;
    const destChanged = JSON.stringify([...beforeDestinations].sort()) !== JSON.stringify([...fields.destinations].sort());
    const symbolChanged = JSON.stringify(beforeSymbolFilter) !== JSON.stringify(fields.symbolFilter);
    const beforeMode = rule ? rule.delivery_mode || "single" : null;
    const modeChanged = beforeMode !== fields.deliveryMode;

    const diffRows = [
      diffRow("Source", rule ? escapeHtml(beforeSource) : pill("n/a (new rule)", "muted"), escapeHtml(fields.source || "(blank)"), sourceChanged),
      diffRow("Destinations", fmtList(beforeDestinations), fmtList(fields.destinations), destChanged),
      diffRow(
        "Symbol filter",
        beforeSymbolFilter ? escapeHtml(beforeSymbolFilter.join(", ")) : pill("all symbols", "muted"),
        fields.symbolFilter ? escapeHtml(fields.symbolFilter.join(", ")) : pill("all symbols", "muted"),
        symbolChanged
      ),
      diffRow(
        "Delivery mode",
        beforeMode ? escapeHtml(beforeMode) : pill("n/a (new rule)", "muted"),
        escapeHtml(fields.deliveryMode || "single"),
        modeChanged
      ),
    ];
    const anyChange = sourceChanged || destChanged || symbolChanged || modeChanged;

    let diffHtml = `
      <p class="section-note">${rule ? `Diff for rule #${rule.id} against your pending, not-yet-saved edit.` : "Diff for a brand-new rule against your pending, not-yet-saved fields."} This build tracks no routing-rule revision history (config_routing_rules stores only the current row) -- this is a real BEFORE/AFTER comparison of the currently-saved values vs. your pending form edit, not a rollback-capable version history.</p>
      ${table(["Field", "Before (saved)", "After (pending)", ""], diffRows, "Nothing to diff.")}
    `;
    if (!anyChange) {
      diffHtml += `<p class="sm-empty-message">No changes pending.</p>`;
    }

    // Position impact -- only meaningful once a source is set (a brand-new
    // rule with a blank source has nothing real to check against yet).
    let impactHtml = "";
    if (fields.source) {
      try {
        const impact = await postJSON("/routing-rules/position-impact", {
          rule_id: rule ? rule.id : null,
          source: fields.source,
          destinations: fields.destinations,
          symbol_filter: fields.symbolFilter,
        });
        const positions = impact.positions || [];
        if (!positions.length) {
          impactHtml = `<h3>Existing position impact</h3><p class="sm-empty-message">No real open positions were routed under source "${escapeHtml(fields.source)}" -- nothing existing to check.</p>`;
        } else {
          const impactLabel = { unaffected: pill("unaffected", "ok"), future_entries_change: pill("future entries change", "warn"), exit_path_removed: pill("exit path removed", "bad") };
          const rows = positions.map((p) => [
            `<span class="mono">${escapeHtml(p.account_id)}</span>`,
            escapeHtml(p.symbol),
            fmtNum(p.net_quantity),
            p.origin_analyst ? escapeHtml(p.origin_analyst) : pill("n/a", "muted"),
            boolPill(p.would_route_now.close, "yes", "no"),
            boolPill(p.would_route_after_save.close, "yes", "no"),
            impactLabel[p.impact] || pill(p.impact, "muted"),
          ]);
          impactHtml = `
            <h3>Existing position impact</h3>
            <p class="section-note">Real open positions whose most recent real filled entry order originated from source "${escapeHtml(fields.source)}" (app/db.py's signal->order join) -- existing positions are never modified by a rule edit; this shows whether a FUTURE close signal for this source/symbol would still reach each one under your pending destinations/symbol_filter.</p>
            ${table(["Account", "Symbol", "Net qty", "Origin analyst", "Exit reaches it today", "Exit reaches it after save", "Impact"], rows, "No positions.")}
          `;
        }
      } catch (err) {
        impactHtml = `<h3>Existing position impact</h3><p class="sm-empty-message">Could not compute position impact: ${escapeHtml(err.message)}</p>`;
      }
    }

    diffEl.removeAttribute("aria-busy");
    diffEl.innerHTML = `<div class="sm-state sm-state-ready">${diffHtml}${impactHtml}</div>`;
  }

  // --- Signal simulator panel ---
  function renderSimulator(el, { previewSourceOptions, ctx }) {
    StateMatrix.render(el, {
      state: "ready",
      html: `
        <p class="section-note">Calls the real engine: POST /routing-rules/simulate reuses app/routing.py's RoutingConfig.evaluate (the exact matching/precedence/dedup function real signals are routed through), app/providers.py's ProviderRegistry.effective_settings (real entry-admission override), each real broker adapter's can_trade_asset_class, and the real, shared CapitalAllocator (app/capital_allocator.py) for capital reservation -- with any reservation released immediately, so this never leaves a side effect. No signal is created, no broker is called, nothing is persisted.</p>
        <div class="tr-controls-row">
          <label>Source<select id="tr11-sim-source">${previewSourceOptions.map((s) => `<option value="${escapeAttr(s)}">${escapeHtml(s)}</option>`).join("")}</select></label>
          <label>Symbol<input type="text" id="tr11-sim-symbol" placeholder="BTCUSDT"></label>
          <label>Side<select id="tr11-sim-side"><option value="buy">buy</option><option value="sell">sell</option><option value="close">close</option></select></label>
          <label>Asset class<select id="tr11-sim-asset-class"><option value="crypto">crypto</option><option value="forex">forex</option><option value="equity">equity</option><option value="option">option</option><option value="future">future</option></select></label>
        </div>
        <div class="tr-controls-row">
          <label>Analyst (optional)<input type="text" id="tr11-sim-analyst" placeholder="alice"></label>
          <label>Quantity (optional)<input type="number" step="any" id="tr11-sim-quantity"></label>
          <label>Price (optional -- needed for the capital-reservation check)<input type="number" step="any" id="tr11-sim-price"></label>
        </div>
        <button type="button" id="tr11-sim-run">Run simulation</button>
        <div id="tr11-sim-result"></div>
      `,
    });
    el.querySelector("#tr11-sim-run").addEventListener("click", async () => {
      const resultEl = el.querySelector("#tr11-sim-result");
      resultEl.innerHTML = `<div class="sm-state sm-state-loading"></div>`;
      const source = el.querySelector("#tr11-sim-source").value;
      const symbol = el.querySelector("#tr11-sim-symbol").value.trim();
      const side = el.querySelector("#tr11-sim-side").value;
      const assetClass = el.querySelector("#tr11-sim-asset-class").value;
      const analyst = el.querySelector("#tr11-sim-analyst").value.trim() || null;
      const quantityRaw = el.querySelector("#tr11-sim-quantity").value;
      const priceRaw = el.querySelector("#tr11-sim-price").value;
      if (!symbol) { resultEl.innerHTML = `<p class="sm-empty-message">Enter a symbol.</p>`; return; }
      try {
        const body = await postJSON("/routing-rules/simulate", {
          source,
          symbol,
          side,
          asset_class: assetClass,
          analyst,
          quantity: quantityRaw ? Number(quantityRaw) : null,
          price: priceRaw ? Number(priceRaw) : null,
        });
        resultEl.innerHTML = renderSimResult(body);
        const dedupSlot = resultEl.querySelector("#tr11-sim-dedup-slot");
        if (dedupSlot) Components.renderCapabilityState(dedupSlot, body.deduplication);
      } catch (err) {
        resultEl.innerHTML = `<p class="sm-empty-message">${escapeHtml(err.message)}</p>`;
      }
    });
  }

  function renderSimResult(body) {
    const ruleRows = body.rules_evaluated.map((r) => {
      if (!r.matched) {
        return [`#${r.id}`, escapeHtml(r.source), pill("not matched", "muted"), escapeHtml(r.reason)];
      }
      const parts = [];
      if (r.admitted_accounts.length) parts.push(`admitted: ${escapeHtml(r.admitted_accounts.join(", "))}`);
      if (r.deduped_accounts.length) parts.push(`deduped (SIG-01, earlier rule already claimed): ${escapeHtml(r.deduped_accounts.join(", "))}`);
      if (r.paused_accounts.length) parts.push(`paused (EXE-10, entry-disabled): ${escapeHtml(r.paused_accounts.join(", "))}`);
      return [`#${r.id}`, escapeHtml(r.source), pill("matched", "ok"), parts.join("; ") || "(no destinations configured)"];
    });

    const statusPill = (ok, label) => (ok ? pill(label || "pass", "ok") : pill(label || "fail", "bad"));
    const accountRows = body.accounts.map((a) => {
      const cap = a.capital_reservation;
      let capCell;
      if (cap.status === "not_applicable" || cap.status === "skipped") {
        capCell = `${pill(cap.status === "skipped" ? "skipped" : "n/a", "muted")} <span class="section-note">${escapeHtml(cap.reason)}</span>`;
      } else {
        capCell = `${statusPill(cap.status === "would_admit", cap.status === "would_admit" ? "would admit" : "would reject")} <span class="mono">req ${fmtNum(cap.requested_notional)} / deployed ${fmtNum(cap.deployed_notional)} / ceiling ${fmtNum(cap.max_notional_exposure)}</span>`;
      }
      return [
        `<span class="mono">${escapeHtml(a.account_id)}</span>`,
        escapeHtml(a.broker),
        escapeHtml(a.symbol_for_account),
        statusPill(a.entry_admission.status === "admitted"),
        statusPill(a.asset_class_admission.status === "admitted"),
        capCell,
        a.would_receive_this_signal ? pill(a.allocation && a.allocation.status === "selected" ? "SELECTED (one account per signal)" : "FINAL DESTINATION", "ok") : (a.allocation && a.allocation.status === "eligible_not_selected" ? pill("eligible alternative -- not selected", "muted") : pill("would not receive it", "bad")),
      ];
    });

    return `
      <div class="sm-state sm-state-ready">
        <h3>Rules evaluated (real order/precedence)</h3>
        ${table(["Rule", "Source", "Match", "Detail"], ruleRows, "No rules.")}
        <h3>Accounts (real admission checks)</h3>
        ${table(["Account", "Broker", "Symbol used", "Entry admission", "Asset class", "Capital reservation", "Outcome"], accountRows, "No candidate destinations matched.")}
        <h3>Deduplication</h3>
        <div>${capSlot("tr11-sim-dedup-slot")}</div>
        <h3>Final destination(s)</h3>
        ${body.final_destinations.length ? body.final_destinations.map((id) => pill(id, "ok")).join(" ") : `<p class="sm-empty-message">No destination would receive this signal.</p>`}
      </div>
    `;
  }

  // --- Routing graph: Provider -> Analyst -> Rule -> Account ---
  function renderRoutingGraph(el, { rules, accounts, providers, accountsById }) {
    const providerIds = [...new Set([...rules.map((r) => r.source), ...providers.map((p) => p.provider_id)])];
    const providersById = new Map(providers.map((p) => [p.provider_id, p]));

    // Real adjacency, derived entirely from the loaded config -- never a
    // fabricated example node/edge.
    const providerToRules = new Map(providerIds.map((p) => [p, rules.filter((r) => r.source === p).map((r) => r.id)]));
    const providerToAnalysts = new Map(
      providerIds.map((p) => [p, (providersById.get(p) ? providersById.get(p).analysts : []).map((a) => a.analyst_id)])
    );
    const ruleToAccounts = new Map(rules.map((r) => [r.id, r.destinations.filter((id) => accountsById.has(id))]));
    const accountIds = [...new Set(rules.flatMap((r) => r.destinations).filter((id) => accountsById.has(id)))];

    const nodeId = (tier, id) => `tr11-node-${tier}-${String(id).replace(/[^a-zA-Z0-9_-]/g, "_")}`;

    function providerBox(p) {
      const cfg = providersById.get(p);
      return `<div class="tr11-node" id="${nodeId("provider", p)}" data-tier="provider" data-key="${escapeAttr(p)}">
        <strong>${escapeHtml(p)}</strong>
        ${cfg && cfg.settings.enabled === false ? pill("disabled", "bad") : ""}
      </div>`;
    }
    function analystBox(p, analystId) {
      const cfg = providersById.get(p);
      const a = cfg ? cfg.analysts.find((x) => x.analyst_id === analystId) : null;
      return `<div class="tr11-node" id="${nodeId("analyst", `${p}__${analystId}`)}" data-tier="analyst" data-key="${escapeAttr(p)}">
        <strong>${escapeHtml(analystId)}</strong>
        ${a && a.settings.enabled === false ? pill("disabled", "bad") : ""}
      </div>`;
    }
    function ruleBox(r) {
      return `<div class="tr11-node" id="${nodeId("rule", r.id)}" data-tier="rule" data-key="${escapeAttr(r.source)}" data-accounts="${escapeAttr(r.destinations.join(","))}">
        <strong>#${r.id}</strong>
        <div class="section-note">${r.symbol_filter && r.symbol_filter.length ? escapeHtml(r.symbol_filter.join(", ")) : "all symbols"}</div>
      </div>`;
    }
    function accountBox(a) {
      return `<div class="tr11-node" id="${nodeId("account", a.account_id)}" data-tier="account" data-key="${escapeAttr(a.account_id)}">
        <strong class="mono">${escapeHtml(a.account_id)}</strong>
        ${boolPill(a.enabled, "enabled", "paused")}
      </div>`;
    }

    const allAnalystEntries = providerIds.flatMap((p) => providerToAnalysts.get(p).map((aid) => ({ p, aid })));

    const html = `
      <p class="section-note">Real current routing configuration, drawn from GET /providers + GET /routing-rules + GET /accounts -- every node/edge below is a real provider, analyst override, rule or destination account, never an example. A rule matches by <em>source</em> only (this build has no per-rule analyst scoping) -- the Analyst lane shows real per-analyst sizing/entry overrides (app/providers.py), which apply on top of whichever rule already matched its provider.</p>
      <div class="tr-controls-row">
        <label>Filter by provider<select id="tr11-graph-filter-provider"><option value="">All</option>${providerIds.map((p) => `<option value="${escapeAttr(p)}">${escapeHtml(p)}</option>`).join("")}</select></label>
        <label>Filter by account<select id="tr11-graph-filter-account"><option value="">All</option>${accountIds.map((id) => `<option value="${escapeAttr(id)}">${escapeHtml(id)}</option>`).join("")}</select></label>
      </div>
      <div class="tr11-graph-wrap" id="tr11-graph-wrap">
        <svg class="tr11-graph-edges" id="tr11-graph-edges"></svg>
        <div class="tr11-graph-col">
          <h4>Provider</h4>
          ${providerIds.map(providerBox).join("")}
        </div>
        <div class="tr11-graph-col">
          <h4>Analyst</h4>
          ${allAnalystEntries.length ? allAnalystEntries.map((e) => analystBox(e.p, e.aid)).join("") : `<p class="sm-empty-message">No analyst-level overrides configured.</p>`}
        </div>
        <div class="tr11-graph-col">
          <h4>Rule</h4>
          ${rules.map(ruleBox).join("")}
        </div>
        <div class="tr11-graph-col">
          <h4>Account</h4>
          ${accountIds.map((id) => accountBox(accountsById.get(id))).join("")}
        </div>
      </div>
    `;
    StateMatrix.render(el, { state: "ready", html });
    injectGraphStylesOnce();

    const wrap = el.querySelector("#tr11-graph-wrap");
    const svg = el.querySelector("#tr11-graph-edges");

    // Edges: provider->analyst, provider->rule, rule->account. Computed
    // from the exact same real adjacency the filters use below.
    const edges = [];
    for (const p of providerIds) {
      for (const aid of providerToAnalysts.get(p)) edges.push([nodeId("provider", p), nodeId("analyst", `${p}__${aid}`), "analyst"]);
      for (const ruleId of providerToRules.get(p)) edges.push([nodeId("provider", p), nodeId("rule", ruleId), "rule"]);
    }
    for (const r of rules) {
      for (const accountId of ruleToAccounts.get(r.id)) edges.push([nodeId("rule", r.id), nodeId("account", accountId), "account"]);
    }

    function drawEdges(dimSet) {
      const wrapRect = wrap.getBoundingClientRect();
      svg.setAttribute("width", wrapRect.width);
      svg.setAttribute("height", wrapRect.height);
      svg.setAttribute("viewBox", `0 0 ${wrapRect.width} ${wrapRect.height}`);
      const lines = edges.map(([fromId, toId]) => {
        const fromEl = el.querySelector(`#${fromId}`);
        const toEl = el.querySelector(`#${toId}`);
        if (!fromEl || !toEl) return "";
        const fr = fromEl.getBoundingClientRect();
        const tr = toEl.getBoundingClientRect();
        const x1 = fr.right - wrapRect.left;
        const y1 = fr.top - wrapRect.top + fr.height / 2;
        const x2 = tr.left - wrapRect.left;
        const y2 = tr.top - wrapRect.top + tr.height / 2;
        const dimmed = dimSet && (dimSet.has(fromId) === false || dimSet.has(toId) === false);
        const midX = (x1 + x2) / 2;
        return `<path d="M ${x1} ${y1} C ${midX} ${y1}, ${midX} ${y2}, ${x2} ${y2}" class="tr11-edge${dimmed ? " tr11-edge-dim" : ""}"></path>`;
      });
      svg.innerHTML = lines.join("");
    }

    function applyFilter() {
      const providerFilter = el.querySelector("#tr11-graph-filter-provider").value;
      const accountFilter = el.querySelector("#tr11-graph-filter-account").value;
      let highlighted = null; // null = no filter, show everything at full opacity
      if (providerFilter || accountFilter) {
        highlighted = new Set();
        if (providerFilter) {
          highlighted.add(nodeId("provider", providerFilter));
          for (const aid of providerToAnalysts.get(providerFilter) || []) highlighted.add(nodeId("analyst", `${providerFilter}__${aid}`));
          for (const ruleId of providerToRules.get(providerFilter) || []) {
            highlighted.add(nodeId("rule", ruleId));
            for (const accountId of ruleToAccounts.get(ruleId) || []) highlighted.add(nodeId("account", accountId));
          }
        }
        if (accountFilter) {
          highlighted.add(nodeId("account", accountFilter));
          for (const r of rules) {
            if ((ruleToAccounts.get(r.id) || []).includes(accountFilter)) {
              highlighted.add(nodeId("rule", r.id));
              highlighted.add(nodeId("provider", r.source));
              for (const aid of providerToAnalysts.get(r.source) || []) highlighted.add(nodeId("analyst", `${r.source}__${aid}`));
            }
          }
        }
      }
      el.querySelectorAll(".tr11-node").forEach((node) => {
        node.classList.toggle("tr11-node-dim", Boolean(highlighted) && !highlighted.has(node.id));
      });
      drawEdges(highlighted);
    }

    el.querySelector("#tr11-graph-filter-provider").addEventListener("change", applyFilter);
    el.querySelector("#tr11-graph-filter-account").addEventListener("change", applyFilter);
    requestAnimationFrame(() => drawEdges(null));
    window.addEventListener("resize", () => drawEdges(null), { once: false });
  }

  function injectGraphStylesOnce() {
    if (document.getElementById("tr11-graph-styles")) return;
    const style = document.createElement("style");
    style.id = "tr11-graph-styles";
    style.textContent = `
      .tr11-graph-wrap { position: relative; display: flex; gap: var(--space-6, 32px); overflow-x: auto; padding: var(--space-2, 8px) 0; }
      .tr11-graph-col { display: flex; flex-direction: column; gap: var(--space-3, 12px); min-width: 170px; flex: 1; z-index: 1; }
      .tr11-graph-col h4 { margin: 0 0 var(--space-1, 4px); color: var(--muted); font-weight: 600; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.03em; }
      .tr11-node { border: 1px solid var(--border); background: var(--panel); border-radius: var(--radius-sm, 6px); padding: var(--space-2, 8px) var(--space-3, 12px); font-size: 0.82rem; transition: opacity 0.15s ease; }
      .tr11-node-dim { opacity: 0.22; }
      .tr11-graph-edges { position: absolute; top: 0; left: 0; pointer-events: none; z-index: 0; }
      .tr11-edge { fill: none; stroke: var(--accent, #5b8cff); stroke-width: 1.5; opacity: 0.55; }
      .tr11-edge-dim { opacity: 0.08; }
    `;
    document.head.appendChild(style);
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
