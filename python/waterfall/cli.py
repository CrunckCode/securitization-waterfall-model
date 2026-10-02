"""Regenerates every table, CSV and chart in outputs/.  Run: py -3 -m waterfall.cli [--no-mc]"""
import json
import sys
from dataclasses import replace
from pathlib import Path
import pandas as pd
from . import charts, checks, config as cfg, metrics as mt, montecarlo, retention as rt
from . import waterfall as wf

# ------------------------------------------------------------------ USER INPUTS
OUT = Path(__file__).resolve().parent.parent / "outputs"
BREAKEVEN_SEVERITIES = (0.40, 0.60)
GRID_CDRS = (0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40)
GRID_SEVERITIES = (0.30, 0.50, 0.70)
RATE_SHOCK_INDEX = 5          # position of "Rate shock" in cfg.SCENARIOS
# ------------------------------------------------------------------------------


def scenario_table(deal, scenarios):
    rows, dfs = [], {}
    for s in scenarios:
        df = wf.results_frame(deal, s)
        dfs[s.name] = df
        summ = mt.summarize(df, deal, s)
        row = dict(scenario=s.name, cpr=s.cpr, cdr=s.cdr, severity=s.severity, lag=s.lag, index_shift=s.index_shift,
                   cum_net_loss_pct=df[wf.CUM_LOSS].iloc[-1] / deal.pool_balance, **mt.class_labels(summ))
        for c in mt.CLASS_ORDER:
            row[f"writedown_pct_{c}"] = summ[c]["writedown_pct"]
            row[f"unpaid_int_end_{c}"] = summ[c]["unpaid_int_end"]
            row[f"unpaid_int_peak_{c}"] = summ[c]["unpaid_int_peak"]
        rows.append(row)
    return pd.DataFrame(rows), dfs


def main():
    run_mc = "--no-mc" not in sys.argv[1:]
    (OUT / "charts").mkdir(parents=True, exist_ok=True)
    deal = cfg.Deal()

    scen, dfs = scenario_table(deal, cfg.SCENARIOS)
    scen.to_csv(OUT / "scenario_summary.csv", index=False)
    for name, s in (("base", cfg.BASE), ("stress", cfg.STRESS)):
        dfs[s.name].to_csv(OUT / f"cashflows_{name}.csv", index=False)

    tranche_rows, check_out = [], {}
    for s in cfg.SCENARIOS:
        for c, v in mt.summarize(dfs[s.name], deal, s).items():
            tranche_rows.append(dict(scenario=s.name, tranche=c, **v))
        check_out[s.name] = checks.run_checks(deal, s, dfs[s.name])
    pd.DataFrame(tranche_rows).to_csv(OUT / "tranche_summary_by_scenario.csv", index=False)
    (OUT / "checks.json").write_text(json.dumps(check_out, indent=1))

    be = pd.DataFrame({c: {sev: mt.breakeven_cdr(deal, cfg.BASE, c, sev) for sev in BREAKEVEN_SEVERITIES}
                       for c in mt.CLASS_ORDER})
    be.to_csv(OUT / "breakeven_cdr.csv")

    grid = []
    for sev in GRID_SEVERITIES:
        for cdr in GRID_CDRS:
            s = replace(cfg.BASE, name="grid", cdr=cdr, severity=sev)
            summ = mt.summarize(wf.results_frame(deal, s), deal, s)
            grid.append(dict(severity=sev, cdr=cdr, **{f"writedown_pct_{c}": summ[c]["writedown_pct"] for c in mt.CLASS_ORDER}))
    pd.DataFrame(grid).to_csv(OUT / "loss_grid_cdr_severity.csv", index=False)

    rows, opts, fv = rt.returns_table(deal, dfs[cfg.BASE.name], dfs[cfg.STRESS.name], cfg.BASE, cfg.STRESS)
    pd.DataFrame(rows).T.to_csv(OUT / "retention_summary.csv")
    pd.Series(fv).to_csv(OUT / "fair_values.csv", header=["fair_value"])
    fwd = cfg.forward_curve(deal.horizon)
    cf_b = mt.tranche_cashflows(dfs[cfg.BASE.name], deal)
    cf_s = mt.tranche_cashflows(dfs[cfg.STRESS.name], deal)
    fin = []
    for name, spec in opts.items():
        b, s_ = rt.financing_view(deal, name, spec, cf_b, fwd), rt.financing_view(deal, name, spec, cf_s, fwd)
        fin.append(dict(option=name, advance=b["advance"], coverage_base=b["coverage"], coverage_stress=s_["coverage"],
                        repaid_base=b["repaid_month"], repaid_stress=s_["repaid_month"], unpaid_stress=s_["unpaid"]))
    pd.DataFrame(fin).to_csv(OUT / "financing_view.csv", index=False)

    # improvement 1: swap hedge on the floating notes vs the unhedged deal in the rate shock
    hedge_rows = []
    for hedged in (False, True):
        d, s = deal.with_(hedged=hedged), cfg.SCENARIOS[RATE_SHOCK_INDEX]
        summ = mt.summarize(wf.results_frame(d, s), d, s)
        hedge_rows.append(dict(hedged=hedged, c_unpaid_int_peak=summ["C"]["unpaid_int_peak"],
                               **{f"writedown_pct_{c}": summ[c]["writedown_pct"] for c in mt.CLASS_ORDER}))
    pd.DataFrame(hedge_rows).to_csv(OUT / "rate_shock_hedge_comparison.csv", index=False)

    # improvement 2: Monte Carlo over credit and rate drivers
    if run_mc:
        _, table = montecarlo.simulate(deal)
        table.to_csv(OUT / "monte_carlo_summary.csv")
        charts.mc_loss(table, OUT / "charts" / "monte_carlo_shortfall_probability.png")

    charts.paydown({n: dfs[n] for n in (cfg.BASE.name, cfg.STRESS.name)}, OUT / "charts" / "tranche_balance_paydown.png")
    charts.loss_vs_subordination({n: dfs[n] for n in ("Base", "Mild stress", "Stress", "Severe stress")}, deal,
                                 OUT / "charts" / "cumulative_loss_vs_subordination.png")
    charts.breakeven(be, OUT / "charts" / "breakeven_cdr_by_tranche.png")

    base = mt.summarize(dfs[cfg.BASE.name], deal, cfg.BASE)
    headline = dict(pool_balance=deal.pool_balance, fair_values=fv,
                    base={c: {k: v for k, v in d.items() if v is not None} for c, d in base.items()},
                    all_checks_passed=all(checks.all_passed(r) for r in check_out.values()))
    (OUT / "headline.json").write_text(json.dumps(headline, indent=1, default=float))
    print(scen[["scenario", "cum_net_loss_pct", "junior_writedown", "senior_writedown"]].to_string(index=False))
    print("all checks passed:", headline["all_checks_passed"])


if __name__ == "__main__":
    main()
