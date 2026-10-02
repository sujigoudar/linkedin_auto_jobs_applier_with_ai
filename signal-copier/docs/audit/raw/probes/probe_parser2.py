from app.sources.text_parser import classify_text_signal
from app.models import AssetClass
msgs = ["BUY 10 AAPL", "BUY TO OPEN AAPL 150C", "Buy to open AAPL", "BUY AAPL 150-152", "BUY AAPL @ 150-152",
        "Sold AAPL", "Stopped out of AAPL", "Closed AAPL +5%", "Sell to close AAPL", "STC AAPL", "SELL AAPL at market",
        "BUY AAPL 10 @ 150 SL 145 TP 160 (swing)", "Long AAPL here", "AAPL long", "BUY AAPL 1,000", "BUY ESZ6 1 @ 5000",
        "BUY MES 2", "SELL EURUSD 0.1 lots", "BUY SPY 100 shares @ 450", "BUY AAPL 10 units", "BUY AAPL now",
        "going long AAPL", "BUY AAPL 10 - stop 145", "BUY AAPL 10 TP1 155 TP2 160 TP3 170 SL 145"]
for m in msgs:
    d = classify_text_signal(m, source="probe", asset_class=AssetClass.CRYPTO)
    s = d.signal
    if s:
        print(f"{m!r:48} -> {d.outcome.value:12} side={s.side.value:5} sym={s.symbol:10} ac={s.asset_class.value:6} qty={s.quantity} px={s.price} sl={s.stop_loss} tp={s.take_profit} tgts={[t.price for t in s.targets]}")
    else:
        print(f"{m!r:48} -> {d.outcome.value:12} {d.detail!r}")
