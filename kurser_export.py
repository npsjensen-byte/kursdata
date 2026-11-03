"""Daglig kurs-eksport til Drive-porteføljen (de planlagte Claude-kørsler).

Henter daglige kurser (OHLCV, ujusterede) for de 50 største selskaber i Nasdaq-100
og QQQ, beregner RSI(14) og breakout-niveauer og skriver det hele til
kurser/nasdaq50.json. Workflowet .github/workflows/kurser.yml committer filen,
og de planlagte kørsler læser den fra repoet.

Rører ikke paper.db eller Trading Desk-strategierne.

    python kurser_export.py            # skriver kurser/nasdaq50.json
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf

HER = os.path.dirname(os.path.abspath(__file__))
UD = os.path.join(HER, 'kurser', 'nasdaq50.json')
ANTAL = 50
DAGE = 60          # rækker pr. ticker i filen
BENCHMARK = 'QQQ'

# Reserve hvis Nasdaqs liste ikke kan hentes: seneste kendte top-50 (efter markedsværdi).
RESERVE_UNIVERS = [
    'NVDA', 'GOOGL', 'AAPL', 'MSFT', 'AMZN', 'AVGO', 'TSLA', 'META', 'MU', 'WMT',
    'AMD', 'ASML', 'INTC', 'CSCO', 'COST', 'LRCX', 'ARM', 'PLTR', 'AMAT', 'NFLX',
    'TXN', 'QCOM', 'KLAC', 'LIN', 'PANW', 'ADI', 'STX', 'TMUS', 'PEP', 'APP',
    'WDC', 'AMGN', 'CRWD', 'MRVL', 'GILD', 'ISRG', 'SHOP', 'HON', 'BKNG', 'PDD',
    'SBUX', 'VRTX', 'CEG', 'CDNS', 'MAR', 'ADBE', 'FTNT', 'SNPS', 'CMCSA', 'ADP',
]
_BROWSER = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/141.0 Safari/537.36',
            'Accept': 'application/json'}


def hent_univers() -> tuple[list[str], str]:
    """Top-50 i Nasdaq-100 efter markedsværdi. GOOG springes over (GOOGL er med)."""
    try:
        r = requests.get('https://api.nasdaq.com/api/quote/list-type/nasdaq100',
                         headers=_BROWSER, timeout=30)
        r.raise_for_status()
        rows = r.json()['data']['data']['rows']

        def mcap(row):
            try:
                return float(str(row.get('marketCap', '0')).replace(',', '').replace('$', ''))
            except ValueError:
                return 0.0
        rows = sorted(rows, key=mcap, reverse=True)
        tickers = [row['symbol'].strip().upper().replace('.', '-') for row in rows]
        tickers = [t for t in tickers if t != 'GOOG']
        if len(tickers) >= ANTAL:
            return tickers[:ANTAL], 'nasdaq'
    except Exception as e:  # noqa: BLE001
        print(f'Nasdaq-listen fejlede ({e}) - bruger reservelisten', file=sys.stderr)
    return RESERVE_UNIVERS[:ANTAL], 'reserve'


def rsi_wilder(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    op = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    ned = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = op / ned
    return 100 - 100 / (1 + rs)


def fjern_ufaerdig_dag(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    ny = datetime.now(ZoneInfo('America/New_York'))
    if df.index[-1].date() >= ny.date() and (ny.hour, ny.minute) < (16, 15):
        return df.iloc[:-1]
    return df


def r2(x) -> float | None:
    return None if pd.isna(x) else round(float(x), 2)


def byg_ticker(df: pd.DataFrame) -> dict | None:
    df = fjern_ufaerdig_dag(df.dropna(subset=['Close']))
    if len(df) < 25:
        return None
    rsi = rsi_wilder(df['Close'])
    high20_prev = df['Close'].shift(1).rolling(20).max()
    vol_prev = df['Volume'].shift(1)
    rows = []
    for dato, r in df.iloc[-DAGE:].iterrows():
        j = df.index.get_loc(dato)
        rows.append({
            'date': dato.strftime('%Y-%m-%d'),
            'open': r2(r['Open']), 'high': r2(r['High']), 'low': r2(r['Low']),
            'close': r2(r['Close']), 'volume': int(r['Volume']) if not pd.isna(r['Volume']) else None,
            'rsi14': r2(rsi.iloc[j]),
            'high20_prev': r2(high20_prev.iloc[j]),
            'volume_prev': int(vol_prev.iloc[j]) if not pd.isna(vol_prev.iloc[j]) else None,
        })
    return {'last_date': rows[-1]['date'], 'rows': rows}


def main() -> int:
    univers, kilde = hent_univers()
    alle = univers + [BENCHMARK]
    print(f'Univers ({kilde}): {len(univers)} tickers')
    raw = yf.download(alle, period='8mo', interval='1d', auto_adjust=False,
                      group_by='ticker', progress=False, threads=True)
    data, mangler = {}, []
    for t in alle:
        try:
            df = raw[t][['Open', 'High', 'Low', 'Close', 'Volume']]
        except KeyError:
            mangler.append(t)
            continue
        b = byg_ticker(df)
        if b is None:
            mangler.append(t)
        else:
            data[t] = b
    if BENCHMARK not in data:
        print('QQQ mangler - skriver ikke filen', file=sys.stderr)
        return 2
    as_of = data[BENCHMARK]['last_date']
    forældede = sorted(t for t, b in data.items() if b['last_date'] < as_of)
    ud = {
        'as_of': as_of,
        'source': 'yahoo (ujusterede kurser)',
        'universe_source': kilde,
        'universe': [t for t in univers if t in data],
        'missing': mangler,
        'stale': forældede,
        'benchmark': BENCHMARK,
        'tickers': data,
    }
    os.makedirs(os.path.dirname(UD), exist_ok=True)
    with open(UD, 'w', encoding='utf-8') as f:
        json.dump(ud, f, separators=(',', ':'))
    print(f'Skrev {UD}: data til {as_of}, {len(data)} tickers, mangler {mangler}, forældede {forældede}')
    # Mangler mere end 5 tickers, er noget galt - lad workflowet fejle (filen er stadig skrevet).
    return 1 if len(mangler) > 5 else 0


if __name__ == '__main__':
    sys.exit(main())
