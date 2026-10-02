"""Monte Carlo over credit and rate drivers: tranche loss probability, expected loss and tail (improvement)."""
from dataclasses import replace
import numpy as np
import pandas as pd
from . import config as cfg
from . import waterfall as wf
from . import metrics as mt

# ------------------------------------------------------------------ USER INPUTS
N_SIMS = 1000
SEED = 7
CDR_MEDIAN, CDR_SIGMA = 0.04, 0.60          # lognormal annual CDR
CPR_MEAN, CPR_SD = 0.10, 0.04               # clipped normal annual CPR
SEV_ALPHA, SEV_BETA = 6.0, 6.0              # beta severity, mean 50%
LAG_CHOICES = (3, 6, 9, 12)
SHIFT_SD = 0.01                              # normal parallel index shift
# ------------------------------------------------------------------------------

LOSS_PCT, LOSS_PROB = "expected_loss_pct", "prob_loss"
P95_LOSS, MEAN_WAL = "p95_loss_pct", "mean_wal"


def draw_scenarios(n=N_SIMS, seed=SEED):
    rng = np.random.default_rng(seed)
    return [cfg.Scenario(f"mc{i}",
                         float(np.clip(rng.normal(CPR_MEAN, CPR_SD), 0.0, 0.5)),
                         float(min(CDR_MEDIAN * np.exp(CDR_SIGMA * rng.standard_normal()), 0.6)),
                         float(rng.beta(SEV_ALPHA, SEV_BETA)),
                         int(rng.choice(LAG_CHOICES)),
                         float(rng.normal(0.0, SHIFT_SD)))
            for i in range(n)]


def simulate(deal, n=N_SIMS, seed=SEED):
    """Per-simulation write-down % (and unpaid-interest flag) by class, and the summary table."""
    rows = []
    for s in draw_scenarios(n, seed):
        df = wf.results_frame(deal, s)
        summ = mt.summarize(df, deal, s)
        row = {"cdr": s.cdr, "severity": s.severity, "shift": s.index_shift}
        for c in mt.CLASS_ORDER:
            row[f"loss_{c}"] = summ[c]["writedown_pct"]
            row[f"short_{c}"] = summ[c]["any_shortfall"]
            row[f"wal_{c}"] = summ[c]["wal"]
        rows.append(row)
    sims = pd.DataFrame(rows)
    table = pd.DataFrame({
        c: {LOSS_PROB: sims[f"short_{c}"].mean(), LOSS_PCT: sims[f"loss_{c}"].mean(),
            P95_LOSS: sims[f"loss_{c}"].quantile(0.95), MEAN_WAL: sims[f"wal_{c}"].mean()}
        for c in mt.CLASS_ORDER}).T
    return sims, table
