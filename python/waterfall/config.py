"""All deal inputs, scenarios and constants. Synthetic data: nothing here is market data."""
from dataclasses import dataclass, field, replace
import numpy as np

# ------------------------------------------------------------------ USER INPUTS
HORIZON_MONTHS = 120
BREACH_TOL = 1e-6          # dollars; below this a balance counts as retired
SHORTFALL_TOL = 1.0        # dollars; shortfall reporting threshold

# (name, balance USD, WAC, original term in months)
POOL_LINES = [
    ("L01", 39.0e6, 0.0730, 36), ("L02", 75.5e6, 0.0780, 48), ("L03", 21.6e6, 0.0830, 48),
    ("L04", 67.2e6, 0.0880, 60), ("L05", 19.0e6, 0.0930, 60), ("L06", 22.7e6, 0.0955, 60),
    ("L07", 35.2e6, 0.0980, 72), ("L08", 36.4e6, 0.1030, 72), ("L09", 36.1e6, 0.1055, 84),
    ("L10", 47.3e6, 0.1105, 84),
]

INDEX_START, INDEX_END, INDEX_DECAY_MONTHS = 0.0430, 0.0325, 24.0

# senior fees, annual, on beginning pool balance
SERVICING_FEE, TRUSTEE_FEE = 0.0060, 0.0003
RESERVE_INITIAL = 3.0e6

# class -> (original balance, floating?, margin or fixed coupon, OC trigger, IC trigger)
NOTES = {
    "A": (300.0e6, True, 0.0110, 1.25, 1.50),
    "B": (40.0e6, True, 0.0225, 1.12, 1.30),
    "C": (30.0e6, False, 0.0750, 1.045, 1.15),
}
CLASSES = list(NOTES)                    # A, B, C in seniority order
RESIDUAL = "R"

# retention / valuation assumptions
FV_MARGIN = {"A": 0.0110, "B": 0.0275, "C": 0.0450, "R": 0.1200}
ADVANCE_RATE = {"A": 0.85, "B": 0.70, "C": 0.55, "R": 0.30}
FINANCING_MARGIN = 0.0275
RETENTION_PCT = 0.05

# optional swap on floating notes (improvement over the unhedged base deal)
SWAP_FIXED_RATE = 0.0360


@dataclass(frozen=True)
class Scenario:
    name: str
    cpr: float
    cdr: float
    severity: float
    lag: int
    index_shift: float = 0.0


SCENARIOS = [
    Scenario("Base", 0.10, 0.03, 0.40, 6),
    Scenario("Mild stress", 0.10, 0.06, 0.50, 6),
    Scenario("Stress", 0.06, 0.12, 0.60, 9),
    Scenario("Severe stress", 0.04, 0.20, 0.70, 12),
    Scenario("Fast prepay", 0.30, 0.03, 0.40, 6),
    Scenario("Rate shock", 0.10, 0.06, 0.50, 6, 0.03),
]
BASE, STRESS = SCENARIOS[0], SCENARIOS[2]
# ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Deal:
    pool: tuple = tuple(POOL_LINES)
    horizon: int = HORIZON_MONTHS
    pro_rata_start: bool = False       # False: sequential from period 1
    hedged: bool = False
    swap_fixed: float = SWAP_FIXED_RATE

    @property
    def pool_balance(self):
        return sum(p[1] for p in self.pool)

    @property
    def reserve_target(self):
        return RESERVE_INITIAL

    def original(self, cls):
        return NOTES[cls][0]

    @property
    def residual_par(self):
        return self.pool_balance + RESERVE_INITIAL - sum(NOTES[c][0] for c in CLASSES)

    def with_(self, **kw):
        return replace(self, **kw)


def forward_curve(horizon, shift=0.0):
    """fwd[t] for t = 0..horizon; the coupon for period t uses fwd[t] (set in arrears)."""
    t = np.arange(horizon + 1)
    return INDEX_START + (INDEX_END - INDEX_START) * (1 - np.exp(-t / INDEX_DECAY_MONTHS)) + shift


def monthly_rate(annual):
    return 1 - (1 - annual) ** (1 / 12)
