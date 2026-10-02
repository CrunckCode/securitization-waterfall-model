"""Monthly priority of payments: tests, turbo, reserve, write-downs and the post-retirement arrears sweep."""
import numpy as np
import pandas as pd
from . import config as cfg
from .collateral import project_pool

# result column names (always referenced by constant)
PERIOD = "period"
FWD = "forward_rate"
FEES_PAID, FEES_UNPAID = "fees_paid", "fees_unpaid"
INT_COLL, PRIN_COLL, SWAP_NET = "interest_collections", "principal_funds", "swap_net"
RES_END, RES_DRAW, RES_TOPUP, RES_RELEASE = "reserve_end", "reserve_draw", "reserve_topup", "reserve_release"
RESID_CASH, ARREARS_SWEPT, BREACH_ANY = "residual_cash", "arrears_swept", "any_breach"
EXCESS_SPREAD, EXCESS_SPREAD_BP, TURBO = "excess_spread", "excess_spread_bp", "turbo_principal"
POOL_END, POOL_VALUE, POOL_DEFAULTS = "pool_end", "pool_value", "pool_defaults"
CUM_LOSS = "cum_net_loss"


def col(kind, cls):
    return f"{kind}_{cls}"


def _pay_seq(bal, amount):
    """Pay classes in seniority order; returns (paid per class, leftover)."""
    paid = np.zeros(len(bal))
    for i in range(len(bal)):
        x = min(bal[i], amount)
        paid[i], amount = x, amount - x
    return paid, amount


def _pay_pro_rata(bal, beg, amount):
    paid = np.zeros(len(bal))
    live = bal > cfg.BREACH_TOL
    while amount > 1e-9 and live.any():
        share = beg * live
        share = share / share.sum() if share.sum() > 0 else live / live.sum()
        add = np.minimum(share * amount, bal - paid)
        paid += add
        amount -= add.sum()
        newlive = (bal - paid) > cfg.BREACH_TOL
        if (newlive == live).all():
            break
        live = newlive
    return paid, amount


def run_waterfall(deal, scn):
    T = deal.horizon
    _, pool = project_pool(deal.pool, scn.cpr, scn.cdr, scn.severity, scn.lag, T)
    fwd = cfg.forward_curve(T, scn.index_shift)
    base_fwd = cfg.forward_curve(T)
    K = len(cfg.CLASSES)
    orig = np.array([cfg.NOTES[c][0] for c in cfg.CLASSES])
    oc_trig = np.array([cfg.NOTES[c][3] for c in cfg.CLASSES])
    ic_trig = np.array([cfg.NOTES[c][4] for c in cfg.CLASSES])
    bal = orig.copy()
    reserve = cfg.RESERVE_INITIAL
    carry_fee, carry_int = 0.0, np.zeros(K)
    sequential = not deal.pro_rata_start
    fee_rate = (cfg.SERVICING_FEE + cfg.TRUSTEE_FEE) / 12

    rows = []
    for t in range(1, T + 1):
        i = t - 1
        beg = bal.copy()
        coupon = np.array([(fwd[t] + cfg.NOTES[c][2]) if cfg.NOTES[c][1] else cfg.NOTES[c][2]
                           for c in cfg.CLASSES]) / 12
        fees_accr = pool["beg"][i] * fee_rate
        int_accr = beg * coupon
        swap = 0.0
        if deal.hedged:
            floating = sum(beg[k] for k, c in enumerate(cfg.CLASSES) if cfg.NOTES[c][1])
            swap = floating * (fwd[t] - deal.swap_fixed) / 12
        fees_due = carry_fee + fees_accr + max(-swap, 0.0)
        int_due = carry_int + int_accr
        ic = pool["interest"][i] + max(swap, 0.0)
        pc = pool["sched"][i] + pool["prepay"][i] + pool["recoveries"][i]

        # regular principal
        if sequential:
            reg, resid_prin = _pay_seq(bal, pc)
        else:
            reg, resid_prin = _pay_pro_rata(bal, beg, pc)
        pf = bal - reg
        cv = pool["value"][i]

        # tests on pro forma balances, before turbo
        breach = np.zeros(K, bool)
        oc = np.full(K, np.inf)
        icr = np.full(K, np.inf)
        for k in range(K):
            cum = pf[: k + 1].sum()
            if cum > cfg.BREACH_TOL:
                oc[k] = cv / cum
            den = int_accr[: k + 1].sum()
            if den > 1e-9:
                icr[k] = (ic - fees_accr - max(-swap, 0.0)) / den
            breach[k] = oc[k] < oc_trig[k] or icr[k] < ic_trig[k]

        avail = ic
        fee_paid = min(avail, fees_due)
        avail -= fee_paid
        int_paid = np.zeros(K)
        turbo_paid = np.zeros(K)

        def turbo(limit):
            nonlocal avail
            amt, _ = _pay_seq(pf - turbo_paid, min(avail, limit))
            turbo_paid[:] += amt
            avail -= amt.sum()

        for k in range(K):
            int_paid[k] = min(avail, int_due[k])
            avail -= int_paid[k]
            if breach[k]:
                turbo(avail)
        turbo(max(0.0, pf.sum() - turbo_paid.sum() - cv))     # cure principal deficiency
        topup = min(avail, max(0.0, deal.reserve_target - reserve)) if beg.sum() > cfg.BREACH_TOL else 0.0
        reserve += topup
        avail -= topup
        resid_int = avail

        # reserve draw for fees, A and B interest only
        draw = 0.0
        need = [("fee", fees_due - fee_paid), (0, int_due[0] - int_paid[0]), (1, int_due[1] - int_paid[1])]
        for key, short in need:
            x = min(max(short, 0.0), reserve)
            if key == "fee":
                fee_paid += x
            else:
                int_paid[key] += x
            reserve -= x
            draw += x

        # write-downs against C, B, A
        after = pf - turbo_paid
        defic = max(0.0, after.sum() - cv)
        wd = np.zeros(K)
        for k in reversed(range(K)):
            wd[k] = min(after[k], defic)
            defic -= wd[k]
        bal = after - wd
        bal[bal < cfg.BREACH_TOL] = 0.0

        carry_fee = fees_due - fee_paid
        carry_int = int_due - int_paid

        release = swept = 0.0
        resid_prin_cash = 0.0
        if bal.sum() <= cfg.BREACH_TOL:
            # notes retired: principal and reserve release settle arrears first, remainder to residual
            release, reserve = reserve, 0.0
            cash = resid_prin + release
            x = min(carry_fee, cash)
            carry_fee, cash, fee_paid, swept = carry_fee - x, cash - x, fee_paid + x, swept + x
            for k in range(K):
                x = min(carry_int[k], cash)
                carry_int[k] -= x
                int_paid[k] += x
                cash -= x
                swept += x
            resid_prin_cash = cash
        resid_cash = resid_int + resid_prin_cash

        rows.append(dict(
            beg=beg, fees_accr=fees_accr, fee_paid=fee_paid, int_accr=int_accr, int_paid=int_paid,
            prin=reg + turbo_paid, turbo=turbo_paid.sum(), wd=wd, bal=bal.copy(), oc=oc, ic=icr,
            breach=breach.copy(), swap=swap, ic_coll=ic, pc=pc, draw=draw, topup=topup, release=release,
            reserve=reserve, resid=resid_cash, resid_int=resid_int, resid_prin=resid_prin_cash,
            swept=swept, carry_fee=carry_fee, carry_int=carry_int.copy(), t=t, cv=cv,
        ))
        if breach.any():
            sequential = True
    return pool, fwd, rows


def results_frame(deal, scn):
    """Tidy per-period DataFrame for one scenario."""
    pool, fwd, rows = run_waterfall(deal, scn)
    T = deal.horizon
    d = {PERIOD: np.arange(1, T + 1), FWD: fwd[1:]}
    d[POOL_END], d[POOL_VALUE], d[POOL_DEFAULTS] = pool["end"], pool["value"], pool["defaults"]
    d[INT_COLL] = np.array([r["ic_coll"] for r in rows])
    d[PRIN_COLL] = np.array([r["pc"] for r in rows])
    d[SWAP_NET] = np.array([r["swap"] for r in rows])
    d[FEES_PAID] = np.array([r["fee_paid"] for r in rows])
    d[FEES_UNPAID] = np.array([r["carry_fee"] for r in rows])
    for k, c in enumerate(cfg.CLASSES):
        d[col("bal", c)] = np.array([r["bal"][k] for r in rows])
        d[col("int_paid", c)] = np.array([r["int_paid"][k] for r in rows])
        d[col("prin_paid", c)] = np.array([r["prin"][k] for r in rows])
        d[col("writedown", c)] = np.array([r["wd"][k] for r in rows])
        d[col("unpaid_int", c)] = np.array([r["carry_int"][k] for r in rows])
        d[col("oc", c)] = np.array([r["oc"][k] for r in rows])
        d[col("ic", c)] = np.array([r["ic"][k] for r in rows])
        d[col("breach", c)] = np.array([r["breach"][k] for r in rows])
    d[TURBO] = np.array([r["turbo"] for r in rows])
    d[RES_DRAW] = np.array([r["draw"] for r in rows])
    d[RES_TOPUP] = np.array([r["topup"] for r in rows])
    d[RES_RELEASE] = np.array([r["release"] for r in rows])
    d[RES_END] = np.array([r["reserve"] for r in rows])
    d[RESID_CASH] = np.array([r["resid"] for r in rows])
    d[col("resid", "int")] = np.array([r["resid_int"] for r in rows])
    d[col("resid", "prin")] = np.array([r["resid_prin"] for r in rows])
    d[ARREARS_SWEPT] = np.array([r["swept"] for r in rows])
    d[BREACH_ANY] = np.array([r["breach"].any() for r in rows])
    accr = np.array([r["int_accr"].sum() for r in rows])
    d[EXCESS_SPREAD] = (d[INT_COLL] - np.array([r["fees_accr"] for r in rows]) - accr
                        - np.maximum(-d[SWAP_NET], 0.0))
    beg_pool = pool["beg"]
    with np.errstate(divide="ignore", invalid="ignore"):
        d[EXCESS_SPREAD_BP] = np.where(beg_pool > 0, d[EXCESS_SPREAD] * 12 / beg_pool * 1e4, 0.0)
    d[CUM_LOSS] = np.cumsum(pool["defaults"]) - np.cumsum(pool["recoveries"])
    return pd.DataFrame(d)
