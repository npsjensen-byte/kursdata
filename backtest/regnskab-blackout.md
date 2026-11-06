# Backtest: ingen nye køb lige før regnskab

Periode: 2022-02-03 – 2026-11-05 (1194 handelsdage). Univers: 49 tickers (dagens top-50 i Nasdaq-100). Startkapital 100.000 (USD-regnet; valuta er ligegyldig for %).

## Dom

**INDFØR IKKE: N = 5 opfylder ikke alle krav (slår baseline i mindst halvdelen af årene (2/5) fejler).**

Robusthed (Sharpe mod baseline): N = 2: dårligere, N = 5: bedre, N = 10: bedre. Retningen skifter med N, så resultatet er følsomt — stol mindre på det.

## Nøgletal

| Variant | Total | CAGR | Sharpe | Max DD | Handler | Win rate | Snit/handel | Profit factor | Værste | Tab > 2 % | Holdt over regnskab | Snit for dem | Signaler blokeret |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Baseline (live-regler) | -3.1 % | -0.7 % | 0.01 | -27.6 % | 1777 | 42 % | +0.00 % | 0.99 | -16.8 % | 131 | 34 | -0.26 % | 0 |
| Blackout 2 dage | -3.1 % | -0.7 % | 0.00 | -21.4 % | 1754 | 42 % | +0.00 % | 0.99 | -12.3 % | 128 | 23 | -0.89 % | 83 |
| Blackout 5 dage ★ | -2.4 % | -0.5 % | 0.02 | -21.7 % | 1725 | 42 % | +0.00 % | 0.99 | -12.3 % | 125 | 14 | -1.62 % | 176 |
| Blackout 10 dage | +9.5 % | +1.9 % | 0.22 | -13.3 % | 1672 | 42 % | +0.04 % | 1.02 | -8.3 % | 117 | 6 | +2.96 % | 414 |
| QQQ (køb og hold) | +83.0 % | +13.6 % | 0.67 | -29.9 % | – | – | – | – | – | – | – | – | – |

## Afkast pr. år

| År | Baseline (live-regler) | Blackout 2 dage | Blackout 5 dage ★ | Blackout 10 dage | QQQ |
|---|---|---|---|---|---|
| 2022 | +9.6 % | +4.3 % | +9.2 % | +5.3 % | -24.7 % |
| 2023 | +5.4 % | +12.1 % | +7.0 % | +8.5 % | +53.8 % |
| 2024 | -17.7 % | -11.2 % | -12.3 % | -6.5 % | +24.8 % |
| 2025 | -0.3 % | -4.8 % | -2.1 % | +4.2 % | +20.2 % |
| 2026 | +2.3 % | -2.0 % | -2.7 % | -1.7 % | +5.3 % |

## Sådan læses det

- **Tab > 2 %** = handler hvor kursen sprang forbi stoppet (gap). Det er præcis den risiko, reglen skal fjerne.
- **Holdt over regnskab** = handler, der var åbne, da kursen reagerede på et regnskab.
- Hovedvarianten (★, N = 5) og beslutningsreglen blev fastlagt før kørslen. N = 2 og 10 er kun et robusthedstjek.

## Forbehold

- **Survivorship bias:** universet er dagens største selskaber. Det puster de absolutte afkast op, men rammer begge varianter ens. Se på forskellen, ikke niveauet.
- Ingen kurtage eller spread. Flere handler = mere overvurderet afkast.
- Regnskabsdatoer mangler for: ingen. Reglen virker ikke for dem i testen.
- Kurser mangler for: PANW.
