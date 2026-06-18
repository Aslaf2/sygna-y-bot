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
            "----------------------\n" + li
