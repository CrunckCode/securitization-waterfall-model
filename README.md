# Securitization Waterfall and Risk Retention Model

A structured-finance model in Python: an amortizing 400m synthetic pool feeding a four-class capital structure (A, B floating; C fixed; R residual) through an explicit priority of payments, with OC and IC tests, turbo, reserve account, write-downs, scenario and break-even analysis, and a module that sizes the 5% risk retention interest under US Regulation RR and EU Securitisation Regulation Article 6.

**Provenance.** The structure follows the public design in github.com/DarkKnight6499/securitization-waterfall-model (README spec). This is an independent re-implementation written from that written specification; no source files were copied. The rep lines, tests, Monte Carlo, hedge option and review fixes are this project's own. The Excel workbook below is also built here from scratch.

**The collateral is synthetic.** Rep lines, forward curve, fees, triggers, fair value margins and advance rates are invented for illustration. Nothing here is market data or a real transaction.

## Run

```
cd python
py -3 -m pip install -r requirements.txt
py -3 -m waterfall.cli            # regenerates outputs/ (add --no-mc to skip Monte Carlo, about 1 minute)
py -3 -m pytest -q                # 32 tests (2 need LibreOffice), about 30 seconds
```

Layout: `waterfall/config.py` (all inputs), `collateral.py`, `waterfall.py` (priority of payments), `metrics.py`, `retention.py`, `checks.py`, `montecarlo.py`, `charts.py`, `cli.py`.

## The deal

Pool: 400.0m, 10 rep lines, WAC 9.15%, WA remaining term 61.9 months (36 to 84). Monthly 30/360. Classes: A 300m (index + 1.10%), B 40m (index + 2.25%), C 30m (7.50% fixed), R 33m (30m principal plus the 3.0m reserve funded by the residual holder). Principal subordination below A, B, C is 25.0%, 15.0%, 7.5%. Index curve: `4.30% + (3.25% - 4.30%) * (1 - exp(-t/24))`, set in arrears. Senior fees 0.63% a year on pool balance.

Per month: defaults from CDR, interest on the performing balance, level-pay scheduled principal, prepayments from CPR, recoveries after a lag. Tests (OC and IC per class, on pro forma balances before turbo) drive the priority of payments: fees, A interest, turbo if A breached, B interest, turbo if B breached, C interest, turbo if C breached, principal deficiency cure, reserve top-up, then residual. The reserve pays only fees and Class A and B interest. Write-downs hit C, then B, then A, and are permanent. Once the notes are retired, collected principal and the reserve release settle carried fees and interest before the residual. Pro rata principal is a one-way latch to sequential after the first breach.

## Results (from `python/outputs/`)

Base case (CPR 10%, CDR 3%, severity 40%, lag 6):

| Class | WAL (yrs) | Yield at par | Discount margin | Principal window (months) |
|-|-|-|-|-|
| A | 1.59 | 5.00% | 1.10% | 1 to 42 |
| B | 3.83 | 5.97% | 2.25% | 42 to 51 |
| C | 4.71 | 7.50% | 3.83% | 51 to 63 |
| R | 5.88 | 18.16% | 14.45% | 63 to 90 |

| Scenario | Cum. net loss | A | B | C | R (capital not returned) |
|-|-|-|-|-|-|
| Base | 2.7% | 0 | 0 | 0 | 0 |
| Mild stress (CDR 6%, sev 50%) | 6.6% | 0 | 0 | 0 | 0 |
| Stress (CPR 6%, CDR 12%, sev 60%, lag 9) | 15.7% | 0 | 0 | 46.4% | 73.2% |
| Severe stress (CPR 4%, CDR 20%, sev 70%, lag 12) | 28.5% | 0 | 78.0% | 100% | 92.1% |
| Fast prepay (CPR 30%) | 1.9% | 0 | 0 | 0 | 0 |
| Rate shock (+300bp, mild-stress credit) | 6.6% | 0 | 0 | 0 | 39.0% |

Break-even annual CDR at severity 40%: A 50.9%, B 27.1%, C 13.3%, R 7.9%; at severity 60%: A 29.6%, B 17.3%, C 9.1%, R 5.5%. The Rate shock has the same credit assumptions as Mild stress, yet the unhedged floating notes reprice up against fixed-rate collateral: Class C defers 2.44m of interest at the peak and the residual loses 39%.

Retention (fair values A 300.0m, B 39.3m, C 29.2m, R 35.2m): vertical 5% of every class is 20.15m par (base yield 7.3%, stress 1.0%); horizontal keeps R1 (17.99m par, base 18.5%, stress no cash); L-shaped keeps 2.5% of A, B, C, R2 plus R1 (18.03m par, base 16.5%, stress -22.4%); EU first loss keeps the whole residual (33.0m par; the 5% test runs on its 30.0m of principal, excluding the reserve funding). Horizontal and L-shaped options measure exactly 5.00% under 246.4(a)(3); the L-shaped test avoids counting the 2.5% vertical slice of R1 twice. See `retention_summary.csv`, `financing_view.csv`.

## Excel workbook

`excel/securitization_waterfall.xlsx` (about 26,700 live formulas), built by `excel/build_workbook.py`. Sheets: Inputs, Pool, Waterfall, CashFlows, Scenario_Summary. All typed numbers are on Inputs (scenario table with four spare custom slots, deal terms, pool rep lines, pro rata and hedge switches); the time-series sheets hold only formulas, the period index and zero opening seeds. Change "Selected scenario number" on Inputs and everything recalculates. The workbook covers the pool, tests, turbo, reserve, write-downs, arrears sweep, WAL, yield, write-downs, unpaid interest, shortfall flags, class labels and its own integrity checks. Break-even CDR, discount margin, Monte Carlo and the retention module are Python only.

`py -3 -m waterfall.reconcile_excel` recalculates the workbook in headless LibreOffice and compares it to Python for 12 cases: the six scenarios, pro rata Base, Stress and Rate shock (Rate shock breaches then cures, so it exercises the latch), hedged Rate shock, and two extra runs that exercise the post-retirement arrears sweep (CPR 2%, CDR 18%, severity 20%, lag 12; CPR 10%, CDR 40%, severity 70%, lag 6). Compared every period: balances, interest, principal, write-downs, unpaid interest, residual cash, arrears swept, reserve, fees and draws; plus WAL, yield, write-downs, shortfall flags, class labels and breach month counts. Tolerances: USD 0.01 per cash flow cell, 1e-6 for WAL and yields. Result: all 12 cases pass, worst cash difference 5.4e-7 USD (`python/outputs/excel_reconciliation.json`).

Caveats: recalculation verified in LibreOffice only, not Microsoft Excel. `MINIFS` and `MAXIFS` need Excel 2019 or later. IRR uses fallback guesses so deeply loss-making tranches still converge.

## Improvements over the reference design

1. **Swap hedge option** (`Deal(hedged=True)`): receive floating, pay a fixed 3.60% on the floating notional. In the rate shock the residual loss falls from 39.0% to 0 and Class C no longer defers interest (`rate_shock_hedge_comparison.csv`).
2. **Monte Carlo** (1,000 draws of lognormal CDR, normal CPR, beta severity, random lag and index shift): probability of any shortfall A 0.1%, B 0.8%, C 6.7%, R 22.7%; expected residual loss 8.0% with a 95th percentile of 62.7% (`monte_carlo_summary.csv`). Seeded and reproducible.
3. **Integrity checks on every scenario**, hedged and unhedged: pool roll-forward, lifetime identity, cash conservation each period, per-class identity, residual never paid with arrears outstanding, no negatives (`checks.json`).
4. **Independent model review** found and led to fixing swap payments missing from the IC ratio and excess spread, and a double count of the reserve release in the residual WAL.

## Limitations

* Synthetic pool of ten rep lines with constant CPR and CDR; no delinquency, advances or seasoning.
* Unpaid fees and interest carry forward without interest on interest.
* While Class A tests are breached, the turbo ranks ahead of Class B interest, so the reserve can fund Class B interest while collections pay Class A principal. This follows the assumed priority of payments, not a recommendation.
* R1 and R2 are an analytical split of the residual (R2 accrues at forward + 12%). R1 cost basis is its nominal remainder, so its yield is on a par basis rather than a fair value basis and reads somewhat high.
* After notes are written down, the reserve release can still reach the residual, so the model does not claim that 17 CFR 246.2 condition (2) holds for the residual as a whole in every scenario.
* Fair values are discounted cash flows at assumed margins, not GAAP fair value. Break-even and discount margin come from Python only. Nothing here is a compliance opinion or investment advice.

## Next steps

* Retention sizing in Excel (currently Python only).
* Interest on unpaid interest, delinquency and servicer advance modelling, loan-level collateral.
* Regulation text extracts for the retention module should be re-read from primary sources (eCFR 17 CFR 246.2 and 246.4; Regulation (EU) 2017/2402 Article 6) before quoting any rule in an interview or document.
