"""Tranche analytics: WAL, yield at par, discount margin, write-downs, unpaid interest, break-even CDR."""
import numpy as np
from scipy.optimize import brentq
from . import config as cfg
from . import waterfall as wf
from .waterfall import col

CLASS_ORDER = cfg.CLASSES + [cfg.RESIDUAL]       # seniority, then residual
LOSS_ORDER = [cfg.RESIDUAL] + cfg.CLASSES[::-1]  # who is hit first


def tranche_cashflows(df, deal):
    """Cash received per class per month (index 0 = month 1). R includes reserve release."""
    cf = {c: df[col("int_paid", c)].values + df[col("prin_paid", c)].values for c in cfg.CLASSES}
    cf[cfg.RESIDUAL] = df[wf.RESID_CASH].values.copy()
    return cf


def principal_series(df, c):
    if c == cfg.RESIDUAL:
        return df[col("resid", "prin")].values + 0.0
    return df[col("prin_paid", c)].values


def par_of(deal, c):
    return deal.residual_par if c == cfg.RESIDUAL else cfg.NOTES[c][0]


def irr_annual(par, flows):
    """Annualised monthly IRR x 12 of a purchase at par; None if nothing is received."""
    flows = np.asarray(flows, float)
    if flows.sum() <= 0:
        return None
    f = lambda r: -par + (flows / (1 + r) ** np.arange(1, len(flows) + 1)).sum()
    lo, hi = -0.99, 1.0
    if f(lo) * f(hi) > 0:
        return None
    return brentq(f, lo, hi, xtol=1e-14) * 12


def pv(flows, fwd, margin):
    """PV at forward + margin, monthly compounding on the period rate."""
    r = (fwd[1: len(flows) + 1] + margin) / 12
    return float((np.asarray(flows) * np.cumprod(1 / (1 + r))).sum())


def discount_margin(par, flows, fwd):
    f = lambda m: pv(flows, fwd, m) - par
    try:
        return brentq(f, -0.5, 2.0, xtol=1e-12)
    except ValueError:
        return None


def wal(df, c, deal):
    p = principal_series(df, c)
    tot = p.sum()      # R principal already includes the reserve release
    return float((df[wf.PERIOD].values * p).sum() / 12 / tot) if tot > 0 else None


def summarize(df, deal, scn):
    """One row per class with the headline analytics for a scenario."""
    fwd = cfg.forward_curve(deal.horizon, scn.index_shift)
    cf = tranche_cashflows(df, deal)
    out = {}
    for c in CLASS_ORDER:
        par = par_of(deal, c)
        flows = cf[c]
        paid_prin = principal_series(df, c)
        months = np.nonzero(paid_prin > 1)[0] + 1
        if c == cfg.RESIDUAL:
            wd = max(par - flows.sum(), 0.0)
            unpaid_end, unpaid_peak = 0.0, 0.0
        else:
            wd = df[col("writedown", c)].sum()
            unpaid_end = df[col("unpaid_int", c)].iloc[-1]
            unpaid_peak = df[col("unpaid_int", c)].max()
        out[c] = dict(
            par=par, wal=wal(df, c, deal), yield_at_par=irr_annual(par, flows),
            discount_margin=discount_margin(par, flows, fwd),
            first_prin_month=int(months[0]) if len(months) else None,
            last_prin_month=int(months[-1]) if len(months) else None,
            interest_paid=df[col("int_paid", c)].sum() if c != cfg.RESIDUAL else 0.0,
            cash_received=flows.sum(), writedown=wd, writedown_pct=wd / par,
            unpaid_int_end=unpaid_end, unpaid_int_peak=unpaid_peak,
            any_shortfall=bool(wd > cfg.SHORTFALL_TOL or unpaid_peak > cfg.SHORTFALL_TOL or unpaid_end > cfg.SHORTFALL_TOL),
            write_down_flag=bool(wd > cfg.SHORTFALL_TOL),
        )
    return out


def class_labels(summary):
    """Junior-most / most senior class with a write-down, and the same for any shortfall."""
    def pick(flag, seq):
        for c in seq:
            if summary[c][flag]:
                return c
        return "none"
    return dict(junior_writedown=pick("write_down_flag", LOSS_ORDER),
                senior_writedown=pick("write_down_flag", CLASS_ORDER),
                first_shortfall=pick("any_shortfall", LOSS_ORDER),
                senior_shortfall=pick("any_shortfall", CLASS_ORDER))


def breakeven_cdr(deal, base, cls, severity=None, hi=0.95, tol=1e-5):
    """Highest annual CDR with no write-down above $1 for the class (bisection)."""
    sev = base.severity if severity is None else severity

    def loss(cdr):
        from dataclasses import replace
        s = replace(base, cdr=cdr, severity=sev)
        df = wf.results_frame(deal, s)
        return summarize(df, deal, s)[cls]["writedown"]

    lo = 0.0
    if loss(hi) <= cfg.SHORTFALL_TOL:
        return hi
    while hi - lo > tol:
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if loss(mid) <= cfg.SHORTFALL_TOL else (lo, mid)
    return lo
