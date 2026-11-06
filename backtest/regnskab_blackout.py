"""Backtest: hjælper det at undgå nye køb lige før regnskab?

Samme regler som drive_portefolje.py (breakout/RSI-signal ved lukning, køb ved næste åbning,
stop -2 %, mål +4 %, salg ved RSI > 70, maks 5 positioner, maks 2 nye pr. dag). Eneste forskel
mellem varianterne: et købssignal ved lukning på dag D droppes, hvis aktien har regnskabsreaktion
inden for de næste N handelsdage. Så tager næste kandidat pladsen.

Fastlagt FØR kørslen (så vi ikke vælger det bedste tal bagefter):
- Hovedvariant: N = 5 handelsdage (ca. én uge).
- N = 2 og N = 10 vises kun for at se, om retningen holder. De bruges ikke til at vælge N.
- Beslutningsregel: indfør reglen kun, hvis N = 5 giver højere Sharpe end baseline, max drawdown
  ikke er værre, og N = 5 slår baseline i mindst halvdelen af årene.

Kendte svagheder (står også i rapporten):
- Universet er dagens top-50 i Nasdaq-100 -> survivorship bias. Det rammer begge varianter ens,
  så forskellen mellem dem er mere troværdig end de absolutte afkast.
- Regnskabsdatoer kommer fra Yahoo og kan mangle for enkelte tickers. Mangler gør reglen svagere,
  ikke stærkere.

    python backtest/regnskab_blackout.py                 # henter data (kræver yfinance), skriver rapport
    python backtest/regnskab_blackout.py --valider PF KURSER FRA_DATO
        # genafspiller kursfilen fra FRA_DATO og sammenligner med porteføljens trade_log
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from bisect import bisect_left, bisect_right

STOP_PCT, MAAL_PCT = 0.98, 1.04
MAKS_POS, MAKS_NYE = 5, 2
HOVED_N = 5
VARIANTER = [0, 2, 5, 10]
HER = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------- motor (spejler drive_portefolje.py)

def simuler(rk: dict, univers: list[str], dage: list[str], blackout: int = 0,
            reaktion: dict | None = None, kapital: float = 100_000.0) -> dict:
    """rk[ticker][dato] = række med open/high/low/close/volume/rsi14/high20_prev/volume_prev.
    reaktion[ticker] = sorteret liste af handelsdage, hvor kursen reagerer på regnskab."""
    reaktion = reaktion or {}
    idx = {d: i for i, d in enumerate(dage)}
    kasse, positioner, ventende = kapital, [], []
    handler, kurve, blokeret = [], [], 0
    # forrige dags række pr. ticker (til RSI-kryds) - seedes med dagen før første simulerede dag
    foer = {}
    for t in univers:
        tidl = [d for d in rk.get(t, {}) if d < dage[0]]
        foer[t] = rk[t][max(tidl)] if tidl else None

    def egenkapital(dato: str, felt: str) -> float:
        v = kasse
        for p in positioner:
            r = rk.get(p['ticker'], {}).get(dato)
            v += p['shares'] * (r[felt] if r else p['last'])
        return v

    def luk(p: dict, dato: str, kurs: float, grund: str) -> None:
        nonlocal kasse
        kasse += p['shares'] * kurs
        handler.append({'ticker': p['ticker'], 'entry_date': p['entry_date'], 'exit_date': dato,
                        'entry_usd': p['entry_usd'], 'exit_usd': round(kurs, 2),
                        'pnl_pct': round((kurs / p['entry_usd'] - 1) * 100, 2),
                        'pnl_usd': p['shares'] * (kurs - p['entry_usd']), 'reason': grund,
                        'signal': p['signal']})

    def har_regnskab_snart(t: str, dato: str) -> bool:
        if blackout <= 0 or t not in reaktion:
            return False
        i = idx[dato]
        slut = dage[min(i + blackout, len(dage) - 1)]
        rl = reaktion[t]
        j = bisect_right(rl, dato)          # første reaktionsdag efter D
        return j < len(rl) and rl[j] <= slut

    for dato in dage:
        # 1. Køb ventende signaler til åbningskurs
        for t, typ in list(ventende):
            ventende.remove((t, typ))
            r = rk.get(t, {}).get(dato)
            if not r or not r['open']:
                continue
            if len(positioner) >= MAKS_POS or any(p['ticker'] == t for p in positioner):
                continue
            beloeb = min(egenkapital(dato, 'open') / MAKS_POS, kasse)
            if beloeb < kapital / 100:
                continue
            kurs = r['open']
            p = {'ticker': t, 'entry_usd': kurs, 'shares': round(beloeb / kurs, 6),
                 'stop_usd': round(kurs * STOP_PCT, 2), 'target_usd': round(kurs * MAAL_PCT, 2),
                 'entry_date': dato, 'last': kurs, 'signal': typ}
            kasse -= p['shares'] * kurs
            positioner.append(p)

        # 2. Stop, mål og RSI
        behold = []
        for p in positioner:
            r = rk.get(p['ticker'], {}).get(dato)
            if not r:
                behold.append(p)
            elif r['open'] <= p['stop_usd']:
                luk(p, dato, r['open'], 'stop (gap under stop)')
            elif r['open'] >= p['target_usd']:
                luk(p, dato, r['open'], 'mål (gap over mål)')
            elif r['low'] <= p['stop_usd']:
                luk(p, dato, p['stop_usd'], 'stop -2%')
            elif r['high'] >= p['target_usd']:
                luk(p, dato, p['target_usd'], 'mål +4%')
            elif r['rsi14'] is not None and r['rsi14'] > 70:
                luk(p, dato, r['close'], 'RSI > 70')
            else:
                p['last'] = r['close']
                behold.append(p)
        positioner = behold

        # 3. Nye signaler ved lukning
        ledige = MAKS_POS - len(positioner) - len(ventende)
        ejet = {p['ticker'] for p in positioner} | {t for t, _ in ventende}
        breakouts, vendinger = [], []
        for t in univers:
            r = rk.get(t, {}).get(dato)
            f, foer[t] = foer[t], (r or foer[t])
            if t in ejet or r is None or f is None:
                continue
            if (r['high20_prev'] and r['close'] > r['high20_prev'] and r['volume'] and r['volume_prev']
                    and r['volume'] > r['volume_prev']):
                breakouts.append((r['close'] / r['high20_prev'] - 1, t, 'breakout'))
            elif r['rsi14'] is not None and f['rsi14'] is not None and f['rsi14'] < 30 <= r['rsi14']:
                vendinger.append((r['rsi14'] - f['rsi14'], t, 'rsi30'))
        kandidater = sorted(breakouts, reverse=True) + sorted(vendinger, reverse=True)
        valgt = []
        for _, t, typ in kandidater:
            if len(valgt) >= max(0, min(MAKS_NYE, ledige)):
                break
            if har_regnskab_snart(t, dato):
                blokeret += 1
                continue
            valgt.append((t, typ))
        ventende.extend(valgt)

        kurve.append((dato, egenkapital(dato, 'close')))

    return {'handler': handler, 'kurve': kurve, 'blokeret': blokeret, 'kapital': kapital}


# ---------------------------------------------------------------- nøgletal

def noegletal(res: dict, reaktion: dict) -> dict:
    kurve, h = res['kurve'], res['handler']
    v = [e for _, e in kurve]
    afk = [v[i] / v[i - 1] - 1 for i in range(1, len(v))]
    snit = sum(afk) / len(afk)
    sd = math.sqrt(sum((a - snit) ** 2 for a in afk) / (len(afk) - 1)) if len(afk) > 1 else 0
    top, mdd = v[0], 0.0
    for e in v:
        top = max(top, e)
        mdd = min(mdd, e / top - 1)
    aar = len(v) / 252
    gevinst = sum(t['pnl_usd'] for t in h if t['pnl_usd'] > 0)
    tab = -sum(t['pnl_usd'] for t in h if t['pnl_usd'] < 0)

    def over_regnskab(t):
        rl = reaktion.get(t['ticker'], [])
        j = bisect_right(rl, t['entry_date'])
        return j < len(rl) and rl[j] <= t['exit_date']
    holdt = [t for t in h if over_regnskab(t)]
    return {
        'total_pct': (v[-1] / v[0] - 1) * 100,
        'cagr_pct': ((v[-1] / v[0]) ** (1 / aar) - 1) * 100 if aar > 0 else 0,
        'sharpe': snit / sd * math.sqrt(252) if sd else 0,
        'max_dd_pct': mdd * 100,
        'handler': len(h),
        'win_rate_pct': 100 * sum(t['pnl_usd'] > 0 for t in h) / len(h) if h else 0,
        'snit_pnl_pct': sum(t['pnl_pct'] for t in h) / len(h) if h else 0,
        'profit_factor': gevinst / tab if tab else float('inf'),
        'vaerste_pct': min((t['pnl_pct'] for t in h), default=0),
        'tab_over_2pct': sum(t['pnl_pct'] < -2.0 for t in h),
        'holdt_over_regnskab': len(holdt),
        'snit_pnl_over_regnskab': sum(t['pnl_pct'] for t in holdt) / len(holdt) if holdt else 0,
        'blokeret': res['blokeret'],
    }


def aarsafkast(kurve: list) -> dict:
    ud, start = {}, {}
    for d, e in kurve:
        y = d[:4]
        start.setdefault(y, e)
        ud[y] = e
    aar = sorted(ud)
    res, forr = {}, None
    for y in aar:
        b = forr if forr is not None else start[y]
        res[y] = (ud[y] / b - 1) * 100
        forr = ud[y]
    return res


# ---------------------------------------------------------------- data (kun i GitHub Actions)

def hent_data(univers: list[str], periode: str = '5y'):
    import pandas as pd
    import yfinance as yf
    sys.path.insert(0, os.path.dirname(HER))
    from kurser_export import rsi_wilder, r2  # samme RSI som live

    raw = yf.download(univers + ['QQQ'], period=periode, interval='1d', auto_adjust=False,
                      group_by='ticker', progress=False, threads=True)
    rk, mangler = {}, []
    for t in univers + ['QQQ']:
        try:
            df = raw[t][['Open', 'High', 'Low', 'Close', 'Volume']].dropna(subset=['Close'])
        except KeyError:
            mangler.append(t)
            continue
        if len(df) < 60:
            mangler.append(t)
            continue
        rsi = rsi_wilder(df['Close'])
        h20 = df['Close'].shift(1).rolling(20).max()
        vp = df['Volume'].shift(1)
        rk[t] = {}
        for j, (dato, r) in enumerate(df.iterrows()):
            rk[t][dato.strftime('%Y-%m-%d')] = {
                'open': r2(r['Open']), 'high': r2(r['High']), 'low': r2(r['Low']),
                'close': r2(r['Close']), 'volume': None if pd.isna(r['Volume']) else int(r['Volume']),
                'rsi14': r2(rsi.iloc[j]), 'high20_prev': r2(h20.iloc[j]),
                'volume_prev': None if pd.isna(vp.iloc[j]) else int(vp.iloc[j])}
    return rk, mangler


def hent_reaktionsdage(univers: list[str], dage: list[str]) -> tuple[dict, list]:
    """Regnskab før åbning -> reaktion samme dag. Efter lukning -> næste handelsdag.
    Ukendt tidspunkt -> begge dage (forsigtigt)."""
    import yfinance as yf
    ud, mangler = {}, []
    for t in univers:
        df = None
        for forsoeg in range(3):
            try:
                df = yf.Ticker(t).get_earnings_dates(limit=30)
                break
            except Exception as e:  # noqa: BLE001
                print(f'{t}: regnskabsdatoer fejlede ({e}) - forsøg {forsoeg + 1}', file=sys.stderr)
                time.sleep(5)
        if df is None or df.empty:
            mangler.append(t)
            continue
        sæt = set()
        for ts in df.index:
            ts = ts.tz_convert('America/New_York') if ts.tzinfo else ts
            d = ts.strftime('%Y-%m-%d')
            i = bisect_left(dage, d)            # første handelsdag >= d
            if i >= len(dage):
                continue
            samme = dage[i] == d
            naeste = dage[i + 1] if samme and i + 1 < len(dage) else dage[i]
            t_min = ts.hour * 60 + ts.minute
            if not samme:                       # weekend/helligdag -> næste handelsdag
                sæt.add(dage[i])
            elif t_min == 0:                    # tidspunkt ukendt
                sæt.update({dage[i], naeste})
            elif t_min < 9 * 60 + 30:           # før åbning
                sæt.add(dage[i])
            else:                               # efter lukning (eller i løbet af dagen)
                sæt.add(naeste)
        ud[t] = sorted(sæt)
        time.sleep(0.5)
    return ud, mangler


# ---------------------------------------------------------------- rapport

def rapport(resultater: dict, reaktion: dict, qqq: dict, dage: list, univers: list,
            mangler_kurs: list, mangler_regnskab: list) -> tuple[str, dict]:
    nt = {n: noegletal(r, reaktion) for n, r in resultater.items()}
    aa = {n: aarsafkast(r['kurve']) for n, r in resultater.items()}
    q_kurve = [(d, qqq[d]['close']) for d in dage if d in qqq]
    q_nt = noegletal({'kurve': q_kurve, 'handler': [], 'blokeret': 0}, {})
    q_aa = aarsafkast(q_kurve)

    b, h = nt[0], nt[HOVED_N]
    aar = sorted(aa[0])
    slaar = sum(aa[HOVED_N][y] > aa[0][y] for y in aar)
    ok_sharpe = h['sharpe'] > b['sharpe']
    ok_dd = h['max_dd_pct'] >= b['max_dd_pct']
    ok_aar = slaar >= math.ceil(len(aar) / 2)
    retning = all((nt[n]['sharpe'] > b['sharpe']) == ok_sharpe for n in VARIANTER if n)
    if ok_sharpe and ok_dd and ok_aar:
        dom = 'INDFØR: N = 5 opfylder alle tre krav.'
    else:
        dom = 'INDFØR IKKE: N = 5 opfylder ikke alle krav (' + ', '.join(
            k for k, ok in [('højere Sharpe', ok_sharpe), ('max drawdown ikke værre', ok_dd),
                            (f'slår baseline i mindst halvdelen af årene ({slaar}/{len(aar)})', ok_aar)]
            if not ok) + ' fejler).'

    def navn(n):
        return 'Baseline (live-regler)' if n == 0 else f'Blackout {n} dage' + (' ★' if n == HOVED_N else '')

    L = [f'# Backtest: ingen nye køb lige før regnskab',
         '',
         f'Periode: {dage[0]} – {dage[-1]} ({len(dage)} handelsdage). Univers: {len(univers)} tickers '
         f'(dagens top-50 i Nasdaq-100). Startkapital 100.000 (USD-regnet; valuta er ligegyldig for %).',
         '',
         f'## Dom',
         '',
         f'**{dom}**',
         '',
         'Robusthed (Sharpe mod baseline): ' + ', '.join(
             f'N = {n}: {"bedre" if nt[n]["sharpe"] > b["sharpe"] else "dårligere"}' for n in VARIANTER if n)
         + ('. Samme retning for alle N.' if retning else
            '. Retningen skifter med N, så resultatet er følsomt — stol mindre på det.'),
         '',
         '## Nøgletal',
         '',
         '| Variant | Total | CAGR | Sharpe | Max DD | Handler | Win rate | Snit/handel | Profit factor | Værste | Tab > 2 % | Holdt over regnskab | Snit for dem | Signaler blokeret |',
         '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for n in VARIANTER:
        x = nt[n]
        L.append(f"| {navn(n)} | {x['total_pct']:+.1f} % | {x['cagr_pct']:+.1f} % | {x['sharpe']:.2f} | "
                 f"{x['max_dd_pct']:.1f} % | {x['handler']} | {x['win_rate_pct']:.0f} % | "
                 f"{x['snit_pnl_pct']:+.2f} % | {x['profit_factor']:.2f} | {x['vaerste_pct']:+.1f} % | "
                 f"{x['tab_over_2pct']} | {x['holdt_over_regnskab']} | {x['snit_pnl_over_regnskab']:+.2f} % | "
                 f"{x['blokeret']} |")
    L.append(f"| QQQ (køb og hold) | {q_nt['total_pct']:+.1f} % | {q_nt['cagr_pct']:+.1f} % | "
             f"{q_nt['sharpe']:.2f} | {q_nt['max_dd_pct']:.1f} % | – | – | – | – | – | – | – | – | – |")
    L += ['', '## Afkast pr. år', '', '| År | ' + ' | '.join(navn(n) for n in VARIANTER) + ' | QQQ |',
          '|---|' + '---|' * (len(VARIANTER) + 1)]
    for y in aar:
        L.append(f'| {y} | ' + ' | '.join(f'{aa[n][y]:+.1f} %' for n in VARIANTER) + f' | {q_aa.get(y, 0):+.1f} % |')
    L += ['',
          '## Sådan læses det',
          '',
          '- **Tab > 2 %** = handler hvor kursen sprang forbi stoppet (gap). Det er præcis den risiko, reglen skal fjerne.',
          '- **Holdt over regnskab** = handler, der var åbne, da kursen reagerede på et regnskab.',
          '- Hovedvarianten (★, N = 5) og beslutningsreglen blev fastlagt før kørslen. N = 2 og 10 er kun et robusthedstjek.',
          '',
          '## Forbehold',
          '',
          '- **Survivorship bias:** universet er dagens største selskaber. Det puster de absolutte afkast op, '
          'men rammer begge varianter ens. Se på forskellen, ikke niveauet.',
          '- Ingen kurtage eller spread. Flere handler = mere overvurderet afkast.',
          f'- Regnskabsdatoer mangler for: {", ".join(mangler_regnskab) or "ingen"}. '
          'Reglen virker ikke for dem i testen.',
          f'- Kurser mangler for: {", ".join(mangler_kurs) or "ingen"}.',
          ]
    return '\n'.join(L) + '\n', {'noegletal': nt, 'aar': aa, 'qqq': q_nt, 'dom': dom}


# ---------------------------------------------------------------- kørsel

def koer() -> int:
    kurser = json.load(open(os.path.join(os.path.dirname(HER), 'kurser', 'nasdaq50.json'), encoding='utf-8'))
    univers = kurser['universe']
    print(f'Henter 5 års kurser for {len(univers)} tickers + QQQ ...')
    rk, mangler_kurs = hent_data(univers)
    if 'QQQ' not in rk:
        print('QQQ mangler', file=sys.stderr)
        return 2
    alle_dage = sorted(rk['QQQ'])
    dage = alle_dage[60:]                       # opvarmning til RSI og 20-dages high
    univers = [t for t in univers if t in rk]
    print('Henter regnskabsdatoer ...')
    reaktion, mangler_regnskab = hent_reaktionsdage(univers, alle_dage)
    print(f'Regnskabsdatoer for {len(reaktion)} tickers, mangler {mangler_regnskab}')
    if len(mangler_regnskab) > 10:
        print('::warning::Over 10 tickers mangler regnskabsdatoer - resultatet er svagt')

    resultater = {n: simuler(rk, univers, dage, n, reaktion) for n in VARIANTER}
    md, js = rapport(resultater, reaktion, rk['QQQ'], dage, univers, mangler_kurs, mangler_regnskab)
    with open(os.path.join(HER, 'regnskab-blackout.md'), 'w', encoding='utf-8') as f:
        f.write(md)
    with open(os.path.join(HER, 'regnskab-blackout.json'), 'w', encoding='utf-8') as f:
        json.dump(js, f, ensure_ascii=False, indent=1, default=str)
    print(md)
    return 0


def valider(pf_sti: str, k_sti: str, fra: str) -> int:
    """Genafspil kursfilen fra dagen efter `fra` og sammenlign med porteføljens trade_log."""
    pf = json.load(open(pf_sti, encoding='utf-8'))
    k = json.load(open(k_sti, encoding='utf-8'))
    rk = {t: {r['date']: r for r in d['rows']} for t, d in k['tickers'].items()}
    dage = sorted(d for d in rk[k['benchmark']] if d > fra)
    res = simuler(rk, k['universe'], dage, 0)
    mine = [(t['ticker'], t['entry_date'], t['exit_date'], t['pnl_pct']) for t in res['handler']]
    live = [(t['ticker'], t['entry_date'], t['exit_date'], t['pnl_pct']) for t in pf['trade_log']
            if t['entry_date'] > fra]
    print('Backtest:', mine)
    print('Live:    ', live)
    ok = sorted(mine) == sorted(live)
    print('OK - identiske handler' if ok else 'AFVIGELSE')
    return 0 if ok else 1


if __name__ == '__main__':
    if len(sys.argv) == 5 and sys.argv[1] == '--valider':
        sys.exit(valider(*sys.argv[2:]))
    sys.exit(koer())
