/* TR-10: Source onboarding and parser laboratory (`#/trade/sources/new`).
 *
 * Real backing capability: POST /sources/{source_name}/classify-messages --
 * the exact same, already-tested sandbox endpoint TR-05's "Reclassify in
 * sandbox" action calls (app/main.py's classify_messages, batch-testing
 * app/sources/text_parser.py's shared grammar against pasted text with NO
 * signal ever created or routed) -- this screen's "parser laboratory" IS
 * that endpoint, made into a first-class multi-message workspace instead
 * of a single-signal replay. Provider identity is saved through the same,
 * already-tested POST /providers/{provider_id} upsert TR-09/TR-12 use
 * (app/main.py's create_or_update_provider). History coverage reuses
 * GET /signals filtered to this provider.
 *
 * Honest gaps, disclosed rather than invented: this build's real
 * ProviderConfig/AnalystConfig model (app/providers.py) has no
 * transport_instance_id, channel_product_id, analyst_mapping VERSION,
 * allowed_products id_list, parser_version_id registry, rights_grant_id,
 * or history_start/end import-range field -- those render "unsupported"
 * with the exact missing capability named, never a fabricated form field
 * this build's backend would silently ignore. "Import authorized history"
 * (TR-10-A02) has no backing at all: there is no async import-job queue
 * anywhere in this codebase, so it stays disabled/unsupported rather than
 * pretending to enqueue something real. "History coverage" therefore
 * shows this provider's already-RECEIVED signal history (GET /signals),
 * honestly labelled as that, not an import job's coverage report.
 * "Expected action"/"Unconsumed fields"/"Difference" in the parser
 * comparison table have no ground-truth store in this build (no labeled-
 * dataset table) -- each renders "not tracked in this build" per row
 * rather than a fabricated comparison.
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
      <section class="tr-panel" id="tr10-p01"><h2>Transport/access</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr10-p02"><h2>Channel/analyst</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr10-p03"><h2>Rights</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr10-p04"><h2>History coverage</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr10-p05"><h2>Labeled classifications</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr10-p06"><h2>Parser comparison</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr10-p07"><h2>Review</h2><div class="tr-panel-body"></div></section>
    `;
  }

  async function load(ctx) {
    const els = {
      transport: ctx.container.querySelector("#tr10-p01 .tr-panel-body"),
      channel: ctx.container.querySelector("#tr10-p02 .tr-panel-body"),
      rights: ctx.container.querySelector("#tr10-p03 .tr-panel-body"),
      history: ctx.container.querySelector("#tr10-p04 .tr-panel-body"),
      classifications: ctx.container.querySelector("#tr10-p05 .tr-panel-body"),
      comparison: ctx.container.querySelector("#tr10-p06 .tr-panel-body"),
      review: ctx.container.querySelector("#tr10-p07 .tr-panel-body"),
    };
    for (const el of Object.values(els)) StateMatrix.render(el, { state: "loading" });

    const [providersRes, routingRes] = await Promise.all([
      ctx.fetchJSON("/providers"),
      ctx.fetchJSON("/routing-rules"),
    ]);
    if (providersRes.status === 401 || providersRes.status === 403) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "denied", deniedCode: providersRes.status });
      return;
    }
    if (!providersRes.ok) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "error", message: "Could not load providers." });
      return;
    }
    const providers = (providersRes.data && providersRes.data.providers) || [];
    const routingRules = (routingRes.ok && routingRes.data && routingRes.data.routing_rules) || [];

    StateMatrix.render(els.transport, {
      state: "ready",
      html: `
        <label>Provider ID<input type="text" id="tr10-provider-id" maxlength="80" placeholder="telegram" required></label>
        <p class="section-note">Matches Signal.source -- inbound messages reach this exact identity via a dedicated bot process (Telegram/Discord/Slack/Twitter -- needs an env var + restart, see README.md) or a push endpoint (POST /webhook/{provider_id}, /sms/twilio, /whatsapp/webhook, /ninjatrader/webhook).</p>
        ${unsupportedNote("Transport instance selection (transport_instance_id) -- a shared collector-instance registry with duplicate-bot detection -- is not tracked in this build. Each provider ID maps directly to at most one bot process/env-var set, checked manually.")}
      `,
    });

    StateMatrix.render(els.channel, {
      state: "ready",
      html: `
        <label>Display name<input type="text" id="tr10-display-name" maxlength="100" placeholder="Optional label"></label>
        <div id="tr10-analyst-section"></div>
        ${unsupportedNote("Channel/product identity (channel_product_id) and a reviewed, versioned analyst-mapping registry (analyst_mapping) are not tracked in this build. Real analyst entries (below) exist per provider, but display-name matching is not independently reviewed/versioned.")}
      `,
    });
    renderAnalystSection();

    StateMatrix.render(els.rights, {
      state: "unsupported",
      reason: "No rights/resale-grant model exists anywhere in this codebase (no rights_grant_id field) -- there is nothing real to display or collect here.",
    });

    function currentProviderId() {
      return (els.transport.querySelector("#tr10-provider-id").value || "").trim();
    }

    async function renderHistory() {
      const providerId = currentProviderId();
      if (!providerId) {
        StateMatrix.render(els.history, { state: "empty", emptyMessage: "Enter a provider ID to see its already-received signal history." });
        return;
      }
      const res = await ctx.fetchJSON(`/signals?limit=200`);
      if (!res.ok) {
        StateMatrix.render(els.history, { state: "error", message: "Could not load signal history." });
        return;
      }
      const matches = (res.data.signals || []).filter((s) => s.source === providerId);
      if (!matches.length) {
        StateMatrix.render(els.history, { state: "empty", emptyMessage: "No source messages have been imported for validation.", nextRoute: "/trade/sources", nextLabel: "Signal providers and collectors (TR-09)" });
        return;
      }
      const rows = matches.slice(0, 25).map((s) => [
        `<span class="mono">${escapeHtml(String(s.id))}</span>`,
        escapeHtml(s.symbol),
        escapeHtml(s.side),
        escapeHtml(s.received_at),
        pill("received", "ok"),
      ]);
      StateMatrix.render(els.history, {
        state: "ready",
        html: `<p class="section-note">This is this provider's already-RECEIVED signal history (GET /signals), not an import job -- this build has no async history-import queue (see TR-10-A02 below). Showing ${Math.min(25, matches.length)} of ${matches.length}.</p>${table(
          ["Signal ID", "Symbol", "Side", "Received at", "Origin"],
          rows,
          "No signals."
        )}`,
      });
    }
    await renderHistory();
    els.transport.querySelector("#tr10-provider-id").addEventListener("change", () => { renderHistory(); renderReview(); });

    function renderAnalystSection() {
      const provider = providers.find((p) => p.provider_id === currentProviderId());
      const target = els.channel.querySelector("#tr10-analyst-section");
      if (!target) return;
      const rows = provider && provider.analysts.length
        ? provider.analysts.map((a) => `<li><span class="mono">${escapeHtml(a.analyst_id)}</span>${a.display_name ? ` — ${escapeHtml(a.display_name)}` : ""}</li>`).join("")
        : `<li>${pill("no analysts configured yet", "muted")}</li>`;
      target.innerHTML = `
        <p class="section-note">Analysts for this provider (real, POST /providers/{provider_id}/analysts/{analyst_id}):</p>
        <ul>${rows}</ul>
        <div class="tr-controls-row">
          <input type="text" id="tr10-new-analyst-id" placeholder="analyst_id" maxlength="80">
          <button type="button" id="tr10-add-analyst">Add analyst</button>
        </div>
        <div id="tr10-analyst-result"></div>
      `;
      const btn = target.querySelector("#tr10-add-analyst");
      if (btn) {
        btn.addEventListener("click", async () => {
          const providerId = currentProviderId();
          const analystId = target.querySelector("#tr10-new-analyst-id").value.trim();
          const resultEl = target.querySelector("#tr10-analyst-result");
          if (!providerId || !analystId) {
            resultEl.innerHTML = `<p class="sm-error-message">Provider ID and analyst ID are both required.</p>`;
            return;
          }
          try {
            await postJSON(`/providers/${encodeURIComponent(providerId)}/analysts/${encodeURIComponent(analystId)}`, {});
            resultEl.innerHTML = `<p class="section-note">Analyst saved.</p>`;
            await load(ctx);
          } catch (err) {
            resultEl.innerHTML = `<p class="sm-error-message">${escapeHtml(err.message)}</p>`;
          }
        });
      }
    }

    // --- Parser laboratory: paste message text -> real sandbox classification ---
    let lastDispositions = [];
    StateMatrix.render(els.classifications, {
      state: "ready",
      html: `
        <label>Message text (one message per line)
          <textarea id="tr10-messages" rows="6" placeholder="buy BTCUSDT sl 95 tp 110&#10;close ETHUSDT"></textarea>
        </label>
        <label>Asset class
          <select id="tr10-asset-class">
            <option value="crypto">crypto</option>
            <option value="equity">equity</option>
            <option value="forex">forex</option>
            <option value="futures">futures</option>
            <option value="option">option</option>
          </select>
        </label>
        <label>Analyst (optional)<input type="text" id="tr10-classify-analyst" maxlength="80"></label>
        <div class="tr-controls-row">
          <button type="button" id="tr10-run-parser">Run parser validation</button>
        </div>
        <p class="section-note">Real, no-effects dry run against app/sources/text_parser.py's current grammar (POST /sources/{provider_id}/classify-messages) -- never creates or routes a live signal. A new parser is not live merely because these sample messages parse (see acceptance note below).</p>
        <div id="tr10-classify-result"></div>
      `,
    });
    els.classifications.querySelector("#tr10-run-parser").addEventListener("click", async () => {
      const providerId = currentProviderId();
      const resultEl = els.classifications.querySelector("#tr10-classify-result");
      if (!providerId) {
        resultEl.innerHTML = `<p class="sm-error-message">Provider ID is required before running parser validation.</p>`;
        return;
      }
      const texts = els.classifications
        .querySelector("#tr10-messages").value.split("\n").map((t) => t.trim()).filter(Boolean);
      if (!texts.length) {
        resultEl.innerHTML = `<p class="sm-error-message">Enter at least one message line.</p>`;
        return;
      }
      resultEl.textContent = "Running sandbox classification…";
      try {
        const result = await postJSON(`/sources/${encodeURIComponent(providerId)}/classify-messages`, {
          texts,
          asset_class: els.classifications.querySelector("#tr10-asset-class").value,
          analyst: els.classifications.querySelector("#tr10-classify-analyst").value.trim() || null,
        });
        lastDispositions = result.dispositions || [];
        resultEl.innerHTML = `<p class="section-note">Classified ${lastDispositions.length} message(s). See Parser comparison below.</p>`;
        renderComparison();
        renderReview();
      } catch (err) {
        resultEl.innerHTML = `<p class="sm-error-message">${escapeHtml(err.message)}</p>`;
      }
    });

    function renderComparison() {
      if (!lastDispositions.length) {
        StateMatrix.render(els.comparison, { state: "empty", emptyMessage: "Run parser validation above to see per-message dispositions here." });
        return;
      }
      const rows = lastDispositions.map((d) => [
        `<span class="mono">${escapeHtml(d.text)}</span>`,
        `<span class="tr-not-tracked">not tracked in this build</span>`,
        d.outcome === "matched" || d.signal ? pill(escapeHtml(d.signal ? d.signal.side : d.outcome), "ok") : pill(escapeHtml(d.outcome), "bad"),
        `<span class="tr-not-tracked">not tracked in this build</span>`,
        d.signal ? escapeHtml(d.signal.symbol) : pill("n/a", "muted"),
        `<span class="tr-not-tracked">not tracked in this build</span>`,
      ]);
      StateMatrix.render(els.comparison, {
        state: "ready",
        html: `<p class="section-note">"Expected action", "Unconsumed fields" and "Difference" have no ground-truth/labeled-dataset store in this build -- shown honestly as not tracked rather than fabricated.</p>${table(
          ["Message/revision", "Expected action", "Parser action", "Unconsumed fields", "Instrument", "Difference"],
          rows,
          "No dispositions."
        )}`,
      });
    }
    renderComparison();

    // --- Review checklist ---
    function renderReview() {
      const providerId = currentProviderId();
      const existing = providers.find((p) => p.provider_id === providerId);
      const hasRule = routingRules.some((r) => r.source === providerId);
      const checks = [
        ["Provider ID set", providerId ? pill("yes", "ok") : pill("no", "bad"), providerId ? "OK" : "MISSING_PROVIDER_ID", "form"],
        ["Source draft saved", existing ? pill("yes", "ok") : pill("not yet", "warn"), existing ? "OK" : "NOT_SAVED", "GET /providers"],
        ["Parser validated at least once", lastDispositions.length ? pill("yes", "ok") : pill("no", "warn"), lastDispositions.length ? "OK" : "NOT_VALIDATED", "POST classify-messages (this session)"],
        ["Routing rule exists (signals will be routed, not just recorded)", hasRule ? pill("yes", "ok") : pill("no", "warn"), hasRule ? "OK" : "NO_ROUTING_RULE", "GET /routing-rules"],
      ];
      StateMatrix.render(els.review, {
        state: "ready",
        html: `${table(["Condition", "Outcome", "Reason code", "Evidence"], checks, "No conditions.")}
          <div class="form-error" id="tr10-form-error"></div>
          <div class="tr-controls-row">
            <button type="button" id="tr10-save">Save source draft</button>
            <button type="button" class="ghost" id="tr10-import" disabled>Import authorized history</button>
          </div>
          ${unsupportedNote("Import authorized history (TR-10-A02) has no backing capability: there is no async history-import job queue anywhere in this codebase. This build's real 'history' is whatever GET /signals has already recorded from live traffic (see History coverage above).")}
          <div id="tr10-save-result"></div>
          <p class="section-note">If routing rules exist here, editing them is done on <a href="#/trade/routing">Routing and allocation rules (TR-11)</a> -- not duplicated on this form.</p>`,
      });
      els.review.querySelector("#tr10-save").addEventListener("click", async () => {
        const errorEl = els.review.querySelector("#tr10-form-error");
        errorEl.textContent = "";
        const pid = currentProviderId();
        if (!pid) {
          errorEl.textContent = "Provider ID is required.";
          return;
        }
        try {
          await postJSON(`/providers/${encodeURIComponent(pid)}`, {
            display_name: els.channel.querySelector("#tr10-display-name").value.trim(),
          });
          els.review.querySelector("#tr10-save-result").innerHTML = `<p class="section-note">Saved. <a href="#/trade/sources">Open Signal providers and collectors (TR-09)</a> to review it.</p>`;
          await load(ctx);
        } catch (err) {
          errorEl.textContent = err.message;
        }
      });
    }
    renderReview();

    ctx.setChrome({ asOf: new Date().toISOString() });
  }

  window.Views = window.Views || {};
  window.Views.tr10 = {
    title: "Source onboarding and parser laboratory",
    breadcrumb: "Trade / Sources / New",
    scope: "private_owner",
    origin: "private_execution",
    async render(ctx) {
      ctx.container.innerHTML = shell();
      await load(ctx);
    },
  };
  Router.register("/trade/sources/new", "tr10");
})();
