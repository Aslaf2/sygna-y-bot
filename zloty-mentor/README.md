# Złoty Mentor

Bot, który obserwuje złoto przez całą dobę, uprzedza Cię **30 minut przed** otwarciem
okna handlowego, a gdy setup faktycznie powstanie — wysyła sygnał z wykresem
i wyjaśnieniem, dlaczego akurat ten.

## Co dostajesz na Telegramie

**1. RADAR — 30 minut przed oknem**

Wiadomość + wykres: w którą stronę wskazuje trend godzinowy, jakich poziomów
pilnować, gdzie trafi stop-loss i **o której zamykają się świece**, na których
szukamy wejścia. Żółty pas na wykresie to okno handlowe.

**2. SYGNAŁ — w momencie wejścia**

Wejście, stop, cel (3R), wykres ze strzałką wskazującą świecę, która dała
sygnał, zaznaczona luka FVG oraz krótkie wyjaśnienie strategii.

**3. ROZLICZENIE — gdy pozycja się zamknie**

Wynik w R, bieżący bilans i jedno zdanie nauki.

## Okna handlowe (czas polski)

| Okno | Radar | Sesja |
|---|---|---|
| 09:00–10:00 | 08:30 | Londyn — historycznie najlepsze dla złota |
| 20:00–21:00 | 19:30 | popołudnie w Nowym Jorku |
| 02:00–03:00 | 01:30 | otwarcie Tokio |

Poza tymi oknami bot milczy. Nocne okno można wyłączyć — usuń `20` z listy
`KILLZONE` w `mentor.py`.

## Na czym opiera się selekcja

Bot rozpoznaje cztery układy (Silver Bullet, Liquidity Sweep, Order Block + FVG,
OTE) i przepuszcza sygnał dopiero, gdy przejdzie przez cztery sita:

1. **okno handlowe** — poza nim nie gramy,
2. **zgodność z trendem godzinowym** — nie wchodzimy pod prąd,
3. **ryzyko ≥ 3× ATR** — stop musi stać poza szumem,
4. **impet** — świeca musi zamknąć się przy właściwej krawędzi zakresu.

Plus filtr oceny TradingView i twardy limit 3 sygnałów dziennie.

## Wyniki testów (60 dni, świece 5m, spread wliczony)

| Wariant | Sygnałów | Wynik | Trafność |
|---|---|---|---|
| sam silnik, bez nowych sit | 120 | +0,47R/sygnał | 37% |
| + ryzyko ≥ 3 ATR | 97 | +0,69R/sygnał | 42% |
| + impet (wersja wdrożona) | 83 | +0,69R/sygnał | 42% |

Odsiane sygnały (ryzyko poniżej 3 ATR) dawały **−0,48R każdy** — oba sita
sprawdzono także na srebrze i Hang Sengu, czyli rynkach, na których ich
nie szukałem, i tam również pomagają.

**Uczciwie o ograniczeniach:** druga połowa próbki jest wyraźnie słabsza
(+0,33R/sygnał wobec +1,05R w pierwszej). Trafność 42% oznacza, że **większość
pojedynczych sygnałów to straty** — zarabia dopiero seria, bo jeden trafiony
cel (+3R) pokrywa trzy stopy. To przewaga statystyczna, nie pewność.
Testy to nie obietnica przyszłych wyników.

## Uruchomienie

```bash
pip install -r requirements.txt
```

Token Telegrama podaj przez zmienne środowiskowe `TG_TOKEN` i `TG_CHAT_ID`
albo w pliku `tajne.json` (nie trafia do repozytorium):

```json
{"TG_TOKEN": "...", "TG_CHAT_ID": "..."}
```

Potem:

```bash
python mentor.py
```

Bot wykonuje **jeden skan** i kończy pracę — do działania 24/7 trzeba go
uruchamiać co kilka minut (GitHub Actions albo `petla.bat` na własnym komputerze).

## Podgląd bez wysyłania

```bash
python podglad.py
```

Znajduje prawdziwy setup w danych historycznych i zapisuje `podglad_radar.png`
oraz `podglad_sygnal.png` razem z treścią wiadomości — nic nie idzie na Telegram.

## Pliki

| Plik | Rola |
|---|---|
| `mentor.py` | silnik: radar, rozpoznanie setupu, sygnał, rozliczenie |
| `wykres.py` | rysowanie obrazków wysyłanych na Telegram |
| `podglad.py` | podgląd wiadomości bez wysyłki |
| `stan_mentor.json` | pamięć bota (wysłane radary, otwarte pozycje, bilans) |
| `sygnaly_mentor.csv` | historia sygnałów do późniejszej oceny |

To narzędzie edukacyjne. Nie jest poradą inwestycyjną.
