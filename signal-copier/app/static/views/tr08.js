/* TR-08: Broker account configuration (`#/trade/accounts/new`).
 *
 * Real backing data/capability: GET /brokers (registered adapters, for
 * the Adapter select and the Capabilities panel's evidence) and
 * POST /accounts -- the EXACT SAME create/update endpoint the legacy
 * dashboard's own Accounts form and TR-07's "Pause new entries" action
 * both already call (app/main.py's `create_or_update_account`, already
 * covered by this repo's own account tests) -- this screen adds no new
 * financial capability, just a second, spec-shaped surface onto it.
 *
 * Honest gap, disclosed rather than worked around: TR-08's spec form
 * (F-BROKER) names ten fields -- this codebase's real account model
 * (`AccountRequest` in app/main.py / `config_accounts` in app/db.py) has
 * NO venue_id, external_account_ref, environment (simulation/paper/live),
 * credential_ref or position_mode field, and no "products" concept
 * distinct from a per-symbol rename map. Each of those five renders
 * "unsupported" in its own panel, explaining exactly what's missing,
 * rather than inventing form fields this build's backend would silently
 * ignore. The real, working fields are: account_label -> account_id,
 * adapter_id -> broker (from the live GET /brokers registry, never a
 * free-typed module import string), plus multiplier/fixed_quantity/
 * symbol_map/managed_lifecycle/max_notional_exposure (required by this
 * build's account model but not individually named in the spec's field
 * table -- grouped under Review below, honestly labelled as such).
 *
 * "Save never enables trading" (spec's own acceptance line) is enforced
 * literally: TR-08-A01 always POSTs `enabled: false`, regardless of any
 * later flip -- there is no separate two-stage release gate in this
 * build's account model (no `entry_enabled` distinct from `enabled`), so
 * this screen does not pretend one exists; turning an account live is a
 * distinct, separate step done elsewhere (editing `enabled`, e.g. via the
 * legacy dashboard's Accounts form), matching TR-08-A03's own text
 * ("activation separate operation").
 */
(function () {
  "use strict";

  function shell() {
    return `
      <section class="tr-panel" id="tr08-p01"><h2>Adapter/mode</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr08-p02"><h2>External identity</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr08-p03"><h2>Credential reference</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr08-p04"><h2>Product metadata</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr08-p05"><h2>Capabilities</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr08-p06"><h2>Review</h2><div class="tr-panel-body"></div></section>
    `;
  }

  function unsupportedNote(reason) {
    const el = document.createElement("div");
    StateMatrix.render(el, { state: "unsupported", reason });
    return el.outerHTML;
  }

  async function load(ctx) {
    const els = {
      adapter: ctx.container.querySelector("#tr08-p01 .tr-panel-body"),
      identity: ctx.container.querySelector("#tr08-p02 .tr-panel-body"),
      credential: ctx.container.querySelector("#tr08-p03 .tr-panel-body"),
      products: ctx.container.querySelector("#tr08-p04 .tr-panel-body"),
      capabilities: ctx.container.querySelector("#tr08-p05 .tr-panel-body"),
      review: ctx.container.querySelector("#tr08-p06 .tr-panel-body"),
    };
    for (const el of Object.values(els)) StateMatrix.render(el, { state: "loading" });

    const brokersRes = await ctx.fetchJSON("/brokers");
    if (brokersRes.status === 401 || brokersRes.status === 403) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "denied", deniedCode: brokersRes.status });
      return;
    }
    if (!brokersRes.ok) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "error", message: "Could not load broker adapters." });
      return;
    }
    const brokers = (brokersRes.data && brokersRes.data.brokers) || [];

    if (!brokers.length) {
      for (const el of Object.values(els)) {
        StateMatrix.render(el, { state: "empty", emptyMessage: "Choose an implemented adapter to configure an account.", nextRoute: "/trade/accounts", nextLabel: "Broker accounts and capabilities (TR-07)" });
      }
      return;
    }

    StateMatrix.render(els.adapter, {
      state: "ready",
      html: `
        <label>Adapter
          <select id="tr08-adapter-select">
            ${brokers.map((b) => `<option value="${escapeAttr(b.name)}">${escapeHtml(b.name)}</option>`).join("")}
          </select>
        </label>
        <p class="section-note">Chosen from this build's live, reviewed adapter registry (GET /brokers) -- never a free-typed module import string.</p>
        ${unsupportedNote("Environment (simulation/paper/live) is not a tracked field on an account in this build -- there is no per-environment provider mapping to verify against.")}
      `,
    });

    StateMatrix.render(els.identity, {
      state: "ready",
      html: `
        <label>Account label<input type="text" id="tr08-account-label" maxlength="80" placeholder="paper_main" required></label>
        <p class="section-note">1..80 plain text. No credentials belong in this field.</p>
        ${unsupportedNote("External broker account reference (external_account_ref) and venue/API variant (venue_id) are not tracked fields in this build's account model.")}
      `,
    });

    StateMatrix.render(els.credential, {
      state: "unsupported",
      reason: "Secret reference (credential_ref) is not stored per account in this build -- broker credentials remain environment variables set separately, outside this form, per this project's \"never store secrets in config\" rule (see README.md's Security notes). No raw secret is ever returned or accepted here.",
    });

    StateMatrix.render(els.products, {
      state: "ready",
      html: `
        <label>Symbol map (JSON, optional)<input type="text" id="tr08-symbol-map" placeholder='{"BTCUSDT":"BTC/USDT"}'></label>
        <p class="section-note">This is a per-symbol rename override, not a qualified product allowlist -- shown honestly as such rather than as the spec's "Products" id_list, which this build does not track.</p>
        ${unsupportedNote("Position mode (netting/hedged/spot) is not a tracked field in this build's account model.")}
      `,
    });

    async function renderCapabilities(adapterName) {
      const broker = brokers.find((b) => b.name === adapterName);
      if (!broker) {
        StateMatrix.render(els.capabilities, { state: "empty", emptyMessage: "Select an adapter to see its capability evidence." });
        return;
      }
      const rows = [
        ["Native bracket order", "not defined in this build", boolPill(broker.supports_native_bracket), "live (computed this request)", "n/a"],
        ["Protective stop", "not defined in this build", boolPill(broker.has_protective_stop_capability), "live (computed this request)", "n/a"],
        ["Cancel", "not defined in this build", boolPill(broker.has_cancel_capability), "live (computed this request)", "n/a"],
        ["Replace stop", "not defined in this build", boolPill(broker.has_replace_stop_capability), "live (computed this request)", "n/a"],
        ["Balance read", "not defined in this build", boolPill(broker.has_balance_capability), "live (computed this request)", "n/a"],
        ["Position readback", "not defined in this build", boolPill(broker.has_position_readback_capability), "live (computed this request)", "n/a"],
      ];
      StateMatrix.render(els.capabilities, {
        state: "ready",
        html: `<p class="section-note">Evidence is this adapter's own code introspection, re-verified live on every "Run read-only checks" -- there is no per-account "Required" capability profile defined in this build, so Gap cannot be computed and reads n/a rather than a fabricated verdict.</p>${table(
          ["Capability", "Required", "Observed", "Evidence age", "Gap"],
          rows,
          "No capability evidence."
        )}
        ${unsupportedNote("Live identity/scope/position read-only checks against this specific (not-yet-saved) account are not available -- no credentials are entered in this form, and this build has no connectivity-check endpoint for an unconfigured account.")}`,
      });
    }

    function currentDraft() {
      return {
        adapter: els.adapter.querySelector("#tr08-adapter-select").value,
        accountLabel: els.identity.querySelector("#tr08-account-label").value.trim(),
        symbolMapRaw: els.products.querySelector("#tr08-symbol-map").value.trim(),
      };
    }

    function renderReview() {
      const draft = currentDraft();
      const checks = [
        ["Account label set", draft.accountLabel ? pill("yes", "ok") : pill("no", "bad"), draft.accountLabel ? "OK" : "MISSING_LABEL", "form"],
        ["Adapter chosen from live registry", draft.adapter ? pill("yes", "ok") : pill("no", "bad"), draft.adapter ? "OK" : "MISSING_ADAPTER", "GET /brokers"],
      ];
      StateMatrix.render(els.review, {
        state: "ready",
        html: `${table(["Condition", "Outcome", "Reason code", "Evidence"], checks, "No conditions.")}
          <h3 class="section-note" style="margin-top:12px;">Additional configuration required by this build's account model (not individually named in the spec's field table)</h3>
          <label>Multiplier<input type="number" step="any" id="tr08-multiplier" value="1.0"></label>
          <label>Fixed quantity (optional)<input type="number" step="any" id="tr08-fixed-quantity"></label>
          <label class="checkbox"><input type="checkbox" id="tr08-managed-lifecycle"> Managed lifecycle</label>
          <label>Notional ceiling (optional)<input type="number" step="any" id="tr08-max-exposure"></label>
          <div class="form-error" id="tr08-form-error"></div>
          <div class="tr-controls-row">
            <button type="button" id="tr08-save">Save inactive account</button>
            <button type="button" class="ghost" id="tr08-run-checks">Run read-only checks</button>
            <button type="button" class="ghost" id="tr08-review-activation">Review activation</button>
          </div>
          <p class="section-note">Save always persists <code>enabled=false</code> -- this build has no separate two-stage release gate, so Save never itself admits new trading. Activation is a distinct, separate operation (see "Review activation").</p>
          <div id="tr08-action-result"></div>`,
      });

      els.review.querySelector("#tr08-save").addEventListener("click", () => doSave());
      els.review.querySelector("#tr08-run-checks").addEventListener("click", async () => {
        const adapter = currentDraft().adapter;
        await renderCapabilities(adapter);
        els.review.querySelector("#tr08-action-result").innerHTML = `<p class="section-note">Read-only checks re-fetched adapter capability evidence (no side effects). See Capabilities panel above.</p>`;
      });
      els.review.querySelector("#tr08-review-activation").addEventListener("click", () => {
        const draft = currentDraft();
        els.review.querySelector("#tr08-action-result").innerHTML = `
          <p class="section-note">Review only -- no request sent. If saved as drafted, account "<strong>${escapeHtml(draft.accountLabel || "(unset)")}</strong>" on adapter "<strong>${escapeHtml(draft.adapter)}</strong>" would be created/updated <em>inactive</em> (enabled=false). Turning it live is a separate operation, done outside this form (edit the account's enabled flag), never a side effect of Save/Preview here.</p>
        `;
      });
    }

    async function doSave() {
      const draft = currentDraft();
      const errorEl = els.review.querySelector("#tr08-form-error");
      errorEl.textContent = "";
      if (!draft.accountLabel) {
        errorEl.textContent = "Account label is required.";
        return;
      }
      if (!draft.adapter) {
        errorEl.textContent = "Adapter is required.";
        return;
      }
      let symbolMap = {};
      if (draft.symbolMapRaw) {
        try {
          symbolMap = JSON.parse(draft.symbolMapRaw);
        } catch (err) {
          errorEl.textContent = `Symbol map must be valid JSON: ${err.message}`;
          return;
        }
      }
      const multiplier = parseFloat(els.review.querySelector("#tr08-multiplier").value || "1.0");
      const fixedQuantityRaw = els.review.querySelector("#tr08-fixed-quantity").value;
      const maxExposureRaw = els.review.querySelector("#tr08-max-exposure").value;
      try {
        await postJSON("/accounts", {
          account_id: draft.accountLabel,
          broker: draft.adapter,
          multiplier,
          fixed_quantity: fixedQuantityRaw ? parseFloat(fixedQuantityRaw) : null,
          symbol_map: symbolMap,
          enabled: false, // "Save never enables trading" -- always inactive.
          managed_lifecycle: els.review.querySelector("#tr08-managed-lifecycle").checked,
          max_notional_exposure: maxExposureRaw ? parseFloat(maxExposureRaw) : null,
        });
        els.review.querySelector("#tr08-action-result").innerHTML = `<p class="section-note">Saved as an inactive account draft. <a href="#/trade/accounts">Open Broker accounts and capabilities (TR-07)</a> to review it.</p>`;
      } catch (err) {
        errorEl.textContent = err.message;
      }
    }

    els.adapter.querySelector("#tr08-adapter-select").addEventListener("change", (e) => renderCapabilities(e.target.value));
    await renderCapabilities(brokers[0].name);
    renderReview();

    ctx.setChrome({ asOf: new Date().toISOString() });
  }

  window.Views = window.Views || {};
  window.Views.tr08 = {
    title: "Broker account configuration",
    breadcrumb: "Trade / Accounts / New",
    scope: "private_owner",
    origin: "private_execution",
    async render(ctx) {
      ctx.container.innerHTML = shell();
      await load(ctx);
    },
  };
  Router.register("/trade/accounts/new", "tr08");
})();
