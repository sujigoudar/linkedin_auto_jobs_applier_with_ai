/* TR-07: Broker accounts and capabilities (`#/trade/accounts`).
 *
 * Real backing data: GET /accounts (config_accounts -- every live-managed
 * destination account) joined by broker name to GET /brokers (every
 * registered adapter's code-verified capability flags -- see that
 * route's own docstring: computed from whether the adapter overrides the
 * base no-op, not a name/imported-SDK claim). Balance/permission state:
 * GET /accounts/{id}/balance per configured account, same call TR-01
 * already makes for its own risk cards.
 *
 * Honest gaps, disclosed rather than worked around: the spec's column
 * list (Account, Adapter, Venue/API, Environment, Products, Connection,
 * Qualification, Writer site) is wider than what `config_accounts`
 * tracks. Venue/API variant, Environment (simulation/paper/live) and
 * Writer site have NO field anywhere in this schema -- shown as "not
 * tracked in this build" rather than invented. Products is approximated
 * from `symbol_map` (a rename map, not a product allowlist) and labelled
 * accordingly, never presented as a real restriction list.
 *
 * TR-07-A03 "Pause new entries" is a REAL action, not a new financial
 * capability: `account.enabled=False` is an existing, already-tested
 * entry pause (see app/routing.py's `destinations_for` and
 * app/engine.py's `handle_signal`, both documented "EXE-10" inline) that
 * stops new admissions on this exact account while explicitly preserving
 * its ability to exit/flatten an already-open position (CLOSE signals
 * pass `include_disabled=True`). This screen calls the exact same,
 * already-tested `POST /accounts` upsert the legacy dashboard's Accounts
 * form and TR-08 both use -- it changes no other field on the account.
 */
(function () {
  "use strict";

  // See tr04.js for why this placeholder-slot pattern exists: capability-
  // state badges render into a real DOM element, but this view builds
  // table rows as one HTML string before insertion.
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
      <section class="tr-panel" id="tr07-p01"><h2>Accounts</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr07-p02"><h2>Capability matrix</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr07-p03"><h2>Balance/permission state</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr07-p04"><h2>Change review</h2><div class="tr-panel-body"></div></section>
    `;
  }

  async function load(ctx) {
    const els = {
      accounts: ctx.container.querySelector("#tr07-p01 .tr-panel-body"),
      capabilities: ctx.container.querySelector("#tr07-p02 .tr-panel-body"),
      balance: ctx.container.querySelector("#tr07-p03 .tr-panel-body"),
      review: ctx.container.querySelector("#tr07-p04 .tr-panel-body"),
    };
    for (const el of Object.values(els)) StateMatrix.render(el, { state: "loading" });

    const [accountsRes, brokersRes, positionsRes] = await Promise.all([
      ctx.fetchJSON("/accounts"),
      ctx.fetchJSON("/brokers"),
      ctx.fetchJSON("/positions"),
    ]);
    if (accountsRes.status === 401 || accountsRes.status === 403) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "denied", deniedCode: accountsRes.status });
      return;
    }
    if (!accountsRes.ok || !brokersRes.ok) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "error", message: "Could not load accounts/broker capabilities." });
      return;
    }

    const accounts = (accountsRes.data && accountsRes.data.accounts) || [];
    const brokers = (brokersRes.data && brokersRes.data.brokers) || [];
    const brokersByName = new Map(brokers.map((b) => [b.name, b]));
    const lifecycles = (positionsRes.ok && positionsRes.data && positionsRes.data.managed_lifecycles) || [];
    const allPositions = (positionsRes.ok && positionsRes.data && positionsRes.data.positions) || [];
    const hasOpenExposure = new Set(allPositions.filter((p) => p.net_quantity !== 0).map((p) => p.account_id));

    // --- Capability matrix (independent of account count -- always the
    // full registered-broker read model). ---
    if (!brokers.length) {
      StateMatrix.render(els.capabilities, { state: "empty", emptyMessage: "No broker adapters are registered in this build." });
    } else {
      const rows = brokers.map((b) => [
        `<span class="mono" id="tr07-broker-${escapeAttr(b.name)}">${escapeHtml(b.name)}</span>`,
        boolPill(b.supports_native_bracket),
        boolPill(b.has_protective_stop_capability),
        boolPill(b.has_cancel_capability),
        boolPill(b.has_replace_stop_capability),
        boolPill(b.has_balance_capability),
        b.supported_asset_classes ? escapeHtml(b.supported_asset_classes.join(", ")) : pill("undeclared", "muted"),
      ]);
      StateMatrix.render(els.capabilities, {
        state: "ready",
        html: `<p class="section-note">Every flag is code-verified (computed from whether the adapter overrides the base no-op), not a name or imported-SDK claim.</p>${table(
          ["Adapter", "Native bracket", "Protective stop", "Cancel", "Replace stop", "Balance read", "Supported asset classes"],
          rows,
          "No brokers registered."
        )}`,
      });
    }

    if (!accounts.length) {
      StateMatrix.render(els.accounts, {
        state: "empty",
        emptyMessage: "No accounts are configured.",
        nextRoute: "/trade/accounts/new",
        nextLabel: "Broker account configuration (TR-08)",
      });
      StateMatrix.render(els.balance, { state: "empty", emptyMessage: "No accounts are configured." });
      StateMatrix.render(els.review, { state: "empty", emptyMessage: "No accounts are configured." });
      ctx.setChrome({ asOf: new Date().toISOString() });
      return;
    }

    const accountRows = accounts.map((a) => {
      const broker = brokersByName.get(a.broker);
      const products = Object.keys(a.symbol_map || {}).length
        ? `${Object.keys(a.symbol_map).length} symbol mapping(s)`
        : pill("no product allowlist (not restricted)", "muted");
      const qualification = !broker
        ? pill("broker not registered", "bad")
        : a.managed_lifecycle
        ? boolPill(broker.can_protect_a_managed_position, "qualified for managed lifecycle", "not qualified -- missing protective stop")
        : pill("qualified (plain account, no protection required)", "ok");
      return [
        `<a class="mono" href="#tr07-broker-${escapeAttr(a.broker)}" data-open-capability="${escapeAttr(a.broker)}">${escapeHtml(a.account_id)}</a>`,
        `<span class="mono">${escapeHtml(a.broker)}</span>`,
        capSlot(`tr07-cap-venue-${escapeAttr(a.account_id)}`),
        capSlot(`tr07-cap-env-${escapeAttr(a.account_id)}`),
        products,
        broker ? pill("registered adapter", "ok") : pill("no adapter registered", "bad"),
        typeof qualification === "string" ? qualification : qualification,
        capSlot(`tr07-cap-writer-${escapeAttr(a.account_id)}`),
      ];
    });
    StateMatrix.render(els.accounts, {
      state: "ready",
      html: `<p class="section-note">Products is approximated from each account's symbol_map (a rename map, not a real product allowlist) -- click an account to jump to its adapter's row in the Capability matrix below.</p>${table(
        ["Account", "Adapter", "Venue/API", "Environment", "Products", "Connection", "Qualification", "Writer site"],
        accountRows,
        "No accounts."
      )}`,
    });
    mountCapStates(
      els.accounts,
      accounts.flatMap((a) => [
        [`tr07-cap-venue-${escapeAttr(a.account_id)}`, { status: "not_tracked", reason: "Venue/API variant is not tracked per account in this build." }],
        [`tr07-cap-env-${escapeAttr(a.account_id)}`, { status: "not_tracked", reason: "Environment (simulation/paper/live) is not tracked per account in this build." }],
        [`tr07-cap-writer-${escapeAttr(a.account_id)}`, { status: "not_tracked", reason: "Writer site is not tracked per account in this build." }],
      ])
    );
    els.accounts.querySelectorAll("[data-open-capability]").forEach((a) => {
      a.addEventListener("click", () => {
        const target = els.capabilities.querySelector(`#tr07-broker-${CSS.escape(a.getAttribute("data-open-capability"))}`);
        if (target) target.scrollIntoView({ behavior: "smooth", block: "center" });
      });
    });

    // --- Balance/permission state ---
    const balances = await Promise.all(accounts.map((a) => ctx.fetchJSON(`/accounts/${encodeURIComponent(a.account_id)}/balance`)));
    const balanceRows = accounts.map((a, i) => {
      const b = balances[i].ok ? balances[i].data : null;
      return [
        `<span class="mono">${escapeHtml(a.account_id)}</span>`,
        b && b.equity !== null && b.equity !== undefined ? fmtNum(b.equity) : pill("unknown", "muted"),
        b && b.buying_power !== null && b.buying_power !== undefined ? fmtNum(b.buying_power) : pill("unknown / not applicable", "muted"),
        b && b.maintenance_margin !== null && b.maintenance_margin !== undefined ? fmtNum(b.maintenance_margin) : pill("unknown / not applicable", "muted"),
        balances[i].ok ? pill("reachable", "ok") : pill("unreachable", "bad"),
      ];
    });
    StateMatrix.render(els.balance, {
      state: "ready",
      html: table(["Account", "Equity", "Buying power", "Maintenance margin", "Connection"], balanceRows, "No accounts."),
    });

    // --- Change review checklist ---
    const reviewRows = accounts.flatMap((a) => {
      const broker = brokersByName.get(a.broker);
      const exposed = hasOpenExposure.has(a.account_id) || lifecycles.some((l) => l.account_id === a.account_id);
      return [
        [`${escapeHtml(a.account_id)}: broker registered`, broker ? pill("yes", "ok") : pill("no", "bad"), broker ? "OK" : "NO_ADAPTER", "GET /brokers (live)"],
        [`${escapeHtml(a.account_id)}: new entries admitted`, boolPill(a.enabled, "admitted", "paused"), a.enabled ? "OK" : "PAUSED", "GET /accounts (enabled)"],
        [`${escapeHtml(a.account_id)}: has open exposure`, boolPill(exposed, "yes", "no"), exposed ? "HAS_EXPOSURE" : "FLAT", "GET /positions (live)"],
      ];
    });
    const actionRows = accounts.map((a) => `
      <div class="tr-controls-row">
        <span class="mono">${escapeHtml(a.account_id)}</span>
        <button type="button" class="danger" data-pause-account="${escapeAttr(a.account_id)}" ${a.enabled ? "" : "disabled"}>Pause new entries</button>
        ${a.enabled ? "" : `<span class="section-note">Already paused.</span>`}
      </div>
    `).join("");
    StateMatrix.render(els.review, {
      state: "ready",
      html: `${table(["Condition", "Outcome", "Reason code", "Evidence"], reviewRows, "No conditions.")}
             <h3 class="section-note" style="margin-top:14px;">Pause new entries (TR-07-A03)</h3>
             <p class="section-note">Sets this exact account's own <code>enabled=false</code> via the same, already-tested POST /accounts upsert -- preserves authorized exits/flattens/outstanding commands (EXE-10). No other field on the account is changed.</p>
             ${actionRows}`,
    });
    els.review.querySelectorAll("[data-pause-account]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const accountId = btn.getAttribute("data-pause-account");
        const acct = accounts.find((a) => a.account_id === accountId);
        if (!acct) return;
        if (!confirm(`Pause new entries on account "${accountId}"? This does not close or affect any existing position.`)) return;
        try {
          await postJSON("/accounts", {
            account_id: acct.account_id,
            broker: acct.broker,
            multiplier: acct.multiplier,
            fixed_quantity: acct.fixed_quantity,
            symbol_map: acct.symbol_map,
            enabled: false,
            managed_lifecycle: acct.managed_lifecycle,
            max_notional_exposure: acct.max_notional_exposure,
          });
        } catch (err) {
          alert(`Could not pause account: ${err.message}`);
        }
        await load(ctx);
      });
    });

    ctx.setChrome({ asOf: new Date().toISOString() });
  }

  window.Views = window.Views || {};
  window.Views.tr07 = {
    title: "Broker accounts and capabilities",
    breadcrumb: "Trade / Accounts",
    scope: "private_owner",
    origin: "private_execution",
    async render(ctx) {
      ctx.container.innerHTML = shell();
      await load(ctx);
      ctx.registerPoll("tr07", 10000, () => load(ctx));
    },
  };
  Router.register("/trade/accounts", "tr07");
})();
