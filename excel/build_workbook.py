"""Builds securitization_waterfall.xlsx: live formulas for the pool, waterfall, tests and tranche summary.

Typed numbers live on Inputs only (plus integer period indexes and zero opening seeds on the time-series
sheets). Change "Selected scenario number" on Inputs and every sheet recalculates.
Run: py -3 build_workbook.py
"""
import re
import sys
from pathlib import Path
import openpyxl
from openpyxl.utils import get_column_letter as L

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))
from waterfall import config as cfg  # noqa: E402

# ------------------------------------------------------------------ USER INPUTS
OUT_FILE = Path(__file__).resolve().parent / "securitization_waterfall.xlsx"
AUTHOR = "Deepak Chaudhary"
HORIZON = cfg.HORIZON_MONTHS
SEED_ROW, FIRST_ROW = 5, 6                 # t = 0 seed row, t = 1 row
LAST_ROW = FIRST_ROW + HORIZON - 1
N_CUSTOM_SCENARIOS = 4                      # spare scenario slots for probes
# ------------------------------------------------------------------------------

wb = openpyxl.Workbook()
wb.properties.creator = AUTHOR
wb.properties.lastModifiedBy = AUTHOR

# ============================================================== Inputs
ws_in = wb.active
ws_in.title = "Inputs"
NAMES = {}                                   # input name -> absolute reference
_row = [3]


def put(name, label, value, note=""):
    r = _row[0]
    ws_in.cell(r, 1, label)
    ws_in.cell(r, 2, value)
    ws_in.cell(r, 3, note)
    NAMES[name] = f"Inputs!$B${r}"
    _row[0] += 1
    return r


ws_in["A1"] = "Securitization waterfall: inputs (synthetic data, not a real transaction)"
sel_row = put("sel", "Selected scenario number", 1, "1 to 6 standard; 7 to 10 custom slots")
SCN_FIRST = 45
ncols = ["name", "cpr", "cdr", "sev", "lag", "shift"]
for i, h in enumerate(["Scenario", "CPR", "CDR", "Severity", "Lag (months)", "Index shift"]):
    ws_in.cell(SCN_FIRST - 1, 1 + i, h)
scn_rows = list(cfg.SCENARIOS) + [cfg.Scenario(f"Custom {i + 1}", 0.10, 0.03, 0.40, 6) for i in range(N_CUSTOM_SCENARIOS)]
for i, s in enumerate(scn_rows):
    for j, v in enumerate([s.name, s.cpr, s.cdr, s.severity, s.lag, s.index_shift]):
        ws_in.cell(SCN_FIRST + i, 1 + j, v)
SCN_LAST = SCN_FIRST + len(scn_rows) - 1
for j, key in enumerate(["cpr", "cdr", "sev", "lag", "shift"], start=2):
    rng = f"Inputs!${L(j)}${SCN_FIRST}:${L(j)}${SCN_LAST}"
    r = put(key, f"Selected {key}", f"=INDEX({rng},{NAMES['sel']})")
put("mdr", "Monthly default rate", f"=1-(1-{NAMES['cdr']})^(1/12)")
put("smm", "Single monthly mortality", f"=1-(1-{NAMES['cpr']})^(1/12)")
put("idx_start", "Index start", cfg.INDEX_START)
put("idx_end", "Index long run", cfg.INDEX_END)
put("idx_decay", "Index decay (months)", cfg.INDEX_DECAY_MONTHS)
put("fee_rate", "Senior fees, annual (servicing + trustee)", cfg.SERVICING_FEE + cfg.TRUSTEE_FEE)
put("reserve0", "Initial and target reserve", cfg.RESERVE_INITIAL)
put("prorata", "Pro rata start (1) or sequential (0)", 0)
put("hedge", "Swap hedge on floating notes (1/0)", 0)
put("swap_fixed", "Swap fixed rate", cfg.SWAP_FIXED_RATE)
put("tol", "Retirement tolerance (USD)", cfg.BREACH_TOL)
put("sf_tol", "Shortfall tolerance (USD)", cfg.SHORTFALL_TOL)
for c in cfg.CLASSES:
    orig, flt, mar, oc, ic = cfg.NOTES[c]
    put(f"orig_{c}", f"Class {c} original balance", orig)
    put(f"float_{c}", f"Class {c} floating (1) or fixed (0)", 1 if flt else 0)
    put(f"margin_{c}", f"Class {c} margin or fixed coupon", mar)
    put(f"oc_{c}", f"Class {c} OC trigger", oc)
    put(f"ic_{c}", f"Class {c} IC trigger", ic)
POOL_FIRST = SCN_LAST + 4
ws_in.cell(POOL_FIRST - 1, 1, "Pool rep lines")
for i, h in enumerate(["Line", "Balance", "WAC", "Term (months)"]):
    ws_in.cell(POOL_FIRST - 1 + 0, 1 + i, h if i else "Pool rep line")
for i, (nm, bal, wac, term) in enumerate(cfg.POOL_LINES):
    for j, v in enumerate([nm, bal, wac, term]):
        ws_in.cell(POOL_FIRST + i, 1 + j, v)
POOL_LAST = POOL_FIRST + len(cfg.POOL_LINES) - 1
put("pool_bal", "Pool balance", f"=SUM(Inputs!$B${POOL_FIRST}:$B${POOL_LAST})")
put("resid_par", "Residual par (pool + reserve - notes)",
    f"={NAMES['pool_bal']}+{NAMES['reserve0']}-{NAMES['orig_A']}-{NAMES['orig_B']}-{NAMES['orig_C']}")
ws_in.column_dimensions["A"].width = 46
ws_in.column_dimensions["B"].width = 16


# ============================================================== column registry helper
class Sheet:
    def __init__(self, ws, title):
        self.ws, self.title, self.cols, self.next = ws, title, {}, 1
        self.pending = []                       # (name, template, seed)

    def add(self, name, template, seed=None, header=None):
        self.cols[name] = self.next
        self.ws.cell(4, self.next, header or name)
        self.pending.append((name, template, seed))
        self.next += 1

    def ref(self, name, row, absolute_col=False):
        return f"{'$' if absolute_col else ''}{L(self.cols[name])}{row}"

    def build(self, other=None):
        for name, template, seed in self.pending:
            c = self.cols[name]
            if seed is not None:
                self.ws.cell(SEED_ROW, c, seed(self) if callable(seed) else seed)
            for r in range(FIRST_ROW, LAST_ROW + 1):
                self.ws.cell(r, c, "=" + self.expand(template, r, other))

    def expand(self, template, r, other):
        def sub(m):
            tok = m.group(1)
            if tok.startswith("I:"):
                return NAMES[tok[2:]]
            if tok.startswith("P:"):
                return f"Pool!{L(other.cols[tok[2:]])}{r}"
            if tok.endswith(":p"):
                return self.ref(tok[:-2], r - 1)
            if tok.endswith(":col"):
                n = tok[:-4]
                return f"${L(self.cols[n])}${FIRST_ROW}:${L(self.cols[n])}${LAST_ROW}"
            return self.ref(tok, r)
        return re.sub(r"\[\[([^\]]+)\]\]", sub, template)


# ============================================================== Pool
ws_pool = wb.create_sheet("Pool")
pool = Sheet(ws_pool, "Pool")
pool.add("t", "ROW()-5", header="Period")
for i in range(len(cfg.POOL_LINES)):
    ir = POOL_FIRST + i
    bal, wac, term = f"Inputs!$B${ir}", f"Inputs!$C${ir}", f"Inputs!$D${ir}"
    p = f"L{i + 1}_"
    pool.add(p + "rem", f"{term}-([[t]]-1)")
    pool.add(p + "beg", f"IF([[{p}rem]]<=0,0,IF([[t]]=1,{bal},[[{p}end:p]]))")
    pool.add(p + "def", f"[[{p}beg]]*[[I:mdr]]")
    pool.add(p + "int", f"([[{p}beg]]-[[{p}def]])*{wac}/12")
    pool.add(p + "sch", (f"IF([[{p}rem]]<=0,0,IF([[{p}rem]]=1,[[{p}beg]]-[[{p}def]],"
                         f"MIN(([[{p}beg]]-[[{p}def]])*({wac}/12)/(1-(1+{wac}/12)^(-[[{p}rem]]))-[[{p}int]],[[{p}beg]]-[[{p}def]])))"))
    pool.add(p + "pre", f"([[{p}beg]]-[[{p}def]]-[[{p}sch]])*[[I:smm]]")
    pool.add(p + "end", f"[[{p}beg]]-[[{p}def]]-[[{p}sch]]-[[{p}pre]]", seed=0)
for k, key in (("beg", "beg"), ("defaults", "def"), ("interest", "int"), ("sched", "sch"), ("prepay", "pre"), ("end", "end")):
    pool.add("tot_" + k, "+".join(f"[[L{i + 1}_{key}]]" for i in range(len(cfg.POOL_LINES))), seed=0 if k == "end" else None)
pool.add("recov", "IF([[t]]>[[I:lag]],(1-[[I:sev]])*INDEX([[tot_defaults:col]],[[t]]-[[I:lag]]),0)")
pool.add("pending", "[[pending:p]]+(1-[[I:sev]])*[[tot_defaults]]-[[recov]]", seed=0)
pool.add("value", "[[tot_end]]+[[pending]]")
pool.add("cash", "[[tot_interest]]+[[tot_sched]]+[[tot_prepay]]+[[recov]]")
pool.add("cum_loss", "[[cum_loss:p]]+[[tot_defaults]]-[[recov]]", seed=0)
pool.build()

# ============================================================== Waterfall
ws_wf = wb.create_sheet("Waterfall")
wf = Sheet(ws_wf, "Waterfall")
K = cfg.CLASSES
wf.add("t", "ROW()-5", header="Period")
wf.add("fwd", "[[I:idx_start]]+([[I:idx_end]]-[[I:idx_start]])*(1-EXP(-[[t]]/[[I:idx_decay]]))+[[I:shift]]")
for c in K:
    wf.add(f"beg_{c}", f"[[bal_{c}:p]]")
for c in K:
    wf.add(f"cpn_{c}", f"IF([[I:float_{c}]]=1,[[fwd]]+[[I:margin_{c}]],[[I:margin_{c}]])/12")
wf.add("fees_accr", "[[P:tot_beg]]*[[I:fee_rate]]/12")
for c in K:
    wf.add(f"accr_{c}", f"[[beg_{c}]]*[[cpn_{c}]]")
wf.add("swap", "[[I:hedge]]*(" + "+".join(f"[[beg_{c}]]*[[I:float_{c}]]" for c in K) + ")*([[fwd]]-[[I:swap_fixed]])/12")
wf.add("fees_due", "[[carry_fee:p]]+[[fees_accr]]+MAX(-[[swap]],0)")
for c in K:
    wf.add(f"due_{c}", f"[[carry_{c}:p]]+[[accr_{c}]]")
wf.add("ic", "[[P:tot_interest]]+MAX([[swap]],0)")
wf.add("pc", "[[P:tot_sched]]+[[P:tot_prepay]]+[[P:recov]]")
wf.add("seq", "MAX([[seq:p]],[[any_breach:p]])", seed=lambda s: f"=1-{NAMES['prorata']}")
# sequential regular principal
wf.add("sq_A", "MIN([[beg_A]],[[pc]])")
wf.add("sq_B", "MIN([[beg_B]],[[pc]]-[[sq_A]])")
wf.add("sq_C", "MIN([[beg_C]],[[pc]]-[[sq_A]]-[[sq_B]])")
# pro rata water-filling, three rounds
for rd in (1, 2, 3):
    prior = (lambda c: "0") if rd == 1 else (lambda c, rd=rd: "+".join(f"[[pr{q}_{c}]]" for q in range(1, rd)))
    rest = "[[pc]]" if rd == 1 else "([[pc]]-(" + "+".join(f"[[pr{q}_{c2}]]" for q in range(1, rd) for c2 in K) + "))"
    for c in K:
        wf.add(f"lv{rd}_{c}", f"IF([[beg_{c}]]-({prior(c)})>[[I:tol]],1,0)")
    wsum = "(" + "+".join(f"[[lv{rd}_{c2}]]*[[beg_{c2}]]" for c2 in K) + ")"
    for c in K:
        wf.add(f"pr{rd}_{c}", f"IF({wsum}>0,MIN([[beg_{c}]]-({prior(c)}),{rest}*[[lv{rd}_{c}]]*[[beg_{c}]]/{wsum}),0)")
for c in K:
    wf.add(f"reg_{c}", f"IF([[seq]]=1,[[sq_{c}]],[[pr1_{c}]]+[[pr2_{c}]]+[[pr3_{c}]])")
wf.add("resid_prin_raw", "[[pc]]-" + "-".join(f"[[reg_{c}]]" for c in K))
for c in K:
    wf.add(f"pf_{c}", f"[[beg_{c}]]-[[reg_{c}]]")
wf.add("cv", "[[P:value]]")
for i, c in enumerate(K):
    cum = "+".join(f"[[pf_{k}]]" for k in K[: i + 1])
    den = "+".join(f"[[accr_{k}]]" for k in K[: i + 1])
    wf.add(f"oc_{c}", f"IF(({cum})>[[I:tol]],[[cv]]/({cum}),1E+9)")
    wf.add(f"icr_{c}", f"IF(({den})>0.000000001,([[ic]]-[[fees_accr]]-MAX(-[[swap]],0))/({den}),1E+9)")
    wf.add(f"br_{c}", f"IF(OR([[oc_{c}]]<[[I:oc_{c}]],[[icr_{c}]]<[[I:ic_{c}]]),1,0)")
wf.add("any_breach", "MAX([[br_A]],[[br_B]],[[br_C]])", seed=0)
# interest waterfall
wf.add("fee1", "MIN([[ic]],[[fees_due]])")
wf.add("av1", "[[ic]]-[[fee1]]")
wf.add("intA1", "MIN([[av1]],[[due_A]])")
wf.add("av2", "[[av1]]-[[intA1]]")


def turbo_block(tag, flag, avail_in, cap_expr):
    """Sequential turbo of `avail_in` into A, B, C against remaining capacity; returns avail_out column name."""
    wf.add(f"{tag}_A", f"{flag}*MIN({cap_expr('A')},{avail_in})")
    wf.add(f"{tag}_B", f"{flag}*MIN({cap_expr('B')},{avail_in}-[[{tag}_A]])")
    wf.add(f"{tag}_C", f"{flag}*MIN({cap_expr('C')},{avail_in}-[[{tag}_A]]-[[{tag}_B]])")


turbo_block("t1", "[[br_A]]", "[[av2]]", lambda c: f"[[pf_{c}]]")
wf.add("av3", "[[av2]]-[[t1_A]]-[[t1_B]]-[[t1_C]]")
wf.add("intB1", "MIN([[av3]],[[due_B]])")
wf.add("av4", "[[av3]]-[[intB1]]")
turbo_block("t2", "[[br_B]]", "[[av4]]", lambda c: f"([[pf_{c}]]-[[t1_{c}]])")
wf.add("av5", "[[av4]]-[[t2_A]]-[[t2_B]]-[[t2_C]]")
wf.add("intC1", "MIN([[av5]],[[due_C]])")
wf.add("av6", "[[av5]]-[[intC1]]")
turbo_block("t3", "[[br_C]]", "[[av6]]", lambda c: f"([[pf_{c}]]-[[t1_{c}]]-[[t2_{c}]])")
wf.add("av7", "[[av6]]-[[t3_A]]-[[t3_B]]-[[t3_C]]")
wf.add("defic0", "MAX(0," + "+".join(f"[[pf_{c}]]" for c in K) + "-" +
       "-".join(f"[[t{q}_{c}]]" for q in (1, 2, 3) for c in K) + "-[[cv]])")
turbo_block("t4", "1", "MIN([[av7]],[[defic0]])", lambda c: f"([[pf_{c}]]-[[t1_{c}]]-[[t2_{c}]]-[[t3_{c}]])")
wf.add("av8", "[[av7]]-[[t4_A]]-[[t4_B]]-[[t4_C]]")
wf.add("topup", "IF(" + "+".join(f"[[beg_{c}]]" for c in K) + ">[[I:tol]],MIN([[av8]],MAX(0,[[I:reserve0]]-[[reserve_end:p]])),0)")
wf.add("resid_int", "[[av8]]-[[topup]]")
wf.add("res1", "[[reserve_end:p]]+[[topup]]")
wf.add("dr_fee", "MIN(MAX([[fees_due]]-[[fee1]],0),[[res1]])")
wf.add("dr_A", "MIN(MAX([[due_A]]-[[intA1]],0),[[res1]]-[[dr_fee]])")
wf.add("dr_B", "MIN(MAX([[due_B]]-[[intB1]],0),[[res1]]-[[dr_fee]]-[[dr_A]])")
wf.add("draw", "[[dr_fee]]+[[dr_A]]+[[dr_B]]")
wf.add("res2", "[[res1]]-[[draw]]")
wf.add("fee2", "[[fee1]]+[[dr_fee]]")
wf.add("ipA2", "[[intA1]]+[[dr_A]]")
wf.add("ipB2", "[[intB1]]+[[dr_B]]")
wf.add("ipC2", "[[intC1]]")
for c in K:
    wf.add(f"aft_{c}", f"[[pf_{c}]]-[[t1_{c}]]-[[t2_{c}]]-[[t3_{c}]]-[[t4_{c}]]")
wf.add("defic", "MAX(0," + "+".join(f"[[aft_{c}]]" for c in K) + "-[[cv]])")
wf.add("wd_C", "MIN([[aft_C]],[[defic]])")
wf.add("wd_B", "MIN([[aft_B]],[[defic]]-[[wd_C]])")
wf.add("wd_A", "MIN([[aft_A]],[[defic]]-[[wd_C]]-[[wd_B]])")
for c in K:
    wf.add(f"bal_{c}", f"IF([[aft_{c}]]-[[wd_{c}]]<[[I:tol]],0,[[aft_{c}]]-[[wd_{c}]])",
           seed=lambda s, c=c: f"={NAMES['orig_' + c]}")
wf.add("cf1", "[[fees_due]]-[[fee2]]")
for c in K:
    wf.add(f"ci1_{c}", f"[[due_{c}]]-[[ip{c}2]]")
wf.add("retired", "IF(" + "+".join(f"[[bal_{c}]]" for c in K) + "<=[[I:tol]],1,0)")
wf.add("release", "[[retired]]*[[res2]]")
wf.add("cash", "[[retired]]*([[resid_prin_raw]]+[[release]])")
wf.add("sw_fee", "[[retired]]*MIN([[cf1]],[[cash]])")
wf.add("sw_A", "[[retired]]*MIN([[ci1_A]],[[cash]]-[[sw_fee]])")
wf.add("sw_B", "[[retired]]*MIN([[ci1_B]],[[cash]]-[[sw_fee]]-[[sw_A]])")
wf.add("sw_C", "[[retired]]*MIN([[ci1_C]],[[cash]]-[[sw_fee]]-[[sw_A]]-[[sw_B]])")
wf.add("swept", "[[sw_fee]]+[[sw_A]]+[[sw_B]]+[[sw_C]]")
wf.add("reserve_end", "[[res2]]-[[release]]", seed=lambda s: f"={NAMES['reserve0']}")
wf.add("carry_fee", "[[cf1]]-[[sw_fee]]", seed=0)
for c in K:
    wf.add(f"carry_{c}", f"[[ci1_{c}]]-[[sw_{c}]]", seed=0)
wf.add("fees_paid", "[[fee2]]+[[sw_fee]]")
for c in K:
    wf.add(f"int_{c}", f"[[ip{c}2]]+[[sw_{c}]]")
    wf.add(f"prin_{c}", f"[[reg_{c}]]+[[t1_{c}]]+[[t2_{c}]]+[[t3_{c}]]+[[t4_{c}]]")
wf.add("resid_prin", "[[cash]]-[[swept]]")
wf.add("resid_cash", "[[resid_int]]+[[resid_prin]]")
wf.add("excess_spread", "[[ic]]-[[fees_accr]]-MAX(-[[swap]],0)-[[accr_A]]-[[accr_B]]-[[accr_C]]")
wf.add("conservation_gap",
       "[[ic]]+[[pc]]+[[draw]]+[[release]]-[[topup]]-[[fees_paid]]-[[int_A]]-[[int_B]]-[[int_C]]"
       "-[[prin_A]]-[[prin_B]]-[[prin_C]]-[[resid_cash]]")
wf.add("bad_residual", "IF(AND([[resid_cash]]>0.000001,[[carry_fee]]+[[carry_A]]+[[carry_B]]+[[carry_C]]>0.000001),1,0)")
wf.build(pool)

# ============================================================== CashFlows
ws_cf = wb.create_sheet("CashFlows")
cf = Sheet(ws_cf, "CashFlows")
cf.add("t", "ROW()-5", header="Period")
PARS = {c: NAMES[f"orig_{c}"] for c in K}
PARS["R"] = NAMES["resid_par"]
for c in K:
    cf.add(f"cf_{c}", f"Waterfall!{L(wf.cols['int_' + c])}ROWNUM+Waterfall!{L(wf.cols['prin_' + c])}ROWNUM",
           seed=lambda s, c=c: f"=-{PARS[c]}")
cf.add("cf_R", f"Waterfall!{L(wf.cols['resid_cash'])}ROWNUM", seed=lambda s: f"=-{PARS['R']}")
for name, template, seed in cf.pending:
    col_ = cf.cols[name]
    ws_cf.cell(SEED_ROW, col_, seed(cf) if callable(seed) else seed) if seed is not None else None
    for r in range(FIRST_ROW, LAST_ROW + 1):
        ws_cf.cell(r, col_, "=" + template.replace("ROWNUM", str(r)))

# ============================================================== Scenario_Summary
ws_s = wb.create_sheet("Scenario_Summary")
ws_s["A1"] = "Tranche summary for the selected scenario (Python-only: break-even CDR, discount margin, retention)"
heads = ["Class", "Par", "WAL (yrs)", "Yield at par", "First principal month", "Last principal month",
         "Interest paid", "Cash received", "Write-down", "Unpaid interest end", "Unpaid interest peak", "Any shortfall"]
for j, h in enumerate(heads, start=1):
    ws_s.cell(3, j, h)
rng = lambda sheet, obj, name: f"{sheet}!${L(obj.cols[name])}${FIRST_ROW}:${L(obj.cols[name])}${LAST_ROW}"
rng0 = lambda sheet, obj, name: f"{sheet}!${L(obj.cols[name])}${SEED_ROW}:${L(obj.cols[name])}${LAST_ROW}"
tcol = rng("Waterfall", wf, "t")
for i, c in enumerate(K + ["R"]):
    r = 4 + i
    ws_s.cell(r, 1, c)
    ws_s.cell(r, 2, f"={PARS[c]}")
    prin = rng("Waterfall", wf, "resid_prin") if c == "R" else rng("Waterfall", wf, f"prin_{c}")
    ws_s.cell(r, 3, f'=IF(SUM({prin})>0,SUMPRODUCT({tcol},{prin})/12/SUM({prin}),"n/a")')
    ws_s.cell(r, 4, f'=IF(SUM({rng("CashFlows", cf, "cf_" + c)})>0,IFERROR(IRR({rng0("CashFlows", cf, "cf_" + c)},0.005),IFERROR(IRR({rng0("CashFlows", cf, "cf_" + c)},-0.03),IFERROR(IRR({rng0("CashFlows", cf, "cf_" + c)},-0.1),IFERROR(IRR({rng0("CashFlows", cf, "cf_" + c)},-0.3),IRR({rng0("CashFlows", cf, "cf_" + c)},-0.6)))))*12,"n/a")')
    ws_s.cell(r, 5, f'=IFERROR(MINIFS({tcol},{prin},">1"),"n/a")')
    ws_s.cell(r, 6, f'=IFERROR(MAXIFS({tcol},{prin},">1"),"n/a")')
    if c == "R":
        ws_s.cell(r, 7, 0)
        ws_s.cell(r, 8, f"=SUM({rng('CashFlows', cf, 'cf_R')})")
        ws_s.cell(r, 9, f"=MAX(B{r}-H{r},0)")
        ws_s.cell(r, 10, 0)
        ws_s.cell(r, 11, 0)
    else:
        ws_s.cell(r, 7, f"=SUM({rng('Waterfall', wf, 'int_' + c)})")
        ws_s.cell(r, 8, f"=SUM({rng('CashFlows', cf, 'cf_' + c)})")
        ws_s.cell(r, 9, f"=SUM({rng('Waterfall', wf, 'wd_' + c)})")
        ws_s.cell(r, 10, f"=Waterfall!{L(wf.cols['carry_' + c])}{LAST_ROW}")
        ws_s.cell(r, 11, f"=MAX({rng('Waterfall', wf, 'carry_' + c)})")
    ws_s.cell(r, 12, f"=IF(OR(I{r}>{NAMES['sf_tol']},J{r}>{NAMES['sf_tol']},K{r}>{NAMES['sf_tol']}),1,0)")
ws_s["A10"] = "Junior-most class with a write-down"
ws_s["B10"] = '=IF(I7>Inputs!$B$%d,"R",IF(I6>Inputs!$B$%d,"C",IF(I5>Inputs!$B$%d,"B",IF(I4>Inputs!$B$%d,"A","none"))))' % ((int(NAMES["sf_tol"].split("$")[-1]),) * 4)
ws_s["A11"] = "Most senior class with a write-down"
ws_s["B11"] = '=IF(I4>Inputs!$B$%d,"A",IF(I5>Inputs!$B$%d,"B",IF(I6>Inputs!$B$%d,"C",IF(I7>Inputs!$B$%d,"R","none"))))' % ((int(NAMES["sf_tol"].split("$")[-1]),) * 4)
ws_s["A12"] = "Cumulative net loss, % of original pool"
ws_s["B12"] = f"=Pool!{L(pool.cols['cum_loss'])}{LAST_ROW}/{NAMES['pool_bal']}"
ws_s["A14"] = "Integrity checks"
ws_s["A15"] = "Max abs cash conservation gap"
ws_s["B15"] = f"=MAX(MAX({rng('Waterfall', wf, 'conservation_gap')}),-MIN({rng('Waterfall', wf, 'conservation_gap')}))"
ws_s["A16"] = "Periods with residual paid while arrears remain"
ws_s["B16"] = f"=SUM({rng('Waterfall', wf, 'bad_residual')})"
ws_s["A17"] = "Months with any test breach"
ws_s["B17"] = f"=SUM({rng('Waterfall', wf, 'any_breach')})"
ws_s["A18"] = "Max abs pool roll-forward difference (ending pool balance at horizon)"
ws_s["B18"] = f"=ABS(Pool!{L(pool.cols['tot_end'])}{LAST_ROW})"
ws_s["A19"] = "All checks pass (1 = yes)"
ws_s["B19"] = "=IF(AND(B15<0.001,B16=0),1,0)"
ws_s.column_dimensions["A"].width = 52

# map for reconcile script
import json  # noqa: E402
meta = {"wf": wf.cols, "pool": pool.cols, "cf": cf.cols, "names": NAMES, "first": FIRST_ROW, "last": LAST_ROW,
        "scn_first": SCN_FIRST}
OUT_FILE.with_suffix(".meta.json").write_text(json.dumps(meta))
wb.save(OUT_FILE)
print("saved", OUT_FILE, "formulas:", sum(1 for ws in wb for row in ws.iter_rows() for c in row
                                          if isinstance(c.value, str) and c.value.startswith("=")))
