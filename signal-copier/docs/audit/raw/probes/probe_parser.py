from app.sources.text_parser import classify_text_signal
from app.parser_tooling import classify_message_type
from app.models import AssetClass

msgs = [
    "BUY AAPL 150C 1/17",
    "BUY AAPL 150C 1/17 @ 2.50",
    "BTO AAPL 150C 1/17 @ 2.50",
    "AAPL 150C 1/17 @ 2.50",
    "SELL half AAPL",
    "Sell half of AAPL here",
    "SELL AAPL",
    "sell AAPL 10",
    "Close half ETHUSDT",
    "Trim 50% TSLA",
    "Move stop to 140 on AAPL",
    "AAPL SL to breakeven",
    "Add to AAPL here 5 more",
    "Cancel the AAPL order",
    "Cancel BUY AAPL 10",
    "Still holding AAPL long from 150",
    "BUY AAPL 10 hold for swing",
    "BUY AAPL 10 - wait for pullback",
    "BUY BTC 0.1",
    "BUY BTCUSDT",
    "BUY EURUSD 0.50 lots SL 1.0950 TP 1.1050",
    "BUY AAPL $500",
    "BUY AAPL 2% risk SL 140",
    "BUY AAPL 2%",
    "BUY AAPL limit 150",
    "BUY AAPL @ 150 limit",
    "LONG AAPL above 150",
    "Short TSLA 10 @ 250 SL 260",
    "BUY ES 1 @ 5000",
    "BUY NQ",
    "BUY XAUUSD 0.1",
    "BUY SPY 10 TP 450",
    "Buy GOLD",
    "exit AAPL",
    "AAPL hit TP1, closing half",
    "TP1 hit on AAPL",
    "BUY AAPL 10 don't chase above 152",
    "BUY AAPL 10 if it breaks 150",
    "BUY AAPL 10 @ 150, exit by Friday",
    "I'm long AAPL",
    "Buy AAPL 10 @ 150 (stop 145)",
    "BUY AAPL 10 SL: 145 TP: 160",
    "SELL AAPL 10 (closing long)",
    "Buy AAPL 10 - trim half at 155",
    "Buy AAPL 10 @150 and sell half at 155",
]
for m in msgs:
    d = classify_text_signal(m, source="probe", asset_class=AssetClass.CRYPTO)
    s = d.signal
    mt = classify_message_type(m)
    if s:
        print(f"{m!r:55} -> {d.outcome.value:12} side={s.side.value:5} sym={s.symbol:10} ac={s.asset_class.value:6} qty={s.quantity} px={s.price} sl={s.stop_loss} tp={s.take_profit} tgts={[t.price for t in s.targets]} | msgtype={mt.value}")
    else:
        print(f"{m!r:55} -> {d.outcome.value:12} detail={d.detail!r} | msgtype={mt.value}")
