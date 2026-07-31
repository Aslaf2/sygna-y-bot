"""ZLOTY MENTOR - bot uczacy handlu zlotem.

Czym rozni sie od starszego smc_bot.py:
  1. Pilnuje JEDNEGO rynku - zlota. Dzieki temu sam z siebie daje 1-3 sygnaly
     dziennie (zmierzone: 2.0/dzien, 90% dni miesci sie w widelkach).
  2. Wysyla RADAR na 30 minut przed otwarciem okna handlowego: podaje kierunek,
     poziomy i to, ktora swieca ma sie zamknac.
  3. Do kazdej wiadomosci dolacza WYKRES z zaznaczonym wejsciem, stopem i celem.
  4. Tlumaczy, co widzi - kazda wiadomosc mowi, czemu ten setup powstal.

DOWODY dla progow uzytych nizej (backtest 60 dni, swiece 5m, rozliczenie 3R,
spread wliczony; eksperymenty 11-15 w katalogu roboczym sesji):
  - baza (silnik jak w starym bocie):        n=120  +0.47R/sygnal  win 37%
  - po filtrze ryzyka >= 3 ATR:              n= 97  +0.69R/sygnal  win 42%
  - po obu filtrach (ryzyko + impet):        n= 83  +0.69R/sygnal  win 42%
  Filtry sprawdzone takze na srebrze i Hang Sengu (rynkach, na ktorych ich NIE
  szukalem) - tam tez pomagaja. Na Brencie filtr ryzyka szkodzi, dlatego ten
  bot celowo NIE handluje ropa.
UCZCIWIE: druga polowa probki jest slabsza (+0.33R/sygnal wobec +1.05R w
pierwszej). Przewaga jest realna, ale zmienna w czasie - to nie jest pewniak.
"""

import datetime as dt
import json
import os
import time

import numpy as np
import pandas as pd
import requests
import yfinance as yf

from zoneinfo import ZoneInfo

import wykres as W

TZ_PL = ZoneInfo("Europe/Warsaw")
TZ_NY = ZoneInfo("America/New_York")

# ---------------------------------------------------------------- ustawienia

RYNEK      = "GC=F"                 # zloto (kontrakt futures - zrodlo danych)
NAZWA      = "ZLOTO (XAUUSD)"
TV_SYMBOL  = "OANDA:XAUUSD"         # do linku i oceny TradingView
INTERWAL   = "5m"

KILLZONE   = [3, 14, 20]            # godziny NY, w ktorych szukamy setupu
RADAR_MIN  = 30                     # ile minut przed oknem leci radar

EMA_LEN    = 200                    # trend na swiecach 5m
EMA_1H     = 50                     # trend nadrzedny (godzinowy)
ATR_LEN    = 14
SWING_LEN  = 5
SL_BUF     = 0.5                    # bufor stopu w ATR
RR_TP      = 3.0                    # jeden cel, bez czesciowych zamkniec

MIN_RISK_ATR = 3.0                  # dowod: <3 ATR to -0.48R/sygnal na zlocie
IMPET_PROG   = 0.7                  # LONG musi zamknac sie w gornych 30% zakresu
IMPET_OKNO   = 12                   # ile swiec wstecz liczy sie zakres
MAX_DZIENNIE = 3                    # twardy limit sygnalow na dzien
COOLDOWN_MIN = 25                   # pauza po wyslanym sygnale

# Sciezki liczone od pliku, nie od katalogu uruchomienia - w GitHub Actions
# bot startuje z katalogu glownego repozytorium, a stan ma lezec przy nim.
KATALOG    = os.path.dirname(os.path.abspath(__file__))
STAN_PLIK  = os.path.join(KATALOG, "stan_mentor.json")
LOG_PLIK   = os.path.join(KATALOG, "sygnaly_mentor.csv")

TOKEN   = os.getenv("TG_TOKEN", "")
CHAT_ID = os.getenv("TG_CHAT_ID", "")
DRY_RUN = os.getenv("DRY_RUN", "") == "1"
PODPIS  = "\U0001F947 Zloty Mentor"


def _wczytaj_tajne():
    """Token moze siedziec w zmiennych srodowiskowych albo w tajne.json
    (plik lokalny, nie trafia do repozytorium)."""
    global TOKEN, CHAT_ID
    if TOKEN and CHAT_ID:
        return
    try:
        with open(os.path.join(KATALOG, "tajne.json"), encoding="utf-8") as f:
            t = json.load(f)
        TOKEN = TOKEN or t.get("TG_TOKEN", "")
        CHAT_ID = CHAT_ID or str(t.get("TG_CHAT_ID", ""))
    except Exception:
        pass


# ------------------------------------------------------------------ telegram

def tg_tekst(tekst):
    if DRY_RUN:
        print("\n----- (TEST, nic nie wyslano) -----\n" + tekst + "\n")
        return True
    ok = True
    for chat in [c.strip() for c in CHAT_ID.split(",") if c.strip()]:
        try:
            r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                              json={"chat_id": chat, "text": tekst,
                                    "disable_web_page_preview": True}, timeout=20)
            j = r.json() if r.content else {}
            if r.status_code == 200 and j.get("ok"):
                print(f"Telegram OK ({chat}): {tekst.splitlines()[0][:50]}")
            else:
                ok = False
                print(f"Telegram ODRZUCIL ({chat}): HTTP {r.status_code} {str(j)[:180]}")
        except Exception as e:
            ok = False
            print(f"Telegram blad ({chat}): {e}")
    return ok


def tg_zdjecie(png, podpis):
    """Wysyla wykres. Telegram tnie podpis na 1024 znakach - dluzszy tekst
    dosylamy osobna wiadomoscia, zeby nic sie nie zgubilo."""
    if DRY_RUN:
        print("\n----- (TEST) WYKRES + PODPIS -----\n" + podpis + "\n")
        return True
    krotki = podpis if len(podpis) <= 1000 else podpis[:990] + "\n(...)"
    ok = True
    for chat in [c.strip() for c in CHAT_ID.split(",") if c.strip()]:
        try:
            r = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendPhoto",
                              data={"chat_id": chat, "caption": krotki},
                              files={"photo": ("setup.png", png, "image/png")},
                              timeout=45)
            j = r.json() if r.content else {}
            if not (r.status_code == 200 and j.get("ok")):
                ok = False
                print(f"Telegram (foto) ODRZUCIL: HTTP {r.status_code} {str(j)[:180]}")
            else:
                print(f"Telegram OK ({chat}): wykres dostarczony")
        except Exception as e:
            ok = False
            print(f"Telegram (foto) blad: {e}")
    if len(podpis) > 1000:
        tg_tekst(podpis)
    return ok


# --------------------------------------------------------------------- stan

def wczytaj_stan():
    try:
        with open(STAN_PLIK, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def zapisz_stan(s):
    with open(STAN_PLIK, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=1, ensure_ascii=False)


def teraz_pl():
    return dt.datetime.now(TZ_PL)


def stopka():
    return f"\n\U0001F552 {teraz_pl().strftime('%d.%m.%Y %H:%M')} · {PODPIS}"


# ------------------------------------------------------------------- rynek

def pobierz(okres="10d", interwal=INTERWAL, prob=3):
    for i in range(prob):
        try:
            df = yf.download(RYNEK, period=okres, interval=interwal,
                             progress=False, auto_adjust=False, threads=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if not df.empty:
                return df.dropna()
        except Exception as e:
            print(f"  pobieranie proba {i+1}: {e}")
        time.sleep(3 * (i + 1))
    return pd.DataFrame()


def atr(df, n=ATR_LEN):
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()],
                   axis=1).max(axis=1)
    return tr.rolling(n).mean()


def trend_1h(df5):
    """Kierunek nadrzedny: cena wobec EMA50 na swiecach godzinowych.
    Liczony tylko z ZAMKNIETYCH godzin - biezaca jeszcze sie zmienia."""
    h = df5[["Open", "High", "Low", "Close"]].resample("1h").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    if len(h) < EMA_1H + 3:
        return 0
    h = h.iloc[:-1]
    e = h["Close"].ewm(span=EMA_1H, adjust=False).mean().iloc[-1]
    return 1 if float(h["Close"].iloc[-1]) > float(e) else -1


def swingi(highs, lows, L=SWING_LEN):
    sh, sl = [], []
    for i in range(L, len(highs) - L):
        if highs[i] == highs[i - L:i + L + 1].max():
            sh.append(highs[i])
        if lows[i] == lows[i - L:i + L + 1].min():
            sl.append(lows[i])
    return sh, sl


def swieza_luka(highs, lows, i, kierunek):
    """FVG - luka: swieca porusza sie tak szybko, ze miedzy knotem sprzed
    dwoch swiec a obecnym zostaje pusta przestrzen. Slad duzego zlecenia."""
    if i < 2:
        return None
    if kierunek == "LONG" and lows[i] > highs[i - 2]:
        return (highs[i - 2], lows[i])
    if kierunek == "SHORT" and highs[i] < lows[i - 2]:
        return (highs[i], lows[i - 2])
    return None


def w_killzone(ny):
    return any(h <= ny.hour < h + 1 for h in KILLZONE)


# ------------------------------------------------------------- rozpoznanie

STRATEGIE_OPIS = {
    "Silver Bullet": (
        "Silver Bullet to setup Michaela Huddlestona (ICT). W waskim oknie "
        "czasowym instytucje domykaja pozycje i zostawiaja na wykresie luke "
        "(FVG). Wchodzimy w kierunku trendu godzinowego zaraz po jej powstaniu."),
    "Liquidity Sweep": (
        "Zebranie plynnosci: cena wybija poprzedni szczyt albo dolek, "
        "uruchamia stopy innych graczy, po czym natychmiast wraca. "
        "Wchodzimy w strone powrotu - wybicie bylo pulapka."),
    "Order Block + FVG": (
        "Order Block to ostatnia swieca przeciwna przed silnym ruchem - slad "
        "duzego zlecenia. Gdy cena wraca do niej PIERWSZY raz i jest tam luka, "
        "gramy odbicie w kierunku trendu."),
    "OTE": (
        "Optimal Trade Entry: po ruchu impulsywnym cena cofa sie o 62-79% "
        "poprzedniej fali. To strefa, w ktorej ryzyko jest najmniejsze "
        "wzgledem zasiegu ruchu."),
}


def znajdz_setup(df, ny):
    """Rozpoznaje, ktora strategia wlasnie sie ksztaltuje na ostatniej swiecy.

    Zwraca slownik setupu albo None. Wszystkie warunki musza byc spelnione
    naraz - to celowo waskie sito, bo bot ma dawac 1-3 sygnaly dziennie,
    a nie kilkanascie.
    """
    if not w_killzone(ny) or len(df) < EMA_LEN + SWING_LEN + 12:
        return None
    d = df.copy()
    d["EMA"] = d["Close"].ewm(span=EMA_LEN, adjust=False).mean()
    d["ATR"] = atr(d)
    o = d["Open"].values
    h, l, c = d["High"].values, d["Low"].values, d["Close"].values
    i = len(d) - 1
    a = float(d["ATR"].iloc[i])
    if not np.isfinite(a) or a <= 0:
        return None
    cena = float(c[i])
    trend5 = 1 if cena > float(d["EMA"].iloc[i]) else -1
    byk, niedz = c[i] > o[i], c[i] < o[i]

    sh, sl_pts = swingi(h, l)
    ost_sh = sh[-1] if sh else None
    ost_sl = sl_pts[-1] if sl_pts else None
    poprz_sh = sh[-2] if len(sh) >= 2 else ost_sh
    poprz_sl = sl_pts[-2] if len(sl_pts) >= 2 else ost_sl

    kand = []

    luka_l = swieza_luka(h, l, i, "LONG")
    luka_s = swieza_luka(h, l, i, "SHORT")
    if trend5 == 1 and luka_l:
        baza = ost_sl if ost_sl else l[i]
        kand.append(("Silver Bullet", "LONG", cena, baza - a * SL_BUF, luka_l,
                     "swieza luka wzrostowa w oknie handlowym"))
    if trend5 == -1 and luka_s:
        baza = ost_sh if ost_sh else h[i]
        kand.append(("Silver Bullet", "SHORT", cena, baza + a * SL_BUF, luka_s,
                     "swieza luka spadkowa w oknie handlowym"))

    if poprz_sh is not None and h[i] > poprz_sh and cena < poprz_sh and niedz:
        kand.append(("Liquidity Sweep", "SHORT", cena, h[i] + a * SL_BUF, None,
                     f"zebrano plynnosc nad szczytem {poprz_sh:.2f} i odrzucono"))
    if poprz_sl is not None and l[i] < poprz_sl and cena > poprz_sl and byk:
        kand.append(("Liquidity Sweep", "LONG", cena, l[i] - a * SL_BUF, None,
                     f"zebrano plynnosc pod dolkiem {poprz_sl:.2f} i odrzucono"))

    if ost_sh is not None and ost_sl is not None and ost_sh > ost_sl:
        noga = ost_sh - ost_sl
        if trend5 == 1 and byk:
            lo, hi = ost_sh - noga * 0.79, ost_sh - noga * 0.62
            if lo <= cena <= hi and c[i - 1] > hi:
                kand.append(("OTE", "LONG", cena, ost_sl - a * SL_BUF, None,
                             "cofniecie do strefy 62-79% poprzedniej fali"))
        if trend5 == -1 and niedz:
            lo, hi = ost_sl + noga * 0.62, ost_sl + noga * 0.79
            if lo <= cena <= hi and c[i - 1] < lo:
                kand.append(("OTE", "SHORT", cena, ost_sh + a * SL_BUF, None,
                             "cofniecie do strefy 62-79% poprzedniej fali"))

    if not kand:
        return None

    hdir = trend_1h(df)
    okno = d.iloc[max(0, i - IMPET_OKNO):i + 1]
    zakres = float(okno["High"].max() - okno["Low"].min())
    poz = (cena - float(okno["Low"].min())) / zakres if zakres > 0 else 0.5

    for strat, strona, wejscie, stop, luka, powod in kand:
        ryzyko = abs(wejscie - stop)
        # --- trzy sita, kazde poparte pomiarem ---
        if hdir == 0 or (strona == "LONG") != (hdir == 1):
            continue                                    # musi zgadzac sie z 1h
        if ryzyko < MIN_RISK_ATR * a:
            continue                                    # stop w szumie: -0.48R
        impet = poz >= IMPET_PROG if strona == "LONG" else poz <= 1 - IMPET_PROG
        if not impet:
            continue                                    # wejscie pod prad: +0.04R
        return {"strategia": strat, "strona": strona, "wejscie": wejscie,
                "sl": stop, "tp": wejscie + (RR_TP * ryzyko if strona == "LONG"
                                             else -RR_TP * ryzyko),
                "ryzyko": ryzyko, "atr": a, "luka": luka, "powod": powod,
                "ryzyko_atr": ryzyko / a, "impet": poz,
                "swieca": d.index[i], "trend_1h": hdir}
    return None


# ----------------------------------------------------------- TradingView

def ocena_tv():
    try:
        from tradingview_ta import TA_Handler, Interval
        a = TA_Handler(symbol="XAUUSD", exchange="OANDA", screener="cfd",
                       interval=Interval.INTERVAL_5_MINUTES).get_analysis()
        s = a.summary
        return {"ocena": s["RECOMMENDATION"],
                "opis": f"{s['RECOMMENDATION']} ({s['BUY']}/{s['SELL']}/{s['NEUTRAL']})",
                "close": float(a.indicators.get("close", 0)) or None}
    except Exception as e:
        print("  TradingView:", e)
        return None


def link_tv():
    return f"https://www.tradingview.com/chart/?symbol={TV_SYMBOL}&interval=5"


# ------------------------------------------------------------- wiadomosci

def okno_pl(kz_h, teraz_ny):
    """Zamienia godzine killzone (NY) na konkretne okno w czasie polskim."""
    start_ny = teraz_ny.replace(hour=kz_h, minute=0, second=0, microsecond=0)
    if start_ny < teraz_ny - dt.timedelta(hours=2):
        start_ny += dt.timedelta(days=1)
    return start_ny.astimezone(TZ_PL), (start_ny + dt.timedelta(hours=1)).astimezone(TZ_PL)


NAZWY_OKIEN = {3: "sesja londynska", 14: "popoludnie w Nowym Jorku",
               20: "otwarcie Tokio"}


def tekst_radaru(kierunek, cena, sw_hi, sw_lo, start, koniec, kz_h, min_ryz, tv):
    strzalka = "\U0001F53A" if kierunek == "LONG" else "\U0001F53B"
    slowo = "WZROSTOWY" if kierunek == "LONG" else "SPADKOWY"
    czego = ("luki wzrostowej - trzech swiec, miedzy ktorymi zostanie pusta "
             "przestrzen w gore") if kierunek == "LONG" else \
            ("luki spadkowej - trzech swiec, miedzy ktorymi zostanie pusta "
             "przestrzen w dol")
    stop_przy = sw_hi if kierunek == "SHORT" else sw_lo
    linie = [
        f"\U0001F52D RADAR - za {RADAR_MIN} min otwiera sie okno",
        "",
        f"{NAZWA} · {NAZWY_OKIEN.get(kz_h, 'okno handlowe')}",
        f"Okno: {start.strftime('%H:%M')}-{koniec.strftime('%H:%M')} (czas polski)",
        "",
        f"{strzalka} Trend godzinowy: {slowo}",
        f"→ jesli setup sie pojawi, bedzie to {kierunek}",
        "",
        "CZEGO SZUKAM:",
        f"· {czego}",
        f"· ruch musi byc szeroki - stop min. {min_ryz:,.2f} od wejscia",
        f"· zamkniecie swiecy przy {'gornej' if kierunek == 'LONG' else 'dolnej'} "
        f"krawedzi ostatniego zakresu",
        "",
        "POZIOMY:",
        f"· cena teraz: {cena:,.2f}",
    ]
    if sw_hi:
        linie.append(f"· ostatni szczyt: {sw_hi:,.2f}")
    if sw_lo:
        linie.append(f"· ostatni dolek: {sw_lo:,.2f}")
    if stop_przy:
        linie.append(f"· stop-loss trafi w okolice {stop_przy:,.2f}")
    linie += [
        "",
        "KIEDY WCHODZIMY:",
        "Na ZAMKNIECIU swiecy 5-minutowej, ktora spelni warunki.",
        f"Swiece zamykaja sie o pelnych 5 minutach ({start.strftime('%H:%M')}, "
        f"{(start + dt.timedelta(minutes=5)).strftime('%H:%M')}, "
        f"{(start + dt.timedelta(minutes=10)).strftime('%H:%M')}...).",
        "Sygnal dostaniesz do 2 minut po zamknieciu takiej swiecy.",
        "",
        "\U0001F4CC Uczciwie: setup pojawia sie mniej wiecej w co drugim oknie.",
        "Jesli sie nie pojawi - nie wchodzimy. Brak sygnalu to tez decyzja.",
    ]
    if tv:
        linie.append(f"\n\U0001F4CA TradingView teraz: {tv['opis']}")
    linie.append(f"\n\U0001F517 Wykres: {link_tv()}")
    return "\n".join(linie) + stopka()


def tekst_sygnalu(s, tv, spot_delta, nr_dnia):
    ikona = "\U0001F7E2" if s["strona"] == "LONG" else "\U0001F534"
    off = spot_delta or 0.0
    we, sl, tp = s["wejscie"] + off, s["sl"] + off, s["tp"] + off
    czas = s["swieca"].astimezone(TZ_PL)
    linie = [
        f"{ikona} {s['strona']} · {NAZWA}",
        f"\U0001F9E0 {s['strategia']}",
        "─" * 22,
        f"Wejscie: {we:,.2f}",
        f"Stop:    {sl:,.2f}   (ryzyko {s['ryzyko']:,.2f})",
        f"Cel:     {tp:,.2f}   (+3R = {3*s['ryzyko']:,.2f})",
        "",
        f"\U0001F551 Swieca sygnalowa zamknieta o {czas.strftime('%H:%M')}, "
        f"wchodzimy TERAZ po cenie rynkowej.",
        "",
        "DLACZEGO TEN SETUP:",
        f"· {s['powod']}",
        f"· kierunek zgodny z trendem godzinowym",
        f"· ryzyko {s['ryzyko_atr']:.1f}× ATR (wymagam min. {MIN_RISK_ATR})",
        f"· swieca zamknela sie z impetem ({100*s['impet']:.0f}% zakresu)",
        "",
        f"\U0001F393 {STRATEGIE_OPIS.get(s['strategia'], '')}",
        "",
        "ZASADA WYJSCIA: jeden cel, jeden stop. Nie przesuwamy stopa i nie "
        "zamykamy czesciowo - tak wychodzi najlepiej w testach.",
        f"\nSygnal {nr_dnia} z maks. {MAX_DZIENNIE} dzisiaj.",
    ]
    if spot_delta:
        linie.append(f"\U0001F4CD Ceny przeliczone na SPOT (jak u brokera); "
                     f"kontrakt futures jest {-off:+,.2f} od spotu.")
    if tv:
        linie.append(f"\U0001F4CA TradingView 5m: {tv['opis']}")
    linie.append(f"\U0001F517 {link_tv()}")
    return "\n".join(linie) + stopka()


# ------------------------------------------------------------------- logika

def zapisz_log(s, tv):
    import csv
    nowy = not os.path.exists(LOG_PLIK)
    with open(LOG_PLIK, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if nowy:
            w.writerow(["data_pl", "data_utc", "strona", "strategia", "wejscie",
                        "sl", "tp", "ryzyko", "ryzyko_atr", "impet", "swieca", "tv"])
        w.writerow([teraz_pl().strftime("%d.%m.%Y %H:%M"),
                    dt.datetime.now(dt.timezone.utc).isoformat(),
                    s["strona"], s["strategia"], round(s["wejscie"], 2),
                    round(s["sl"], 2), round(s["tp"], 2), round(s["ryzyko"], 2),
                    round(s["ryzyko_atr"], 2), round(s["impet"], 2),
                    s["swieca"].isoformat(), (tv or {}).get("opis", "")])


def spot_korekta(df, tv):
    """Yahoo podaje kontrakt futures, uzytkownik patrzy na spot u brokera.
    Roznica siega kilkunastu dolarow, wiec poziomy trzeba przesunac."""
    if not tv or not tv.get("close"):
        return None
    try:
        fut = float(df["Close"].iloc[-1])
        d = tv["close"] - fut
        return d if 0.0005 < abs(d) / fut < 0.02 else None
    except Exception:
        return None


def obsluz_radar(df, stan, teraz_ny):
    """Wysyla radar, jesli do startu ktoregos okna zostalo okolo RADAR_MIN minut."""
    for kz in KILLZONE:
        start_ny = teraz_ny.replace(hour=kz, minute=0, second=0, microsecond=0)
        do_startu = (start_ny - teraz_ny).total_seconds() / 60
        if do_startu < 0:
            do_startu += 24 * 60
        if not (RADAR_MIN - 6 <= do_startu <= RADAR_MIN + 6):
            continue
        klucz = f"{teraz_ny.date()}|{kz}"
        if stan.get("radary", {}).get(klucz):
            return False
        hdir = trend_1h(df)
        if hdir == 0:
            print("radar: brak wyraznego trendu 1h, pomijam")
            return False
        kierunek = "LONG" if hdir == 1 else "SHORT"
        h, l = df["High"].values, df["Low"].values
        sh, sl_pts = swingi(h[-150:], l[-150:])
        a = float(atr(df).iloc[-1])
        start_pl, koniec_pl = okno_pl(kz, teraz_ny)
        cena = float(df["Close"].iloc[-1])
        tv = ocena_tv()
        png = W.radar_png(df, NAZWA, kierunek, start_pl, koniec_pl,
                          sh[-1] if sh else None, sl_pts[-1] if sl_pts else None,
                          MIN_RISK_ATR * a, TZ_PL)
        tekst = tekst_radaru(kierunek, cena, sh[-1] if sh else None,
                             sl_pts[-1] if sl_pts else None, start_pl, koniec_pl,
                             kz, MIN_RISK_ATR * a, tv)
        tg_zdjecie(png, tekst)
        stan.setdefault("radary", {})[klucz] = teraz_pl().isoformat()
        return True
    return False


def obsluz_sygnal(df, stan, teraz_ny):
    dzis = teraz_pl().date().isoformat()
    licznik = stan.get("licznik", {})
    if licznik.get("dzien") != dzis:
        licznik = {"dzien": dzis, "n": 0}
    if licznik["n"] >= MAX_DZIENNIE:
        print(f"limit {MAX_DZIENNIE} sygnalow na dzis wyczerpany")
        stan["licznik"] = licznik
        return False

    ost = stan.get("ostatni_sygnal")
    if ost:
        try:
            minelo = (dt.datetime.now(dt.timezone.utc)
                      - dt.datetime.fromisoformat(ost)).total_seconds() / 60
            if minelo < COOLDOWN_MIN:
                print(f"pauza po ostatnim sygnale ({minelo:.0f}/{COOLDOWN_MIN} min)")
                return False
        except Exception:
            pass

    zamkniete = df.iloc[:-1]              # ostatnia swieca jeszcze sie tworzy
    setup = znajdz_setup(zamkniete, teraz_ny)
    if not setup:
        print("brak setupu na ostatniej zamknietej swiecy")
        return False
    klucz = setup["swieca"].isoformat()
    if klucz in stan.get("wyslane_swiece", []):
        print("ta swieca byla juz wyslana")
        return False

    tv = ocena_tv()
    # filtr TradingView: nie gramy wbrew ocenie rynku, ale gdy TV milczy - gramy
    if tv:
        zgodne = (tv["ocena"].replace("STRONG_", "") ==
                  ("BUY" if setup["strona"] == "LONG" else "SELL"))
        if not zgodne:
            print(f"wstrzymany - TradingView pokazuje {tv['ocena']}")
            stan.setdefault("wyslane_swiece", []).append(klucz)
            return True
    delta = spot_korekta(df, tv)
    licznik["n"] += 1
    png = W.sygnal_png(zamkniete, NAZWA, setup["strona"],
                       setup["wejscie"] + (delta or 0), setup["sl"] + (delta or 0),
                       setup["tp"] + (delta or 0), setup["swieca"],
                       setup["strategia"],
                       tuple(x + (delta or 0) for x in setup["luka"]) if setup["luka"] else None,
                       TZ_PL)
    tg_zdjecie(png, tekst_sygnalu(setup, tv, delta, licznik["n"]))
    zapisz_log(setup, tv)
    stan.setdefault("wyslane_swiece", []).append(klucz)
    stan["wyslane_swiece"] = stan["wyslane_swiece"][-200:]
    stan["ostatni_sygnal"] = dt.datetime.now(dt.timezone.utc).isoformat()
    stan["licznik"] = licznik
    stan.setdefault("otwarte", []).append({
        "czas": dt.datetime.now(dt.timezone.utc).isoformat(),
        "strona": setup["strona"], "wejscie": setup["wejscie"],
        "sl": setup["sl"], "tp": setup["tp"], "strategia": setup["strategia"],
        "swieca": klucz})
    print(f"WYSLANO {setup['strona']} ({setup['strategia']})")
    return True


def rozlicz(df, stan):
    """Sprawdza otwarte sygnaly: czy trafily cel, czy stop. Uczy na wyniku."""
    otwarte = stan.get("otwarte", [])
    if not otwarte:
        return False
    zostaja, zmiana = [], False
    for poz in otwarte:
        try:
            t0 = pd.to_datetime(poz["swieca"], utc=True)
            po = df[df.index > t0]
            if po.empty:
                zostaja.append(poz)
                continue
            lng = poz["strona"] == "LONG"
            status = None
            for hi, lo in zip(po["High"].values, po["Low"].values):
                if (lo <= poz["sl"]) if lng else (hi >= poz["sl"]):
                    status = ("STOP", -1.0)
                    break
                if (hi >= poz["tp"]) if lng else (lo <= poz["tp"]):
                    status = ("CEL", RR_TP)
                    break
            if not status:
                zostaja.append(poz)
                continue
            nazwa, r = status
            stan["bilans"] = round(stan.get("bilans", 0.0) + r, 2)
            ikona = "\U0001F48E" if r > 0 else "\U0001F534"
            nauka = ("Cel osiagniety. Tak wygladaja te 40% przypadkow, "
                     "ktore pokrywaja straty z pozostalych."
                     if r > 0 else
                     "Stop zadzialal - i dobrze. Jedna strata to -1R; "
                     "jeden trafiony cel oddaje trzy takie straty.")
            tg_tekst(f"{ikona} {nazwa}: {poz['strona']} {poz['strategia']} "
                     f"({r:+.0f}R)\n{nauka}\n"
                     f"Bilans od startu: {stan['bilans']:+.1f}R" + stopka())
            zmiana = True
        except Exception as e:
            print("rozliczenie blad:", e)
            zostaja.append(poz)
    stan["otwarte"] = zostaja
    return zmiana


def main():
    _wczytaj_tajne()
    if not DRY_RUN and (not TOKEN or not CHAT_ID):
        print("Brak TG_TOKEN / TG_CHAT_ID - ustaw zmienne albo tajne.json")
        return
    stan = wczytaj_stan()
    df = pobierz()
    if df.empty or len(df) < EMA_LEN + 20:
        print("brak danych rynkowych")
        return
    teraz_ny = dt.datetime.now(TZ_NY)
    print(f"{teraz_pl().strftime('%d.%m %H:%M')} PL · {len(df)} swiec · "
          f"cena {float(df['Close'].iloc[-1]):,.2f} · trend 1h "
          f"{'wzrostowy' if trend_1h(df) == 1 else 'spadkowy'}")

    zmiana = False
    zmiana |= bool(obsluz_radar(df, stan, teraz_ny))
    if w_killzone(teraz_ny):
        zmiana |= bool(obsluz_sygnal(df, stan, teraz_ny))
    else:
        print("poza oknem handlowym - tylko obserwuje")
    zmiana |= bool(rozlicz(df, stan))
    if zmiana:
        zapisz_stan(stan)


if __name__ == "__main__":
    main()
