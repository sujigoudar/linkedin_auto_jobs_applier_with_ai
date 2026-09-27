// Signal Copier Auto-Journal for NinjaTrader 8 — Indicator Edition
//
// *** DISCLOSED, UNVERIFIED ***
// This file has not been compiled or run against a real or Sim101
// NinjaTrader 8 install (there is no NinjaTrader/Windows in the
// environment that wrote it). It is modeled on the verified, currently
// public `Account.ExecutionUpdate` -> `HttpClient.PostAsync` pattern used
// by Apex-Logics/TradVue's `TradVueAutoJournal.cs` and Shadowscr-7/
// tradingadmin's `TradeMonitor.cs` (both fetched and read directly to
// confirm the pattern is real and NinjaTrader's own documented API
// surface, not copied — neither repository carries a LICENSE file).
// Treat this as a reviewed reference implementation, not a tested one.
// Import and run it against Sim101 (paper) first, watch the Output
// window and your signal-copier's /signals and /orders endpoints for a
// few real fills, and only then point it at a live account.
//
// Captures every account fill (manual, Chart Trader, ATM, SuperDOM, any
// strategy) and POSTs it to this service's /ninjatrader/webhook route.
// See app/sources/ninjatrader.py's module docstring for exactly how each
// field below is interpreted (entry/exit, Long/Short, asset_class).
//
// WHY INDICATOR (not Strategy):
//   A Strategy's OnExecutionUpdate only sees orders IT placed. This
//   never places orders, so that event would never fire. An Indicator
//   can subscribe to Account.ExecutionUpdate at the account level, which
//   receives every fill regardless of what placed it.
//
// INSTALLATION:
//   1. In NinjaTrader: Tools -> Import -> NinjaScript Add-On -> select
//      this file.
//   2. Add the indicator to any chart: Chart -> right-click ->
//      Indicators -> SignalCopierAutoJournal.
//   3. Set WebhookUrl to https://<your-public-host>/ninjatrader/webhook
//      and Secret to the same value as this service's
//      NINJATRADER_WEBHOOK_SECRET environment variable.
//   4. Optionally set AccountName (leave blank to monitor ALL accounts —
//      recommended to start with a single Sim101 account instead).
//   5. Click OK — fills report from this point on.
//
// SECURITY:
//   - Only sends data (outbound HTTPS POST). Cannot place, modify, or
//     cancel orders, and never reads account balance or credentials.
//   - The Secret is sent in a plain custom header (X-NinjaTrader-Secret),
//     not embedded in the URL, so it doesn't end up in ordinary HTTP
//     access logs the way a query-string token would.

#region Using declarations
using System;
using System.Collections.Generic;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Globalization;
using NinjaTrader.Cbi;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.Indicators;
#endregion

namespace NinjaTrader.NinjaScript.Indicators
{
    public class SignalCopierAutoJournal : Indicator
    {
        // ── HTTP ──────────────────────────────────────────────────────────────
        private static readonly HttpClient httpClient = new HttpClient();

        // ── Deduplication ─────────────────────────────────────────────────────
        // Static so it's shared across all chart instances running this
        // indicator -- prevents duplicate sends when the same execution
        // fires on multiple charts.
        private static readonly HashSet<string> seenExecutionIds = new HashSet<string>();
        private static readonly object seenLock = new object();
        private const int MAX_SEEN_IDS = 2000;

        private readonly List<Account> subscribedAccounts = new List<Account>();

        // Key = "accountName|instrumentName" -- net position: positive =
        // long, negative = short, 0 = flat.
        private readonly Dictionary<string, int> positionMap = new Dictionary<string, int>();

        // ─────────────────────────────────────────────────────────────────────
        // STATE MACHINE
        // ─────────────────────────────────────────────────────────────────────

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "Reports every account fill to a Signal Copier /ninjatrader/webhook endpoint.";
                Name        = "SignalCopierAutoJournal";
                Calculate   = Calculate.OnBarClose;
                IsOverlay   = true;
                IsSuspendedWhileInactive = false;

                WebhookUrl  = "https://YOUR-HOST/ninjatrader/webhook";
                Secret      = "";
                AccountName = "Sim101";
                LogToOutput = true;
            }
            else if (State == State.DataLoaded)
            {
                SubscribeToAccounts();
            }
            else if (State == State.Historical)
            {
                if (subscribedAccounts.Count == 0)
                    SubscribeToAccounts();
            }
            else if (State == State.Realtime)
            {
                if (subscribedAccounts.Count == 0)
                    SubscribeToAccounts();
            }
            else if (State == State.Terminated)
            {
                UnsubscribeFromAccounts();
            }
        }

        private void SubscribeToAccounts()
        {
            try
            {
                if (LogToOutput)
                    Print("[SignalCopier] Attempting to subscribe... State=" + State);

                lock (Account.All)
                {
                    foreach (Account acct in Account.All)
                    {
                        if (!string.IsNullOrWhiteSpace(AccountName) &&
                            !acct.Name.Equals(AccountName, StringComparison.OrdinalIgnoreCase))
                            continue;

                        acct.ExecutionUpdate += OnAccountExecutionUpdate;
                        subscribedAccounts.Add(acct);

                        if (LogToOutput)
                            Print("[SignalCopier] Subscribed to account: " + acct.Name);
                    }
                }

                if (LogToOutput && subscribedAccounts.Count == 0)
                    Print("[SignalCopier] WARNING: no matching account found. Check AccountName.");
            }
            catch (Exception ex)
            {
                Print("[SignalCopier] ERROR subscribing: " + ex.Message);
            }
        }

        private void UnsubscribeFromAccounts()
        {
            foreach (Account acct in subscribedAccounts)
            {
                try { acct.ExecutionUpdate -= OnAccountExecutionUpdate; }
                catch { }
            }
            subscribedAccounts.Clear();

            if (LogToOutput)
                Print("[SignalCopier] Unsubscribed from all accounts.");
        }

        protected override void OnBarUpdate() { }

        // ─────────────────────────────────────────────────────────────────────
        // EXECUTION EVENT HANDLER
        // ─────────────────────────────────────────────────────────────────────

        private void OnAccountExecutionUpdate(object sender, ExecutionEventArgs e)
        {
            try
            {
                Execution exec = e.Execution;
                if (exec == null || exec.Instrument == null) return;

                string execId = exec.ExecutionId ?? "";
                if (!string.IsNullOrEmpty(execId))
                {
                    lock (seenLock)
                    {
                        if (seenExecutionIds.Contains(execId)) return;
                        seenExecutionIds.Add(execId);
                        if (seenExecutionIds.Count > MAX_SEEN_IDS)
                        {
                            var oldest = seenExecutionIds.Take(MAX_SEEN_IDS / 2).ToList();
                            foreach (var id in oldest) seenExecutionIds.Remove(id);
                        }
                    }
                }

                string accountName = exec.Account != null ? exec.Account.Name : "Unknown";
                string symbol      = exec.Instrument.MasterInstrument.Name;
                string posKey      = accountName + "|" + symbol;
                double fillPrice   = exec.Price;
                int    fillQty     = exec.Quantity;
                bool   isBuy       = (exec.MarketPosition == MarketPosition.Long);

                InstrumentType instType = exec.Instrument.MasterInstrument.InstrumentType;
                string assetClass = "Stock";
                if (instType == InstrumentType.Future)
                    assetClass = "Futures";
                else if (instType == InstrumentType.Forex)
                    assetClass = "Forex";

                int prevPosition = positionMap.ContainsKey(posKey) ? positionMap[posKey] : 0;
                int posChange    = isBuy ? fillQty : -fillQty;
                int newPosition  = prevPosition + posChange;
                positionMap[posKey] = newPosition;

                string orderId = exec.OrderId ?? execId;

                bool isReversal = (prevPosition != 0) && (newPosition != 0) &&
                                  ((prevPosition > 0) != (newPosition > 0));

                if (isReversal)
                {
                    int  closeQty = Math.Abs(prevPosition);
                    int  openQty  = Math.Abs(newPosition);
                    bool wasLong  = prevPosition > 0;
                    bool nowLong  = newPosition > 0;

                    string exitDir = wasLong ? "Long" : "Short";
                    SendFill(symbol, "exit", exitDir, fillPrice, closeQty, assetClass, orderId, exec.Time);

                    string entryDir = nowLong ? "Long" : "Short";
                    SendFill(symbol, "entry", entryDir, fillPrice, openQty, assetClass, orderId, exec.Time);
                    return;
                }

                bool isEntry = (prevPosition == 0) ||
                               (prevPosition > 0 && isBuy) ||
                               (prevPosition < 0 && !isBuy);

                if (isEntry)
                {
                    string dir = isBuy ? "Long" : "Short";
                    SendFill(symbol, "entry", dir, fillPrice, fillQty, assetClass, orderId, exec.Time);
                }
                else
                {
                    bool closingLong = prevPosition > 0;
                    string dir = closingLong ? "Long" : "Short";
                    SendFill(symbol, "exit", dir, fillPrice, fillQty, assetClass, orderId, exec.Time);
                }
            }
            catch (Exception ex)
            {
                if (LogToOutput)
                    Print("[SignalCopier] Error in execution handler: " + ex.Message);
            }
        }

        // ─────────────────────────────────────────────────────────────────────
        // PAYLOAD + SEND
        // ─────────────────────────────────────────────────────────────────────

        // Field names/shapes match app/sources/ninjatrader.py's parse()
        // exactly -- see that file's docstring.
        private string BuildPayload(string symbol, string action, string direction,
            double price, int qty, string assetClass, string orderId, DateTime time)
        {
            return string.Format(CultureInfo.InvariantCulture,
                "{{" +
                "\"ticker\":\"{0}\"," +
                "\"action\":\"{1}\"," +
                "\"direction\":\"{2}\"," +
                "\"price\":{3}," +
                "\"qty\":{4}," +
                "\"asset_class\":\"{5}\"," +
                "\"order_id\":\"{6}\"," +
                "\"time\":\"{7}\"" +
                "}}",
                EscapeJson(symbol),
                action,
                direction,
                price.ToString("F4", CultureInfo.InvariantCulture),
                qty,
                assetClass,
                EscapeJson(orderId),
                time.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffZ", CultureInfo.InvariantCulture)
            );
        }

        private static string EscapeJson(string s)
        {
            if (string.IsNullOrEmpty(s)) return "";
            return s.Replace("\\", "\\\\").Replace("\"", "\\\"");
        }

        private async void SendFill(string symbol, string action, string direction,
            double price, int qty, string assetClass, string orderId, DateTime time)
        {
            string json = BuildPayload(symbol, action, direction, price, qty, assetClass, orderId, time);
            try
            {
                var request = new HttpRequestMessage(HttpMethod.Post, WebhookUrl);
                request.Content = new StringContent(json, Encoding.UTF8, "application/json");
                request.Headers.Add("X-NinjaTrader-Secret", Secret ?? "");

                var response = await httpClient.SendAsync(request);

                if (LogToOutput)
                {
                    if (response.IsSuccessStatusCode)
                        Print(string.Format(CultureInfo.InvariantCulture,
                            "[SignalCopier] {0} {1} {2} {3}x @ {4:F2} -- sent OK",
                            action.ToUpper(), direction, symbol, qty, price));
                    else
                    {
                        string body = await response.Content.ReadAsStringAsync();
                        Print(string.Format(CultureInfo.InvariantCulture,
                            "[SignalCopier] HTTP {0}: {1}", (int)response.StatusCode, body));
                    }
                }
            }
            catch (Exception ex)
            {
                if (LogToOutput)
                    Print("[SignalCopier] Send failed: " + ex.Message);
            }
        }

        // ─────────────────────────────────────────────────────────────────────
        // PROPERTIES
        // ─────────────────────────────────────────────────────────────────────

        #region Properties

        [NinjaScriptProperty]
        [Display(Name = "Webhook URL",
            Description = "https://<your-public-host>/ninjatrader/webhook",
            Order = 1, GroupName = "Signal Copier Settings")]
        public string WebhookUrl { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Secret",
            Description = "Must match this service's NINJATRADER_WEBHOOK_SECRET environment variable.",
            Order = 2, GroupName = "Signal Copier Settings")]
        [Password]
        public string Secret { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Account Name",
            Description = "Account to monitor. Leave blank to monitor ALL accounts. Start with Sim101.",
            Order = 3, GroupName = "Signal Copier Settings")]
        public string AccountName { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Log to Output",
            Description = "Print confirmations in the NinjaTrader Output window.",
            Order = 4, GroupName = "Signal Copier Settings")]
        public bool LogToOutput { get; set; }

        #endregion
    }
}
