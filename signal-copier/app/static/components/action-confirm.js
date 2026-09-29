/* Components.confirmAction -- shared "preview -> impact -> confirmation ->
 * durable operation status" gate for trade-affecting commands (2026-09
 * design review: "Financial controls look too similar to ordinary forms.
 * Trade-affecting actions need preview -> impact -> confirmation ->
 * durable operation status" / "Emergency actions must remain accessible
 * but difficult to trigger accidentally"). Vanilla JS, no dependencies --
 * matches the style of router.js/state-matrix.js and the other
 * app/static/components/*.js files (IIFE attaching to `window`, no
 * framework, no build step).
 *
 * This component NEVER calls fetch() itself and NEVER decides what a
 * button is allowed to do -- it only gates a caller-supplied async
 * function behind an explicit two-step confirmation, and renders
 * whatever real preview/result data the caller's own functions return.
 * The caller is responsible for wiring `previewFn`/`onConfirm` to a real
 * backend endpoint (or to data already fetched from one) -- this
 * component has no opinion on what is "real" and will happily render a
 * fabricated preview if a caller passes one, so callers must not do that
 * (see e.g. app/static/views/tr06.js's own wiring for the honest-preview
 * pattern this was built for).
 *
 * Usage:
 *   Components.confirmAction({
 *     title: "Flatten account acct1",
 *     confirmWord: "acct1",              // operator must type this exact
 *                                        // text before the confirm button
 *                                        // enables -- the "difficult to
 *                                        // trigger accidentally" gate.
 *     confirmLabel: "Flatten account",   // optional, default "Confirm"
 *     previewFn: async () => ({
 *       severity: "critical",            // "critical" | "warning" | "info"
 *       rows: [
 *         { label: "AAPL", value: "40 shares -- uncovered 12 (no stop)", tone: "crit" },
 *       ],
 *       notes: [
 *         "Fill price and slippage cannot be previewed -- this broker has " +
 *           "no quote-before-order capability.",
 *       ],
 *     }),
 *     impactRenderer: (impact) => "...", // optional, overrides the default
 *                                        // rows/notes rendering entirely
 *     onConfirm: async () => (await postJSON("/accounts/acct1/flatten", {})),
 *     renderResult: (outcome) => "...",  // optional; outcome is
 *                                        // {ok:true, result} or
 *                                        // {ok:false, error}
 *   }).then((outcome) => { ... });
 *
 * Returns a Promise that resolves once the operator dismisses the modal:
 *   { status: "cancelled" }                     -- backed out before firing
 *   { status: "confirmed", outcome: {ok, ...} }  -- onConfirm ran (either
 *                                                    result)
 * It never resolves "confirmed" until `onConfirm` has actually settled,
 * and the result panel it shows afterward only closes on an explicit
 * "Dismiss" click -- not on a timeout, not on Escape, not on a backdrop
 * click. This module deliberately implements no retry/idempotency logic
 * of its own; that already lives at the backend/engine layer (idempotency
 * keys, protect-first-on-fill, per-(account,symbol) locks -- see
 * app/main.py's close_single_position/flatten_account docstrings).
 */
(function (global) {
  "use strict";

  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  const VALID_SEVERITIES = new Set(["critical", "warning", "info"]);
  const VALID_TONES = new Set(["crit", "warn", "ok", "neutral"]);

  function severityToTone(severity) {
    if (severity === "critical") return "crit";
    if (severity === "warning") return "warn";
    return "neutral";
  }

  let stylesInjected = false;
  function injectStyles() {
    if (stylesInjected || document.getElementById("action-confirm-styles")) {
      stylesInjected = true;
      return;
    }
    const style = document.createElement("style");
    style.id = "action-confirm-styles";
    // Reuses the page's own --crit/--warn/--ok/--bg/--panel/--border/--text/
    // --muted design tokens (app/static/design-system.css, loaded on every
    // page this component is used from) rather than redefining them here --
    // this file only adds new selectors, all scoped under
    // `.action-confirm-*` so it can never collide with unrelated CSS.
    style.textContent = `
      .action-confirm-backdrop {
        position: fixed; inset: 0; z-index: 1000;
        background: rgba(4, 6, 10, 0.72);
        display: flex; align-items: center; justify-content: center;
        padding: 1rem;
      }
      .action-confirm-modal {
        width: 100%; max-width: 30rem; max-height: 85vh; overflow-y: auto;
        background: var(--panel, #141926);
        border: 1px solid var(--border, #262c3d);
        border-radius: 10px;
        padding: 1.1rem 1.25rem 1.25rem;
        box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5);
        color: var(--text, #e6e9f0);
      }
      .action-confirm-modal h2 {
        margin: 0 0 0.75rem;
        font-size: 1.02rem;
      }
      .action-confirm-body { margin-bottom: 0.85rem; }
      .action-confirm-body.action-confirm-impact-crit {
        border-left: 3px solid var(--crit, #ff5d3b);
        background: var(--crit-bg, rgba(255, 93, 59, 0.14));
        padding: 0.6rem 0.75rem;
        border-radius: 6px;
      }
      .action-confirm-body.action-confirm-impact-warn {
        border-left: 3px solid var(--warn, #f5b942);
        background: var(--warn-bg, rgba(245, 185, 66, 0.14));
        padding: 0.6rem 0.75rem;
        border-radius: 6px;
      }
      .action-confirm-row {
        display: flex; justify-content: space-between; gap: 0.75rem;
        padding: 0.28rem 0; font-size: 0.85rem;
        border-bottom: 1px solid var(--border, #262c3d);
      }
      .action-confirm-row:last-child { border-bottom: none; }
      .action-confirm-row.tone-crit { color: var(--crit, #ff5d3b); }
      .action-confirm-row.tone-warn { color: var(--warn, #f5b942); }
      .action-confirm-row.tone-ok { color: var(--ok, #3ddc84); }
      .action-confirm-row .ac-label { font-weight: 600; white-space: nowrap; }
      .action-confirm-row .ac-value { text-align: right; }
      .action-confirm-note {
        font-size: 0.8rem; color: var(--muted, #8b93a7); margin: 0.4rem 0 0;
      }
      .action-confirm-note.action-confirm-impact-crit { color: var(--crit, #ff5d3b); }
      .action-confirm-note.action-confirm-impact-warn { color: var(--warn, #f5b942); }
      .action-confirm-typed { margin: 0.75rem 0; }
      .action-confirm-typed label { display: block; font-size: 0.82rem; margin-bottom: 0.3rem; }
      .action-confirm-input {
        width: 100%; box-sizing: border-box; padding: 0.45rem 0.6rem;
        background: var(--bg, #0b0e14); color: var(--text, #e6e9f0);
        border: 1px solid var(--border, #262c3d); border-radius: 6px;
        font: inherit;
      }
      .action-confirm-actions {
        display: flex; justify-content: flex-end; gap: 0.5rem; margin-top: 0.9rem;
      }
      .action-confirm-btn {
        padding: 0.45rem 0.9rem; border-radius: 6px; font: inherit;
        border: 1px solid var(--border, #262c3d);
        background: transparent; color: var(--text, #e6e9f0); cursor: pointer;
      }
      .action-confirm-btn:hover { border-color: var(--accent, #5b8cff); }
      .action-confirm-btn:disabled { opacity: 0.45; cursor: not-allowed; }
      .action-confirm-btn-danger {
        background: var(--bad, #ef5b5b); border-color: var(--bad, #ef5b5b); color: #fff;
      }
      .action-confirm-btn-danger:disabled { background: var(--border, #262c3d); border-color: var(--border, #262c3d); color: var(--muted, #8b93a7); }
      .action-confirm-result-ok .action-confirm-result-heading { color: var(--ok, #3ddc84); }
      .action-confirm-result-error .action-confirm-result-heading { color: var(--bad, #ef5b5b); }
      .action-confirm-result-heading { font-weight: 600; margin: 0 0 0.5rem; }
    `;
    document.head.appendChild(style);
    stylesInjected = true;
  }

  function defaultImpactRenderer(impact) {
    const rows = (impact && impact.rows) || [];
    const notes = (impact && impact.notes) || [];
    const rowsHtml = rows.length
      ? rows
          .map((r) => {
            const tone = VALID_TONES.has(r.tone) ? r.tone : "neutral";
            return `<div class="action-confirm-row tone-${tone}"><span class="ac-label">${escapeHtml(r.label)}</span><span class="ac-value">${escapeHtml(r.value)}</span></div>`;
          })
          .join("")
      : `<p class="action-confirm-note">No further detail was returned for this preview.</p>`;
    const notesHtml = notes.map((n) => `<p class="action-confirm-note">${escapeHtml(n)}</p>`).join("");
    return `${rowsHtml}${notesHtml}`;
  }

  function defaultRenderResult(outcome) {
    if (!outcome || !outcome.ok) {
      const message = (outcome && outcome.error) || "Unknown error.";
      return `<p class="action-confirm-result-heading">Failed</p><p class="action-confirm-note action-confirm-impact-crit">${escapeHtml(message)}</p>`;
    }
    let detail = "";
    try {
      detail = JSON.stringify(outcome.result, null, 2);
    } catch (e) {
      detail = String(outcome.result);
    }
    return `<p class="action-confirm-result-heading">Completed</p><pre class="action-confirm-note" style="white-space:pre-wrap;">${escapeHtml(detail)}</pre>`;
  }

  function confirmAction(opts) {
    opts = opts || {};
    return new Promise((resolve) => {
      injectStyles();

      const backdrop = document.createElement("div");
      backdrop.className = "action-confirm-backdrop";
      document.body.appendChild(backdrop);

      let settled = false;
      // "resultShown" gates Escape/backdrop-click dismissal: once
      // onConfirm has been invoked, the operator can only leave via the
      // explicit Dismiss button on the durable result panel -- never by
      // accident.
      let resultShown = false;

      function close(value) {
        if (settled) return;
        settled = true;
        document.removeEventListener("keydown", onKeydown, true);
        backdrop.remove();
        resolve(value);
      }

      function onKeydown(e) {
        if (e.key === "Escape" && !resultShown) close({ status: "cancelled" });
      }
      document.addEventListener("keydown", onKeydown, true);

      backdrop.addEventListener("mousedown", (e) => {
        if (e.target === backdrop && !resultShown) close({ status: "cancelled" });
      });

      function renderStage(innerHtml) {
        backdrop.innerHTML = `<div class="action-confirm-modal" role="dialog" aria-modal="true" aria-label="${escapeHtml(opts.title || "Confirm action")}">${innerHtml}</div>`;
        return backdrop.querySelector(".action-confirm-modal");
      }

      function impactHtml(impact) {
        const renderer = typeof opts.impactRenderer === "function" ? opts.impactRenderer : defaultImpactRenderer;
        return renderer(impact);
      }

      function showConfirmStage(impact) {
        const severity = VALID_SEVERITIES.has(impact && impact.severity) ? impact.severity : "info";
        const toneClass = severity === "critical" ? "action-confirm-impact-crit" : severity === "warning" ? "action-confirm-impact-warn" : "";
        const needsTyped = Boolean(opts.confirmWord);

        renderStage(`
          <h2>${escapeHtml(opts.title || "Confirm action")}</h2>
          <div class="action-confirm-body ${toneClass}">${impactHtml(impact)}</div>
          ${
            needsTyped
              ? `<div class="action-confirm-typed">
                   <label for="ac-confirm-input">Type <strong>${escapeHtml(opts.confirmWord)}</strong> to confirm</label>
                   <input id="ac-confirm-input" class="action-confirm-input" type="text" autocomplete="off" spellcheck="false" />
                 </div>`
              : ""
          }
          <div class="action-confirm-actions">
            <button type="button" class="action-confirm-btn" data-ac-cancel>Cancel</button>
            <button type="button" class="action-confirm-btn action-confirm-btn-danger" data-ac-confirm ${needsTyped ? "disabled" : ""}>${escapeHtml(opts.confirmLabel || "Confirm")}</button>
          </div>
        `);

        const cancelBtn = backdrop.querySelector("[data-ac-cancel]");
        const confirmBtn = backdrop.querySelector("[data-ac-confirm]");
        cancelBtn.addEventListener("click", () => close({ status: "cancelled" }));

        if (needsTyped) {
          const input = backdrop.querySelector("#ac-confirm-input");
          input.addEventListener("input", () => {
            confirmBtn.disabled = input.value !== opts.confirmWord;
          });
        }

        confirmBtn.addEventListener("click", async () => {
          // Load-bearing gate check, kept even though the `disabled`
          // attribute already blocks this in a normal click -- a
          // regression that only removes the `disabled` attribute wiring
          // must still be caught here, not just by the DOM attribute.
          if (needsTyped) {
            const input = backdrop.querySelector("#ac-confirm-input");
            if (!input || input.value !== opts.confirmWord) return;
          }
          resultShown = true; // no more Escape/backdrop dismissal from here on
          confirmBtn.disabled = true;
          cancelBtn.disabled = true;
          renderStage(`<h2>${escapeHtml(opts.title || "Confirm action")}</h2><div class="action-confirm-body"><p class="action-confirm-note">Submitting…</p></div>`);
          let outcome;
          try {
            const result = await opts.onConfirm();
            outcome = { ok: true, result };
          } catch (e) {
            outcome = { ok: false, error: (e && e.message) || String(e) };
          }
          showResultStage(outcome);
        });
      }

      function showResultStage(outcome) {
        resultShown = true;
        const renderer = typeof opts.renderResult === "function" ? opts.renderResult : defaultRenderResult;
        const modal = renderStage(`
          <h2>${escapeHtml(opts.title || "Confirm action")}</h2>
          <div class="action-confirm-body">${renderer(outcome)}</div>
          <div class="action-confirm-actions">
            <button type="button" class="action-confirm-btn" data-ac-dismiss>Dismiss</button>
          </div>
        `);
        modal.className += outcome.ok ? " action-confirm-result-ok" : " action-confirm-result-error";
        backdrop.querySelector("[data-ac-dismiss]").addEventListener("click", () => close({ status: "confirmed", outcome }));
      }

      // Stage 1: preview. Always shown first, and it is the caller's own
      // `previewFn` -- wired to a real backend read, never a client-side
      // guess -- that decides what the operator sees here.
      renderStage(`<h2>${escapeHtml(opts.title || "Confirm action")}</h2><div class="action-confirm-body"><p class="action-confirm-note">Loading preview…</p></div>`);
      (async () => {
        let impact;
        try {
          impact = await opts.previewFn();
        } catch (e) {
          const message = (e && e.message) || String(e);
          renderStage(`
            <h2>${escapeHtml(opts.title || "Confirm action")}</h2>
            <div class="action-confirm-body">
              <p class="action-confirm-note action-confirm-impact-crit">Could not load a real preview: ${escapeHtml(message)}</p>
            </div>
            <div class="action-confirm-actions">
              <button type="button" class="action-confirm-btn" data-ac-cancel>Cancel</button>
            </div>
          `);
          backdrop.querySelector("[data-ac-cancel]").addEventListener("click", () => close({ status: "cancelled" }));
          return;
        }
        showConfirmStage(impact);
      })();
    });
  }

  global.Components = global.Components || {};
  global.Components.confirmAction = confirmAction;
})(window);
