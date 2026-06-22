"""
SMC / ICT 2-Agent Bot — OPCJA A (kod, dziala 24/7 na GitHub Actions).
Agent 1 (Skaut) skanuje 4 strategie ICT; Agent 2 (Walidator) zatwierdza.
Skanuje ostatnie ZAMKNIETE swiece (odpornosc na opoznienia harmonogramu).
"""

import os
import json
import datetime as dt

import requests
import pandas as pd
import yfinance as yf

try:
    from zoneinfo import ZoneInfo
    TZ_PL = ZoneInfo("Europe/Warsaw")
    TZ_NY = ZoneInfo("America/New_York")
except Exception:
    TZ_PL = dt.timezone.utc
    TZ_NY = dt.timezone.utc

TOKEN   = os.getenv("TG_TOKEN", "")
CHAT_ID = os.getenv("TG_CHAT_ID", "")
DRY_RUN = os.getenv("DRY_RUN", "") == "1"

SYMBOLS = [
    ("XAUUSD (zloto)",  "GC=F",     ["USD"]),
    ("XAGUSD (srebro)", "SI=F",     ["USD"]),
    ("EURUSD",          "EURUSD=X", ["USD", "EUR"]),
    ("US100 (Nasdaq)",  "^NDX",     ["USD"]),
]

INTERVAL  = os.getenv("TF", "5m")
SWING_LEN = 5
EMA_LEN   = 200
EMA_FAST  = 50
ATR_LEN   = 14
SL_BUF    = 0.5
RR_TP1    = 2.0
RR_TP2    = 3.0
FVG_LOOKBACK = 8
APPROVE_SCORE = 3
SCAN_BARS    = 6
COOLDOWN_MIN = 20

HTF_INTERVAL = "60m"

NEWS_URL        = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NEWS_WARN_MIN   = 40
NEWS_ATTACH_MIN = 120
NEWS_BLOCK_MIN  = 30
STATE_FILE      = "state.json"


def send_telegram(text):
    if DRY_RUN:
        print("----- (DRY_RUN) -----\n" + text + "\n")
        return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    for chat in [c.strip() for c in CHAT_ID.split(",") if c.strip()]:
        try:
            requests.post(url, json={"chat_id": chat, "text": text,
                                     "disable_web_page_preview": True}, timeout=15)
        except Exception as e:
            print(f"Telegram blad ({chat}):", e)


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def now_pl():
    return dt.datetime.now(TZ_PL).strftime("%d.%m.%Y %H:%M")


def stopka():
    return f"\n\U0001F552 {now_pl()} (czas PL)"


def fetch_calendar():
    try:
        r = requests.get(NEWS_URL, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
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
            events.append({"title": ev.get("title", ""), "country": ev.get("country", ""),
                           "impact": ev.get("impact", ""), "when": when,
                           "key": f"{ev.get('country')}|{ev.get('title')}|{ev['date']}"})
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


def fmt_event(ev):
    local = ev["when"].astimezone(TZ_PL).strftime("%d.%m %H:%M")
    flag = "\U0001F534" if ev["impact"] == "High" else "\U0001F7E0"
    return f"{flag} {local} {ev['country']} - {ev['title']}"


def daily_summary(events):
    now = dt.datetime.now(TZ_PL)
    todays = [e for e in events if e["when"].astimezone(TZ_PL).date() == now.date()
              and e["impact"] in ("High", "Medium")]
    todays.sort(key=lambda e: e["when"])
    if not todays:
        return "\U0001F4C5 Dzis brak waznych wydarzen makro. Spokojny dzien." + stopka()
    lines = "\n".join(fmt_event(e) for e in todays)
    return ("\U0001F4C5 PLAN DNIA - wazne wydarzenia:\n----------------------\n" +
            lines + "\n\nHigh=duzy wplyw  Medium=sredni" + stopka())


def atr(df, n):
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def find_swings(highs, lows, L):
    sh, sl = [], []
    n = len(highs)
    for i in range(L, n - L):
        if highs[i] == highs[i - L:i + L + 1].max():
            sh.append((i, highs[i]))
        if lows[i] == lows[i - L:i + L + 1].min():
            sl.append((i, lows[i]))
    return sh, sl


def has_fvg(highs, lows, i, direction, lookback=FVG_LOOKBACK):
    for j in range(max(2, i - lookback), i + 1):
        if direction == "LONG" and lows[j] > highs[j - 2]:
            return True
        if direction == "SHORT" and highs[j] < lows[j - 2]:
            return True
    return False


def new_fvg(highs, lows, i, direction):
    if i < 2:
        return False
    if direction == "LONG":
        return lows[i] > highs[i - 2]
    return highs[i] < lows[i - 2]


def last_ob(opens, closes, highs, lows, i, direction):
    for k in range(1, 15):
        idx = i - k
        if idx < 1:
            break
        if direction == "LONG" and closes[idx] < opens[idx]:
            return (lows[idx], highs[idx])
        if direction == "SHORT" and closes[idx] > opens[idx]:
            return (lows[idx], highs[idx])
    return None


def in_killzone(now_ny):
    t = now_ny.hour * 60 + now_ny.minute
    zones = [(3 * 60, 4 * 60), (10 * 60, 11 * 60), (14 * 60, 15 * 60)]
    return any(a <= t <= b for a, b in zones)


def build(side, entry, sl, strategy, reasons):
    if side == "LONG":
        risk = entry - sl
        if risk <= 0:
            return None
        tp1, tp2 = entry + risk * RR_TP1, entry + risk * RR_TP2
    else:
        risk = sl - entry
        if risk <= 0:
            return None
        tp1, tp2 = entry - risk * RR_TP1, entry - risk * RR_TP2
    return {"side": side, "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2,
            "strategy": strategy, "reasons": list(reasons)}


def agent1_scout(df, now_ny):
    df = df.dropna().copy()
    if len(df) < EMA_LEN + SWING_LEN + 10:
        return []
    df["EMA"] = df["Close"].ewm(span=EMA_LEN, adjust=False).mean()
    df["ATR"] = atr(df, ATR_LEN)

    opens = df["Open"].values
    highs, lows, closes = df["High"].values, df["Low"].values, df["Close"].values
    ema, atrv = df["EMA"].values, df["ATR"].values
    n = len(df)
    i = n - 1
    price = closes[i]
    a = atrv[i]
    trend = 1 if price > ema[i] else -1

    sh, sl_pts = find_swings(highs, lows, SWING_LEN)
    last_sh = sh[-1][1] if sh else None
    last_sl = sl_pts[-1][1] if sl_pts else None
    prev_sh = sh[-2][1] if len(sh) >= 2 else last_sh
    prev_sl = sl_pts[-2][1] if len(sl_pts) >= 2 else last_sl

    cands = []
    bull = closes[i] > opens[i]
    bear = closes[i] < opens[i]

    if in_killzone(now_ny):
        if trend == 1 and new_fvg(highs, lows, i, "LONG"):
            base = last_sl if last_sl else lows[i]
            c = build("LONG", price, base - a * SL_BUF, "Silver Bullet",
                      ["killzone", "swiezy FVG byczy", "trend up"])
            if c:
                cands.append(c)
        if trend == -1 and new_fvg(highs, lows, i, "SHORT"):
            base = last_sh if last_sh else highs[i]
            c = build("SHORT", price, base + a * SL_BUF, "Silver Bullet",
                      ["killzone", "swiezy FVG nizdwiedzi", "trend down"])
            if c:
                cands.append(c)

    if prev_sh is not None and highs[i] > prev_sh and price < prev_sh and bear:
        c = build("SHORT", price, highs[i] + a * SL_BUF, "Liquidity Sweep",
                  ["zebrano plynnosc (gora)", "odrzucenie"])
        if c:
            cands.append(c)
    if prev_sl is not None and lows[i] < prev_sl and price > prev_sl and bull:
        c = build("LONG", price, lows[i] - a * SL_BUF, "Liquidity Sweep",
                  ["zebrano plynnosc (dol)", "odrzucenie"])
        if c:
            cands.append(c)

    if trend == 1:
        ob = last_ob(opens, closes, highs, lows, i, "LONG")
        if (ob and lows[i] <= ob[1] and lows[i - 1] > ob[1] and bull
                and has_fvg(highs, lows, i, "LONG")):
            c = build("LONG", price, ob[0] - a * SL_BUF, "Order Block + FVG",
                      ["pierwszy dotyk OB", "FVG byczy", "trend up"])
            if c:
                cands.append(c)
    if trend == -1:
        ob = last_ob(opens, closes, highs, lows, i, "SHORT")
        if (ob and highs[i] >= ob[0] and highs[i - 1] < ob[0] and bear
                and has_fvg(highs, lows, i, "SHORT")):
            c = build("SHORT", price, ob[1] + a * SL_BUF, "Order Block + FVG",
                      ["pierwszy dotyk OB", "FVG nizdwiedzi", "trend down"])
            if c:
                cands.append(c)

    if last_sh is not None and last_sl is not None:
        prev_price = closes[i - 1]
        if trend == 1 and last_sl < last_sh:
            leg = last_sh - last_sl
            lo, hi = last_sh - leg * 0.79, last_sh - leg * 0.62
            if leg > 0 and lo <= price <= hi and prev_price > hi and bull:
                c = build("LONG", price, last_sl - a * SL_BUF, "OTE",
                          ["strefa 62-79%", "reakcja bycza", "trend up"])
                if c:
                    cands.append(c)
        if trend == -1 and last_sh > last_sl:
            leg = last_sh - last_sl
            lo, hi = last_sl + leg * 0.62, last_sl + leg * 0.79
            if leg > 0 and lo <= price <= hi and prev_price < lo and bear:
                c = build("SHORT", price, last_sh + a * SL_BUF, "OTE",
                          ["strefa 62-79%", "reakcja nizdwiedzia", "trend down"])
                if c:
                    cands.append(c)

    kz = in_killzone(now_ny)
    side_count = {}
    for c in cands:
        side_count[c["side"]] = side_count.get(c["side"], 0) + 1
    for c in cands:
        c["kz"] = kz
        c["fvg_fresh"] = new_fvg(highs, lows, i, c["side"])
        c["n_side"] = side_count[c["side"]]

    return cands, df.index[-1].isoformat()


def htf_trend(dfh):
    try:
        d = dfh.dropna()
        if len(d) < 52:
            return 0
        e = d["Close"].ewm(span=EMA_FAST, adjust=False).mean().iloc[-1]
        return 1 if d["Close"].iloc[-1] > e else -1
    except Exception:
        return 0


def agent2_validate(cand, hdir, events, ccys, now):
    score, why = 0, []
    blockers = [e for e in high_impact_for(events, ccys, 0, NEWS_BLOCK_MIN, now)
                if e["impact"] == "High"]
    if blockers:
        return (False, 0, ["blokada: dane makro za chwile"], blockers[0])
    if hdir != 0 and ((cand["side"] == "LONG" and hdir == 1) or
                      (cand["side"] == "SHORT" and hdir == -1)):
        score += 1
        why.append("zgodne z trendem 1h")
    if cand.get("kz"):
        score += 1
        why.append("killzone")
    if cand.get("fvg_fresh"):
        score += 1
        why.append("swiezy FVG")
    if cand.get("n_side", 1) >= 2:
        score += 1
        why.append("zbieznosc strategii")
    return (score >= APPROVE_SCORE, score, why, None)


def fmt_signal(name, cand, score, why, news):
    emoji = "\U0001F7E2 LONG" if cand["side"] == "LONG" else "\U0001F534 SHORT"
    e = cand["entry"]
    d = 5 if abs(e) < 10 else 2
    msg = (f"{emoji}  {name}\n"
           f"\U0001F9E0 Strategia: {cand['strategy']}\n"
           f"----------------------\n"
           f"Wejscie: {cand['entry']:.{d}f}\n"
           f"SL: {cand['sl']:.{d}f}\n"
           f"TP1: {cand['tp1']:.{d}f}\n"
           f"TP2: {cand['tp2']:.{d}f}\n"
           f"\U00002705 Agent2 ({score} pkt): " + ", ".join(why) + "\n"
           f"\U0001F50E Agent1: " + ", ".join(cand["reasons"]))
    if news:
        msg += "\n\n\U000026A0 Wkrotce wazne wydarzenia:\n" + \
               "\n".join(fmt_event(x) for x in news)
    return msg + stopka()


def main():
    if not DRY_RUN and (not TOKEN or not CHAT_ID):
        print("Brak TG_TOKEN / TG_CHAT_ID.")
        return

    state = load_state()
    state.setdefault("sent_events", [])
    sent_bars = state.setdefault("sent_bars", {})
    last_send = state.setdefault("last_send", {})
    changed = False
    events = fetch_calendar()
    now = dt.datetime.now(dt.timezone.utc)
    now_ny = dt.datetime.now(TZ_NY)

    today = dt.datetime.now(TZ_PL).date().isoformat()
    if state.get("daily_summary") != today and dt.datetime.now(TZ_PL).hour >= 7:
        send_telegram(daily_summary(events))
        state["daily_summary"] = today
        changed = True

    all_ccy = sorted({c for _, _, cc in SYMBOLS for c in cc})
    for ev in high_impact_for(events, all_ccy, 0, NEWS_WARN_MIN, now):
        if ev["key"] in state["sent_events"]:
            continue
        mins = int((ev["when"] - now).total_seconds() / 60)
        send_telegram(f"\U000026A0 ZA ~{mins} MIN - wazne wydarzenie!\n{fmt_event(ev)}\n"
                      f"Mozliwa duza zmiennosc - ostroznie, zwlaszcza na zlocie." + stopka())
        state["sent_events"].append(ev["key"])
        changed = True

    for name, sym, ccys in SYMBOLS:
        try:
            df = yf.download(sym, period="10d", interval=INTERVAL,
                             progress=False, auto_adjust=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if df.empty or len(df) < EMA_LEN + SWING_LEN + 12:
                print(f"{name}: brak danych"); continue

            li = last_send.get(sym)
            if li:
                try:
                    if (now - dt.datetime.fromisoformat(li)).total_seconds() < COOLDOWN_MIN * 60:
                        print(f"{name}: cooldown"); continue
                except Exception:
                    pass

            dff = df.iloc[:-1]
            dfh = yf.download(sym, period="60d", interval=HTF_INTERVAL,
                              progress=False, auto_adjust=False)
            if isinstance(dfh.columns, pd.MultiIndex):
                dfh.columns = dfh.columns.get_level_values(0)
            hdir = htf_trend(dfh)
            seen = sent_bars.get(sym, [])

            chosen = None
            for k in range(1, SCAN_BARS + 1):
                end = len(dff) - k
                if end < EMA_LEN + SWING_LEN + 10:
                    break
                sub = dff.iloc[:end + 1]
                bar_time = sub.index[-1].isoformat()
                if bar_time in seen:
                    continue
                ts = sub.index[-1].to_pydatetime()
                try:
                    nyt = ts.astimezone(TZ_NY)
                except Exception:
                    nyt = now_ny
                res = agent1_scout(sub, nyt)
                if not res or not res[0]:
                    continue
                best = None
                for c in res[0]:
                    ok, score, why, blocker = agent2_validate(c, hdir, events, ccys, now)
                    if blocker is not None:
                        best = None; break
                    if ok and (best is None or score > best[1]):
                        best = (c, score, why)
                if best:
                    chosen = (best, bar_time)
                    break

            if not chosen:
                print(f"{name}: brak nowego sygnalu"); continue

            (c, score, why), bar_time = chosen
            news = high_impact_for(events, ccys, 0, NEWS_ATTACH_MIN, now)
            send_telegram(fmt_signal(name, c, score, why, news))
            seen.append(bar_time)
            sent_bars[sym] = seen[-120:]
            last_send[sym] = now.isoformat()
            changed = True
            print(f"{name}: WYSLANO {c['side']} ({c['strategy']})")

        except Exception as e:
            print(f"{name}: blad -> {e}")

    state["sent_events"] = state["sent_events"][-100:]
    if changed:
        save_state(state)


if __name__ == "__main__":
    main()
