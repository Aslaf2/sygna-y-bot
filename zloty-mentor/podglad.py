"""Podglad tego, co bot wysyla - bez wysylania czegokolwiek na Telegram.

Przechodzi po danych historycznych, znajduje prawdziwy setup i generuje
oba obrazki (radar + sygnal) razem z trescia wiadomosci. Sluzy do sprawdzenia,
jak wyglada produkt, zanim pojdzie do uzytkownika.

Uruchomienie:  python podglad.py
"""
import datetime as dt
import os
import pickle
import sys

import pandas as pd
import yfinance as yf

import mentor as M
import wykres as W

KAT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(KAT, "dane_podglad.pkl")


def dane():
    if os.path.exists(CACHE):
        with open(CACHE, "rb") as f:
            return pickle.load(f)
    df = yf.download(M.RYNEK, period="30d", interval="5m", progress=False,
                     auto_adjust=False, threads=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna()
    with open(CACHE, "wb") as f:
        pickle.dump(df, f)
    return df


def main():
    df = dane()
    print(f"Dane: {len(df)} swiec, {df.index[0]} -> {df.index[-1]}\n")

    # 1) szukamy prawdziwego setupu w historii, idac od najnowszych
    znaleziony = None
    for i in range(len(df) - 1, M.EMA_LEN + 40, -1):
        sub = df.iloc[:i + 1]
        ny = sub.index[-1].to_pydatetime().astimezone(M.TZ_NY)
        s = M.znajdz_setup(sub, ny)
        if s:
            znaleziony = (s, sub)
            break
    if not znaleziony:
        print("Nie znalazlem setupu w tym zakresie danych.")
        return
    setup, sub = znaleziony
    czas_pl = setup["swieca"].astimezone(M.TZ_PL)
    print("=" * 74)
    print(f"ZNALEZIONY SETUP: {setup['strona']} {setup['strategia']}  "
          f"({czas_pl.strftime('%d.%m %H:%M')} PL)")
    print("=" * 74)

    # 2) radar - tak, jak wygladalby 30 min przed oknem tego dnia
    ny = setup["swieca"].astimezone(M.TZ_NY)
    kz = [h for h in M.KILLZONE if h <= ny.hour < h + 1][0]
    przed = sub[sub.index <= setup["swieca"] - dt.timedelta(
        minutes=ny.minute + M.RADAR_MIN)]
    h, l = przed["High"].values, przed["Low"].values
    sh, sl_pts = M.swingi(h[-150:], l[-150:])
    a = float(M.atr(przed).iloc[-1])
    start_pl, koniec_pl = M.okno_pl(kz, przed.index[-1].to_pydatetime().astimezone(M.TZ_NY))
    kier = "LONG" if M.trend_1h(przed) == 1 else "SHORT"

    t_radar = M.tekst_radaru(kier, float(przed["Close"].iloc[-1]),
                             sh[-1] if sh else None, sl_pts[-1] if sl_pts else None,
                             start_pl, koniec_pl, kz, M.MIN_RISK_ATR * a, None)
    print("\n" + t_radar + "\n")
    png = W.radar_png(przed, M.NAZWA, kier, start_pl, koniec_pl,
                      sh[-1] if sh else None, sl_pts[-1] if sl_pts else None,
                      M.MIN_RISK_ATR * a, M.TZ_PL)
    open(os.path.join(KAT, "podglad_radar.png"), "wb").write(png)

    # 3) sygnal
    t_sygnal = M.tekst_sygnalu(setup, None, None, 1)
    print("=" * 74)
    print(t_sygnal + "\n")
    png2 = W.sygnal_png(sub, M.NAZWA, setup["strona"], setup["wejscie"],
                        setup["sl"], setup["tp"], setup["swieca"],
                        setup["strategia"], setup["luka"], M.TZ_PL)
    open(os.path.join(KAT, "podglad_sygnal.png"), "wb").write(png2)

    # 4) jak ten setup sie skonczyl
    po = df[df.index > setup["swieca"]]
    lng = setup["strona"] == "LONG"
    wynik = "jeszcze otwarty"
    for hi, lo in zip(po["High"].values, po["Low"].values):
        if (lo <= setup["sl"]) if lng else (hi >= setup["sl"]):
            wynik = "STOP (-1R)"
            break
        if (hi >= setup["tp"]) if lng else (lo <= setup["tp"]):
            wynik = "CEL (+3R)"
            break
    print("=" * 74)
    print(f"Jak sie skonczyl w rzeczywistosci: {wynik}")
    print("Zapisano: podglad_radar.png, podglad_sygnal.png")


if __name__ == "__main__":
    main()
