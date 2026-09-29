/* Hash-based client-side router for signal-copier's private-execution
 * screens (TR-01..TR-16). Established in the TR-01..TR-04 batch as the
 * pattern every later batch (TR-05..08, TR-09..12, TR-13..16) extends.
 *
 * ## Why hash routing, and how it stays "browser-history-backed"
 *
 * There is no build step/bundler and no server-side router in this project
 * (see app/main.py's `dashboard()` docstring) -- `GET /` always serves the
 * one static app/static/dashboard.html. Real path-based routes
 * (`/trade/positions/AAPL`) would 404 on a hard reload or a shared link
 * unless the server grew a catch-all route just to keep re-serving the
 * same HTML file for every possible client path -- adding that complexity
 * isn't needed here. `location.hash = "#/trade/positions/AAPL"` instead:
 * setting `location.hash` from JS (as every `<a href="#/...">` link and
 * `Router.navigate()` below does) pushes a REAL history entry the
 * browser's own back/forward buttons and address bar already honor, and
 * the `hashchange` event fires on every one of those transitions -- this
 * satisfies each TR-0x spec's "browser-history-backed" navigation
 * requirement without any new server route.
 *
 * ## How a new view registers itself (for the next 3 batches)
 *
 * 1. Add `app/static/views/trNN.js`. It must set `window.Views.trNN = {...}`
 *    (see any of tr01.js..tr04.js for the exact shape: `title`,
 *    `breadcrumb`, `scope`, `origin`, `render(ctx)`).
 * 2. Call `Router.register("/trade/whatever/:id", "trNN")` in this file's
 *    own registration block at the bottom (or, if you'd rather keep this
 *    file untouched, alongside the other `<script>` tags in
 *    dashboard.html -- either is fine, this file's own registrations are
 *    just the ones TR-01..TR-04 needed).
 * 3. Include `<script src="/static/views/trNN.js"></script>` in
 *    dashboard.html AFTER router.js and state-matrix.js.
 *
 * ## What a view's `render(ctx)` gets and must do
 *
 * `ctx` = {
 *   params:      {} of matched path params (e.g. {account_id, symbol}),
 *   container:    the <div id="route-panels"> DOM node to fill,
 *   setChrome:    (opts) => void -- updates breadcrumb/title/badges/as-of,
 *   fetchJSON:    same authenticated fetch dashboard.html's own code uses
 *                 (401 triggers the SAME session-expired path as the
 *                 legacy dashboard -- see `onSessionExpired` below),
 *   registerPoll: (key, intervalMs, fn) => void -- polls `fn` on
 *                 `intervalMs`, but only while this tab is visible AND
 *                 only ever 1 in-flight call per `key` at a time (a slow
 *                 response never overlaps with the next scheduled tick --
 *                 matches every TR-0x spec's "10s fallback with 1
 *                 in-flight request per resource... hidden tabs stop
 *                 polling"). Automatically cancelled when the route
 *                 changes away from this view.
 *   navigate:     (path) => void -- programmatic navigation, same as
 *                 clicking a `#/...` link.
 * }
 * A view's `render` is called once per route entry (including re-entry
 * with different params, e.g. switching between two positions) and should
 * do its own StateMatrix.render(panelEl, {state: ...}) per panel.
 */
(function (global) {
  "use strict";

  const routes = []; // {pattern, keys, viewName}
  let activePolls = new Map(); // key -> {timer, inFlight}
  let currentRouteKey = null;
  let mountEl = null;
  let legacyEl = null;
  let chromeEls = null;
  let authHooks = { fetchJSON: null, onSessionExpired: null };
  let onNoRoute = null; // called when hash present but no route/legacy fallback

  function register(pathPattern, viewName) {
    const keys = [];
    const regexSource = pathPattern
      .split("/")
      .map((seg) => {
        if (seg.startsWith(":")) {
          keys.push(seg.slice(1));
          return "([^/]+)";
        }
        return seg.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      })
      .join("/");
    routes.push({ pattern: new RegExp(`^${regexSource}$`), keys, viewName });
  }

  function currentPath() {
    const hash = global.location.hash;
    if (!hash || hash === "#") return "";
    return hash.slice(1);
  }

  function matchRoute(path) {
    for (const r of routes) {
      const m = path.match(r.pattern);
      if (m) {
        const params = {};
        r.keys.forEach((k, i) => {
          params[k] = decodeURIComponent(m[i + 1]);
        });
        return { view: global.Views && global.Views[r.viewName], params, viewName: r.viewName };
      }
    }
    return null;
  }

  function clearPolls() {
    for (const [, entry] of activePolls) {
      if (entry.timer) clearTimeout(entry.timer);
    }
    activePolls = new Map();
  }

  function registerPoll(key, intervalMs, fn) {
    // Cancel any prior poll under the same key (a view re-rendering with
    // new params replaces its own poll rather than stacking a second one).
    const existing = activePolls.get(key);
    if (existing && existing.timer) clearTimeout(existing.timer);
    const entry = { timer: null, inFlight: false, tick: null };
    activePolls.set(key, entry);

    async function tick() {
      if (!activePolls.has(key)) return; // cancelled (route changed)
      if (document.hidden) {
        // Hidden tabs stop polling entirely -- resumes on visibilitychange below.
        entry.timer = null;
        return;
      }
      if (entry.inFlight) {
        // 1 in-flight request per resource: skip this tick rather than pile up.
        entry.timer = setTimeout(tick, intervalMs);
        return;
      }
      entry.inFlight = true;
      try {
        await fn();
      } catch (e) {
        // Individual view fn() is responsible for its own error rendering;
        // a poll tick failing must never throw out of this loop.
      } finally {
        entry.inFlight = false;
        if (activePolls.has(key)) entry.timer = setTimeout(tick, intervalMs);
      }
    }
    // The caller (a view's render()) has already done its own initial
    // load before registering this poll -- the first tick fires after
    // one full interval, not immediately, so it never races that initial
    // render with a second concurrent fetch cycle (which used to
    // intermittently reset an already-ready panel back to its loading
    // skeleton mid-render).
    entry.tick = tick;
    entry.timer = setTimeout(tick, intervalMs);
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    // Resume any poll whose timer went idle while hidden.
    for (const [, entry] of activePolls) {
      if (!entry.timer && !entry.inFlight && entry.tick) {
        entry.tick();
      }
    }
  });

  function setChrome(opts) {
    if (!chromeEls) return;
    if (opts.breadcrumb) chromeEls.breadcrumb.textContent = opts.breadcrumb;
    if (opts.title) chromeEls.title.textContent = opts.title;
    if (opts.scope !== undefined) chromeEls.scope.textContent = opts.scope ? `Scope: ${opts.scope}` : "";
    if (opts.origin !== undefined) chromeEls.origin.textContent = opts.origin ? `Origin: ${opts.origin}` : "";
    if (opts.mode !== undefined) chromeEls.mode.textContent = opts.mode ? `Mode: ${opts.mode}` : "";
    if (opts.asOf !== undefined) chromeEls.asOf.textContent = opts.asOf ? `As of ${opts.asOf}` : "";
  }

  function navigate(path) {
    global.location.hash = `#${path}`;
  }

  async function resolve() {
    const path = currentPath();
    if (!path.startsWith("/trade")) {
      // No routed view for this hash (including the empty hash) -- let
      // dashboard.html show its legacy view instead.
      clearPolls();
      currentRouteKey = null;
      if (mountEl) mountEl.hidden = true;
      if (legacyEl) legacyEl.hidden = false;
      if (onNoRoute) onNoRoute();
      return false;
    }

    const match = matchRoute(path);
    if (mountEl) mountEl.hidden = false;
    if (legacyEl) legacyEl.hidden = true;

    if (!match || !match.view) {
      clearPolls();
      currentRouteKey = null;
      setChrome({ breadcrumb: "Trade", title: "Not found", scope: "", origin: "", mode: "", asOf: "" });
      StateMatrix.render(mountEl.querySelector("#route-panels"), {
        state: "denied",
        deniedCode: 404,
      });
      return true;
    }

    clearPolls();
    currentRouteKey = path;
    const panels = mountEl.querySelector("#route-panels");
    const ctx = {
      params: match.params,
      container: panels,
      setChrome,
      fetchJSON: authHooks.fetchJSON,
      registerPoll,
      navigate,
    };
    setChrome({
      breadcrumb: match.view.breadcrumb || "Trade",
      title: match.view.title || "",
      scope: match.view.scope || "private_owner",
      origin: match.view.origin || "private_execution",
      mode: match.view.mode || "live",
      asOf: "",
    });
    try {
      await match.view.render(ctx);
    } catch (err) {
      StateMatrix.render(panels, { state: "error", message: "Could not load this screen.", correlationId: "" });
    }
    return true;
  }

  function init(opts) {
    mountEl = opts.mountEl;
    legacyEl = opts.legacyEl || null;
    chromeEls = opts.chromeEls;
    authHooks.fetchJSON = opts.fetchJSON;
    authHooks.onSessionExpired = opts.onSessionExpired;
    onNoRoute = opts.onNoRoute || null;
    global.addEventListener("hashchange", resolve);
  }

  global.Router = { register, init, resolve, navigate, registerPoll, currentPath };
})(window);
