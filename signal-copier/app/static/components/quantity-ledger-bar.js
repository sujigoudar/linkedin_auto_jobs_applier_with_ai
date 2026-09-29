/* Components.renderQuantityLedgerBar -- pure presentational horizontal
 * proportional stacked bar, e.g.:
 *   Owned 100 | Working stop 70 | Pending target 20 | Uncommitted 10
 *
 * The caller supplies already-computed segment values -- this component
 * does no position math of its own. Vanilla JS, no dependencies.
 *
 * Usage:
 *   Components.renderQuantityLedgerBar(container, {
 *     total: 100,
 *     segments: [
 *       { label: "Working stop", value: 70, tone: "ok" },
 *       { label: "Pending target", value: 20, tone: "warn" },
 *       { label: "Uncommitted", value: 10, tone: "crit" },
 *     ],
 *   });
 *
 * `segments[].tone` is one of ok | warn | crit | bad | accent | neutral
 * (default "neutral"). `total` is optional -- if omitted, it's computed
 * as the sum of segment values (falling back to 0-width segments if the
 * sum is 0).
 */
(function (global) {
  "use strict";

  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  const VALID_TONES = new Set(["ok", "warn", "crit", "bad", "accent", "neutral"]);

  function fmtNum(n) {
    if (n === null || n === undefined || Number.isNaN(n)) return "—";
    return Number(n).toLocaleString(undefined, { maximumFractionDigits: 6 });
  }

  function renderQuantityLedgerBar(container, opts) {
    if (!container) return;
    const raw = opts || {};
    const segments = raw.segments || [];
    if (!segments.length) {
      container.innerHTML = `<div class="qledger-empty">No quantity data available.</div>`;
      return;
    }

    const sumOfSegments = segments.reduce((acc, s) => acc + (Number(s.value) || 0), 0);
    const total = raw.total !== undefined && raw.total !== null ? Number(raw.total) : sumOfSegments;
    const denom = total > 0 ? total : sumOfSegments;

    const barSegments = segments
      .map((s) => {
        const tone = VALID_TONES.has(s.tone) ? s.tone : "neutral";
        const value = Number(s.value) || 0;
        const pct = denom > 0 ? (value / denom) * 100 : 0;
        return `<div class="qledger-segment tone-${tone}" style="width:${pct.toFixed(3)}%" title="${escapeHtml(s.label ?? "")}: ${escapeHtml(fmtNum(value))}"></div>`;
      })
      .join("");

    const legendItems = segments
      .map((s) => {
        const tone = VALID_TONES.has(s.tone) ? s.tone : "neutral";
        return `<span class="qledger-legend-item"><span class="qledger-swatch tone-${tone}"></span>${escapeHtml(s.label ?? "")} ${escapeHtml(fmtNum(s.value))}</span>`;
      })
      .join("");

    container.innerHTML = `
      <div class="qledger">
        <div class="qledger-bar" role="img" aria-label="Quantity breakdown">${barSegments}</div>
        <div class="qledger-legend">${legendItems}</div>
      </div>`;
  }

  global.Components = global.Components || {};
  global.Components.renderQuantityLedgerBar = renderQuantityLedgerBar;
})(window);
