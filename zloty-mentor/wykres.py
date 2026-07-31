"""Rysowanie wykresu setupu - to, co ma zobaczyc uzytkownik na Telegramie.

Dwa rodzaje obrazkow:
  radar_png()  - sytuacja 30 minut PRZED oknem: gdzie jest cena, gdzie beda
                 poziomy, ktora swieca ma sie zamknac
  sygnal_png() - gotowy setup: swieca sygnalowa, wejscie, SL, TP, luka FVG

Wszystko rysowane recznie na matplotlib (bez mplfinance), bo potrzebna jest
pelna kontrola nad adnotacjami - obrazek ma UCZYC, nie tylko pokazywac ceny.
"""
import datetime as dt
import io

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np

# ciemny motyw, czytelny na telefonie
TLO      = "#12141a"
PANEL    = "#171a21"
SIATKA   = "#262b36"
TEKST    = "#e6e8ee"
SZARY    = "#8b93a7"
BYK      = "#26a69a"
NIEDZ    = "#ef5350"
ZLOTY    = "#e0b64a"
CZERWONY = "#ff6b6b"
ZIELONY  = "#4ade80"
NIEBIESKI = "#60a5fa"


def _osie(tytul, podtytul):
    fig, ax = plt.subplots(figsize=(11, 6.6), dpi=125)
    fig.patch.set_facecolor(TLO)
    ax.set_facecolor(PANEL)
    for s in ax.spines.values():
        s.set_color(SIATKA)
    ax.tick_params(colors=SZARY, labelsize=9)
    ax.grid(True, color=SIATKA, linewidth=0.6, alpha=0.55)
    ax.set_axisbelow(True)
    fig.suptitle(tytul, color=TEKST, fontsize=15, fontweight="bold",
                 x=0.012, ha="left", y=0.975)
    ax.set_title(podtytul, color=SZARY, fontsize=10, loc="left", pad=8)
    return fig, ax


def _swiece(ax, df, szer_min=5):
    """Rysuje swiece. szer_min = ile minut trwa jedna swieca."""
    szer = szer_min / (24 * 60) * 0.68
    for ts, row in df.iterrows():
        o, h, l, c = float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"])
        kolor = BYK if c >= o else NIEDZ
        x = mdates.date2num(ts)
        ax.plot([x, x], [l, h], color=kolor, linewidth=0.9, zorder=2)
        ax.add_patch(plt.Rectangle((x - szer / 2, min(o, c)), szer,
                                   max(abs(c - o), 1e-9), facecolor=kolor,
                                   edgecolor=kolor, linewidth=0.6, zorder=3))


def _poziom(ax, y, kolor, etykieta, x_tekst, styl="--", grubosc=1.4, alpha=1.0):
    ax.axhline(y, color=kolor, linestyle=styl, linewidth=grubosc, alpha=alpha, zorder=4)
    ax.annotate(etykieta, xy=(x_tekst, y), xytext=(6, 0), textcoords="offset points",
                color=kolor, fontsize=9.5, fontweight="bold", va="center",
                bbox=dict(boxstyle="round,pad=0.28", facecolor=TLO,
                          edgecolor=kolor, linewidth=0.9), zorder=6)


def _os_czasu(ax, tz):
    """Podzialka na pelnych polgodzinach - inaczej matplotlib liczy odstepy
    od pierwszej swiecy i na osi wychodza godziny w rodzaju 03:37."""
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=tz))
    ax.xaxis.set_major_locator(mdates.MinuteLocator(byminute=[0, 30], tz=tz))


def _stopka(fig, tekst):
    fig.text(0.012, 0.022, tekst, color=SZARY, fontsize=8.5, ha="left")
    fig.text(0.988, 0.022, "Zloty Mentor - nauka, nie porada inwestycyjna",
             color="#5c6478", fontsize=8, ha="right")


def _zapisz(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=TLO, bbox_inches="tight", pad_inches=0.28)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


# ------------------------------------------------------------------ RADAR

def radar_png(df5, nazwa, kierunek, kz_start, kz_koniec, swing_hi, swing_lo,
              min_ryzyko, tz_pl, n_swiec=70):
    """Obrazek wysylany 30 minut przed otwarciem okna.

    Pokazuje: gdzie stoi cena, gdzie leza poziomy, ktore beda stopem,
    oraz zacieniony pas czasu = okno, w ktorym szukamy wejscia.
    """
    d = df5.tail(n_swiec)
    cena = float(d["Close"].iloc[-1])
    strona = "SHORT (spadek)" if kierunek == "SHORT" else "LONG (wzrost)"
    kolor_k = CZERWONY if kierunek == "SHORT" else ZIELONY

    fig, ax = _osie(f"RADAR  ·  {nazwa}",
                    f"za 30 min otwiera sie okno {kz_start.strftime('%H:%M')}"
                    f"-{kz_koniec.strftime('%H:%M')} (czas PL)  ·  "
                    f"trend godzinowy wskazuje {strona}")
    _swiece(ax, d)

    x_end = mdates.date2num(kz_koniec + dt.timedelta(minutes=12))
    ax.set_xlim(mdates.date2num(d.index[0] - dt.timedelta(minutes=6)), x_end)

    # pas okna handlowego
    ax.axvspan(mdates.date2num(kz_start), mdates.date2num(kz_koniec),
               color=ZLOTY, alpha=0.10, zorder=1)
    ax.axvline(mdates.date2num(kz_start), color=ZLOTY, linewidth=1.6,
               linestyle="-", alpha=0.85, zorder=4)
    ax.annotate("START OKNA", xy=(mdates.date2num(kz_start), ax.get_ylim()[1]),
                xytext=(4, -14), textcoords="offset points", color=ZLOTY,
                fontsize=9.5, fontweight="bold", zorder=6)

    _poziom(ax, cena, TEKST, f"cena teraz  {cena:,.2f}", x_end, styl="-", grubosc=1.1)
    if swing_hi:
        _poziom(ax, swing_hi, SZARY, f"ostatni szczyt  {swing_hi:,.2f}", x_end, alpha=0.9)
    if swing_lo:
        _poziom(ax, swing_lo, SZARY, f"ostatni dolek  {swing_lo:,.2f}", x_end, alpha=0.9)

    # gdzie realnie stanie stop przy takim kierunku - opis zawsze do SRODKA
    # wykresu, zeby nie wyszedl poza obszar rysowania
    baza = swing_hi if kierunek == "SHORT" else swing_lo
    if baza:
        ax.annotate(f"tu trafi stop-loss ({kierunek})",
                    xy=(mdates.date2num(d.index[len(d) // 3]), baza),
                    xytext=(0, -16 if kierunek == "SHORT" else 8),
                    textcoords="offset points", color=kolor_k, fontsize=9,
                    fontweight="bold", zorder=6)

    _os_czasu(ax, tz_pl)
    _stopka(fig, f"Wejscie nastapi na ZAMKNIECIU swiecy 5-minutowej w zoltym oknie.  "
                 f"Minimalne ryzyko setupu: {min_ryzyko:,.2f}")
    return _zapisz(fig)


# ----------------------------------------------------------------- SYGNAL

def sygnal_png(df5, nazwa, side, entry, sl, tp, bar_ts, strategia, fvg, tz_pl,
               n_swiec=70):
    """Obrazek gotowego setupu: swieca sygnalowa + trzy poziomy + luka FVG."""
    d = df5.tail(n_swiec)
    kolor_k = ZIELONY if side == "LONG" else CZERWONY
    fig, ax = _osie(f"{'LONG' if side == 'LONG' else 'SHORT'}  ·  {nazwa}",
                    f"{strategia}  ·  swieca sygnalowa zamknieta "
                    f"{bar_ts.astimezone(tz_pl).strftime('%H:%M')} (czas PL)")
    _swiece(ax, d)

    x_end = mdates.date2num(d.index[-1] + dt.timedelta(minutes=26))
    ax.set_xlim(mdates.date2num(d.index[0] - dt.timedelta(minutes=6)), x_end)

    # obszary zysku i straty
    ax.axhspan(min(entry, tp), max(entry, tp), color=ZIELONY, alpha=0.07, zorder=1)
    ax.axhspan(min(entry, sl), max(entry, sl), color=CZERWONY, alpha=0.09, zorder=1)

    _poziom(ax, entry, NIEBIESKI, f"WEJSCIE  {entry:,.2f}", x_end, styl="-", grubosc=1.7)
    _poziom(ax, sl, CZERWONY, f"STOP  {sl:,.2f}   (-1R)", x_end)
    _poziom(ax, tp, ZIELONY, f"CEL  {tp:,.2f}   (+3R)", x_end)

    # luka FVG - miejsce, ktore uruchomilo sygnal
    if fvg:
        lo, hi = min(fvg), max(fvg)
        x0 = mdates.date2num(bar_ts - dt.timedelta(minutes=12))
        ax.add_patch(plt.Rectangle((x0, lo), mdates.date2num(d.index[-1]) - x0 + 0.004,
                                   hi - lo, facecolor=ZLOTY, alpha=0.20,
                                   edgecolor=ZLOTY, linewidth=1.0, zorder=2))
        ax.annotate("luka FVG", xy=(x0, hi), xytext=(-8, 16),
                    textcoords="offset points", color=ZLOTY, fontsize=9,
                    fontweight="bold", ha="right", zorder=6)

    # strzalka na swiece sygnalowa - opis ODSUNIETY w lewo, bo sama swieca
    # stoi tuz przy prawej krawedzi i napis nachodzilby na etykiety poziomow
    if bar_ts in d.index:
        row = d.loc[bar_ts]
        y = float(row["Low"]) if side == "LONG" else float(row["High"])
        znak = -1 if side == "LONG" else 1
        rozp = float(d["High"].max() - d["Low"].min())
        ax.annotate("TA swieca dala sygnal",
                    xy=(mdates.date2num(bar_ts), y),
                    xytext=(mdates.date2num(bar_ts - dt.timedelta(minutes=70)),
                            y - znak * rozp * 0.42),
                    color=kolor_k, fontsize=10, fontweight="bold", ha="center",
                    arrowprops=dict(arrowstyle="->", color=kolor_k, linewidth=1.8,
                                    connectionstyle="arc3,rad=0.15"),
                    zorder=7)

    _os_czasu(ax, tz_pl)
    ryzyko = abs(entry - sl)
    _stopka(fig, f"Ryzyko 1R = {ryzyko:,.2f}   ·   cel = 3R = {3*ryzyko:,.2f}   ·   "
                 f"jeden cel, jeden stop, bez przesuwania")
    return _zapisz(fig)
