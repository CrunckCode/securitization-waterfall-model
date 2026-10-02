"""Integrity checks run on every scenario."""
import numpy as np
from . import config as cfg
from . import waterfall as wf
from .waterfall import col
from .collateral import project_pool


def run_checks(deal, scn, df):
    out, pool = project_pool(deal.pool, scn.cpr, scn.cdr, scn.severity, scn.lag, deal.horizon)
    res = {}
    # pool roll-forward per line and in total
    recon = out["beg"] - out["defaults"] - out["sched"] - out["prepay"] - out["end"]
    res["pool_recon_max_abs"] = float(np.abs(recon).max())
    life = deal.pool_balance - (pool["sched"].sum() + pool["prepay"].sum() + pool["defaults"].sum() + pool["end"][-1])
    res["pool_lifetime_identity"] = float(abs(life))
    # cash conservation: collections + swap receipts + reserve movements = payments + residual
    paid = df[wf.FEES_PAID] + sum(df[col("int_paid", c)] + df[col("prin_paid", c)] for c in cfg.CLASSES)
    inflow = pool["cash"] + np.maximum(df[wf.SWAP_NET], 0.0)
    d_reserve = df[wf.RES_END].diff().fillna(df[wf.RES_END].iloc[0] - cfg.RESERVE_INITIAL)
    gap = inflow - paid - df[wf.RESID_CASH] - d_reserve
    # fees_paid already contains swap payments; reserve draws are paid items funded by the reserve
    res["cash_conservation_max_abs"] = float(np.abs(gap).max())
    # per class: principal + write-downs + ending balance = original
    worst = 0.0
    for c in cfg.CLASSES:
        ident = df[col("prin_paid", c)].sum() + df[col("writedown", c)].sum() + df[col("bal", c)].iloc[-1]
        worst = max(worst, abs(ident - cfg.NOTES[c][0]))
    res["class_identity_max_abs"] = float(worst)
    # residual never paid while fees or note interest remain unpaid
    unpaid = df[wf.FEES_UNPAID] + sum(df[col("unpaid_int", c)] for c in cfg.CLASSES)
    res["residual_paid_with_arrears_periods"] = int(((df[wf.RESID_CASH] > 1e-6) & (unpaid > 1e-6)).sum())
    cols = [col("bal", c) for c in cfg.CLASSES] + [wf.RES_END, wf.RESID_CASH, wf.FEES_PAID]
    res["negative_values"] = int((df[cols] < -1e-6).sum().sum())
    return res


def all_passed(res, tol=1e-3):
    return (res["pool_recon_max_abs"] < tol and res["pool_lifetime_identity"] < tol
            and res["cash_conservation_max_abs"] < tol and res["class_identity_max_abs"] < tol
            and res["residual_paid_with_arrears_periods"] == 0 and res["negative_values"] == 0)
