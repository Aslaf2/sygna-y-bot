"""
SMC / ICT 2-Agent Bot — OPCJA A. Odporny na rate-limit Yahoo (mniej pobran + retry).
"""

import os
import csv
import json
import time
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
    ("BRENT (ropa)",    "BZ=F",     ["USD"]),
    ("HK50 (Hang Seng)", "^HSI",    ["USD"]),
    ("EURUSD",          "EURUSD=X", ["USD", "EUR"]),
    ("US100 (Nasdaq)",  "^NDX",     ["USD"]),
]
# HANG SENG dodany 30.07.2026 (eksperyment9.py - przeglad 15 rynkow juz z
# killzone azjatycka): n=49, +0.61R/sygnal, win 43%, obie polowki dodatnie
# (+0.90/+0.34), lacznie +29.9R/60dni. Sygnaly powstaja w godzinach 3-4 NY,
# czyli na popoludniowej sesji w Hongkongu. Prawdziwa dywersyfikacja:
# korelacja dzienna z Brentem -0.01, ze zlotem 0.27, ze srebrem 0.29.
# TradingView GO OBSLUGUJE (TVC:HSI, cena zgodna z Yahoo), wiec ma filtr TV.
# NAJSLABSZY z dodanych rynkow: najmniejsza probka (n=49) i 68% wyniku pochodzi
# z 3 najlepszych dni (Brent 46%, zloto 75%) - jesli cos ma wypasc, to on.
# WTI (CL=F) ODRZUCONY mimo +0.39R: 72% jego sygnalow to ta sama godzina i
# kierunek co Brent. Odrzucone ujemne: Nikkei, miedz, NZDUSD, BTC, AUDUSD,
# GBPUSD, AUDJPY, USDJPY, EURJPY (najgorszy: -0.72R/sygnal).
# BRENT dodany 29.07.2026 po przegladzie 17 rynkow tym samym silnikiem
# (uniwersum.py, 60 dni, rozliczenie 3R ze spreadem). Brent: n=93, +0.56R/sygnal,
# win 40%, obie polowki probki dodatnie (+0.79/+0.33), 22 zyskowne dni na 42,
# 1.4 sygnalu dziennie. Odrzucone jako ujemne albo niespojne: miedz, platyna,
# gaz, GBPUSD, USDJPY, AUDUSD, BTC, ETH, S&P/Nasdaq/Dow futures.
# WTI (CL=F) ma podobny wynik (+0.43R), ale 72% jego sygnalow pada w tej samej
# godzinie i tym samym kierunku co Brent - to ten sam zaklad, wiec bierzemy JEDEN.
# UWAGA: darmowe API TradingView nie ma ropy pod zadna ze sprawdzonych nazw,
# wiec Brent dziala BEZ filtra TV (tv_info zwroci None -> przepuszczamy).
# Backtest liczony byl tak samo, bez TV, wiec liczba +0.56R jest z tym zgodna.

# Zapasowe zrodlo danych (Twelve Data) - dziala z serwerow chmurowych, gdy Yahoo
# blokuje GitHub Actions. Wlacza sie tylko gdy ustawiony sekret TD_KEY.
TD_KEY = os.getenv("TD_KEY", "")
TD_MAP = {
    "GC=F":     "XAU/USD",
    "SI=F":     "XAG/USD",
    "BZ=F":     "BRENT",
    "EURUSD=X": "EUR/USD",
    "^NDX":     "NDX",
}

# Prog potwierdzen per instrument. Dowod (backtest 30 dni, 305 sygnalow):
# EURUSD na progu 3 ma przewage ~0R (traci na spreadzie); wylaczenie go lub
# prog 4 podnosi przewage calosci z +0.15R do +0.23R. Reszta zostaje na 3.
MIN_SCORE = {"EURUSD=X": 4}

# Sygnaly WYLACZONE per instrument (dane nadal pobierane - potrzebne do SETTLE).
# Dowod (14.07, backtest 30d modelem R + 81 sygnalow live): US100 traci w obu
# zbiorach (-0.23R/sygnal na n=45 backtest; -7.3R live) - wylaczony.
NO_SIGNAL_SYMS = {"^NDX"}

# Godziny NY, w ktorych NIE szukamy setupow. Dowod (14.07): killzone NY AM
# (10-11) ma ~0R na backtescie 30d i -20.4R na 81 sygnalach live; wyciecie
# podnosi przewage metali z +0.35R do +0.54R/sygnal (win 45.5% -> 49.3%).
SKIP_NY_HOURS = (10, 11)

# Minimalny dystans SL w ATR. Dowod (14.07, live + backtest NIEZALEZNIE):
# ryzyko <2 ATR = 12 sygnalow, 12 strat (stop w szumie 5m, wybijany knotem);
# zdarzaly sie tez zdegenerowane sygnaly z SL 0.3-0.4$ na zlocie (szum+spread).
# Odrzucenie <2 ATR: +0.50R -> +0.63R/sygnal, win 49.3% -> 52.9% (30d, spread).
MIN_RISK_ATR = 2.0

# TradingView - ocena techniczna (5m) doklejana do sygnalu i logowana,
# zeby z czasem zmierzyc, czy zgodnosc z TV poprawia wyniki.
TV_TA_MAP = {
    "GC=F":     ("XAUUSD", "OANDA",  "cfd"),
    "SI=F":     ("SILVER", "TVC",    "cfd"),
    "EURUSD=X": ("EURUSD", "FX_IDC", "forex"),
    "^NDX":     ("NDX",    "NASDAQ", "america"),
    "^HSI":     ("HSI",    "TVC",    "cfd"),
    # BZ=F (Brent) celowo bez wpisu - darmowe API TradingView nie ma ropy
    # pod zadna ze sprawdzonych nazw, wiec dziala bez filtra TV (fail-open).
}

INTERVAL  = os.getenv("TF", "5m")
SWING_LEN = 5
EMA_LEN   = 200
EMA_FAST  = 50
ATR_LEN   = 14
SL_BUF    = 0.5
RR_TP1    = 2.0
RR_TP2    = 3.0
# JEDEN cel zamiast czesciowych wyjsc - cala pozycja do 3R albo do SL.
# Dowod: sweep 1.5-6R na 58 sygnalach live (ze spreadem) ma gladki szczyt na 3R:
# 2.0R=+0.20, 2.5R=+0.29, 3.0R=+0.47, 3.5R=+0.35, 4.0R=+0.16 R/sygnal;
# stary plan 50/30/20 z SL na BE daje +0.31. Przewaga w obu polowkach probki
# (starsza +0.89 vs +0.66, nowsza +0.06 vs -0.05) i na obu metalach.
RR_TP     = 3.0
FVG_LOOKBACK = 8
APPROVE_SCORE = 3
# 2 swiece = sygnal maks. ~10 min od zamkniecia swiecy sygnalowej. Dowod (log
# 43 sygnalow live): przy 6 swiecach 1/3 sygnalow szla >15 min po fakcie
# (cena wejscia juz nieaktualna). Bot petli sie co 5 min, wiec 2 wystarcza.
SCAN_BARS    = 2
COOLDOWN_MIN = 20

NEWS_URL        = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NEWS_WARN_MIN   = 40
NEWS_ATTACH_MIN = 120
NEWS_BLOCK_MIN  = 30
STATE_FILE      = "state.json"
LOG_FILE        = "sygnaly_log.csv"   # historia WYSLANYCH sygnalow (do Excela z wynikami)
RUN_STATS_FILE  = "run_stats.json"    # statystyki kazdego skanu (dashboard: Scan->Fill)
WYNIKI_FILE     = "wyniki.json"       # rozliczenia TP/SL wyslanych sygnalow (dashboard: Settle)
# Filtr TV: sygnal idzie na Telegram tylko gdy ocena TradingView wspiera kierunek.
# Odrzucone NIE znikaja - trafiaja do plikow "cienia" i sa dalej rozliczane,
# zeby zbierac dowod czy filtr pomaga (ocen TV nie da sie backtestowac wstecz).
TV_GATE         = True
TVGATE_LOG      = "sygnaly_odrzucone_tv.csv"
TVGATE_WYNIKI   = "wyniki_odrzucone_tv.json"

NAME2SYM = {name: sym for name, sym, _ in SYMBOLS}


def send_telegram(text):
    """Wysylka na Telegram. Loguje TEZ pierwsza linie wiadomosci - bez tego
    w logach chmury widac tylko 'dostarczono' i nie da sie stwierdzic, co
    wlasciwie poszlo (przy diagnozie raportu dnia kosztowalo to duzo czasu)."""
    czego = text.strip().splitlines()[0][:60] if text.strip() else "?"
    if DRY_RUN:
        print("----- (DRY_RUN) -----\n" + text + "\n")
        return
    print(f"Telegram -> wysylam: {czego}")
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    for chat in [c.strip() for c in CHAT_ID.split(",") if c.strip()]:
        try:
            r = requests.post(url, json={"chat_id": chat, "text": text,
                                         "disable_web_page_preview": True}, timeout=15)
            try:
                j = r.json()
            except Exception:
                j = {}
            if r.status_code == 200 and j.get("ok"):
                print(f"Telegram OK ({chat}): dostarczono")
            else:
                # Telegram odmowil (np. 400/403/429) - wczesniej ginelo po cichu
                print(f"Telegram ODRZUCIL ({chat}): HTTP {r.status_code} {str(j)[:200]}")
                if r.status_code == 429:
                    wait = int(j.get("parameters", {}).get("retry_after", 5))
                    time.sleep(min(wait, 30))
                    r2 = requests.post(url, json={"chat_id": chat, "text": text,
                                                  "disable_web_page_preview": True}, timeout=15)
                    print(f"Telegram ponowienie ({chat}): HTTP {r2.status_code}")
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


def tv_info(sym):
    """TradingView (5m): ocena + aktualna cena SPOT. None gdy niedostepne."""
    m = TV_TA_MAP.get(sym)
    if not m:
        return None
    try:
        from tradingview_ta import TA_Handler, Interval
        a = TA_Handler(symbol=m[0], exchange=m[1], screener=m[2],
                       interval=Interval.INTERVAL_5_MINUTES).get_analysis()
        s = a.summary
        return {"rating": f"{s['RECOMMENDATION']} ({s['BUY']}/{s['SELL']}/{s['NEUTRAL']})",
                "close": float(a.indicators.get("close", 0)) or None}
    except Exception as e:
        print(f"  TradingView {m[0]}: {e}")
        return None


def tv_rating(sym):
    info = tv_info(sym)
    return info["rating"] if info else None


def tv_zgodny(tv, side):
    """Czy ocena TV wspiera kierunek sygnalu. None = TV niedostepne (nie blokujemy,
    bot ma dzialac takze gdy TV padnie). Dowod live 02-29.07 (n=76 rozliczonych):
    zgodne +0.01R/szt, przeciwne -0.21R/szt, NEUTRAL 6/6 SL."""
    if not tv:
        return None
    kier = tv.split()[0].replace("STRONG_", "")
    if kier == "BUY":
        return side == "LONG"
    if kier == "SELL":
        return side == "SHORT"
    return False


def log_signal(name, cand, score, bar_time, tv=None, spot_delta=None, plik=LOG_FILE):
    """Dopisuje WYSLANY sygnal do CSV (do pozniejszego Excela z wynikami).
    TP3 = 4R liczone tu (wiadomosc pokazuje TP1/TP2, log trzyma tez TP3).
    plik=TVGATE_LOG zapisuje sygnal-cien wstrzymany przez filtr TV."""
    risk = abs(cand["entry"] - cand["sl"])
    tp3 = cand["entry"] + (4 * risk if cand["side"] == "LONG" else -4 * risk)
    nowe = not os.path.exists(plik)
    try:
        with open(plik, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if nowe:
                w.writerow(["data_pl", "data_utc", "instrument", "strona", "strategia",
                            "score", "entry", "sl", "tp1", "tp2", "tp3", "swieca",
                            "powody", "tv", "spot_delta"])
            w.writerow([now_pl(), dt.datetime.now(dt.timezone.utc).isoformat(),
                        name, cand["side"], cand["strategy"], score,
                        cand["entry"], cand["sl"], cand["tp1"], cand["tp2"], tp3,
                        bar_time, "; ".join(cand.get("reasons", [])), tv or "",
                        f"{spot_delta:.5f}" if spot_delta is not None else ""])
    except Exception as e:
        print("log_signal blad:", e)


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)


MODEL_ROZLICZEN = "3R"    # wersja modelu wyjscia; zmiana wymusza przeliczenie wynikow


def _settle_one(row, df):
    """Rozlicza jeden sygnal na swiecach 5m. Model: CALA pozycja trzymana do
    TP=3R albo do SL=-1R. Bez czesciowych zamkniec i bez SL-na-wejscie.
    Dowod (58 sygnalow live w aktualnej konfiguracji, dane 5m, spread wliczony):
    ten plan daje +0.47R/sygnal wobec +0.31R planu 50/30/20 z SL na BE; przewaga
    wystepuje w obu polowkach probki i na obu metalach, a sweep celu 1.5-6R ma
    gladki szczyt na 3R (2.5R=+0.29, 3.5R=+0.35) - to nie pojedynczy pik.
    Ostroznie: gdy SL i TP wypadaja w tej samej swiecy, liczymy SL.
    Zwraca (status, wynik_R) albo None, gdy dane nie obejmuja sygnalu."""
    lng = row["strona"] == "LONG"
    entry, stop = float(row["entry"]), float(row["sl"])
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    tp = entry + (RR_TP * risk if lng else -RR_TP * risk)
    t0 = pd.to_datetime(row["swieca"], utc=True)
    if df.empty:
        return None
    idx0 = df.index[0]
    if idx0.tzinfo is None:
        return None
    if idx0 > t0:
        return None          # historia nie siega poczatku sygnalu
    bars = df[df.index > t0]  # wejscie na zamknieciu swiecy sygnalowej
    if bars.empty:
        return ("otwarty", 0.0)
    for hi, lo in zip(bars["High"].values, bars["Low"].values):
        hi, lo = float(hi), float(lo)
        if (lo <= stop) if lng else (hi >= stop):
            return ("SL", -1.0)
        if (hi >= tp) if lng else (lo <= tp):
            return ("TP", float(RR_TP))
    return ("otwarty", 0.0)


STATUS_EMOJI = {"SL": "\U0001F534", "TP": "\U0001F48E",
                "TP1+BE": "\U0001F3AF", "TP2+BE": "✅", "TP3": "\U0001F48E"}


def _notify_settle(row, prev, status, r):
    """Powiadomienie na Telegram, gdy WYSLANY sygnal sie zamknie (TP albo SL).
    Tylko sygnaly z ostatnich 72h - starsze rozliczenia to backfill, nie spamujemy."""
    try:
        wiek = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(row["data_utc"])
        if wiek.total_seconds() > 72 * 3600:
            return
        naglowek = f'{row["instrument"]} {row["strona"]} z {row["data_pl"]}'
        if status == "TP":
            send_telegram(f"\U0001F48E CEL OSIAGNIETY: TP +{r:.0f}R\n{naglowek}" + stopka())
        elif status == "SL":
            send_telegram(f"\U0001F534 SYGNAL ZAMKNIETY: SL (-1R)\n{naglowek}" + stopka())
        elif status not in ("otwarty", "brak_danych"):
            e = STATUS_EMOJI.get(status, "")
            send_telegram(f"{e} SYGNAL ROZLICZONY: {status} ({r:+.1f}R)\n{naglowek}" + stopka())
    except Exception as e:
        print("notify_settle blad:", e)


def settle_signals(dfs, log_file=LOG_FILE, wyniki_file=WYNIKI_FILE):
    """SETTLE: rozlicza sygnaly z CSV na danych pobranych w tym biegu (zero
    dodatkowych zapytan do Yahoo). Wyniki (R) w wyniki.json, klucz = data_utc.
    Wolane tez dla plikow cienia (sygnaly wstrzymane przez filtr TV)."""
    if not os.path.exists(log_file):
        return
    wyniki = load_json(wyniki_file, {})
    changed = False
    try:
        with open(log_file, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except Exception as e:
        print("settle: blad odczytu CSV:", e)
        return
    for row in rows:
        key = row["data_utc"]
        prev = wyniki.get(key)
        # gotowe wyniki pomijamy - chyba ze policzono je starszym modelem wyjscia
        if (prev and prev.get("status") != "otwarty"
                and prev.get("model") == MODEL_ROZLICZEN):
            continue
        df = dfs.get(NAME2SYM.get(row["instrument"]))
        if df is None or df.empty:
            continue
        try:
            res = _settle_one(row, df)
        except Exception as e:
            print("settle: blad", key, "->", e)
            continue
        if res is None:
            # brak danych: NIE kasujemy wyniku policzonego wczesniej
            if prev is None:
                wyniki[key] = {"instrument": row["instrument"], "strona": row["strona"],
                               "strategia": row["strategia"], "status": "brak_danych",
                               "r": 0.0, "model": MODEL_ROZLICZEN}
                changed = True
            continue
        status, r = res
        bez_zmian = (prev and prev.get("status") == status
                     and abs(prev.get("r", 0) - r) < 1e-9)
        if bez_zmian and prev.get("model") == MODEL_ROZLICZEN:
            continue
        wyniki[key] = {"instrument": row["instrument"], "strona": row["strona"],
                       "strategia": row["strategia"], "status": status, "r": round(r, 4),
                       "model": MODEL_ROZLICZEN,
                       "rozliczono_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
        changed = True
        # tylko realna zmiana wyniku warta powiadomienia (nie samo przestemplowanie)
        if wyniki_file == WYNIKI_FILE and not bez_zmian:
            _notify_settle(row, prev, status, r)
    if changed:
        save_json(wyniki_file, wyniki)
        print(f"settle: zaktualizowano {wyniki_file}")


def save_run_stats(stats):
    """Statystyki biegu dla dashboardu: ostatni skan + skrocona historia."""
    old = load_json(RUN_STATS_FILE, {})
    hist = old.get("historia", [])
    hist.append({"t": stats["czas_utc"],
                 "k": sum(i.get("kandydaci", 0) for i in stats["instrumenty"].values()),
                 "z": sum(i.get("zatwierdzone", 0) for i in stats["instrumenty"].values()),
                 "w": sum(1 for i in stats["instrumenty"].values() if i.get("wyslane"))})
    stats["historia"] = hist[-200:]
    save_json(RUN_STATS_FILE, stats)


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


def _dzien_pl(klucz_utc):
    try:
        return dt.datetime.fromisoformat(klucz_utc).astimezone(TZ_PL).date()
    except Exception:
        return None


def daily_report(state):
    """Raport dnia na Telegram, raz na dzien. Zwraca True gdy zmienil stan.

    Okno 22:00-24:00 PL okazalo sie zawodne: 29.07 bot mial w nim skany, ale
    rownolegle biegi bily sie o repo ("push nieudany"), stan sie nie zapisal
    i raport poszedl dwa razy, a klucz w state.json nie przetrwal. Dlatego:
    - raportujemy DZIEN ZAMKNIETY: po 22:00 dzisiejszy, wczesniej wczorajszy;
    - odhaczamy date PRZED wysylka, wiec nawet gdy stan zginie, kolejny bieg
      wysle najwyzej jeden duplikat, a nie serie;
    - jesli bot przespi caly wieczor, raport i tak dojdzie nastepnego dnia.
    """
    now = dt.datetime.now(TZ_PL)
    cel = now.date() if now.hour >= 22 else now.date() - dt.timedelta(days=1)
    cel_s = cel.isoformat()
    if state.get("daily_report") == cel_s:
        return False
    state["daily_report"] = cel_s
    wyniki = load_json(WYNIKI_FILE, {})
    cien = load_json(TVGATE_WYNIKI, {})
    dzis = {k: v for k, v in wyniki.items() if str(_dzien_pl(k)) == cel_s}
    cien_dzis = {k: v for k, v in cien.items() if str(_dzien_pl(k)) == cel_s}
    if not dzis and not cien_dzis:
        print(f"raport dnia ({cel_s}): brak sygnalow, nie wysylam")
        return True   # cichy dzien - nie spamujemy, ale date odhaczamy

    def suma(d, dni=None):
        gr = cel - dt.timedelta(days=dni) if dni else None
        return sum(v.get("r", 0) for k, v in d.items()
                   if v.get("status") not in ("otwarty", "brak_danych")
                   and (gr is None or (_dzien_pl(k) or gr) >= gr))

    linie = [f"\U0001F4CA RAPORT DNIA ({cel.strftime('%d.%m')})",
             f"Sygnaly wyslane tego dnia: {len(dzis)}"]
    for k in sorted(dzis):
        v = dzis[k]
        e = STATUS_EMOJI.get(v["status"], "⏳")
        godz = dt.datetime.fromisoformat(k).astimezone(TZ_PL).strftime("%H:%M")
        wyn = v["status"] if v["status"] != "otwarty" else "otwarty"
        linie.append(f"{e} {godz} {v['instrument']} {v['strona']} -> {wyn} ({v.get('r', 0):+.1f}R)")
    linie.append(f"Bilans R: ten dzien {suma(dzis):+.1f} | 7 dni {suma(wyniki, 7):+.1f} "
                 f"| od startu {suma(wyniki):+.1f}")
    if cien:
        s_cien = suma(cien)
        ocena = ("filtr oszczedzil straty" if s_cien < 0 else
                 "filtr kosztowal zysk" if s_cien > 0 else "bez roznicy")
        linie.append(f"\U0001F6E1 Filtr TV: tego dnia zatrzymal {len(cien_dzis)}, "
                     f"lacznie {len(cien)} (wynik wstrzymanych: {s_cien:+.1f}R - {ocena})")
    send_telegram("\n".join(linie) + stopka())
    return True


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


def dl(sym, period, interval, retries=3):
    for attempt in range(retries):
        try:
            df = yf.download(sym, period=period, interval=interval,
                             progress=False, auto_adjust=False, threads=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if not df.empty:
                return df
        except Exception as e:
            print(f"  dl {sym} proba {attempt+1}: {e}")
        time.sleep(3 * (attempt + 1))
    td = dl_td(sym, interval)
    if not td.empty:
        print(f"  {sym}: uzyto Twelve Data (Yahoo pusty)")
    return td


def dl_td(sym, interval):
    """Awaryjne zrodlo: Twelve Data. Zwraca DataFrame jak Yahoo (Open/High/Low/Close,
    indeks UTC). Pusty DF, gdy brak klucza TD_KEY lub brak danych."""
    if not TD_KEY:
        return pd.DataFrame()
    tdsym = TD_MAP.get(sym)
    if not tdsym:
        return pd.DataFrame()
    td_int = {"5m": "5min", "15m": "15min", "60m": "1h", "1h": "1h"}.get(interval, "5min")
    try:
        r = requests.get("https://api.twelvedata.com/time_series",
                         params={"symbol": tdsym, "interval": td_int, "outputsize": 5000,
                                 "apikey": TD_KEY, "format": "JSON", "timezone": "UTC"},
                         timeout=20)
        j = r.json()
        if not isinstance(j, dict) or "values" not in j:
            print(f"  TwelveData {tdsym}: {j.get('message', j) if isinstance(j, dict) else j}")
            return pd.DataFrame()
        df = pd.DataFrame(j["values"])
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        df = df.set_index("datetime").sort_index()
        df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"})
        for col in ("Open", "High", "Low", "Close"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["Volume"] = 0
        return df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    except Exception as e:
        print(f"  TwelveData {tdsym} blad: {e}")
        return pd.DataFrame()


def htf_from_5m(df):
    h = df[["Open", "High", "Low", "Close"]].resample("1h").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    return htf_trend(h)


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
    """Okna, w ktorych powstaje Silver Bullet (czas NY). Sesja AZJATYCKA
    (20:00-21:00 NY = otwarcie Tokio, 02:00-03:00 w Polsce) dodana 30.07.2026.

    Dowod (eksperyment8.py: jeden przebieg po CALEJ dobie z udawana killzone
    w kazdej godzinie, 60 dni, 3 instrumenty, rozliczenie 3R ze spreadem):
      Azja 20-21    n=126  +59.1R  +0.47R/szt  win 38%  polowki +0.60/+0.34
      NY PM 14-15   n=111  +60.4R  +0.54R/szt  win 40%  polowki +0.64/+0.45
      Londyn 3-4    n=123  +36.6R  +0.30R/szt  win 33%  polowki +0.61/-0.01
    Azja dziala na kazdym instrumencie osobno (zloto +0.41, srebro +0.46,
    Brent +0.53) i nie jest samotnym pikiem - sasiednie godziny 19 i 21 tez
    sa dodatnie (+0.30 i +0.28). Calosc: +97.0R -> +156.1R, czyli +61%.
    Godzin 19 i 21 NIE dodano: ich druga polowa probki jest plaska/ujemna.
    Okno 10-11 zostaje w liscie, ale SKIP_NY_HOURS i tak je wycina.
    """
    t = now_ny.hour * 60 + now_ny.minute
    zones = [(3 * 60, 4 * 60), (10 * 60, 11 * 60),
             (14 * 60, 15 * 60), (20 * 60, 21 * 60)]
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
    if now_ny.hour in SKIP_NY_HOURS:
        return []
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

    # odsiew: stop blizej niz MIN_RISK_ATR = setup w szumie, nie wysylamy
    cands = [c for c in cands if abs(c["entry"] - c["sl"]) >= MIN_RISK_ATR * a]

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


def agent2_validate(cand, hdir, events, ccys, now, need=None):
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
    return (score >= (need if need is not None else APPROVE_SCORE), score, why, None)


def fmt_signal(name, cand, score, why, news, tv=None, delta=None):
    emoji = "\U0001F7E2 LONG" if cand["side"] == "LONG" else "\U0001F534 SHORT"
    e = cand["entry"]
    d = 5 if abs(e) < 10 else 2
    # delta = spot - futures: przeliczamy poziomy na ceny SPOT (jak u brokera /
    # na TradingView), bo Yahoo dla metali daje FUTURES (np. zloto ~10-40 USD wyzej)
    off = delta if delta is not None else 0.0
    risk = abs(cand["entry"] - cand["sl"])
    tp3 = cand["entry"] + (4 * risk if cand["side"] == "LONG" else -4 * risk)
    msg = (f"{emoji}  {name}\n"
           f"\U0001F9E0 Strategia: {cand['strategy']}\n"
           f"----------------------\n"
           f"Wejscie: {cand['entry'] + off:.{d}f}\n"
           f"SL: {cand['sl'] + off:.{d}f}\n"
           f"TP: {cand['entry'] + (risk * RR_TP if cand['side'] == 'LONG' else -risk * RR_TP) + off:.{d}f}"
           f"  ← zamknij CALOSC ({RR_TP:.0f}R)\n"
           f"\U0001F4A1 Jeden cel, jeden stop - bez czesciowych zamkniec i bez\n"
           f"   przesuwania SL. Tak wychodzi najlepiej w testach na 58 sygnalach\n"
           f"   (+0.47R/sygnal wobec +0.31R starego planu 50/30/20).\n"
           f"\U00002705 Agent2 ({score} pkt): " + ", ".join(why) + "\n"
           f"\U0001F50E Agent1: " + ", ".join(cand["reasons"]))
    if delta is not None:
        msg += f"\n\U0001F4CD Ceny SPOT (broker/TView); futures {-off:+.{d}f} od spot"
    if tv:
        msg += f"\n\U0001F4CA TradingView 5m: {tv}"
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
    print("TV check (zloto):", tv_rating("GC=F") or "niedostepny")

    # test lacznosci: TG_TEST=1 wysyla wiadomosc kontrolna (weryfikacja doreczen)
    if os.getenv("TG_TEST") == "1":
        send_telegram("\U0001F527 TEST LACZNOSCI - jesli to widzisz, doreczanie "
                      "sygnalow na Telegram dziala poprawnie." + stopka())
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

    data_fail = []
    dfs = {}                       # pobrane dane per instrument (do SETTLE)
    run_stats = {"czas_utc": now.isoformat(), "czas_pl": now_pl(), "instrumenty": {}}
    for name, sym, ccys in SYMBOLS:
        ins = run_stats["instrumenty"].setdefault(
            name, {"etap": "scan", "kandydaci": 0, "zatwierdzone": 0,
                   "wyslane": False, "opis": ""})
        try:
            li = last_send.get(sym)
            if li:
                try:
                    if (now - dt.datetime.fromisoformat(li)).total_seconds() < COOLDOWN_MIN * 60:
                        print(f"{name}: cooldown")
                        ins["etap"], ins["opis"] = "cooldown", "pauza po ostatnim sygnale"
                        df = dl(sym, "10d", INTERVAL)   # dane i tak potrzebne do SETTLE
                        if not df.empty:
                            dfs[sym] = df
                        time.sleep(1); continue
                except Exception:
                    pass

            df = dl(sym, "10d", INTERVAL)
            if df.empty or len(df) < EMA_LEN + SWING_LEN + 12:
                print(f"{name}: brak danych (Yahoo)"); data_fail.append(name)
                ins["etap"], ins["opis"] = "scan", "brak danych rynkowych"
                time.sleep(2); continue
            dfs[sym] = df
            ins["bars"] = int(len(df))

            if sym in NO_SIGNAL_SYMS:
                print(f"{name}: sygnaly wylaczone")
                ins["etap"] = "wylaczony"
                ins["opis"] = "sygnaly wylaczone (traci: -0.23R/sygnal 30d, -7.3R live)"
                time.sleep(1); continue

            dff = df.iloc[:-1]
            hdir = htf_from_5m(df)
            seen = sent_bars.get(sym, [])
            time.sleep(1)

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
                ins["kandydaci"] += len(res[0])
                ins["etap"] = "detect"
                best = None
                for c in res[0]:
                    ok, score, why, blocker = agent2_validate(
                        c, hdir, events, ccys, now,
                        need=MIN_SCORE.get(sym, APPROVE_SCORE))
                    if blocker is not None:
                        ins["opis"] = "blokada: dane makro za chwile"
                        best = None; break
                    if ok:
                        ins["zatwierdzone"] += 1
                        if best is None or score > best[1]:
                            best = (c, score, why)
                if best:
                    ins["etap"] = "validate"
                    chosen = (best, bar_time)
                    break

            if not chosen:
                print(f"{name}: brak nowego sygnalu")
                if ins["etap"] == "detect" and not ins["opis"]:
                    ins["opis"] = "kandydaci odrzuceni przez walidator"
                elif ins["etap"] == "scan":
                    ins["opis"] = "brak setupu na ostatnich swiecach"
                continue

            (c, score, why), bar_time = chosen
            news = high_impact_for(events, ccys, 0, NEWS_ATTACH_MIN, now)
            info = tv_info(sym)
            tv = info["rating"] if info else None
            delta = None
            if info and info.get("close"):
                try:
                    fut_now = float(df["Close"].iloc[-1])
                    dlt = info["close"] - fut_now
                    # koryguj tylko realna roznice zrodel (futures vs spot);
                    # szum <0.05% pomijamy, >2% = podejrzane dane, nie ruszamy
                    if 0.0005 < abs(dlt) / fut_now < 0.02:
                        delta = dlt
                except Exception:
                    pass
            if TV_GATE and tv_zgodny(tv, c["side"]) is False:
                # sygnal-cien: nie wysylamy, ale logujemy i rozliczamy jak zwykly,
                # zeby dalej zbierac dowod skutecznosci filtra
                log_signal(name, c, score, bar_time, tv, delta, plik=TVGATE_LOG)
                seen.append(bar_time)
                sent_bars[sym] = seen[-120:]
                changed = True
                ins["etap"] = "validate"
                ins["opis"] = f"wstrzymany przez filtr TV ({tv.split()[0]})"
                print(f"{name}: WSTRZYMANY przez filtr TV ({tv})")
                continue
            send_telegram(fmt_signal(name, c, score, why, news, tv, delta))
            log_signal(name, c, score, bar_time, tv, delta)
            seen.append(bar_time)
            sent_bars[sym] = seen[-120:]
            last_send[sym] = now.isoformat()
            changed = True
            ins["etap"], ins["wyslane"] = "fill", True
            ins["opis"] = f"WYSLANO {c['side']} ({c['strategy']}, {score} pkt)"
            print(f"{name}: WYSLANO {c['side']} ({c['strategy']})")

        except Exception as e:
            print(f"{name}: blad -> {e}")
            ins["opis"] = f"blad: {e}"

    # SETTLE - rozliczenie wyslanych sygnalow na danych z tego biegu
    try:
        settle_signals(dfs)
        settle_signals(dfs, TVGATE_LOG, TVGATE_WYNIKI)
    except Exception as e:
        print("settle: blad ->", e)
    try:
        save_run_stats(run_stats)
    except Exception as e:
        print("run_stats: blad ->", e)

    # RAPORT DNIA - wieczorne podsumowanie na Telegram
    try:
        if daily_report(state):
            changed = True
    except Exception as e:
        print("raport dnia: blad ->", e)

    # ALARM: brak danych rynkowych (np. Yahoo blokuje chmure) - zeby nie bylo
    # "zielono, ale niemo". Wysylany najwyzej raz na 3h.
    if data_fail:
        last_alert = state.get("data_alert")
        send_alert = True
        if last_alert:
            try:
                if (now - dt.datetime.fromisoformat(last_alert)).total_seconds() < 3 * 3600:
                    send_alert = False
            except Exception:
                pass
        if send_alert:
            src = "Twelve Data (zapas)" if TD_KEY else "tylko Yahoo"
            send_telegram("\U000026A0 DIAGNOSTYKA: brak danych rynkowych dla: "
                          + ", ".join(data_fail) + f".\nZrodlo: {src}. "
                          "Mozliwa blokada Yahoo w chmurze - sygnaly wstrzymane do powrotu danych."
                          + stopka())
            state["data_alert"] = now.isoformat()
            changed = True

    state["sent_events"] = state["sent_events"][-100:]
    if changed:
        save_state(state)


if __name__ == "__main__":
    main()
