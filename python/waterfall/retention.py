"""Risk retention sizing: US Reg RR (17 CFR 246.4) vertical / horizontal / L-shaped and EU Art 6(3)(a), (d).

The residual R is split into R1 (retained first loss) and R2 (sold, priority claim accruing at
forward + the residual FV margin). That split is an analytical construct, not a deal term.
"""
import numpy as np
from . import config as cfg
from . import waterfall as wf
from .metrics import tranche_cashflows, pv, irr_annual, par_of

ALL = cfg.CLASSES + [cfg.RESIDUAL]
R_MARGIN = cfg.FV_MARGIN[cfg.RESIDUAL]


def fair_values(deal, df, fwd):
    cf = tranche_cashflows(df, deal)
    return {c: pv(cf[c], fwd, cfg.FV_MARGIN[c]) for c in ALL}, cf


def r2_split(r_cash, par2, fwd):
    """Pay R2's accruing claim first; R1 gets the rest. Returns (r1_cash, r2_cash)."""
    claim, r2 = par2, np.zeros(len(r_cash))
    for t in range(len(r_cash)):
        claim *= 1 + (fwd[t + 1] + R_MARGIN) / 12
        pay = min(r_cash[t], claim)
        r2[t], claim = pay, claim - pay
    return r_cash - r2, r2


def size_options(deal, base_df, fwd):
    """Retained fractions per class (A, B, C, R1, R2) for every option, sized on base-case fair values."""
    fv, _ = fair_values(deal, base_df, fwd)
    total_fv = sum(fv.values())
    par_all = sum(par_of(deal, c) for c in ALL)
    req = cfg.RETENTION_PCT
    out = {}

    out["RR vertical"] = dict(frac={c: req for c in ALL}, par2=0.0)
    out["EU vertical"] = dict(frac={c: req for c in ALL}, par2=0.0)

    # horizontal: R1 fair value equals required; R2 par = FV(R) - required (R2 is worth par if repaid)
    req_h = req * total_fv
    par2 = max(fv["R"] - req_h, 0.0)
    out["RR horizontal"] = dict(frac={"R1": 1.0}, par2=par2, added=[])
    if fv["R"] < req_h:                       # residual alone is short: add whole junior notes
        need, added = req_h - fv["R"], []
        for c in reversed(cfg.CLASSES):
            if need <= 0:
                break
            added.append(c)
            need -= fv[c]
        out["RR horizontal"] = dict(frac={"R1": 1.0, **{c: 1.0 for c in added}}, par2=0.0, added=added)

    # L-shaped: 2.5% vertical (including 2.5% of R1) + 2.5% horizontal measured on the other 97.5%
    v = req / 2
    fv_r1 = (req - v) * total_fv / (1 - v)
    par2_l = max(fv["R"] - fv_r1, 0.0)
    out["RR L-shaped"] = dict(frac={**{c: v for c in cfg.CLASSES}, "R2": v, "R1": 1.0}, par2=par2_l)

    # EU first loss: residual PRINCIPAL (excludes reserve funding) must be at least 5% of the pool
    resid_principal = deal.residual_par - cfg.RESERVE_INITIAL
    frac, added, got = {"R": 1.0}, [], resid_principal
    for c in reversed(cfg.CLASSES):
        if got >= req * deal.pool_balance:
            break
        frac[c] = 1.0
        added.append(c)
        got += cfg.NOTES[c][0]
    out["EU first loss"] = dict(frac=frac, par2=0.0, added=added, tested_principal=resid_principal)
    for k, v_ in out.items():
        v_["total_fv"], v_["par_all"] = total_fv, par_all
    return out, fv


def retained_cashflows(opt_name, spec, cf, deal, fwd):
    """Cash flows of the retained position and its par (cost basis)."""
    T = len(cf["A"])
    par2 = spec["par2"]
    r1_cash, r2_cash = r2_split(cf["R"], par2, fwd) if par2 > 0 else (cf["R"], np.zeros(T))
    r1_par = deal.residual_par - par2
    frac = spec["frac"]
    flows, par = np.zeros(T), 0.0
    if opt_name in ("RR vertical", "EU vertical"):
        for c in ALL:
            flows += frac[c] * cf[c]
            par += frac[c] * par_of(deal, c)
    elif opt_name == "RR horizontal":
        flows += r1_cash
        par += r1_par
        for c in spec.get("added", []):
            flows += cf[c]
            par += par_of(deal, c)
    elif opt_name == "RR L-shaped":
        v = frac["A"]
        for c in cfg.CLASSES:
            flows += v * cf[c]
            par += v * par_of(deal, c)
        flows += v * r2_cash + r1_cash
        par += v * par2 + r1_par
    else:  # EU first loss
        for c, f in frac.items():
            flows += f * cf[c]
            par += f * par_of(deal, c)
    return flows, par


def regulatory_measure(opt_name, spec, fv, deal, fwd):
    """Reg RR 246.4(a)(3): vertical % + horizontal % of total fair value (must be >= 5%)."""
    total = spec["total_fv"]
    if opt_name == "RR vertical":
        return cfg.RETENTION_PCT
    if opt_name == "RR horizontal":
        r1_fv = fv["R"] - spec["par2"]
        return (r1_fv + sum(fv[c] for c in spec.get("added", []))) / total
    if opt_name == "RR L-shaped":
        v = spec["frac"]["A"]
        r1_fv = fv["R"] - spec["par2"]
        return v + (1 - v) * r1_fv / total
    return None


def returns_table(deal, base_df, stress_df, base_scn, stress_scn):
    fwd_b = cfg.forward_curve(deal.horizon, base_scn.index_shift)
    fwd_s = cfg.forward_curve(deal.horizon, stress_scn.index_shift)
    opts, fv = size_options(deal, base_df, fwd_b)
    cf_b, cf_s = tranche_cashflows(base_df, deal), tranche_cashflows(stress_df, deal)
    rows = {}
    for name, spec in opts.items():
        fb, par = retained_cashflows(name, spec, cf_b, deal, fwd_b)
        fs, _ = retained_cashflows(name, spec, cf_s, deal, fwd_s)
        rows[name] = dict(par_retained=par, base_yield=irr_annual(par, fb), stress_yield=irr_annual(par, fs),
                          base_cash=fb.sum(), stress_cash=fs.sum(),
                          reg_measure=regulatory_measure(name, spec, fv, deal, fwd_b))
    return rows, opts, fv


def financing_view(deal, name, spec, cf, fwd, par_by_piece=None):
    """Advance against the retained position; all retained cash sweeps to the lender until repaid."""
    flows, _ = retained_cashflows(name, spec, cf, deal, fwd)
    frac = spec["frac"]
    adv = 0.0
    r1_par = deal.residual_par - spec["par2"]
    for c in cfg.CLASSES:
        f = frac.get(c, 0.0)
        adv += cfg.ADVANCE_RATE[c] * f * cfg.NOTES[c][0]
    if "R" in frac:
        adv += cfg.ADVANCE_RATE["R"] * frac["R"] * deal.residual_par
    if "R1" in frac:
        adv += cfg.ADVANCE_RATE["R"] * r1_par
    if "R2" in frac:
        adv += cfg.ADVANCE_RATE["R"] * frac["R2"] * spec["par2"]
    loan, repaid, pv_cf = adv, None, 0.0
    disc = 1.0
    for t, x in enumerate(flows, start=1):
        rate = (fwd[t] + cfg.FINANCING_MARGIN) / 12
        disc /= 1 + rate
        pv_cf += x * disc
        loan *= 1 + rate
        pay = min(x, loan)
        loan -= pay
        if loan <= 1.0 and repaid is None:
            repaid = t
            loan = 0.0
    return dict(advance=adv, coverage=pv_cf / adv if adv > 0 else None, repaid_month=repaid,
                unpaid=loan if repaid is None else 0.0)
