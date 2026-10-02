"""Rep-line pool projection: defaults, interest, level-pay principal, prepayments, lagged recoveries."""
import numpy as np
from .config import monthly_rate

KEYS = ("beg", "defaults", "interest", "sched", "prepay", "end")


def project_pool(pool, cpr, cdr, severity, lag, horizon):
    """Returns per-line arrays shaped (lines, horizon) for months 1..horizon plus pool totals."""
    bal0 = np.array([p[1] for p in pool], float)
    wac = np.array([p[2] for p in pool], float)
    term = np.array([p[3] for p in pool], int)
    n = len(pool)
    mdr, smm = monthly_rate(cdr), monthly_rate(cpr)
    out = {k: np.zeros((n, horizon)) for k in KEYS}
    bal = bal0.copy()
    r = wac / 12
    for t in range(horizon):
        rem = term - t
        active = rem > 0
        beg = np.where(active, bal, 0.0)
        d = beg * mdr
        perf = beg - d
        interest = perf * r
        with np.errstate(divide="ignore", invalid="ignore"):
            pmt = np.where(rem > 1, perf * r / (1 - (1 + r) ** (-np.maximum(rem, 1))), perf + interest)
        sched = np.where(active, np.minimum(pmt - interest, perf), 0.0)
        pre = (perf - sched) * smm
        end = beg - d - sched - pre
        for k, v in zip(KEYS, (beg, d, interest, sched, pre, end)):
            out[k][:, t] = v
        bal = np.where(active, end, 0.0)
    tot = {k: v.sum(axis=0) for k, v in out.items()}
    rec = np.zeros(horizon)
    shifted = (1 - severity) * tot["defaults"]
    if lag < horizon:
        rec[lag:] = shifted[: horizon - lag]
    # recoveries scheduled but not yet received at the end of each period
    pend = np.cumsum(shifted) - np.cumsum(rec)
    tot.update(recoveries=rec, pending=pend, value=tot["end"] + pend,
               cash=tot["interest"] + tot["sched"] + tot["prepay"] + rec)
    return out, tot
