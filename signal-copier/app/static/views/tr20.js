/* TR-20: Operations center (alerts, halts, unresolved commands, intents).
 *
 * One screen with four panels, each polling its endpoint every 15 seconds:
 * 1. Alerts — unacknowledged first, with "Acknowledge" button
 * 2. Risk halts — from GET /risk-halts (WP-30); gracefully handles 404
 * 3. Unresolved commands — from GET /command-ledger/unresolved with
 *    "Mark not placed" action (uses action-confirm.js)
 * 4. Allocation intents and strategy budgets — intents with state,
 *    budgets editable inline
 *
 * Badge in nav shows count of unacknowledged alerts + open halts +
 * unresolved commands, refreshed along with the main polling cycle.
 */
(function () {
  "use strict";

  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  function shell() {
    return `
      <section class="tr-panel" id="tr20-alerts"><h2>Alerts</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr20-halts"><h2>Risk halts</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr20-commands"><h2>Unresolved commands</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr20-intents"><h2>Allocation intents & budgets</h2><div class="tr-panel-body"></div></section>
    `;
  }

  async function loadAlerts(ctx) {
    const el = ctx.container.querySelector("#tr20-alerts .tr-panel-body");
    StateMatrix.render(el, { state: "loading" });

    try {
      const data = await ctx.fetchJSON("/alerts?unacknowledged=true");
      const alerts = data.alerts || [];

      if (alerts.length === 0) {
        StateMatrix.render(el, { state: "empty", emptyMessage: "No unacknowledged alerts." });
        return [];
      }

      const rows = alerts.map((a) => {
        const timestamp = new Date(a.created_at).toLocaleTimeString();
        return `
          <div class="tr20-alert-row">
            <div class="tr20-alert-info">
              <div class="tr20-alert-kind">${escapeHtml(a.kind)}</div>
              <div class="tr20-alert-account">${a.account_id ? escapeHtml(a.account_id) : "(system)"}</div>
              <div class="tr20-alert-message">${escapeHtml(a.message)}</div>
              <div class="tr20-alert-time">${timestamp}</div>
            </div>
            <button class="tr20-alert-ack" onclick="window.TR20_AckAlert('${escapeHtml(a.id)}')">Acknowledge</button>
          </div>
        `;
      });

      el.innerHTML = rows.join("");
      StateMatrix.render(el, { state: "ready" });
      return alerts;
    } catch (err) {
      StateMatrix.render(el, { state: "error", errorMessage: err.message });
      return [];
    }
  }

  async function loadHalts(ctx) {
    const el = ctx.container.querySelector("#tr20-halts .tr-panel-body");
    StateMatrix.render(el, { state: "loading" });

    try {
      // Fetch risk halts; if endpoint not available, show capability-state
      let halts = [];
      try {
        const data = await ctx.fetchJSON("/risk-halts");
        halts = data.halts || [];
      } catch (fetchErr) {
        // fetchJSON may throw on 404; check if it's a "not found" error
        if (fetchErr.message && fetchErr.message.includes("404")) {
          // WP-30 not yet implemented; show capability-state
          const capSlot = document.createElement("div");
          el.appendChild(capSlot);
          Components.renderCapabilityState(capSlot, {
            status: "not_tracked",
            reason: "risk halts endpoint not available in this build (WP-30 not yet integrated)"
          });
          StateMatrix.render(el, { state: "ready" });
          return [];
        }
        throw fetchErr;
      }

      if (halts.length === 0) {
        StateMatrix.render(el, { state: "empty", emptyMessage: "No active risk halts." });
        return [];
      }

      const rows = halts.map((h) => {
        const timestamp = new Date(h.triggered_at).toLocaleTimeString();
        return `
          <div class="tr20-halt-row">
            <div class="tr20-halt-info">
              <div class="tr20-halt-account">${escapeHtml(h.account_id)}</div>
              <div class="tr20-halt-reason">${escapeHtml(h.reason)}</div>
              <div class="tr20-halt-time">Triggered: ${timestamp}</div>
            </div>
            <button class="tr20-halt-clear" onclick="window.TR20_ClearHalt('${escapeHtml(h.account_id)}')">Clear halt</button>
          </div>
        `;
      });

      el.innerHTML = rows.join("");
      StateMatrix.render(el, { state: "ready" });
      return halts;
    } catch (err) {
      StateMatrix.render(el, { state: "error", errorMessage: err.message });
      return [];
    }
  }

  async function loadCommands(ctx) {
    const el = ctx.container.querySelector("#tr20-commands .tr-panel-body");
    StateMatrix.render(el, { state: "loading" });

    try {
      const data = await ctx.fetchJSON("/command-ledger/unresolved");
      const commands = data.unresolved || [];

      if (commands.length === 0) {
        StateMatrix.render(el, { state: "empty", emptyMessage: "No unresolved commands." });
        return [];
      }

      const rows = commands.map((c) => {
        const timestamp = new Date(c.created_at).toLocaleTimeString();
        return `
          <div class="tr20-command-row">
            <div class="tr20-command-info">
              <div class="tr20-command-type">${escapeHtml(c.command_type)}</div>
              <div class="tr20-command-account">${escapeHtml(c.account_id)}</div>
              <div class="tr20-command-key">${escapeHtml(c.idempotency_key)}</div>
              <div class="tr20-command-time">Since: ${timestamp}</div>
            </div>
            <button class="tr20-command-resolve" onclick="window.TR20_ResolveCommand('${escapeHtml(c.idempotency_key)}')">Mark not placed</button>
          </div>
        `;
      });

      el.innerHTML = rows.join("");
      StateMatrix.render(el, { state: "ready" });
      return commands;
    } catch (err) {
      StateMatrix.render(el, { state: "error", errorMessage: err.message });
      return [];
    }
  }

  async function loadIntents(ctx) {
    const el = ctx.container.querySelector("#tr20-intents .tr-panel-body");
    StateMatrix.render(el, { state: "loading" });

    try {
      const [intentsData, budgetsData] = await Promise.all([
        ctx.fetchJSON("/allocation-intents?limit=100").then(d => d.allocation_intents || []).catch(() => []),
        ctx.fetchJSON("/strategy-budgets").then(d => d.strategy_budgets || []).catch(() => []),
      ]);

      if (intentsData.length === 0 && budgetsData.length === 0) {
        StateMatrix.render(el, { state: "empty", emptyMessage: "No allocation intents or strategy budgets." });
        return [];
      }

      let html = "";

      if (intentsData.length > 0) {
        html += `<h3 class="tr20-subsection-title">Recent intents</h3>`;
        const intentRows = intentsData.slice(0, 10).map((i) => {
          const timestamp = new Date(i.created_at).toLocaleTimeString();
          return `
            <div class="tr20-intent-row">
              <div class="tr20-intent-info">
                <div class="tr20-intent-state">${escapeHtml(i.state)}</div>
                <div class="tr20-intent-account">${i.selected_account ? escapeHtml(i.selected_account) : "(none)"}</div>
                <div class="tr20-intent-time">${timestamp}</div>
              </div>
            </div>
          `;
        });
        html += intentRows.join("");
      }

      if (budgetsData.length > 0) {
        html += `<h3 class="tr20-subsection-title">Strategy budgets</h3>`;
        const budgetRows = budgetsData.map((b) => {
          const reserved = b.reserved_notional || 0;
          const available = b.max_notional ? (b.max_notional - reserved) : "unlimited";
          return `
            <div class="tr20-budget-row">
              <div class="tr20-budget-key">${escapeHtml(b.strategy_key)}</div>
              <div class="tr20-budget-usage">Max: ${b.max_notional || "unlimited"} | Reserved: ${reserved}</div>
            </div>
          `;
        });
        html += budgetRows.join("");
      }

      el.innerHTML = html;
      StateMatrix.render(el, { state: "ready" });
      return { intents: intentsData, budgets: budgetsData };
    } catch (err) {
      StateMatrix.render(el, { state: "error", errorMessage: err.message });
      return [];
    }
  }

  async function load(ctx) {
    const [alerts, halts, commands] = await Promise.all([
      loadAlerts(ctx),
      loadHalts(ctx),
      loadCommands(ctx),
    ]);
    await loadIntents(ctx);

    const badgeCount = alerts.length + halts.length + commands.length;
    updateBadge(badgeCount);
  }

  function updateBadge(count) {
    const badge = document.querySelector("#tr20-badge");
    if (badge) {
      badge.textContent = count || "";
      badge.classList.toggle("hidden", count === 0);
    }
  }

  // Global functions for button clicks
  window.TR20_AckAlert = async function (alertId) {
    try {
      const res = await fetch(`/alerts/${encodeURIComponent(alertId)}/ack`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      if (res.ok) {
        // Reload alerts
        const ctx = window.TR20_CurrentContext;
        if (ctx) await loadAlerts(ctx);
      }
    } catch (err) {
      alert(`Failed to acknowledge: ${err.message}`);
    }
  };

  window.TR20_ClearHalt = async function (accountId) {
    // Use action-confirm for this critical action
    try {
      const outcome = await Components.confirmAction({
        title: `Clear halt for ${accountId}`,
        confirmWord: accountId,
        confirmLabel: "Clear halt",
        previewFn: async () => ({
          severity: "warning",
          rows: [
            { label: "Account", value: accountId, tone: "warn" },
            { label: "Action", value: "Clear risk halt", tone: "crit" },
          ],
          notes: ["This will re-enable trading for this account."],
        }),
        onConfirm: async () => {
          const res = await fetch(`/risk-halts/${encodeURIComponent(accountId)}/clear`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ evidence: "Cleared via operations center" }),
          });
          if (!res.ok) throw new Error(`Failed (${res.status})`);
          return await res.json();
        },
      });

      if (outcome.status === "confirmed") {
        const ctx = window.TR20_CurrentContext;
        if (ctx) await loadHalts(ctx);
      }
    } catch (err) {
      alert(`Clear halt failed: ${err.message}`);
    }
  };

  window.TR20_ResolveCommand = async function (idempotencyKey) {
    // Prompt for evidence (≥3 chars)
    const evidence = prompt("Mark this command as not placed. Enter evidence (min 3 chars):");
    if (!evidence || evidence.length < 3) return;

    try {
      const outcome = await Components.confirmAction({
        title: `Resolve command`,
        confirmWord: "resolve",
        confirmLabel: "Mark not placed",
        previewFn: async () => ({
          severity: "warning",
          rows: [
            { label: "Idempotency key", value: idempotencyKey.slice(0, 8) + "...", tone: "warn" },
            { label: "Action", value: "Mark as not placed", tone: "crit" },
          ],
          notes: ["This releases the capital reservation for this order."],
        }),
        onConfirm: async () => {
          const res = await fetch("/command-ledger/resolve", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              idempotency_key: idempotencyKey,
              outcome: "not_placed",
              evidence: evidence,
            }),
          });
          if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || `Failed (${res.status})`);
          }
          return await res.json();
        },
      });

      if (outcome.status === "confirmed") {
        const ctx = window.TR20_CurrentContext;
        if (ctx) await loadCommands(ctx);
      }
    } catch (err) {
      alert(`Resolve command failed: ${err.message}`);
    }
  };

  window.Views = window.Views || {};
  window.Views.tr20 = {
    title: "Operations center",
    breadcrumb: "Operations",
    scope: "private_owner",
    origin: "private_execution",
    async render(ctx) {
      window.TR20_CurrentContext = ctx;
      ctx.container.innerHTML = shell();
      await load(ctx);
      ctx.registerPoll("tr20", 15000, () => load(ctx));
    },
  };
  Router.register("/trade/operations", "tr20");
})();
