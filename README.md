# kursdata

Offentlige dagskurser til Nis' Drive-portefølje (paper trading, aldrig rigtige handler).

- `kurser/nasdaq50.json` – top-50 i Nasdaq-100 efter markedsværdi + QQQ: 60 dages ujusterede
  OHLCV, RSI(14) (Wilder), højeste luk de forrige 20 dage og gårsdagens volumen. Opdateres af
  `.github/workflows/kurser.yml` efter lukketid i USA (og en reserve om morgenen).
- `drive_portefolje.py` – fører `trade-agent-portfolio.json` frem dag for dag ud fra kursfilen.
  Kun standardbiblioteket. Bruges af den planlagte Claude-kørsel "Trade agent (morgen)".

```
python3 drive_portefolje.py PORTEFØLJE.json kurser/nasdaq50.json UD.json
```

Regler: signal ved lukning (breakout over 20-dages high med stigende volumen, eller RSI krydser
op gennem 30), køb ved næste åbning, stop -2 %, mål +4 %, salg ved RSI > 70. Maks 5 positioner.
