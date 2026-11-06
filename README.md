# kursdata

Offentlige dagskurser til Nis' Drive-portefølje (paper trading, aldrig rigtige handler).

- `kurser/nasdaq50.json` – top-50 i Nasdaq-100 efter markedsværdi + QQQ: 60 dages ujusterede
  OHLCV, RSI(14) (Wilder), højeste luk de forrige 20 dage og gårsdagens volumen. Opdateres af
  `.github/workflows/kurser.yml` efter lukketid i USA (og en reserve om morgenen).
- `drive_portefolje.py` – fører porteføljen frem dag for dag ud fra kursfilen (kun standardbiblioteket).
  Workflowet kører det lige efter kurs-eksporten.
- `portefolje/trade-agent-portfolio.json` – porteføljens tilstand (sandheden). `portefolje/rapport.txt` –
  rapport fra seneste kørsel med nye handelsdage. Den planlagte Claude-kørsel "Trade agent (morgen)"
  kopierer filen til Google Drive-mappen "Trade Agent", så dashboardet kan læse den.

```
python3 drive_portefolje.py PORTEFØLJE.json kurser/nasdaq50.json UD.json
```

Regler: signal ved lukning (breakout over 20-dages high med stigende volumen, eller RSI krydser
op gennem 30), køb ved næste åbning, stop -2 %, mål +4 %, salg ved RSI > 70. Maks 5 positioner.

## Backtest: regnskabs-blackout

`backtest/regnskab_blackout.py` tester, om det hjælper at droppe nye køb, når aktien har regnskab
inden for de næste N handelsdage (5 år, samme regler som ovenfor). Hovedvariant N = 5 og
beslutningsregel er fastlagt på forhånd. Kører i `.github/workflows/backtest.yml` (push til
grenen `backtest-regnskab` eller manuelt) og skriver `backtest/regnskab-blackout.md`.
Ændrer intet i den daglige kørsel.
