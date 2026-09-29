/* Components.renderKPIBand -- Level 1 (decision/KPI) shared component.
 *
 * A large, glanceable, horizontal band of big numbers, meant to live in a
 * persistent header area (e.g. Trading command center / TR-01). Vanilla
 * JS, no dependencies -- matches the style of router.js/state-matrix.js
 * (IIFE attaching to `window`, no framework, no build step).
 *
 * Usage:
 *   Components.renderKPIBand(document.getElementById("kpi-band"), {
 *     items: [
 *       { label: "Trading mode", value: "Live", tone: "neutral" },
 *       { label: "Unprotected exposure", value: "$4,200", tone: "crit",
 *         sublabel: "2 positions" },
 *       ...
 *     ],
 *   });
 *
 * `items[].tone` is one of "neutral" | "ok" | "warn" | "crit" (default
 * "neutral"). Anything else falls back to "neutral" rather than throwing.
 */
(function (global) {
  "use strict";

  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  const VALID_TONES = new Set(["neutral", "ok", "warn", "crit"]);

  function renderKPIBand(container, opts) {
    if (!container) return;
    const items = (opts && opts.items) || [];
    if (!items.length) {
      container.innerHTML = `<div class="kpi-band-empty">No KPI data available.</div>`;
      return;
    }
    const html = items
      .map((item) => {
        const tone = VALID_TONES.has(item.tone) ? item.tone : "neutral";
        const label = escapeHtml(item.label ?? "");
        const value = escapeHtml(item.value ?? "—");
        const sublabel = item.sublabel
          ? `<div class="kpi-sublabel">${escapeHtml(item.sublabel)}</div>`
          : "";
        return `
          <div class="kpi-item tone-${tone}">
            <div class="kpi-label">${label}</div>
            <div class="kpi-value">${value}</div>
            ${sublabel}
          </div>`;
      })
      .join("");
    container.innerHTML = `<div class="kpi-band" role="group" aria-label="Key metrics">${html}</div>`;
  }

  global.Components = global.Components || {};
  global.Components.renderKPIBand = renderKPIBand;
})(window);
