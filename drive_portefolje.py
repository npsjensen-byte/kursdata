"""Fører Drive-porteføljen (trade-agent-portfolio.json) frem med kurserne i kurser/nasdaq50.json.

Bruges af de planlagte Claude-kørsler, så beregningerne er ens hver gang i stedet for
at blive regnet i hovedet. Kun standardbiblioteket - kører uden pip install.

    python3 drive_portefolje.py PORTEFØLJE.json KURSER.json UD.json

Exit-koder: 0 = ny fil skrevet, 3 = ingen nye handelsdage (intet skrevet), 2 = fejl i input.

Regler (simulering, aldrig rigtige handler). Hver handelsdag D efter "updated" behandles i rækkefølge:
1. Køb i pending_buys (signal ved forrige lukning) til D's åbningskurs. Ca. 1/5 af egenkapitalen,
   maks 5 positioner. Stop = entry x 0,98, mål = entry x 1,04.
2. Åbne positioner: åbner under stop / over mål -> luk til åbningskurs. Ellers low <= stop -> luk
   til stop (stop vinder, hvis begge rammes samme dag), high >= mål -> luk til mål,
   RSI(14) > 70 ved lukning -> luk til lukkekurs.
3. Nye signaler ved D's lukning (kun tickers i universet, ikke ejet/ventende, under 5 i alt):
   breakout = luk > højeste luk de forrige 20 dage OG volumen > gårsdagens; eller
   oversolgt vending = RSI(14) krydser op gennem 30. Maks 2 pr. dag, stærkeste breakout først.
4. History-punkt for D med egenkapital og QQQ-luk.
"""
from __future__ import annotations

import json
import re
import sys

STOP_PCT, MAAL_PCT = 0.98, 1.04
MAKS_POS, MAKS_NYE = 5, 2


def laes_json(sti: str) -> dict:
    tekst = open(sti, encoding='utf-8-sig').read().strip()
    try:
        return json.loads(tekst)
    except json.JSONDecodeError:
        # Google Docs-eksport er markdown-escapet: \_ \[ \] osv.
        return json.loads(re.sub(r'\\([_\[\]\*#~`>+\-.!|(){}])', r'\1', tekst))


def main(p_sti: str, k_sti: str, ud_sti: str) -> int:
    pf = laes_json(p_sti)
    kurser = laes_json(k_sti)
    tk = kurser['tickers']
    qqq = {r['date']: r for r in tk[kurser['benchmark']]['rows']}
    rk = {t: {r['date']: r for r in d['rows']} for t, d in tk.items()}
    fx = float(pf['fx_dkk_usd'])

    # Sidste behandlede handelsdag = nyeste history-punkt.
    sidst = max((h['date'] for h in pf.get('history', [])), default=pf['start_date'])
    dage = sorted(d for d in qqq if d > sidst)

    # Ret history-punkter, der ikke er lukkekurser (fx intradag-snapshots), til QQQ's faktiske luk.
    rettet = []
    for h in pf.get('history', []):
        if h['date'] in qqq and abs(h.get('qqq_close', 0) - qqq[h['date']]['close']) > 0.01:
            rettet.append(f"{h['date']}: QQQ {h.get('qqq_close')} -> {qqq[h['date']]['close']}")
            h['qqq_close'] = qqq[h['date']]['close']

    if not dage:
        print(f'INGEN NYE HANDELSDAGE: porteføljen er ført til {sidst}, kursfilen har data til {kurser["as_of"]}.')
        return 3

    univers = kurser['universe']
    log = []

    def egenkapital(dato: str, felt: str = 'close') -> float:
        v = pf['cash_dkk']
        for p in pf['positions']:
            r = rk.get(p['ticker'], {}).get(dato)
            kurs = r[felt] if r else p.get('last_close_usd') or p['entry_usd']
            v += p['shares'] * kurs * fx
        return v

    def luk(p: dict, dato: str, kurs: float, grund: str) -> None:
        pf['cash_dkk'] += p['shares'] * kurs * fx
        pnl = round((kurs / p['entry_usd'] - 1) * 100, 2)
        pf['trade_log'].append({'ticker': p['ticker'], 'entry_date': p['entry_date'], 'exit_date': dato,
                                'entry_usd': p['entry_usd'], 'exit_usd': round(kurs, 2),
                                'pnl_pct': pnl, 'reason': grund})
        log.append(f'{dato} SÆLG {p["ticker"]} @ {kurs:.2f} ({pnl:+.2f} %) - {grund}')

    for dato in dage:
        # 1. Køb ventende signaler til åbningskurs
        for t in list(pf['pending_buys']):
            pf['pending_buys'].remove(t)
            r = rk.get(t, {}).get(dato)
            if not r or not r['open']:
                log.append(f'{dato} KØB {t} droppet - ingen kurs')
                continue
            if len(pf['positions']) >= MAKS_POS or any(p['ticker'] == t for p in pf['positions']):
                continue
            beloeb = min(egenkapital(dato, 'open') / MAKS_POS, pf['cash_dkk'])
            if beloeb < 100:
                log.append(f'{dato} KØB {t} droppet - ikke nok kontanter')
                continue
            kurs = r['open']
            p = {'ticker': t, 'entry_usd': kurs, 'shares': round(beloeb / (kurs * fx), 4),
                 'stop_usd': round(kurs * STOP_PCT, 2), 'target_usd': round(kurs * MAAL_PCT, 2),
                 'entry_date': dato, 'last_close_usd': kurs, 'pnl_pct': 0.0}
            pf['cash_dkk'] -= p['shares'] * kurs * fx
            pf['positions'].append(p)
            log.append(f'{dato} KØB {t} @ {kurs:.2f}  stop {p["stop_usd"]}  mål {p["target_usd"]}')

        # 2. Stop, mål og RSI på åbne positioner
        behold = []
        for p in pf['positions']:
            r = rk.get(p['ticker'], {}).get(dato)
            if not r:
                behold.append(p)
                continue
            if r['open'] <= p['stop_usd']:
                luk(p, dato, r['open'], 'stop-loss (gap under stop)')
            elif r['open'] >= p['target_usd']:
                luk(p, dato, r['open'], 'kursmål (gap over mål)')
            elif r['low'] <= p['stop_usd']:
                luk(p, dato, p['stop_usd'], 'stop-loss -2%')
            elif r['high'] >= p['target_usd']:
                luk(p, dato, p['target_usd'], 'kursmål +4%')
            elif r['rsi14'] is not None and r['rsi14'] > 70:
                luk(p, dato, r['close'], 'RSI > 70 (momentum vender)')
            else:
                p['last_close_usd'] = r['close']
                p['pnl_pct'] = round((r['close'] / p['entry_usd'] - 1) * 100, 2)
                behold.append(p)
        pf['positions'] = behold

        # 3. Nye signaler ved lukning
        ledige = MAKS_POS - len(pf['positions']) - len(pf['pending_buys'])
        ejet = {p['ticker'] for p in pf['positions']} | set(pf['pending_buys'])
        breakouts, vendinger = [], []
        for t in univers:
            if t in ejet:
                continue
            rows = tk[t]['rows']
            idx = next((i for i, r in enumerate(rows) if r['date'] == dato), None)
            if idx is None or idx == 0:
                continue
            r, f = rows[idx], rows[idx - 1]
            if (r['high20_prev'] and r['close'] > r['high20_prev'] and r['volume'] and r['volume_prev']
                    and r['volume'] > r['volume_prev']):
                breakouts.append((r['close'] / r['high20_prev'] - 1, t, 'breakout', r))
            elif r['rsi14'] is not None and f['rsi14'] is not None and f['rsi14'] < 30 <= r['rsi14']:
                vendinger.append((r['rsi14'] - f['rsi14'], t, 'RSI krydser op gennem 30', r))
        kandidater = sorted(breakouts, reverse=True) + sorted(vendinger, reverse=True)
        for styrke, t, typ, r in kandidater[:max(0, min(MAKS_NYE, ledige))]:
            pf['pending_buys'].append(t)
            log.append(f'{dato} KØB-SIGNAL {t} ({typ}) luk {r["close"]:.2f} - købes ved næste åbning')

        # 4. History
        punkt = {'date': dato, 'equity_dkk': round(egenkapital(dato), 2), 'qqq_close': qqq[dato]['close']}
        pf['history'] = [h for h in pf['history'] if h['date'] != dato] + [punkt]
        if not pf['benchmark'].get('start_price'):
            pf['benchmark']['start_price'] = qqq[dato]['close']

    pf['history'].sort(key=lambda h: h['date'])
    pf['cash_dkk'] = round(pf['cash_dkk'], 2)
    pf['updated'] = dage[-1]
    pf['universe'] = univers
    with open(ud_sti, 'w', encoding='utf-8') as f:
        json.dump(pf, f, ensure_ascii=False)

    # Rapport
    h = pf['history']
    nu, forr = h[-1], (h[-2] if len(h) > 1 else h[-1])
    q_dag = (qqq[dage[-1]]['close'] / forr['qqq_close'] - 1) * 100 if forr['qqq_close'] else 0
    dag = (nu['equity_dkk'] / forr['equity_dkk'] - 1) * 100
    total = (nu['equity_dkk'] / pf['start_capital_dkk'] - 1) * 100
    q_total = (nu['qqq_close'] / pf['benchmark']['start_price'] - 1) * 100
    marked = 'op' if q_dag > 0.3 else 'ned' if q_dag < -0.3 else 'sidelæns'
    pos = ', '.join(f"{p['ticker']} {p['pnl_pct']:+.2f} %" for p in pf['positions']) or 'ingen'
    print(f'Behandlede handelsdage: {", ".join(dage)}  (kursdata til {kurser["as_of"]})')
    if rettet:
        print('Rettede history-punkter: ' + '; '.join(rettet))
    print()
    print('📊 STATUS')
    print(f'Marked: {marked}')
    print(f'Position: {len(pf["positions"])} aktive ({pos})')
    print()
    print('🎯 HANDLING')
    print('\n'.join(log) if log else 'HOLD - intet klart signal')
    if pf['pending_buys']:
        print(f'Venter på køb ved næste åbning: {", ".join(pf["pending_buys"])}')
    print()
    print('📈 RESULTAT')
    print(f'Dag: {dag:+.2f} %   Benchmark: {q_dag:+.2f} % (QQQ)')
    print(f'Total siden start: {total:+.2f} %   QQQ siden start: {q_total:+.2f} %')
    print(f'Edge vs. marked: {total - q_total:+.2f} pct.point')
    return 0


if __name__ == '__main__':
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(*sys.argv[1:]))
