/* TR-04: Incoming signal stream (`#/trade/signals`).
 *
 * Real backing data: GET /signals (every accepted, persisted Signal --
 * `app/db.py`'s `list_recent_signals`, extended in this batch to also
 * project the already-stored `analyst` column it wasn't previously
 * selecting -- see that function's comment). No numeric headline metric
 * per spec ("Do not add a decorative performance KPI").
 *
 * Honest gap, disclosed rather than worked around: the spec's purpose
 * line is "classify every authorized incoming event including rejected
 * or ignored instructions," and its Disposition column implies
 * accepted/rejected/ignored outcomes. This schema has NO disposition
 * ledger -- a signal that fails `SignalValidationError` during parsing
 * (see app/main.py's webhook/SMS/WhatsApp/NinjaTrader routes) is
 * rejected with an HTTP 4xx to the sender and never becomes a `signals`
 * row at all, so there is no persisted record of it to show here.
 * `GET /sources/{source}/classify-messages` (E02) can dry-run arbitrary
 * text against the parser, but that is a separate, deliberately
 * non-persisting analysis tool (see its own docstring: "never ingests a
 * signal"), not a log of real rejected events -- using it here would
 * misrepresent hypothetical classification as historical disposition.
 * Every row below is therefore an accepted signal; Disposition reads
 * "accepted (recorded)" for all of them, with an explicit note that
 * rejected/ignored instructions are not persisted in this build.
 */
(function () {
  "use strict";

  function shell() {
    return `
      <section class="tr-panel" id="tr04-p01"><h2>Source filters</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr04-p02"><h2>Stream status</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr04-p03"><h2>Disposition table</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr04-p04"><h2>Backlog</h2><div class="tr-panel-body"></div></section>
    `;
  }

  function applyFilters(signals, filters) {
    return signals.filter((s) => {
      if (filters.source && s.source !== filters.source) return false;
      if (filters.analyst && (s.analyst || "") !== filters.analyst) return false;
      return true;
    });
  }

  async function load(ctx, filters) {
    const els = {
      filters: ctx.container.querySelector("#tr04-p01 .tr-panel-body"),
      status: ctx.container.querySelector("#tr04-p02 .tr-panel-body"),
      disposition: ctx.container.querySelector("#tr04-p03 .tr-panel-body"),
      backlog: ctx.container.querySelector("#tr04-p04 .tr-panel-body"),
    };
    for (const el of Object.values(els)) StateMatrix.render(el, { state: "loading" });

    const health = await ctx.fetchJSON("/health");
    if (health.status === 401 || health.status === 403) {
      StateMatrix.render(els.status, { state: "denied", deniedCode: health.status });
    } else if (!health.ok) {
      StateMatrix.render(els.status, { state: "error", message: "Could not check stream health." });
    } else {
      const h = health.data || {};
      const checklist = [
        ["Signal store reachable", boolPill(h.database_ok)],
        ["Ingestion-adjacent workers (reconciler/price monitor) making progress", boolPill(h.price_monitor_ok && h.reconciler_ok)],
        ["Per-source connection status", pill("not exposed by this build", "muted")],
        ["Trading authority (per source)", pill("not exposed by this build", "muted")],
      ];
      StateMatrix.render(els.status, {
        state: "ready",
        html: `<p class="section-note">Independently evaluated conditions -- not collapsed into one badge. Per-source connection/authority checks are not yet exposed by any endpoint in this build (see TR-09 Signal providers and collectors for that surface, in a later batch).</p>${table(
          ["Condition", "Status"],
          checklist,
          "No conditions."
        )}`,
      });
    }

    const signalsRes = await ctx.fetchJSON("/signals?limit=100");
    if (signalsRes.status === 401 || signalsRes.status === 403) {
      StateMatrix.render(els.filters, { state: "denied", deniedCode: signalsRes.status });
      StateMatrix.render(els.disposition, { state: "denied", deniedCode: signalsRes.status });
      StateMatrix.render(els.backlog, { state: "denied", deniedCode: signalsRes.status });
      return;
    }
    if (!signalsRes.ok) {
      StateMatrix.render(els.filters, { state: "error", message: "Could not load signals." });
      StateMatrix.render(els.disposition, { state: "error", message: "Could not load signals." });
      StateMatrix.render(els.backlog, { state: "error", message: "Could not load signals." });
      return;
    }

    const allSignals = (signalsRes.data && signalsRes.data.signals) || [];
    const sources = [...new Set(allSignals.map((s) => s.source))];
    StateMatrix.render(els.filters, {
      state: "ready",
      html: `
        <form id="tr04-filter-form" class="inline-form" style="margin:0;">
          <label>Source
            <select name="source">
              <option value="">(any)</option>
              ${sources.map((s) => `<option value="${escapeAttr(s)}" ${filters.source === s ? "selected" : ""}>${escapeHtml(s)}</option>`).join("")}
            </select>
          </label>
          <div class="actions"><button type="submit">Apply</button> <button type="button" class="ghost" id="tr04-clear-filters">Clear</button></div>
        </form>
        <p class="section-note">Filters: Account, Analyst, Instrument, Product, Protection scoping is server-side in the spec; this batch filters client-side over the fetched page (Source shown; Analyst/Disposition/Period are not yet independently queryable server-side in this build).</p>
      `,
    });
    const form = els.filters.querySelector("#tr04-filter-form");
    if (form) {
      form.addEventListener("submit", (e) => {
        e.preventDefault();
        const f = new FormData(form);
        load(ctx, { source: f.get("source") || "" });
      });
      els.filters.querySelector("#tr04-clear-filters").addEventListener("click", () => load(ctx, {}));
    }

    const signals = applyFilters(allSignals, filters || {});
    if (!signals.length) {
      StateMatrix.render(els.disposition, {
        state: "empty",
        emptyMessage: "No authorized signals have been received.",
        nextRoute: "/trade/signals",
        nextLabel: "Signal providers and collectors (TR-09, not yet available)",
      });
    } else {
      const rows = signals.map((s) => [
        `<span class="mono">${escapeHtml(s.id)}</span>`,
        `<span class="mono">${escapeHtml(s.source)}</span>`,
        escapeHtml(s.analyst || "(none)"),
        s.received_at || "—",
        escapeHtml(s.side || "—"),
        `<span class="mono">${escapeHtml(s.symbol)}</span>`,
        pill("accepted (recorded)", "ok"),
        `<span class="tr-not-tracked">rejected/ignored instructions are not persisted in this build</span>`,
      ]);
      StateMatrix.render(els.disposition, {
        state: "ready",
        html: table(
          ["Event/revision", "Provider", "Analyst", "Observed", "Interpreted action", "Instrument", "Disposition", "Reason"],
          rows,
          "No authorized signals have been received."
        ),
      });
    }

    StateMatrix.render(els.backlog, { state: "unsupported", reason: "No ingestion queue-depth/backlog metric is exposed by any endpoint in this build -- signals are processed synchronously per request (see app/engine.py), so there is no queue to report depth for." });

    ctx.setChrome({ asOf: new Date().toISOString() });
  }

  window.Views = window.Views || {};
  window.Views.tr04 = {
    title: "Incoming signal stream",
    breadcrumb: "Trade / Signals",
    scope: "private_owner",
    origin: "private_execution",
    async render(ctx) {
      ctx.container.innerHTML = shell();
      await load(ctx, {});
      ctx.registerPoll("tr04", 10000, () => load(ctx, {}));
    },
  };
  Router.register("/trade/signals", "tr04");
})();
