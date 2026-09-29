/* TR-06: Orders, fills and commands (`#/trade/orders`).
 *
 * Real backing data:
 *   - GET /orders (every recorded OrderResult, the same endpoint TR-01/
 *     TR-03 already read) -- app/engine.py processes a routed signal
 *     synchronously per destination account and writes exactly one
 *     `orders` row per attempt, so "Command queue" (P01) is the full
 *     order list read AS the record of every command this service
 *     issued. Each row now also carries real `purpose` ('entry'/'close')
 *     and `family_id` columns (app/db.py's DB-0X migration, populated at
 *     every real order-creation site in app/engine.py) -- see below for
 *     exactly how those are used.
 *   - GET /signals?limit=500 -- matched client-side by `signal_id`, the
 *     same real-price-lookup pattern app/static/views/tr01.js already
 *     uses, needed only to try to price a still-pending order's real
 *     requested notional (see the Unknown outcome queue below).
 *   - GET /accounts/{account_id}/execution-quality -- app/execution_quality
 *     .py's real, per-symbol PU-A2 stage-latency breakdown, computed
 *     directly from `orders.submitted_at`/`executed_at`/
 *     `protection_confirmed_at` and the originating signal's
 *     `received_at`. See the "Execution latency" panel below for exactly
 *     which of the design review's 7 conceptual stages this schema
 *     tracks as genuinely distinct timestamps vs. which honestly
 *     collapse together -- verified against that module's own docstring,
 *     not re-derived here.
 *
 * 2026-09 redesign (design review: "needs to look more like an OMS" --
 * family grouping as the primary structure, real latency segments, a
 * prominent dedicated Unknown-outcome queue):
 *   - Command queue (P01) is now grouped into expandable order families,
 *     keyed by the real `orders.family_id` -- falling back to an order's
 *     own `signal_id` ONLY for a pre-migration row that predates that
 *     column (per app/db.py's own backfill note: `family_id`/`purpose`
 *     are NULL, never fabricated, for any row saved before this
 *     migration). This is the exact same real grouping concept
 *     app/static/views/tr03.js already applies to one position's own
 *     order history -- here it's promoted to be this whole screen's
 *     organizing structure, since this is the OMS-wide queue across every
 *     account, not one position's detail.
 *   - Honest gap on "purpose": this schema's real `purpose` column only
 *     ever holds 'entry' or 'close' (verified against every
 *     `save_order_result(purpose=...)` call site in app/engine.py) --
 *     there is no separate order row, and so no separate purpose value,
 *     for a protective stop placement/tightening or a profit-target leg;
 *     those are tracked instead as app/lifecycle's own stop/target
 *     events (PU-A4, surfaced on TR-03 for a single position), not as
 *     rows in this `orders` table. A family here is genuinely "entry +
 *     however many close orders share its family_id" -- not the fuller
 *     "entry -> partial fills -> stop -> target1 -> target2 -> stop
 *     replacement -> final close" chain the design review sketched,
 *     which would need order rows this codebase doesn't create.
 *   - "Execution latency" (new panel) reports ONLY the segments between
 *     two real, distinct timestamps this schema actually persists.
 *     Every other adjacent pair in the review's 7-stage chain (signal
 *     published, a decision instant distinct from receipt, a broker
 *     acknowledgement/first-fill/final-fill each distinct from one
 *     another) is rendered via Components.renderCapabilityState('not_
 *     tracked', ...) rather than a fabricated duration -- see that
 *     panel's own render function for the exact reasoning per segment,
 *     matched 1:1 against app/execution_quality.py's own disclosure.
 *   - Unknown outcome queue (P00, now first on the screen) is the real
 *     status='pending' subset of GET /orders's own `orders` array (the
 *     same real pending rows this endpoint's `unreconciled_order_count`
 *     already counts, per app/main.py's own docstring on that field) --
 *     rendered via Components.renderAttentionQueue (already used
 *     elsewhere, e.g. TR-01, for exactly this "confirmed-empty is
 *     visibly confirmed-empty" pattern) with each row's real age (now -
 *     its own `executed_at`, the instant this pending record was
 *     written) and, where computable, a real requested notional
 *     (remaining requested quantity x the originating signal's own
 *     `price`) -- never a fabricated fill price. Per-row exposure is
 *     explicitly marked not computable, rather than omitted or guessed,
 *     whenever the signal carried no explicit price (a market order) or
 *     this order predates a resolvable signal.
 *   - Command queue (P01) keeps its one real, owner-only trade-affecting
 *     action from the prior pass: "Emergency: Flatten account", a client
 *     for the REAL `POST /accounts/{account_id}/flatten` (already used,
 *     un-gated, by the legacy dashboard's own `flattenAccount()` -- see
 *     app/main.py's flatten_account docstring: "the dashboard's
 *     account-level 'Flatten account' action"), gated through
 *     Components.confirmAction (app/static/components/action-confirm.js)
 *     instead of a bare confirm()/alert() pair. The preview stage reads
 *     the real GET /positions (+ its managed_lifecycles projection) for
 *     the selected account -- never a client-side guess -- and honestly
 *     discloses that fill price/slippage cannot be previewed (this
 *     service has no quote-before-order capability wired into this
 *     action).
 *   - "Correlations" (P04, chart) has no verified report snapshot/
 *     definition IDs backing it in this build -- no plot when data is
 *     absent, per the panel's own contract; rendered unsupported.
 *   - TR-06-A02 "Request outcome reconciliation": real owner-facing
 *     `POST /reconciliation/run-now`, which calls `OrderReconciler.run_now`
 *     -- the exact same `reconcile_once()` the background loop
 *     (app/reconciliation.py) calls on its own schedule, run synchronously
 *     so the operator gets the real, immediate outcome (orders/exits/
 *     entries examined, how many actually changed state). Gated through
 *     Components.confirmAction (same pattern as "Flatten account" below),
 *     with a real "already running" outcome surfaced honestly if a manual
 *     pass is already in flight (the backend guards against stacking a
 *     second concurrent pass).
 *   - Filters: Account and Status are real (client-side, over the fetched
 *     page, same limitation TR-04 already discloses for its own
 *     filters). A dedicated Family/Purpose filter control is still
 *     omitted -- there is no queryable backing field for either in this
 *     build -- but is far less necessary now that the whole queue is
 *     already grouped by family and each order's own real purpose is
 *     shown per row.
 */
(function () {
  "use strict";

  function shell() {
    return `
      <section class="tr-panel" id="tr06-p00"><h2>Unknown outcome queue</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr06-p01"><h2>Command queue (by order family)</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr06-p02"><h2>Orders/fills tabs</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr06-p05"><h2>Execution latency</h2><div class="tr-panel-body"></div></section>
      <section class="tr-panel" id="tr06-p04"><h2>Correlations</h2><div class="tr-panel-body"></div></section>
    `;
  }

  function outcomePill(status) {
    if (status === "filled") return pill("filled", "ok");
    if (status === "pending") return pill("pending / unknown", "warn");
    return pill(status || "—", "bad");
  }

  function purposePill(purpose) {
    if (purpose === "entry") return pill("entry", "ok");
    if (purpose === "close") return pill("close", "muted");
    return pill("not tracked (pre-migration row)", "muted");
  }

  function applyFilters(orders, filters) {
    return orders.filter((o) => {
      if (filters.account && o.account_id !== filters.account) return false;
      if (filters.status && o.status !== filters.status) return false;
      return true;
    });
  }

  // ---------------------------------------------------------------------
  // Order-family grouping -- the real orders.family_id (falling back to
  // an order's own signal_id ONLY for a pre-migration row with no
  // family_id). Pure/DOM-free so it can be exercised in isolation (see
  // this file's own load-bearing verification note in the redesign
  // commit): breaking the `fallback` branch below silently collapses
  // every pre-migration row into one undifferentiated "" bucket instead
  // of grouping each by its own real signal_id.
  // ---------------------------------------------------------------------

  function groupOrdersByFamily(orders) {
    const groups = new Map(); // family key (string, incl. "") -> orders[]
    orders.forEach((o) => {
      const familyId = o.family_id === null || o.family_id === undefined ? null : String(o.family_id);
      const fallback = o.signal_id === null || o.signal_id === undefined ? null : String(o.signal_id);
      const key = familyId !== null ? familyId : fallback !== null ? fallback : "";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(o);
    });

    return Array.from(groups.entries()).map(([key, familyOrders]) => {
      const usedRealFamilyId = familyOrders.some(
        (o) => o.family_id !== null && o.family_id !== undefined && String(o.family_id) === key
      );
      const symbols = new Set(familyOrders.map((o) => o.symbol || "—"));
      const accounts = new Set(familyOrders.map((o) => o.account_id));
      const statuses = new Set(familyOrders.map((o) => o.status));
      let aggregateStatus;
      let aggregateTone;
      if (statuses.has("pending")) {
        aggregateStatus = "unknown outcome pending";
        aggregateTone = "warn";
      } else if (statuses.has("rejected") || statuses.has("error")) {
        aggregateStatus = "has a failed order";
        aggregateTone = "bad";
      } else if (familyOrders.some((o) => o.purpose === "close" && o.status === "filled")) {
        aggregateStatus = "closed";
        aggregateTone = "muted";
      } else {
        aggregateStatus = "open";
        aggregateTone = "ok";
      }
      const sorted = familyOrders
        .slice()
        .sort((a, b) => String(a.executed_at || "").localeCompare(String(b.executed_at || "")));
      return {
        key,
        usedRealFamilyId,
        orders: sorted,
        symbol: symbols.size === 1 ? [...symbols][0] : "mixed",
        accountId: accounts.size === 1 ? [...accounts][0] : "mixed",
        aggregateStatus,
        aggregateTone,
      };
    });
  }

  function renderFamilyBlocks(families) {
    return families
      .map((f) => {
        const keyLabel = f.usedRealFamilyId
          ? `family_id <span class="mono">${escapeHtml(f.key)}</span>`
          : f.key
          ? `signal_id (pre-migration row, no family_id column yet) <span class="mono">${escapeHtml(f.key)}</span>`
          : "no recorded family_id or signal_id";
        const openByDefault = f.orders.length > 1 || f.aggregateStatus === "unknown outcome pending";
        const rows = f.orders.map((o) => {
          const remaining =
            o.requested_quantity === null ||
            o.requested_quantity === undefined ||
            o.filled_quantity === null ||
            o.filled_quantity === undefined
              ? "—"
              : fmtNum(o.requested_quantity - o.filled_quantity);
          return [
            purposePill(o.purpose),
            outcomePill(o.status),
            `<span class="mono">${escapeHtml(o.broker_order_id || String(o.id))}</span>`,
            fmtNum(o.requested_quantity),
            fmtNum(o.filled_quantity),
            o.filled_price === null || o.filled_price === undefined ? "—" : fmtNum(o.filled_price),
            remaining,
            o.executed_at || "—",
            escapeHtml(o.message || ""),
          ];
        });
        return `<details class="tr06-family"${openByDefault ? " open" : ""}>
          <summary>
            <strong>${escapeHtml(f.symbol)}</strong>
            <span class="mono">${escapeHtml(f.accountId)}</span>
            &middot; ${f.orders.length} order${f.orders.length === 1 ? "" : "s"}
            &middot; ${pill(f.aggregateStatus, f.aggregateTone)}
            <span class="section-note" style="display:inline;margin-left:0.5rem;">grouped by ${keyLabel}</span>
          </summary>
          <div style="margin-top:0.5rem;">${table(
            ["Purpose", "Status", "Broker ID", "Requested", "Filled", "Fill price", "Remaining", "Executed at", "Message"],
            rows,
            "No orders."
          )}</div>
        </details>`;
      })
      .join("");
  }

  // --- TR-06-A0x "Emergency: Flatten account" -- the real, owner-only
  // POST /accounts/{account_id}/flatten, gated through
  // Components.confirmAction instead of the legacy dashboard's bare
  // confirm()/alert() pair. See this file's own module docstring. ---

  function flattenSection(accounts) {
    if (!accounts.length) return "";
    return `
      <div class="tr-controls-row" style="margin-top:0.75rem;">
        <label for="tr06-flatten-account">Emergency: Flatten account</label>
        <select id="tr06-flatten-account">
          ${accounts.map((a) => `<option value="${escapeAttr(a)}">${escapeHtml(a)}</option>`).join("")}
        </select>
        <button type="button" class="danger" id="tr06-flatten-btn">Flatten account…</button>
      </div>
      <p class="section-note">Exits every open position this service tracks for the selected account (real
        <code>POST /accounts/{account_id}/flatten</code> -- app/main.py's flatten_account docstring calls this
        "the dashboard's account-level 'Flatten account' action"). Only positions this service itself opened are
        touched; a manually held position at the broker is left alone. Preview/impact/confirm/result below.</p>
    `;
  }

  // Real preview: reads GET /positions (this service's own tracked
  // positions, plus its managed_lifecycles coverage projection) for the
  // selected account. Never a client-side guess -- if this account has no
  // tracked open positions, or the read fails, that is exactly what gets
  // shown, honestly, rather than a fabricated estimate.
  async function previewFlatten(ctx, accountId) {
    const res = await ctx.fetchJSON("/positions");
    if (res.status === 401 || res.status === 403) {
      throw new Error(`Not authorized to read current positions (HTTP ${res.status}).`);
    }
    if (!res.ok) {
      throw new Error("Could not load current positions for this account -- preview unavailable.");
    }
    const allPositions = (res.data && res.data.positions) || [];
    const lifecycles = (res.data && res.data.managed_lifecycles) || [];
    const positions = allPositions.filter((p) => p.account_id === accountId);
    const lifecycleByKey = new Map(lifecycles.map((l) => [`${l.account_id}::${l.symbol}`, l]));

    const rows = positions.map((p) => {
      const lc = lifecycleByKey.get(`${p.account_id}::${p.symbol}`);
      if (lc) {
        const uncovered = lc.uncovered_quantity;
        return {
          label: p.symbol,
          value: `${fmtNum(p.net_quantity)} sh -- covered ${fmtNum(lc.covered_quantity)} / uncovered ${fmtNum(uncovered)} (stop: ${lc.stop_status})`,
          tone: uncovered > 0 ? "crit" : "ok",
        };
      }
      return {
        label: p.symbol,
        value: `${fmtNum(p.net_quantity)} sh -- protection status not tracked for this position (not a managed-lifecycle account)`,
        tone: "warn",
      };
    });

    const anyUncovered = positions.some((p) => {
      const lc = lifecycleByKey.get(`${p.account_id}::${p.symbol}`);
      return lc && lc.uncovered_quantity > 0;
    });
    const anyUntracked = positions.some((p) => !lifecycleByKey.has(`${p.account_id}::${p.symbol}`));

    const notes = [
      positions.length === 0
        ? `No open positions are tracked for account "${accountId}" -- this call would be a genuine no-op, not a fabricated success.`
        : `Closes ${positions.length} position(s) one at a time, in the order app/main.py's flatten_account itself processes them (not concurrently).`,
      "Fill price and slippage cannot be previewed -- this service has no quote-before-order capability wired into this action; each close submits a real order at whatever price the broker actually fills it at.",
    ];
    if (anyUntracked) {
      notes.push("Protection (stop) coverage is only tracked for managed-lifecycle positions -- see the per-symbol rows above for which ones this build cannot report on.");
    }

    return {
      severity: positions.length === 0 ? "info" : anyUncovered ? "critical" : "warning",
      rows,
      notes,
    };
  }

  function renderFlattenResult(outcome) {
    if (!outcome || !outcome.ok) {
      const message = (outcome && outcome.error) || "Unknown error.";
      return `<p class="action-confirm-result-heading">Flatten failed</p><p class="action-confirm-note action-confirm-impact-crit">${escapeHtml(message)}</p>`;
    }
    const result = outcome.result || {};
    const closed = result.closed || [];
    if (!closed.length) {
      return `<p class="action-confirm-result-heading">Completed</p><p class="action-confirm-note">No open positions were tracked for "${escapeHtml(result.account_id || "")}" -- nothing to close.</p>`;
    }
    const rows = closed
      .map(
        (c) =>
          `<div class="action-confirm-row"><span class="ac-label">${escapeHtml(c.symbol)}</span><span class="ac-value">${escapeHtml(c.status)} (filled ${c.filled_quantity ?? "—"})${c.message ? " -- " + escapeHtml(c.message) : ""}</span></div>`
      )
      .join("");
    return `<p class="action-confirm-result-heading">Completed -- real result from POST /accounts/${escapeHtml(result.account_id || "")}/flatten</p>${rows}`;
  }

  function wireFlattenButton(ctx, queueEl) {
    const btn = queueEl.querySelector("#tr06-flatten-btn");
    if (!btn) return;
    btn.addEventListener("click", async () => {
      const select = queueEl.querySelector("#tr06-flatten-account");
      const accountId = select ? select.value : "";
      if (!accountId) return;
      await Components.confirmAction({
        title: `Flatten account "${accountId}"`,
        confirmWord: accountId,
        confirmLabel: "Flatten account",
        previewFn: () => previewFlatten(ctx, accountId),
        onConfirm: () => postJSON(`/accounts/${encodeURIComponent(accountId)}/flatten`, {}),
        renderResult: renderFlattenResult,
      });
      // Refresh the real order/command record after the operator dismisses
      // the durable result panel, whatever the outcome -- this view's own
      // 10s poll would eventually pick it up anyway, but a same-account
      // flatten's own new order rows are worth showing immediately.
      await load(ctx, { account: accountId, status: "" }, "orders");
    });
  }

  // --- Request outcome reconciliation (TR-06-A02): real POST
  // /reconciliation/run-now, gated through Components.confirmAction (no
  // typed confirmWord -- this is a read/re-check pass, not a destructive
  // one, unlike Flatten account above). Preview is honest about what this
  // triggers: a real, synchronous re-check of every still-pending order/
  // exit/entry this service currently knows about -- not a guess at how
  // many will actually change. ---
  async function previewReconcile(ctx) {
    const res = await ctx.fetchJSON("/orders");
    if (res.status === 401 || res.status === 403) {
      throw new Error(`Not authorized to read pending orders (HTTP ${res.status}).`);
    }
    const count = (res.ok && res.data && res.data.unreconciled_order_count) || 0;
    return {
      severity: count > 0 ? "info" : "info",
      rows: [{ label: "Pending orders (account-wide)", value: String(count), tone: "neutral" }],
      notes: [
        "Runs the real reconciliation pass (app/reconciliation.py's OrderReconciler.reconcile_once) synchronously, " +
          "right now, instead of waiting for its next scheduled background run.",
        "If a manual pass is already in flight, this reports that honestly instead of stacking a second concurrent pass.",
      ],
    };
  }

  function renderReconcileResult(outcome) {
    if (!outcome || !outcome.ok) {
      const message = (outcome && outcome.error) || "Unknown error.";
      return `<p class="action-confirm-result-heading">Reconciliation request failed</p><p class="action-confirm-note action-confirm-impact-crit">${escapeHtml(message)}</p>`;
    }
    const result = outcome.result || {};
    if (result.already_running) {
      return `<p class="action-confirm-result-heading">Already running</p><p class="action-confirm-note">A manual reconciliation pass was already in flight -- this request did not start a second, concurrent one.</p>`;
    }
    return `<p class="action-confirm-result-heading">Completed -- real result from POST /reconciliation/run-now</p>
      <div class="action-confirm-row"><span class="ac-label">Orders examined</span><span class="ac-value">${fmtNum(result.orders_examined ?? 0)} (pending orders ${fmtNum(result.pending_orders_examined ?? 0)}, pending exits ${fmtNum(result.pending_exits_examined ?? 0)}, pending entries ${fmtNum(result.pending_entries_examined ?? 0)})</span></div>
      <div class="action-confirm-row"><span class="ac-label">Corrected</span><span class="ac-value">${fmtNum(result.corrected ?? 0)}</span></div>`;
  }

  function wireReconcileButton(ctx, unknownEl) {
    const btn = unknownEl.querySelector("#tr06-reconcile-btn");
    if (!btn) return;
    btn.addEventListener("click", async () => {
      await Components.confirmAction({
        title: "Request outcome reconciliation",
        confirmLabel: "Run reconciliation now",
        previewFn: () => previewReconcile(ctx),
        onConfirm: () => postJSON("/reconciliation/run-now", {}),
        renderResult: renderReconcileResult,
      });
      await load(ctx, {}, "orders");
    });
  }

  // ---------------------------------------------------------------------
  // Unknown outcome queue -- the real status='pending' subset of GET
  // /orders's own orders array (account-wide, not narrowed by this
  // screen's Account/Status filters, matching app/main.py's own
  // unreconciled_order_count semantics), rendered via
  // Components.renderAttentionQueue so a confirmed-empty queue is
  // visibly confirmed-empty rather than flat "no rows" prose.
  // ---------------------------------------------------------------------

  function buildUnknownOutcomeItems(allOrders, signalsById) {
    const now = Date.now();
    return allOrders
      .filter((o) => o.status === "pending")
      .map((o) => {
        const ageSeconds =
          o.executed_at && Number.isFinite(new Date(o.executed_at).getTime())
            ? (now - new Date(o.executed_at).getTime()) / 1000
            : undefined;
        const remainingQty =
          o.requested_quantity === null || o.requested_quantity === undefined
            ? null
            : o.requested_quantity - (o.filled_quantity || 0);
        const signal = o.signal_id !== null && o.signal_id !== undefined ? signalsById.get(String(o.signal_id)) : null;
        const price = signal && signal.price !== null && signal.price !== undefined ? signal.price : null;
        let exposureText;
        if (remainingQty !== null && price !== null) {
          exposureText = `possible exposure ~${fmtNum(remainingQty * price)} (${fmtNum(remainingQty)} remaining qty x ${fmtNum(
            price
          )} signal price at receipt -- not a guaranteed fill price)`;
        } else if (remainingQty === null) {
          exposureText = "exposure not computable (requested quantity not recorded on this order)";
        } else {
          exposureText = "exposure not computable (originating signal carried no explicit price -- likely a market order)";
        }
        return {
          severity: "warning",
          text: `${o.symbol || "—"} on ${o.account_id} -- order ${o.broker_order_id || o.id}: outcome still UNKNOWN (pending, not yet broker-confirmed). ${exposureText}`,
          ageSeconds,
          correlationId: o.signal_id !== null && o.signal_id !== undefined ? String(o.signal_id) : undefined,
        };
      });
  }

  // ---------------------------------------------------------------------
  // Execution latency -- real segments only. Matched 1:1 against
  // app/execution_quality.py's own honest-scope disclosure: of the design
  // review's 7 conceptual stages (signal published -> local receipt ->
  // decision -> submission -> acknowledgement -> first fill -> final fill
  // -> protection confirmed), this schema separately persists exactly 4
  // distinct instants (received_at, submitted_at, executed_at,
  // protection_confirmed_at), giving 3 real computable segments and 3
  // segments that collapse to zero width or are simply never captured.
  // ---------------------------------------------------------------------

  const LATENCY_SEGMENTS = [
    {
      label: "Signal received -> Decision",
      real: false,
      reason:
        "No source adapter (webhook/Discord/Slack/Telegram/Twitter/SMS/MT4-MT5/NinjaTrader/Rithmic) records a separate 'decision' instant -- Signal.received_at is stamped as the very last step of parsing, in the same call frame that builds the decision. Reporting a distinct gap here would fabricate one that doesn't exist in this code.",
    },
    {
      label: "Decision -> Submission",
      real: true,
      stage: "receipt_to_submission",
      note:
        "Numerically this is signal received (Signal.received_at) -> broker submission (orders.submitted_at) -- 'Decision' collapses with 'Signal received' (see above), so this is the real receipt-to-submission gap app/execution_quality.py itself reports.",
    },
    {
      label: "Submission -> Acknowledgment",
      real: true,
      stage: "submission_to_fill",
      note:
        "Every broker adapter in this codebase returns one synchronous response with no separate 'accepted, working' callback (app/brokers/base.py's place_order signature, verified against every concrete adapter) -- 'Acknowledgment' collapses with 'First fill'/'Final fill' below, so this is the real submission-to-fill gap.",
    },
    {
      label: "Acknowledgment -> First fill",
      real: false,
      reason:
        "Acknowledgment and first fill are the SAME real instant (orders.executed_at) for every broker adapter today -- there is no second, later callback to measure a gap between them.",
    },
    {
      label: "First fill -> Final fill",
      real: false,
      reason:
        "This schema has no partial-fill-event table: orders.filled_quantity/executed_at is a single pair app/reconciliation.py overwrites in place once a PENDING order's terminal status is confirmed, so the original response's own instant isn't preserved separately from the final one -- reporting a distinct first-vs-final-fill gap would require a schema change this build doesn't have.",
    },
    {
      label: "Final fill -> Protection confirmed",
      real: true,
      stage: "fill_to_protection",
      note:
        "orders.protection_confirmed_at (real, entry-only, managed-lifecycle-only) is set from app/lifecycle/manager.py's StopRecord.confirmed_at at the moment a protective stop is broker-confirmed resting -- NULL for a non-managed_lifecycle account, an unprotected entry, or any close/exit order.",
    },
  ];

  function renderLatencyStatRows(perAccountStages, stageName) {
    const rows = [];
    for (const [accountId, stageLatencies] of perAccountStages.entries()) {
      for (const [symbol, stages] of Object.entries(stageLatencies)) {
        const stat = stages.find((s) => s.stage === stageName);
        if (stat && stat.sample_count > 0) {
          rows.push([
            `<span class="mono">${escapeHtml(accountId)}</span>`,
            `<span class="mono">${escapeHtml(symbol)}</span>`,
            stat.sample_count,
            `${fmtNum(stat.mean_seconds)}s`,
            `${fmtNum(stat.median_seconds)}s`,
            `${fmtNum(stat.max_seconds)}s`,
          ]);
        }
      }
    }
    return rows;
  }

  async function loadLatency(ctx, els, accountIds) {
    const perAccountStages = new Map(); // account_id -> stage_latencies
    const unresolvedAccounts = [];
    await Promise.all(
      accountIds.map(async (accountId) => {
        const res = await ctx.fetchJSON(`/accounts/${encodeURIComponent(accountId)}/execution-quality`);
        if (res.ok && res.data && res.data.stage_latencies) {
          perAccountStages.set(accountId, res.data.stage_latencies);
        } else {
          unresolvedAccounts.push(accountId);
        }
      })
    );

    const segmentBlocks = LATENCY_SEGMENTS.map((seg) => {
      if (!seg.real) {
        const holder = document.createElement("div");
        Components.renderCapabilityState(holder, { status: "not_tracked", reason: seg.reason });
        return `<div class="tr06-latency-segment"><p class="section-note"><strong>${escapeHtml(seg.label)}</strong></p>${holder.innerHTML}</div>`;
      }
      const rows = renderLatencyStatRows(perAccountStages, seg.stage);
      const body = table(["Account", "Symbol", "Samples", "Mean", "Median", "Max"], rows, "No filled orders with both real timestamps for this segment yet.");
      return `<div class="tr06-latency-segment"><p class="section-note"><strong>${escapeHtml(seg.label)}</strong> -- real, from app/execution_quality.py's <code>${escapeHtml(seg.stage)}</code> stage. ${escapeHtml(seg.note)}</p>${body}</div>`;
    }).join("");

    const unresolvedNote = unresolvedAccounts.length
      ? `<p class="section-note">Execution-quality could not be read for: ${unresolvedAccounts.map(escapeHtml).join(", ")} (account no longer in routing config, or not yet authorized).</p>`
      : "";

    StateMatrix.render(els.latency, {
      state: "ready",
      html: `<p class="section-note">Of the design review's 7-stage pipeline (signal published -> local receipt -> decision -> submission -> acknowledgement -> first fill -> final fill -> protection confirmed), this schema separately persists exactly 4 real instants: <code>signals.received_at</code>, <code>orders.submitted_at</code>, <code>orders.executed_at</code>, <code>orders.protection_confirmed_at</code> -- giving the 3 real segments below. Every other adjacent pair genuinely collapses to the same instant or was never captured, and is shown as "Not tracked" rather than a fabricated duration. Aggregated per account/symbol from GET /accounts/{account_id}/execution-quality (real, already-computed by this codebase -- not re-derived here).</p>${unresolvedNote}${segmentBlocks}`,
    });
  }

  async function load(ctx, filters, tab) {
    filters = filters || {};
    tab = tab || "orders";
    const els = {
      unknown: ctx.container.querySelector("#tr06-p00 .tr-panel-body"),
      queue: ctx.container.querySelector("#tr06-p01 .tr-panel-body"),
      tabs: ctx.container.querySelector("#tr06-p02 .tr-panel-body"),
      latency: ctx.container.querySelector("#tr06-p05 .tr-panel-body"),
      correlations: ctx.container.querySelector("#tr06-p04 .tr-panel-body"),
    };
    for (const el of Object.values(els)) StateMatrix.render(el, { state: "loading" });

    const [ordersRes, signalsRes] = await Promise.all([
      ctx.fetchJSON("/orders?limit=500"),
      ctx.fetchJSON("/signals?limit=500"),
    ]);
    if (ordersRes.status === 401 || ordersRes.status === 403) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "denied", deniedCode: ordersRes.status });
      return;
    }
    if (!ordersRes.ok) {
      for (const el of Object.values(els)) StateMatrix.render(el, { state: "error", message: "Could not load orders." });
      return;
    }

    const allOrders = (ordersRes.data && ordersRes.data.orders) || [];
    const unreconciledOrderCount = (ordersRes.data && ordersRes.data.unreconciled_order_count) || 0;
    const signalsById = new Map(
      (signalsRes.ok && signalsRes.data && signalsRes.data.signals ? signalsRes.data.signals : []).map((s) => [String(s.id), s])
    );
    const orders = applyFilters(allOrders, filters);

    StateMatrix.render(els.correlations, {
      state: "unsupported",
      reason: "No verified correlation report snapshot with definition IDs exists in this build -- no plot is shown rather than one built from unverified data.",
    });

    if (!allOrders.length) {
      StateMatrix.render(els.unknown, { state: "empty", emptyMessage: "No order or command records in this scope." });
      StateMatrix.render(els.queue, {
        state: "empty",
        emptyMessage: "No order or command records in this scope.",
        nextRoute: "/trade/signals",
        nextLabel: "Incoming signal stream (TR-04)",
      });
      StateMatrix.render(els.tabs, { state: "empty", emptyMessage: "No order or command records in this scope." });
      StateMatrix.render(els.latency, { state: "empty", emptyMessage: "No order records in this scope." });
      return;
    }

    // --- Unknown outcome queue: prominent, account-wide, real pending
    // orders -- rendered first on the screen (P00), not buried. ---
    {
      const unknownItems = buildUnknownOutcomeItems(allOrders, signalsById);
      els.unknown.removeAttribute("aria-busy");
      els.unknown.innerHTML = `<p class="section-note">Every order this service recorded with a still-unresolved (pending, not yet broker-confirmed) outcome, across every account -- resolved automatically by app/reconciliation.py's background loop once the broker confirms a terminal outcome; there is no owner-facing action to trigger that pass on demand (see below). Real, account-wide count from GET /orders's own <code>unreconciled_order_count</code>: ${unreconciledOrderCount}${
        unreconciledOrderCount !== unknownItems.length
          ? ` (differs from the ${unknownItems.length} shown below because that count is not limited to the most recent 500 orders this page fetched -- it is the true total, this page's list may be a subset).`
          : "."
      }</p><div id="tr06-unknown-body"></div>`;
      Components.renderAttentionQueue(els.unknown.querySelector("#tr06-unknown-body"), { items: unknownItems });
      const reconcileRow = document.createElement("div");
      reconcileRow.className = "tr-controls-row";
      reconcileRow.style.marginTop = "0.75rem";
      reconcileRow.innerHTML = `<button type="button" id="tr06-reconcile-btn">Request outcome reconciliation…</button>`;
      els.unknown.appendChild(reconcileRow);
      wireReconcileButton(ctx, els.unknown);
    }

    const accounts = [...new Set(allOrders.map((o) => o.account_id))];
    const statuses = [...new Set(allOrders.map((o) => o.status))];
    const filterForm = `
      <form id="tr06-filter-form" class="inline-form" style="margin:0;">
        <label>Account
          <select name="account">
            <option value="">(any)</option>
            ${accounts.map((a) => `<option value="${escapeAttr(a)}" ${filters.account === a ? "selected" : ""}>${escapeHtml(a)}</option>`).join("")}
          </select>
        </label>
        <label>Status
          <select name="status">
            <option value="">(any)</option>
            ${statuses.map((s) => `<option value="${escapeAttr(s)}" ${filters.status === s ? "selected" : ""}>${escapeHtml(s)}</option>`).join("")}
          </select>
        </label>
        <div class="actions"><button type="submit">Apply</button> <button type="button" class="ghost" id="tr06-clear-filters">Clear</button></div>
      </form>
      <p class="section-note">Filters: Account and Status are applied client-side over the fetched page. Every order below is grouped by its real order family first (see the module docstring) -- a dedicated Family/Purpose filter has no queryable backing field in this build.</p>
    `;

    if (!orders.length) {
      StateMatrix.render(els.queue, { state: "empty", emptyMessage: "No order or command records in this scope.", nextRoute: "/trade/signals", nextLabel: "Incoming signal stream (TR-04)" });
    } else {
      const families = groupOrdersByFamily(orders);
      StateMatrix.render(els.queue, {
        state: "ready",
        html: `${filterForm}${renderFamilyBlocks(families)}${flattenSection(accounts)}`,
      });
      const form = els.queue.querySelector("#tr06-filter-form");
      form.addEventListener("submit", (e) => {
        e.preventDefault();
        const f = new FormData(form);
        load(ctx, { account: f.get("account") || "", status: f.get("status") || "" }, tab);
      });
      els.queue.querySelector("#tr06-clear-filters").addEventListener("click", () => load(ctx, {}, tab));
      wireFlattenButton(ctx, els.queue);
    }

    // --- Orders/fills tabs: same read model, client-side presentational
    // toggle between "all orders" and "fills only" -- no live effect.
    const tabbed = tab === "fills" ? orders.filter((o) => o.status === "filled") : orders;
    if (!tabbed.length) {
      StateMatrix.render(els.tabs, { state: "empty", emptyMessage: tab === "fills" ? "No fills in this scope." : "No orders in this scope." });
    } else {
      const rows = tabbed.map((o) => [
        o.executed_at || "—",
        `<span class="mono">${escapeHtml(o.account_id)}</span>`,
        `<span class="mono">${escapeHtml(o.symbol || "—")}</span>`,
        escapeHtml(o.side || "—"),
        fmtNum(o.filled_quantity),
        o.filled_price === null || o.filled_price === undefined ? "—" : fmtNum(o.filled_price),
        outcomePill(o.status),
      ]);
      StateMatrix.render(els.tabs, {
        state: "ready",
        html: `<div class="tr-controls-row">
                 <button type="button" class="${tab === "orders" ? "" : "ghost"}" id="tr06-tab-orders">All orders</button>
                 <button type="button" class="${tab === "fills" ? "" : "ghost"}" id="tr06-tab-fills">Fills only</button>
               </div>${table(["Executed at", "Account", "Symbol", "Side", "Filled qty", "Filled price", "Status"], rows, "No rows.")}`,
      });
      els.tabs.querySelector("#tr06-tab-orders").addEventListener("click", () => load(ctx, filters, "orders"));
      els.tabs.querySelector("#tr06-tab-fills").addEventListener("click", () => load(ctx, filters, "fills"));
    }

    await loadLatency(ctx, els, accounts);

    ctx.setChrome({ asOf: new Date().toISOString() });
  }

  window.Views = window.Views || {};
  window.Views.tr06 = {
    title: "Orders, fills and commands",
    breadcrumb: "Trade / Orders",
    scope: "private_owner",
    origin: "private_execution",
    async render(ctx) {
      ctx.container.innerHTML = shell();
      await load(ctx, {}, "orders");
      ctx.registerPoll("tr06", 10000, () => load(ctx, {}, "orders"));
    },
  };
  Router.register("/trade/orders", "tr06");
})();
