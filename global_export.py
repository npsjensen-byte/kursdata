"""Global kurs-eksport til mobilsiden "Trade Agent Nordnet".

Henter daglige kurser for et globalt univers af aktier, der kan handles hos Nordnet:
top-50 i Nasdaq-100 (automatisk) + listen i univers_global.json (USA, Norden, Europa,
Kina og Asien via USA-noterede ADR'er) + regionale benchmarks og valutakurser til DKK.
Skriver kurser/global.json. Rører ikke kurser/nasdaq50.json eller papirporteføljen.

    python global_export.py            # skriver kurser/global.json

Exit-koder: 0 = ok, 1 = skrevet men mange tickers mangler (advarsel), 2 = intet skrevet.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf

from kurser_export import RESERVE_UNIVERS, rsi_wilder

HER = os.path.dirname(os.path.abspath(__file__))
UD = os.path.join(HER, 'kurser', 'global.json')
KONFIG = os.path.join(HER, 'univers_global.json')
ANTAL_NASDAQ = 50
DAGE = 40           # rækker pr. ticker i filen (nok til 30-dages graf og signaler)
COLS = ['date', 'open', 'high', 'low', 'close', 'volume', 'rsi14', 'high20_prev', 'volume_prev', 'sma50']

# Suffiks -> (Nordnet-marked, valuta, tidszone, (time, minut) hvor dagens luk er endeligt)
MARKEDER = {
    '':    ('USA', 'USD', 'America/New_York', (16, 15)),
    '.CO': ('København', 'DKK', 'Europe/Copenhagen', (17, 15)),
    '.ST': ('Stockholm', 'SEK', 'Europe/Stockholm', (17, 45)),
    '.OL': ('Oslo', 'NOK', 'Europe/Oslo', (16, 35)),
    '.HE': ('Helsinki', 'EUR', 'Europe/Helsinki', (18, 45)),
    '.DE': ('Tyskland', 'EUR', 'Europe/Berlin', (17, 45)),
    '.AS': ('Amsterdam', 'EUR', 'Europe/Amsterdam', (17, 45)),
    '.PA': ('Paris', 'EUR', 'Europe/Paris', (17, 45)),
    '.SW': ('Schweiz', 'CHF', 'Europe/Zurich', (17, 45)),
    '.MI': ('Milano', 'EUR', 'Europe/Rome', (17, 45)),
    '.MC': ('Madrid', 'EUR', 'Europe/Madrid', (17, 45)),
    '.L':  ('London', 'GBp', 'Europe/London', (16, 45)),
}
INDEKS = {  # indeks-tickers uden suffiks, der ikke handles i USA
    '^STOXX50E': ('Indeks', 'EUR', 'Europe/Berlin', (18, 15)),
    '^OMXC25': ('Indeks', 'DKK', 'Europe/Copenhagen', (17, 15)),
}
FX_TICKERS = {'USD': 'USDDKK=X', 'EUR': 'EURDKK=X', 'CHF': 'CHFDKK=X',
              'GBP': 'GBPDKK=X', 'SEK': 'SEKDKK=X', 'NOK': 'NOKDKK=X'}
_BROWSER = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/141.0 Safari/537.36',
            'Accept': 'application/json'}


def marked_for(t: str) -> tuple[str, str, str, tuple[int, int]]:
    if t in INDEKS:
        return INDEKS[t]
    if '.' in t:
        suf = '.' + t.rsplit('.', 1)[1]
        if suf in MARKEDER:
            return MARKEDER[suf]
    return MARKEDER['']


def rens_navn(navn: str) -> str:
    navn = re.split(r'\s+(Common Stock|Class [A-C]|Ordinary Shares|American Depositary|Common Shares|'
                    r'Capital Stock|Series [A-C])', navn)[0]
    navn = re.sub(r',?\s+(Inc\.?|Corporation|Corp\.?|Incorporated|Holdings?,? Inc\.?|plc|N\.V\.|Ltd\.?)$', '',
                  navn.strip())
    return navn.strip(' ,') or navn


def hent_nasdaq() -> tuple[list[str], dict[str, str], str]:
    """Top-50 i Nasdaq-100 efter markedsværdi med navne. GOOG springes over (GOOGL er med)."""
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
        tickers, navne = [], {}
        for row in rows:
            t = row['symbol'].strip().upper().replace('.', '-')
            if t == 'GOOG':
                continue
            tickers.append(t)
            navne[t] = rens_navn(row.get('companyName') or t)
        if len(tickers) >= ANTAL_NASDAQ:
            return tickers[:ANTAL_NASDAQ], navne, 'nasdaq'
    except Exception as e:  # noqa: BLE001
        print(f'Nasdaq-listen fejlede ({e}) - bruger reservelisten', file=sys.stderr)
    return RESERVE_UNIVERS[:ANTAL_NASDAQ], {}, 'reserve'


def fjern_ufaerdig_dag(df: pd.DataFrame, tz: str, faerdig: tuple[int, int]) -> pd.DataFrame:
    """Fjern dagens række, hvis børsen ikke er lukket endnu i sin egen tidszone."""
    if df.empty:
        return df
    nu = datetime.now(ZoneInfo(tz))
    if df.index[-1].date() >= nu.date() and (nu.hour, nu.minute) < faerdig:
        return df.iloc[:-1]
    return df


def rp(x) -> float | None:
    """Afrund kurser: 2 decimaler, 3 for kurser under 10, 4 under 1."""
    if x is None or pd.isna(x):
        return None
    x = float(x)
    d = 4 if abs(x) < 1 else 3 if abs(x) < 10 else 2
    return round(x, d)


def iv(x) -> int | None:
    return None if x is None or pd.isna(x) else int(x)


def byg(df: pd.DataFrame, tz: str, faerdig: tuple[int, int]) -> tuple[dict | None, str | None]:
    df = df.dropna(subset=['Close'])
    df = df[df['Close'] > 0]
    df = fjern_ufaerdig_dag(df, tz, faerdig)
    if len(df) < 25:
        return None, 'for få dage'
    c = df['Close']
    # Datafejl-vagt: et dagligt hop over x20 eller under /20 er næsten altid en enhedsfejl (fx GBp/GBP).
    hop = (c / c.shift(1)).iloc[-DAGE:]
    if ((hop > 20) | (hop < 0.05)).any():
        return None, 'kursspring (datafejl)'
    rsi = rsi_wilder(c)
    high20 = c.shift(1).rolling(20).max()
    vprev = df['Volume'].shift(1)
    sma50 = c.rolling(50).mean()
    rows = []
    for i in range(max(0, len(df) - DAGE), len(df)):
        r = df.iloc[i]
        rows.append([df.index[i].strftime('%Y-%m-%d'), rp(r['Open']), rp(r['High']), rp(r['Low']),
                     rp(r['Close']), iv(r['Volume']), rp(rsi.iloc[i]), rp(high20.iloc[i]),
                     iv(vprev.iloc[i]), rp(sma50.iloc[i])])
    oms = (c * df['Volume']).iloc[-20:].mean()
    return {'last': rows[-1][0], 'rows': rows, 'oms20': None if pd.isna(oms) else float(oms)}, None


def _uden_tz(df: pd.DataFrame) -> pd.DataFrame:
    if getattr(df.index, 'tz', None) is not None:
        df = df.copy()
        df.index = df.index.tz_localize(None)
    df.index = pd.to_datetime(df.index.date)
    return df


def opdater_enkeltvis(t: str, df: pd.DataFrame, maal: str) -> tuple[pd.DataFrame, str]:
    """Yahoo mangler ofte seneste dagsbar for europæiske aktier i samlet download.
    1) Prøv tickerens egen historik. 2) Ellers byg dagens bar ud fra kursoplysningerne
    (åbning, høj, lav, seneste kurs og volumen), hvis de er fra måldatoen."""
    tk = yf.Ticker(t)
    try:
        h = _uden_tz(tk.history(period='8mo', interval='1d', auto_adjust=False))
        h = h[['Open', 'High', 'Low', 'Close', 'Volume']].dropna(subset=['Close'])
        if not h.empty and h.index[-1].strftime('%Y-%m-%d') >= maal:
            return h, 'historik'
    except Exception as e:  # noqa: BLE001
        print(f'{t}: historik fejlede ({e})', file=sys.stderr)
    try:
        fi = tk.fast_info
        meta = tk.history_metadata or {}
        tid = meta.get('regularMarketTime')
        tz = meta.get('exchangeTimezoneName') or marked_for(t)[2]
        if tid is None:
            dato = None
        else:
            ts = pd.Timestamp(tid, unit='s') if isinstance(tid, (int, float)) else pd.Timestamp(tid)
            ts = ts.tz_localize('UTC') if ts.tzinfo is None else ts
            dato = ts.tz_convert(tz).strftime('%Y-%m-%d')
        sidst = df.dropna(subset=['Close']).index[-1].strftime('%Y-%m-%d')
        if dato == maal and dato > sidst and fi.get('lastPrice'):
            raekke = pd.DataFrame({'Open': [fi.get('open') or fi['lastPrice']],
                                   'High': [fi.get('dayHigh') or fi['lastPrice']],
                                   'Low': [fi.get('dayLow') or fi['lastPrice']],
                                   'Close': [fi['lastPrice']],
                                   'Volume': [fi.get('lastVolume') or 0]},
                                  index=pd.to_datetime([dato]))
            return pd.concat([df.dropna(subset=['Close']), raekke]), 'dagsbar fra kursoplysninger'
        return df, f'ingen nyere data (Yahoo-tid {dato})'
    except Exception as e:  # noqa: BLE001
        return df, f'kursoplysninger fejlede ({e})'


def hent_fx(gammel: dict) -> tuple[dict, str]:
    raw = yf.download(list(FX_TICKERS.values()), period='1mo', interval='1d',
                      auto_adjust=False, group_by='ticker', progress=False, threads=True)
    fx, datoer = {'DKK': 1.0}, []
    for val, t in FX_TICKERS.items():
        try:
            s = raw[t]['Close'].dropna()
            fx[val] = round(float(s.iloc[-1]), 5)
            datoer.append(s.index[-1].strftime('%Y-%m-%d'))
        except (KeyError, IndexError):
            if val in gammel.get('fx', {}):
                fx[val] = gammel['fx'][val]
                print(f'Valuta {val} mangler - bruger gårsdagens kurs', file=sys.stderr)
            else:
                raise RuntimeError(f'Valutakurs {val}/DKK mangler')
    fx['GBp'] = round(fx['GBP'] / 100, 7)
    return fx, max(datoer) if datoer else gammel.get('fx_date', '')


def main() -> int:
    konf = json.load(open(KONFIG, encoding='utf-8'))
    gammel = {}
    if os.path.exists(UD):
        try:
            gammel = json.load(open(UD, encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            gammel = {}

    nasdaq, nasdaq_navne, kilde = hent_nasdaq()
    dedupe = konf.get('dedupe', {})
    univers, info = [], {}
    for t in nasdaq:
        if t in dedupe or t in info:
            continue
        univers.append(t)
        info[t] = {'n': nasdaq_navne.get(t, t), 'r': 'USA'}
    for a in konf['aktier']:
        t = a['t']
        if t in info:
            info[t]['n'] = a['n']   # konfigurationens navn vinder
            continue
        univers.append(t)
        info[t] = {'n': a['n'], 'r': a['r']}
    bench = list(dict.fromkeys(konf['benchmarks'].values()))
    alle = univers + [b for b in bench if b not in info]
    print(f'Univers: {len(univers)} aktier ({kilde} + konfiguration), {len(bench)} benchmarks')

    fx, fx_date = hent_fx(gammel)

    raw = yf.download(alle, period='8mo', interval='1d', auto_adjust=False,
                      group_by='ticker', progress=False, threads=True)
    rå, mangler, fejl = {}, [], {}
    for t in alle:
        try:
            rå[t] = _uden_tz(raw[t][['Open', 'High', 'Low', 'Close', 'Volume']])
        except KeyError:
            mangler.append(t)

    # Seneste dag pr. ticker, og hvilken dag hvert marked burde være nået til
    # (nyeste dag blandt markedets aktier og dets regionale indeks).
    def sidste(t: str) -> str:
        m, _, tz, faerdig = marked_for(t)
        df = fjern_ufaerdig_dag(rå[t].dropna(subset=['Close']), tz, faerdig)
        return df.index[-1].strftime('%Y-%m-%d') if len(df) else ''
    sidst = {t: sidste(t) for t in rå}
    indeks_for = {'København': '^OMXC25', 'Stockholm': '^OMXC25', 'Oslo': '^OMXC25', 'Helsinki': '^OMXC25',
                  'USA': 'SPY'}
    maal = {}
    for t, d in sidst.items():
        m = marked_for(t)[0]
        maal[m] = max(maal.get(m, ''), d)
    for m in maal:
        ref = indeks_for.get(m, '^STOXX50E')
        maal[m] = max(maal[m], sidst.get(ref, ''))
    efterslaeb = [t for t, d in sidst.items() if t in info and d and d < maal[marked_for(t)[0]]]
    syntetisk = []
    if efterslaeb:
        print(f'{len(efterslaeb)} tickers mangler seneste dag i samlet download - henter enkeltvis')
        for t in efterslaeb:
            m = marked_for(t)[0]
            df, hvordan = opdater_enkeltvis(t, rå[t], maal[m])
            rå[t] = df
            if hvordan == 'dagsbar fra kursoplysninger':
                syntetisk.append(t)
            print(f'  {t}: {hvordan}')

    data = {}
    for t in alle:
        if t not in rå:
            continue
        marked, valuta, tz, faerdig = marked_for(t)
        b, grund = byg(rå[t], tz, faerdig)
        if b is None:
            mangler.append(t)
            fejl[t] = grund
            continue
        oms = b.pop('oms20')
        b.update({
            'n': info.get(t, {}).get('n', t),
            'r': info.get(t, {}).get('r', 'Indeks'),
            'm': marked, 'c': valuta,
            'oms20_dkk': None if oms is None else round(oms * fx[valuta]),
        })
        if t in syntetisk:
            b['synth'] = True
        data[t] = b

    andel_mangler = len(mangler) / len(alle)
    if 'SPY' not in data or 'ACWI' not in data or andel_mangler > 0.15:
        print(f'For mange mangler ({len(mangler)}/{len(alle)}: {mangler}) - skriver ikke filen', file=sys.stderr)
        return 2

    # Friskhed: en aktie er forældet, hvis den er bagud i forhold til sin egen regions nyeste dag.
    as_of_region = {}
    for t, b in data.items():
        as_of_region[b['r']] = max(as_of_region.get(b['r'], ''), b['last'])
    stale = sorted(t for t, b in data.items() if b['last'] < as_of_region[b['r']])

    regioner = ['USA', 'Norden', 'Europa', 'Kina', 'Asien', 'Latinamerika', 'ETF']
    ud = {
        'version': 2,
        'generated_utc': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%MZ'),
        'as_of': max(as_of_region.values()),
        'as_of_region': as_of_region,
        'source': 'yahoo (ujusterede dagskurser)',
        'universe_source': kilde,
        'cols': COLS,
        'fx': fx,
        'fx_date': fx_date,
        'regions': [r for r in regioner if any(b['r'] == r for b in data.values())],
        'benchmarks': {k: v for k, v in konf['benchmarks'].items() if v in data},
        'region_benchmark': konf.get('region_benchmark', {}),
        'mine': [t for t in konf.get('mine', []) if t in data],
        'universe': [t for t in univers if t in data],
        'missing': mangler,
        'missing_reason': fejl,
        'stale': stale,
        'synthetic_last_bar': sorted(syntetisk),
        'tickers': data,
    }
    os.makedirs(os.path.dirname(UD), exist_ok=True)
    tmp = UD + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(ud, f, ensure_ascii=False, separators=(',', ':'))
    os.replace(tmp, UD)
    print(f'Skrev {UD}: {len(ud["universe"])} aktier, data til {ud["as_of"]} {as_of_region}, '
          f'mangler {mangler}, forældede {stale}, fx {fx}')
    return 1 if len(mangler) > 8 else 0


if __name__ == '__main__':
    sys.exit(main())
