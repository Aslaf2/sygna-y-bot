"""
Sesje+Strategia (TradingView) -> Telegram. Port skryptu Pine
"Sesje + Strategia BUY/SELL [Aslafek]" (GOLD 4h) na Pythona.

Logika 1:1 z wykresem TradingView (wersja 9 skryptu):
- EMA 9/21 cross + filtr trendu EMA200 + filtr RSI(14) 30/70,
- tylko sesje Londyn/NY (bar 4h otwarty w 07:00-21:00 UTC),
- SL = 1.5 x ATR(14, RMA jak w Pine), TP = SL x 2.5 (RR 2.5),
- sygnal liczony WYLACZNIE na zamknietej swiecy 4h (zero repaintu).

Uruchamiany po smc_bot.py w tej samej petli GitHub Actions. Dedup przez
state.json (klucz "sesje_last_bar"). Wiadomosci sa wyraznie oznaczone,
zeby nie mylily sie z sygnalami SMC.

Roznica zrodla danych: TradingView TVC:GOLD to CFD spot, tu Yahoo GC=F
(futures) przeliczany na spot delta z tradingview_ta - poziomy moga sie
roznic o ulamek procenta, kierunek i moment sygnalu ten sam.
"""

import os
import csv
import datetime as dt

import pandas as pd

from smc_bot import (send_telegram, dl, load_state, save_state, stopka,
                     tv_info, now_pl)

SYM        = "GC=F"          # zloto (futures Yahoo); spot-korekta nizej
NAME       = "GOLD (zloto) 4h"
EMA_FAST   = 9
EMA_SLOW   = 21
EMA_TREND  = 200
RSI_LEN    = 14
RSI_MAX    = 70              # BUY tylko gdy RSI < 70
RSI_MIN    = 30              # SELL tylko gdy RSI > 30
ATR_LEN    = 14
SL_MULT    = 1.5
RR         = 2.5
SES_START  = 7               # Londyn 07-16 + NY 12-21 UTC => bar 4h z [07,21)
SES_END    = 21
LOG_FILE   = "sesje_log.csv"
DRY_RUN    = os.getenv("DRY_RUN", "") == "1"


def rma(s, n):
    """RMA (srednia Wildera) - tak licza ta.rsi i ta.atr w Pine."""
    return s.ewm(alpha=1.0 / n, adjust=False).mean()


def rsi(close, n):
    d = close.diff()
    up = rma(d.clip(lower=0), n)
    dn = rma((-d).clip(lower=0), n)
    return 100 - 100 / (1 + up / dn)


def atr_rma(df, n):
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()],
                   axis=1).max(axis=1)
    return rma(tr, n)


def bars_4h():
    """Swiece 4h: Yahoo 1h zlozony do siatki 0/4/8/12/16/20 UTC.
    Odrzuca ostatnia, jeszcze niedomknieta swiece."""
    df = dl(SYM, "150d", "60m")
    if df.empty:
        return df
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    h4 = df[["Open", "High", "Low", "Close"]].resample(
        "4h", origin="epoch").agg({"Open": "first", "High": "max",
                                   "Low": "min", "Close": "last"}).dropna()
    now = dt.datetime.now(dt.timezone.utc)
    return h4[h4.index + dt.timedelta(hours=4) <= now]   # tylko domkniete


def signal_on(df, i):
    """Sygnal na swiecy i (indeks pozycyjny). Zwraca dict albo None."""
    ef, es = df["EF"], df["ES"]
    cross_up = ef.iloc[i] > es.iloc[i] and ef.iloc[i - 1] <= es.iloc[i - 1]
    cross_dn = ef.iloc[i] < es.iloc[i] and ef.iloc[i - 1] >= es.iloc[i - 1]
    if not (cross_up or cross_dn):
        return None
    hour = df.index[i].hour
    if not (SES_START <= hour < SES_END):
        return None                                     # poza sesja Londyn/NY
    c = float(df["Close"].iloc[i])
    r = float(df["RSI"].iloc[i])
    trend = float(df["ET"].iloc[i])
    a = float(df["ATR"].iloc[i])
    risk = a * SL_MULT
    if cross_up and r < RSI_MAX and c > trend:
        return {"side": "BUY", "entry": c, "sl": c - risk, "tp": c + risk * RR}
    if cross_dn and r > RSI_MIN and c < trend:
        return {"side": "SELL", "entry": c, "sl": c + risk, "tp": c - risk * RR}
    return None


def fmt(sig, bar_time, off):
    e, s, t = sig["entry"] + off, sig["sl"] + off, sig["tp"] + off
    emoji = "\U0001F7E2" if sig["side"] == "BUY" else "\U0001F534"
    msg = (f"{emoji} {sig['side']}  {NAME}\n"
           f"\U0001F4C8 Sesje+Strategia (wykres TradingView) - to NIE sygnal SMC\n"
           f"----------------------\n"
           f"Wejscie: {e:.2f}\n"
           f"TP: {t:.2f}  (RR 2.5)\n"
           f"SL: {s:.2f}  (1.5 x ATR)\n"
           f"Swieca 4h: {bar_time:%d.%m %H:%M} UTC\n"
           f"\U0001F4A1 Dokladnie ten sam sygnal co etykieta na wykresie "
           f"TradingView (moze roznic sie o ulamek % - inne zrodlo cen).")
    return msg + stopka()


def log_row(sig, bar_time):
    new = not os.path.exists(LOG_FILE)
    try:
        with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["data_pl", "swieca_utc", "strona", "entry", "sl", "tp"])
            w.writerow([now_pl(), bar_time.isoformat(), sig["side"],
                        f"{sig['entry']:.2f}", f"{sig['sl']:.2f}", f"{sig['tp']:.2f}"])
    except Exception as e:
        print("sesje log blad:", e)


def main():
    df = bars_4h()
    if df.empty or len(df) < EMA_TREND + 10:
        print("sesje: brak danych 4h (albo za malo historii)")
        return
    df = df.copy()
    df["EF"] = df["Close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ES"] = df["Close"].ewm(span=EMA_SLOW, adjust=False).mean()
    df["ET"] = df["Close"].ewm(span=EMA_TREND, adjust=False).mean()
    df["RSI"] = rsi(df["Close"], RSI_LEN)
    df["ATR"] = atr_rma(df, ATR_LEN)

    # diagnostyka: sygnaly z ostatnich ~30 swiec (do porownania z wykresem TV)
    if os.getenv("SESJE_DIAG") == "1":
        for i in range(max(EMA_TREND, len(df) - 180), len(df)):
            s = signal_on(df, i)
            if s:
                print(f"  DIAG {df.index[i]:%Y-%m-%d %H:%M} {s['side']:4s} "
                      f"entry={s['entry']:.2f} tp={s['tp']:.2f} sl={s['sl']:.2f}")

    i = len(df) - 1
    bar_time = df.index[i]
    state = load_state()
    if state.get("sesje_last_bar") == bar_time.isoformat():
        print(f"sesje: swieca {bar_time:%d.%m %H:%M} juz sprawdzona")
        return
    sig = signal_on(df, i)
    if sig:
        off = 0.0
        info = tv_info(SYM)                    # spot TradingView vs futures
        if info and info.get("close"):
            d = info["close"] - float(df["Close"].iloc[-1])
            if 0.0005 < abs(d) / sig["entry"] < 0.02:
                off = d
        send_telegram(fmt(sig, bar_time, off))
        log_row(sig, bar_time)
        print(f"sesje: WYSLANO {sig['side']} @ {sig['entry']:.2f}")
    else:
        print(f"sesje: swieca {bar_time:%d.%m %H:%M} bez sygnalu")
    state["sesje_last_bar"] = bar_time.isoformat()
    save_state(state)


if __name__ == "__main__":
    main()
