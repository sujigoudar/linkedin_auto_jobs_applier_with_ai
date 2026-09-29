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
 *
 * "Historical message import" (panel 08 below) is a DIFFERENT, real
 * capability from TR-10-A02 above: not an async per-source history-export
 * job (still doesn't exist), but an owner-driven, synchronous batch
 * review -- paste historical messages, see each one's real classify_batch
 * disposition (POST /sources/{source}/classify-messages, same call the
 * parser laboratory above uses), select which ones to import, and
 * persist exactly those through POST /sources/{source}/import-signals
 * (the server re-classifies every selected text itself -- it never
 * trusts a client-side disposition -- and only a PARSED outcome becomes
 * a Signal row, via the same SignalStore.save_signal the live ingestion
 * path uses). Each imported row carries a real `import_batch` label
 * (the signals.import_batch column, additive/nullable) so it stays
 * honestly distinguishable from a signal that arrived over a live
 * transport.
 */
(function () {
  "use strict";

  // Placeholder-slot pattern (see tr04.js): a capability-state badge
  // renders into a real DOM element, but this view composes each panel's
  // markup (form fields plus a gap note) as one HTML string before it is
  // inserted. `unsupportedNote` reserves a slot and queues its opts;
  // `flushCapStates` mounts everything queued so far once the panel's
  // string has actually been assigned to `.innerHTML`.
  let capIdCounter = 0;
  let pendingCapStates = [];
  function capSlot(id) {
    return `<span class="cap-state-slot" id="${id}"></span>`;
  }
  function mountCapStates(root, specs) {
    for (const [id, opts] of specs) {
      const el = root.querySelector(`#${id}`);
      if (el) Components.renderCapabilityState(el, opts);
    }
  }
  function unsupportedNote(reason, remediation) {
    const id = `tr10-cap-${capIdCounter++}`;
    pendingCapStates.push([id, { status: "unsupported", reason, remediation }]);
    return capSlot(id);
  }
  function flushCapStates(container) {
    mountCapStates(container, pendingCapStates);
    pendingCapStates = [];
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
      <section class="tr-panel" id="tr10-p08"><h2>Historical message import</h2><div class="tr-panel-body"></div></section>
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
      historyImport: ctx.container.querySelector("#tr10-p08 .tr-panel-body"),
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
    flushCapStates(els.transport);

    StateMatrix.render(els.channel, {
      state: "ready",
      html: `
        <label>Display name<input type="text" id="tr10-display-name" maxlength="100" placeholder="Optional label"></label>
        <div id="tr10-analyst-section"></div>
        ${unsupportedNote("Channel/product identity (channel_product_id) and a reviewed, versioned analyst-mapping registry (analyst_mapping) are not tracked in this build. Real analyst entries (below) exist per provider, but display-name matching is not independently reviewed/versioned.")}
      `,
    });
    flushCapStates(els.channel);
    renderAnalystSection();

    els.rights.removeAttribute("aria-busy");
    Components.renderCapabilityState(els.rights, {
      status: "unsupported",
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
        s.import_batch
          ? `${pill("imported", "warn")} <span class="section-note">${escapeHtml(s.import_batch)}</span>`
          : pill("received (live)", "ok"),
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
      const rows = lastDispositions.map((d, i) => [
        `<span class="mono">${escapeHtml(d.text)}</span>`,
        capSlot(`tr10-cap-expected-${i}`),
        d.outcome === "matched" || d.signal ? pill(escapeHtml(d.signal ? d.signal.side : d.outcome), "ok") : pill(escapeHtml(d.outcome), "bad"),
        capSlot(`tr10-cap-unconsumed-${i}`),
        d.signal ? escapeHtml(d.signal.symbol) : pill("n/a", "muted"),
        capSlot(`tr10-cap-difference-${i}`),
      ]);
      StateMatrix.render(els.comparison, {
        state: "ready",
        html: table(
          ["Message/revision", "Expected action", "Parser action", "Unconsumed fields", "Instrument", "Difference"],
          rows,
          "No dispositions."
        ),
      });
      const noGroundTruth = {
        status: "not_tracked",
        reason: `"Expected action", "Unconsumed fields" and "Difference" have no ground-truth/labeled-dataset store in this build.`,
      };
      mountCapStates(
        els.comparison,
        lastDispositions.flatMap((_, i) => [
          [`tr10-cap-expected-${i}`, noGroundTruth],
          [`tr10-cap-unconsumed-${i}`, noGroundTruth],
          [`tr10-cap-difference-${i}`, noGroundTruth],
        ])
      );
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
      flushCapStates(els.review);
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

    // --- Historical message import: classify a batch, review, select, import ---
    let lastImportDispositions = []; // [{text, outcome, detail, signal}] from classify-messages, indexed to checkbox state
    function renderHistoryImportForm() {
      StateMatrix.render(els.historyImport, {
        state: "ready",
        html: `
          <p class="section-note">Paste historical messages (one per line) from this source's real backing channel/export. Each one is run through the real, same-grammar POST /sources/{provider_id}/classify-messages -- no signal is created yet. Review the real disposition, then select which rows to actually import as real Signal rows.</p>
          <label>Historical messages (one per line)
            <textarea id="tr10-import-messages" rows="8" placeholder="buy BTCUSDT sl 95 tp 110&#10;maybe consider shorting ETH here&#10;not sure what this line even means"></textarea>
          </label>
          <label>Asset class
            <select id="tr10-import-asset-class">
              <option value="crypto">crypto</option>
              <option value="equity">equity</option>
              <option value="forex">forex</option>
              <option value="future">future</option>
              <option value="option">option</option>
            </select>
          </label>
          <label>Analyst (optional)<input type="text" id="tr10-import-analyst" maxlength="80"></label>
          <div class="tr-controls-row">
            <button type="button" id="tr10-import-classify">Classify batch for review</button>
          </div>
          <div id="tr10-import-classify-result"></div>
          <div id="tr10-import-review"></div>
        `,
      });
      const providerId = currentProviderId();
      els.historyImport.querySelector("#tr10-import-classify").addEventListener("click", async () => {
        const resultEl = els.historyImport.querySelector("#tr10-import-classify-result");
        const pid = currentProviderId();
        if (!pid) {
          resultEl.innerHTML = `<p class="sm-error-message">Provider ID is required before classifying a history batch.</p>`;
          return;
        }
        const texts = els.historyImport
          .querySelector("#tr10-import-messages").value.split("\n").map((t) => t.trim()).filter(Boolean);
        if (!texts.length) {
          resultEl.innerHTML = `<p class="sm-error-message">Enter at least one historical message line.</p>`;
          return;
        }
        resultEl.textContent = "Classifying batch…";
        try {
          const result = await postJSON(`/sources/${encodeURIComponent(pid)}/classify-messages`, {
            texts,
            asset_class: els.historyImport.querySelector("#tr10-import-asset-class").value,
            analyst: els.historyImport.querySelector("#tr10-import-analyst").value.trim() || null,
          });
          lastImportDispositions = result.dispositions || [];
          resultEl.innerHTML = `<p class="section-note">Classified ${lastImportDispositions.length} message(s). Select the rows below to import.</p>`;
          renderImportReviewTable();
        } catch (err) {
          resultEl.innerHTML = `<p class="sm-error-message">${escapeHtml(err.message)}</p>`;
        }
      });
      renderImportReviewTable();
    }

    function renderImportReviewTable() {
      const target = els.historyImport.querySelector("#tr10-import-review");
      if (!target) return;
      if (!lastImportDispositions.length) {
        target.innerHTML = `<p class="section-note">No batch classified yet in this session.</p>`;
        return;
      }
      const rows = lastImportDispositions.map((d, i) => {
        const importable = d.outcome === "parsed" && d.signal;
        const resultCell = importable
          ? `${pill("parsed", "ok")} ${escapeHtml(d.signal.side)} ${escapeHtml(d.signal.symbol)}`
          : `${pill(escapeHtml(d.outcome), "bad")} ${d.detail ? `<span class="section-note">${escapeHtml(d.detail)}</span>` : ""}`;
        return [
          `<input type="checkbox" class="tr10-import-select" data-idx="${i}" ${importable ? "" : "disabled"}>`,
          `<span class="mono">${escapeHtml(d.text)}</span>`,
          resultCell,
        ];
      });
      target.innerHTML = `
        ${table(["Import?", "Raw message", "Classified result (real classify_batch output)"], rows, "No dispositions.")}
        <label>Batch label (optional -- stored on every imported Signal's import_batch field)
          <input type="text" id="tr10-import-batch-label" maxlength="120" placeholder="e.g. telegram-2024-history">
        </label>
        <div class="tr-controls-row">
          <button type="button" id="tr10-import-selected">Import selected as Signal rows</button>
        </div>
        <div id="tr10-import-result"></div>
      `;
      target.querySelector("#tr10-import-selected").addEventListener("click", async () => {
        const resultEl = target.querySelector("#tr10-import-result");
        const pid = currentProviderId();
        const checked = Array.from(target.querySelectorAll(".tr10-import-select:checked"));
        if (!checked.length) {
          resultEl.innerHTML = `<p class="sm-error-message">Select at least one parsed row to import.</p>`;
          return;
        }
        const selectedTexts = checked.map((cb) => lastImportDispositions[Number(cb.dataset.idx)].text);
        resultEl.textContent = "Importing…";
        try {
          const result = await postJSON(`/sources/${encodeURIComponent(pid)}/import-signals`, {
            texts: selectedTexts,
            asset_class: els.historyImport.querySelector("#tr10-import-asset-class").value,
            analyst: els.historyImport.querySelector("#tr10-import-analyst").value.trim() || null,
            batch_label: target.querySelector("#tr10-import-batch-label").value.trim() || null,
          });
          resultEl.innerHTML = `<p class="section-note">Imported ${result.imported.length} of ${selectedTexts.length} selected message(s) as real Signal rows (batch label: <span class="mono">${escapeHtml(result.batch_label)}</span>). ${
            result.skipped.length
              ? `${result.skipped.length} selected message(s) were re-classified server-side as non-parsed and were NOT imported -- the server never trusts the client's earlier disposition.`
              : ""
          }</p>`;
          await renderHistory();
        } catch (err) {
          resultEl.innerHTML = `<p class="sm-error-message">${escapeHtml(err.message)}</p>`;
        }
      });
    }
    renderHistoryImportForm();

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
