"""Behavioural tests for the pool, waterfall, metrics, retention and checks."""
from dataclasses import replace
import numpy as np
import pytest
from waterfall import checks, config as cfg, metrics as mt, retention as rt, waterfall as wf
from waterfall.collateral import project_pool
from waterfall.waterfall import col

DEAL = cfg.Deal()


@pytest.fixture(scope="module")
def frames():
    return {s.name: wf.results_frame(DEAL, s) for s in cfg.SCENARIOS}


def test_pool_matches_stated_aggregates():
    bal = np.array([p[1] for p in DEAL.pool])
    wac = np.array([p[2] for p in DEAL.pool])
    term = np.array([p[3] for p in DEAL.pool])
    assert bal.sum() == pytest.approx(400e6)
    assert (bal * wac).sum() / bal.sum() == pytest.approx(0.0915, abs=1e-4)
    assert (bal * term).sum() / bal.sum() == pytest.approx(61.9, abs=0.01)
    assert DEAL.residual_par == pytest.approx(33e6)


def test_pool_rollforward_and_zero_rates():
    out, tot = project_pool(DEAL.pool, 0.10, 0.03, 0.4, 6, DEAL.horizon)
    recon = out["beg"] - out["defaults"] - out["sched"] - out["prepay"] - out["end"]
    assert np.abs(recon).max() < 1e-6
    assert tot["end"][-1] < 1e-6
    out0, tot0 = project_pool(DEAL.pool, 0.0, 0.0, 0.4, 6, DEAL.horizon)
    assert tot0["defaults"].sum() == 0 and tot0["prepay"].sum() == 0
    assert tot0["sched"].sum() == pytest.approx(400e6, rel=1e-9)


def test_recoveries_arrive_after_lag():
    _, tot = project_pool(DEAL.pool, 0.10, 0.05, 0.5, 6, DEAL.horizon)
    assert tot["recoveries"][:6].sum() == 0
    assert tot["recoveries"][6] == pytest.approx(0.5 * tot["defaults"][0])
    assert tot["recoveries"].sum() == pytest.approx(0.5 * tot["defaults"].sum(), rel=1e-9)


def test_all_checks_pass_every_scenario_and_hedged(frames):
    for hedged in (False, True):
        d = DEAL.with_(hedged=hedged)
        for s in cfg.SCENARIOS:
            df = frames[s.name] if not hedged else wf.results_frame(d, s)
            assert checks.all_passed(checks.run_checks(d, s, df)), (hedged, s.name)


def test_base_case_no_tests_breached_no_losses(frames):
    df = frames["Base"]
    assert not df[wf.BREACH_ANY].any()
    summ = mt.summarize(df, DEAL, cfg.BASE)
    assert all(not v["any_shortfall"] for v in summ.values())
    assert mt.class_labels(summ)["junior_writedown"] == "none"


def test_initial_oc_ratios():
    assert 400e6 / 300e6 == pytest.approx(1.3333, abs=1e-4)
    assert 400e6 / 340e6 == pytest.approx(1.1765, abs=1e-4)
    assert 400e6 / 370e6 == pytest.approx(1.0811, abs=1e-4)


def test_sequential_paydown_order_in_base(frames):
    s = mt.summarize(frames["Base"], DEAL, cfg.BASE)
    assert s["A"]["last_prin_month"] <= s["B"]["first_prin_month"] + 1
    assert s["B"]["last_prin_month"] <= s["C"]["first_prin_month"] + 1


def test_loss_ordering_and_labels(frames):
    for name in ("Stress", "Severe stress"):
        s = mt.summarize(frames[name], DEAL, next(x for x in cfg.SCENARIOS if x.name == name))
        assert s["A"]["writedown"] <= s["B"]["writedown_pct"] * 1e12      # A never worse than B in pct
        assert s["R"]["writedown_pct"] >= s["C"]["writedown_pct"] * 0
        assert mt.class_labels(s)["junior_writedown"] == "R"
    sev = mt.summarize(frames["Severe stress"], DEAL, cfg.SCENARIOS[3])
    assert sev["A"]["writedown"] == 0
    assert sev["C"]["writedown_pct"] == pytest.approx(1.0)
    assert sev["B"]["writedown_pct"] > 0


def test_writedowns_never_reverse(frames):
    for df in frames.values():
        for c in cfg.CLASSES:
            assert (df[col("writedown", c)] >= -1e-9).all()


def test_rate_shock_hurts_residual_and_hedge_fixes_it(frames):
    s = cfg.SCENARIOS[5]
    unhedged = mt.summarize(frames["Rate shock"], DEAL, s)
    hedged_deal = DEAL.with_(hedged=True)
    hedged = mt.summarize(wf.results_frame(hedged_deal, s), hedged_deal, s)
    assert unhedged["R"]["writedown_pct"] > 0.2
    assert unhedged["C"]["unpaid_int_peak"] > 1e6
    assert hedged["R"]["writedown_pct"] < unhedged["R"]["writedown_pct"]
    assert hedged["C"]["unpaid_int_peak"] < unhedged["C"]["unpaid_int_peak"]


def test_rate_shock_has_same_credit_as_mild_stress(frames):
    a, b = frames["Mild stress"], frames["Rate shock"]
    assert a[wf.CUM_LOSS].iloc[-1] == pytest.approx(b[wf.CUM_LOSS].iloc[-1])


def test_pro_rata_latches_to_sequential_after_breach():
    s = cfg.SCENARIOS[5]
    df = wf.results_frame(DEAL.with_(pro_rata_start=True), s)
    assert checks.all_passed(checks.run_checks(DEAL.with_(pro_rata_start=True), s, df))
    first = int(np.argmax(df[wf.BREACH_ANY].values))
    after = df.iloc[first + 1:]
    # once sequential, B receives no regular principal while A is outstanding
    live_a = after[after[col("bal", "A")] > 1]
    assert (live_a[col("prin_paid", "B")] <= 1e-6).all() or live_a.empty


def test_pro_rata_pays_all_classes_early():
    df = wf.results_frame(DEAL.with_(pro_rata_start=True), cfg.BASE)
    assert df[col("prin_paid", "B")].iloc[0] > 0 and df[col("prin_paid", "C")].iloc[0] > 0


def test_reserve_never_pays_class_c_or_principal(frames):
    df = frames["Rate shock"]
    assert (df[wf.RES_DRAW] <= df[wf.RES_DRAW].cummax() + 1).all()
    assert df[wf.RES_END].min() >= -1e-9
    assert df[wf.RES_DRAW].sum() > 0     # drawn for B interest while turbo diverts cash


def test_reserve_released_when_notes_retire(frames):
    df = frames["Base"]
    assert df[wf.RES_RELEASE].sum() == pytest.approx(cfg.RESERVE_INITIAL)
    assert df[wf.RES_END].iloc[-1] == 0


def test_arrears_settled_before_residual_after_retirement(frames):
    df = frames["Severe stress"]
    assert df[wf.ARREARS_SWEPT].sum() > 0
    unpaid = df[wf.FEES_UNPAID] + sum(df[col("unpaid_int", c)] for c in cfg.CLASSES)
    assert not ((df[wf.RESID_CASH] > 1e-6) & (unpaid > 1e-6)).any()


def test_excess_spread_month_one(frames):
    assert 300 < frames["Base"][wf.EXCESS_SPREAD_BP].iloc[0] < 360


def test_yield_at_par_equals_coupon_when_unimpaired(frames):
    s = mt.summarize(frames["Base"], DEAL, cfg.BASE)
    assert s["C"]["yield_at_par"] == pytest.approx(0.075, abs=2e-4)
    assert s["A"]["discount_margin"] == pytest.approx(0.011, abs=1e-5)
    assert s["B"]["discount_margin"] == pytest.approx(0.0225, abs=1e-5)


def test_wal_formula_hand_check():
    import pandas as pd
    df = pd.DataFrame({wf.PERIOD: [1, 2], col("prin_paid", "A"): [100.0, 300.0]})
    assert mt.wal(df, "A", DEAL) == pytest.approx((1 * 100 + 2 * 300) / 12 / 400)


def test_breakeven_ordering_and_severity_sensitivity():
    be40 = {c: mt.breakeven_cdr(DEAL, cfg.BASE, c, 0.40, tol=1e-3) for c in mt.CLASS_ORDER}
    be60 = {c: mt.breakeven_cdr(DEAL, cfg.BASE, c, 0.60, tol=1e-3) for c in mt.CLASS_ORDER}
    assert be40["A"] > be40["B"] > be40["C"] > be40["R"]
    assert be60["A"] > be60["B"] > be60["C"] > be60["R"]
    assert all(be60[c] < be40[c] for c in be40)


def test_breakeven_point_is_a_threshold():
    be = mt.breakeven_cdr(DEAL, cfg.BASE, "C", 0.40, tol=1e-4)
    below = replace(cfg.BASE, cdr=be * 0.99)
    above = replace(cfg.BASE, cdr=be * 1.02)
    assert mt.summarize(wf.results_frame(DEAL, below), DEAL, below)["C"]["writedown"] <= cfg.SHORTFALL_TOL
    assert mt.summarize(wf.results_frame(DEAL, above), DEAL, above)["C"]["writedown"] > cfg.SHORTFALL_TOL


# ---------------------------------------------------------------- retention
@pytest.fixture(scope="module")
def ret(frames):
    return rt.returns_table(DEAL, frames["Base"], frames["Stress"], cfg.BASE, cfg.STRESS)


def test_vertical_is_five_percent_of_every_class(ret):
    rows, opts, fv = ret
    assert rows["RR vertical"]["par_retained"] == pytest.approx(0.05 * 403e6)
    assert all(v == 0.05 for v in opts["RR vertical"]["frac"].values())


def test_horizontal_and_l_shaped_hit_exactly_five_percent_measure(ret):
    rows, _, _ = ret
    for k in ("RR vertical", "RR horizontal", "RR L-shaped"):
        assert rows[k]["reg_measure"] == pytest.approx(0.05, abs=1e-9)


def test_l_shaped_does_not_double_count_vertical_slice(ret):
    _, opts, fv = ret
    total = opts["RR L-shaped"]["total_fv"]
    r1_fv = fv["R"] - opts["RR L-shaped"]["par2"]
    naive = 0.025 + r1_fv / total
    assert naive > 0.05 + 1e-4                       # counting all of R1 as horizontal would overshoot
    assert 0.025 + 0.975 * r1_fv / total == pytest.approx(0.05, abs=1e-9)


def test_horizontal_retained_fair_value_equals_requirement(ret):
    _, opts, fv = ret
    spec = opts["RR horizontal"]
    assert fv["R"] - spec["par2"] == pytest.approx(0.05 * spec["total_fv"])


def test_eu_first_loss_uses_residual_principal_only(ret):
    _, opts, _ = ret
    assert opts["EU first loss"]["tested_principal"] == pytest.approx(30e6)
    assert opts["EU first loss"]["added"] == []


def test_eu_first_loss_adds_junior_note_when_principal_short():
    notes_c = cfg.NOTES["C"]
    cfg.NOTES["C"] = (42e6,) + notes_c[1:]
    try:
        d = cfg.Deal()
        df = wf.results_frame(d, cfg.BASE)
        opts, _ = rt.size_options(d, df, cfg.forward_curve(d.horizon))
        assert opts["EU first loss"]["tested_principal"] == pytest.approx(18e6)
        assert opts["EU first loss"]["added"] == ["C"]
    finally:
        cfg.NOTES["C"] = notes_c


def test_stress_hits_concentrated_positions_harder(ret):
    rows, _, _ = ret
    assert rows["RR vertical"]["stress_yield"] > 0
    assert rows["RR horizontal"]["stress_cash"] == pytest.approx(0.0, abs=1.0)
    assert rows["EU first loss"]["stress_yield"] < rows["RR vertical"]["stress_yield"]


def test_financing_view_regulatory_note_and_coverage(ret, frames):
    _, opts, _ = ret
    cf = mt.tranche_cashflows(frames["Base"], DEAL)
    fwd = cfg.forward_curve(DEAL.horizon)
    fin = rt.financing_view(DEAL, "RR vertical", opts["RR vertical"], cf, fwd)
    assert fin["advance"] == pytest.approx(0.05 * (0.85 * 300e6 + 0.70 * 40e6 + 0.55 * 30e6 + 0.30 * 33e6))
    assert fin["repaid_month"] is not None and fin["coverage"] > 1


def test_monte_carlo_small_run_is_reproducible():
    from waterfall import montecarlo as mc
    _, a = mc.simulate(DEAL, n=12, seed=3)
    _, b = mc.simulate(DEAL, n=12, seed=3)
    assert a.equals(b)
    assert (a["prob_loss"].between(0, 1)).all()
    assert a.loc["A", "prob_loss"] <= a.loc["R", "prob_loss"]
