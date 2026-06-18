"""
SMC Signal Bot - darmowy, dziala 24/7 na GitHub Actions
"""

import os
import json
import datetime as dt

import requests
import pandas as pd
import yfinance as yf

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Warsaw")
except Exception:
    TZ = dt.timezone.utc

# ===================== KONFIGURACJA =====================
TOKEN   = os.getenv("TG_TOKEN", "")
CHAT_ID = os.getenv("TG_CHAT_ID", "")

SYMBOLS = [
    ("XAUUSD (zloto)",  "GC=F",     ["USD"]),
    ("XAGUSD (srebro)", "SI=F",     ["USD"]),
    ("EURUSD",          "EURUSD=X", ["USD", "EUR"]),
    ("US100 (Nasdaq)",  "^NDX",     ["USD"]),
]

INTERVAL  = os.getenv("TF", "15m")
SWING_LEN = 5
EMA_LEN   = 200
ATR_LEN   = 14
SL_BUF    = 0.5
RR_TP1    = 1.5
RR_TP2    = 3.0

REQUIRE_FVG  = True
FVG_LOOKBACK = 12
SHOW_OB      = True

REQUIRE_HTF  = True
HTF_INTERVAL = "60m"
HTF_EMA_LEN  = 50

NEWS_URL        = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NEWS_WARN_MIN   = 40
NEWS_ATTACH_MIN = 120
NEWS_BLOCK_MIN  = 30
STATE_FILE      = "state.json"
# =======================================================


def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    for chat in [c.strip() for c in CHAT_ID.split(",") if c.strip()]:
        try:
            requests.post(url, json={"chat_id": chat, "text": text,
                                     "disable_web_page_preview": True}, timeout=15)
        except Exception as e:
            print(f"Telegram blad ({chat}):", e)


def load_state() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def fetch_calendar():
    try:
        r = requests.get(NEWS_URL, timeout=15,
                         headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print("Kalendarz niedostepny:", e)
        return []

    events = []
    for ev in data:
        try:
            when = dt.datetime.fromisoformat(ev["date"])
            if when.tzinfo is None:
                when = when.replace(tzinfo=dt.timezone.utc)
            events.append({
                "title":   ev.get("title", ""),
                "country": ev.get("country", ""),
                "impact":  ev.get("impact", ""),
                "when":    when,
                "key":     f"{ev.get('country')}|{ev.get('title')}|{ev['date']}",
            })
        except Exception:
            continue
    return events


def high_impact_for(events, currencies, start_min, end_min, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    out = []
    for ev in events:
        if ev["impact"] not in ("High", "Medium"):
            continue
        if ev["country"] not in currencies:
            continue
        mins = (ev["when"] - now).total_seconds() / 60.0
        if start_min <= mins <= end_min:
            out.append(ev)
    return sorted(out, key=lambda e: e["when"])


def now_pl():
    return dt.datetime.now(TZ).strftime("%d.%m.%Y %H:%M")


def stopka():
    return f"\n\U0001F552 {now_pl()} (czas PL)"


def fmt_event(ev):
    local = ev["when"].astimezone(TZ).strftime("%d.%m %H:%M")
    flag = "\U0001F534" if ev["impact"] == "High" else "\U0001F7E0"
    return f"{flag} {local} {ev['country']} - {ev['title']}"


def daily_summary(events):
    now = dt.datetime.now(TZ)
    todays = [e for e in events
              if e["when"].astimezone(TZ).date() == now.date()
              and e["impact"] in ("High", "Medium")]
    todays.sort(key=lambda e: e["when"])
    if not todays:
        return "\U0001F4C5 Dzis brak waznych wydarzen makro. Spokojny dzien."
    lines = "\n".join(fmt_event(e) for e in todays)
    return ("\U0001F4C5 PLAN DNIA - wazne wydarzenia (uwaga na zmiennosc):\n"
            "----------------------\n" + lines +
            "\n\nHigh=duzy wplyw  Medium=sredni" + stopka())


def atr(df, n):
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def has_fvg(highs, lows, i, direction):
    start = max(2, i - FVG_LOOKBACK)
    for j in range(start, i + 1):
        if direction == "LONG" and lows[j] > highs[j - 2]:
            return True
        if direction == "SHORT" and highs[j] < lows[j - 2]:
            return True
    return False


def find_ob(opens, closes, highs, lows, i, direction):
    for k in range(1, 11):
        idx = i - k
        if idx < 0:
            break
        if direction == "LONG" and closes[idx] < opens[idx]:
            return (lows[idx], highs[idx])
        if direction == "SHORT" and closes[idx] > opens[idx]:
            return (lows[idx], highs[idx])
    return None


def htf_trend(df_htf):
    try:
        d = df_htf.dropna().copy()
        if len(d) < HTF_EMA_LEN + 2:
            return 0
        e = d["Close"].ewm(span=HTF_EMA_LEN, adjust=False).mean().iloc[-1]
        c = d["Close"].iloc[-1]
        return 1 if c > e else -1
    except Exception:
        return 0


def compute_signal(df, htf_dir=0):
    df = df.dropna().copy()
    if len(df) < EMA_LEN + SWING_LEN + 5:
        return None

    df["EMA"] = df["Close"].ewm(span=EMA_LEN, adjust=False).mean()
    df["ATR"] = atr(df, ATR_LEN)

    opens = df["Open"].values
    highs, lows, close = df["High"].values, df["Low"].values, df["Close"].values
    ema, atrv = df["EMA"].values, df["ATR"].values
    L, n = SWING_LEN, len(df)

    last_ph = last_pl = None
    trend = 0
    signal = None

    def build(side, i):
        if REQUIRE_HTF and htf_dir != 0:
            if side == "LONG" and htf_dir != 1:
                return None
            if side == "SHORT" and htf_dir != -1:
                return None
        fvg_ok = has_fvg(highs, lows, i, side)
        if REQUIRE_FVG and not fvg_ok:
            return None
        if side == "LONG":
            entry = close[i]
            base  = min(last_pl, lows[i]) if last_pl is not None else lows[i]
            sl    = base - atrv[i] * SL_BUF
            risk  = entry - sl
            if risk <= 0:
                return None
            tp1, tp2 = entry + risk * RR_TP1, entry + risk * RR_TP2
        else:
            entry = close[i]
            base  = max(last_ph, highs[i]) if last_ph is not None else highs[i]
            sl    = base + atrv[i] * SL_BUF
            risk  = sl - entry
            if risk <= 0:
                return None
            tp1, tp2 = entry - risk * RR_TP1, entry - risk * RR_TP2
        conf = ["Struktura: CHoCH", "Trend: EMA200"]
        if REQUIRE_HTF and htf_dir != 0:
            conf.append("HTF 1h: zgodny")
        if fvg_ok:
            conf.append("FVG: tak")
        ob = find_ob(opens, close, highs, lows, i, side) if SHOW_OB else None
        if ob:
            conf.append("Order Block: tak")
        return (side, entry, sl, tp1, tp2, i, conf)

    for i in range(L, n):
        p = i - L
        if p - L >= 0 and p + L < n:
            if highs[p] == highs[p - L:p + L + 1].max():
                last_ph = highs[p]
            if lows[p] == lows[p - L:p + L + 1].min():
                last_pl = lows[p]

        if last_ph is not None and close[i] > last_ph and close[i - 1] <= last_ph:
            if trend <= 0 and close[i] > ema[i]:
                s = build("LONG", i)
                if s:
                    signal = s
            trend = 1

        if last_pl is not None and close[i] < last_pl and close[i - 1] >= last_pl:
            if trend >= 0 and close[i] < ema[i]:
                s = build("SHORT", i)
                if s:
                    signal = s
            trend = -1

    if signal and signal[5] == n - 1:
        return (*signal[:6], signal[6], df.index[-1].isoformat())
    return None


def fmt_signal(side, name, tf, entry, sl, tp1, tp2, conf, news):
    emoji = "\U0001F7E2 LONG" if side == "LONG" else "\U0001F534 SHORT"
    d = 5 if abs(entry) < 10 else 2
    msg = (f"{emoji}  {name}  ({tf})\n"
           f"----------------------\n"
           f"Wejscie: {entry:.{d}f}\n"
           f"SL: {sl:.{d}f}\n"
           f"TP1: {tp1:.{d}f}\n"
           f"TP2: {tp2:.{d}f}\n"
           f"RR: 1:{RR_TP1} / 1:{RR_TP2}\n"
           f"Potwierdzenia: " + ", ".join(conf))
    if news:
        msg += "\n\n\u26A0\uFE0F UWAGA, wkrotce wazne wydarzenia:\n" + \
               "\n".join(fmt_event(e) for e in news) + \
               "\n(rozwaz mniejsze ryzyko lub czekaj po danych)"
    return msg + stopka()


def main():
    if not TOKEN or not CHAT_ID:
        print("Brak TG_TOKEN / TG_CHAT_ID (ustaw jako GitHub Secrets).")
        return

    state = load_state()
    state.setdefault("sent_events", [])
    changed = False
    events = fetch_calendar()
    now = dt.datetime.now(dt.timezone.utc)

    today = dt.datetime.now(TZ).date().isoformat()
    if state.get("daily_summary") != today and dt.datetime.now(TZ).hour >= 7:
        send_telegram(daily_summary(events))
        state["daily_summary"] = today
        changed = True

    all_ccy = sorted({c for _, _, ccys in SYMBOLS for c in ccys})
    for ev in high_impact_for(events, all_ccy, 0, NEWS_WARN_MIN, now):
        if ev["key"] in state["sent_events"]:
            continue
        mins = int((ev["when"] - now).total_seconds() / 60)
        send_telegram(f"\u26A0\uFE0F ZA ~{mins} MIN - wazne wydarzenie!\n"
                      f"{fmt_event(ev)}\n"
                      f"Mozliwa duza zmiennosc - ostroznie z wejsciami, szczegolnie na zlocie." + stopka())
        state["sent_events"].append(ev["key"])
        changed = True

    for name, sym, ccys in SYMBOLS:
        try:
            df = yf.download(sym, period="20d", interval=INTERVAL,
                             progress=False, auto_adjust=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if df.empty:
                print(f"{name}: brak danych")
                continue

            hdir = 0
            if REQUIRE_HTF:
                dfh = yf.download(sym, period="60d", interval=HTF_INTERVAL,
                                  progress=False, auto_adjust=False)
                if isinstance(dfh.columns, pd.MultiIndex):
                    dfh.columns = dfh.columns.get_level_values(0)
                hdir = htf_trend(dfh)

            sig = compute_signal(df, hdir)
            if sig is None:
                print(f"{name}: brak sygnalu")
                continue

            side, entry, sl, tp1, tp2, _idx, conf, bar_time = sig
            if state.get(sym) == bar_time:
                print(f"{name}: juz wyslany")
                continue

            blockers = [e for e in high_impact_for(events, ccys, 0, NEWS_BLOCK_MIN, now)
                        if e["impact"] == "High"]
            if blockers:
                mins = int((blockers[0]["when"] - now).total_seconds() / 60)
                send_telegram(f"\u23F8 Sygnal {side} - {name} WSTRZYMANY.\n"
                              f"Wazne dane za ~{mins} min: {fmt_event(blockers[0])}\n"
                              f"Lepiej poczekac na reakcje rynku po publikacji." + stopka())
                state[sym] = bar_time
                changed = True
                print(f"{name}: wstrzymano (news)")
                continue

            news = high_impact_for(events, ccys, 0, NEWS_ATTACH_MIN, now)
            send_telegram(fmt_signal(side, name, INTERVAL, entry, sl, tp1, tp2, conf, news))
            state[sym] = bar_time
            changed = True
            print(f"{name}: WYSLANO {side}")

        except Exception as e:
            print(f"{name}: blad -> {e}")

    state["sent_events"] = state["sent_events"][-100:]
    if changed:
        save_state(state)


if __name__ == "__main__":
    main()
