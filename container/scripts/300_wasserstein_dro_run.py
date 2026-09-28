#!/usr/bin/env python3
"""Direct-family Qlib runner: certified Wasserstein-hyperplane robust portfolio.

Registered hypothesis, record excerpts and the frozen candidate body live in the
family round-spec; this engine implements only what they require.

Registered mechanism (source = canonical wiki record, excerpted in the candidate body):
    1. Order-1 Wasserstein ambiguity ball of radius eps around the empirical return
       distribution of a rolling N-observation matrix X in R^{N x n} (record: N in
       [20, 252] trading days), long-only simplex W subset Delta_n, compact box
       support [x_min, x_max] estimated per rolling window (sample min/max per asset,
       x_min clipped to > -1), l1 ground norm / l_inf dual norm.
    2. Supporting-hyperplane majorization of the logarithmic Kelly utility
       U(y) = log(1+y) on the scalar return range by M tangent hyperplanes
       h_m(y) = alpha_m y + beta_m, with the record's uniform error certificate:
       mesh dy <= sqrt(8*eta/L_f) guarantees 0 <= Uhat_M(y) - U(y) <= eta on the
       certified interval, where L_f = sup|U''| on that interval. eta = 1e-3 is the
       record's prescribed tolerance.
    3. Box-specialized dual LP (record Corollary 3.7 shape, n + 1 + N + n*M scalar
       variables): maximize  -lam*eps + (1/N) sum_j a_j  s.t.
           a_j + <X_j - x_min, s^m> - alpha_m <w, X_j> <= beta_m   for all j, m
           s^m >= alpha_m w - lam*1,  s^m >= 0                      for all m
           w >= 0, 1'w = 1, lam >= 0
       solved as one LP per rebalance epoch (HiGHS), no cutting planes.
    4. Large-ambiguity finite threshold (record Theorem 3.13 / Corollary 3.15): for
       eps >= eps_bar = sup_{x,x'} ||x-x'||_1 = sum_i (x_max_i - x_min_i) the ball is
       the whole simplex over the support and the optimizer collapses to the closed
       maximin rule over argmax_i (x_min_i). Asserted in-run and by the self-check.

RECORD TRANSCRIPTION NOTE (registered before compute, disclosed in round-spec):
    the sign convention transcribed for Theorem 3.6 / Corollary 3.7 in the canonical
    record (+lam*eps in the objective, -lam*<1, X_j - x_min> inside the a_j bound)
    inverts the record's own claims: taken verbatim it makes eps -> 0 collapse to the
    maximin rule and eps >= eps_bar unbounded, contradicting the record's Research
    interpretation (small eps tracks SAA, moderate eps is risk-adjusted, large eps is
    near-equal weighting) and its Theorem 3.13. The engine therefore implements the
    sign-consistent Kantorovich dual that reproduces BOTH stated anchors: eps = 0 is
    exactly SAA through the hyperplane surrogate (verified against an independent
    epigraph LP) and eps >= eps_bar is exactly the maximin closed form (verified
    against Theorem 3.13). The registered economic mechanism - Wasserstein ball,
    supporting-hyperplane majorization, polyhedral dual LP, finite-threshold large
    ambiguity - is unchanged.

Control mapping (research-defined ladder mapping of the record's allocation rule):
    the book is long-only, so the per-cohort leg is LONG iff the solved weight for
    that asset exceeds the equal-weight anchor 1/n (the optimizer actively overweights
    the asset) and FLAT otherwise. Weights are solved at weekly rebalance epochs
    (record crypto portability: weekly / bi-weekly epochs for crypto) from data
    strictly through the epoch day and executed at the next bar's open with 1 tick
    adverse slippage; tranche #1 opens on a qualified signal, adverse scale-ins
    expand the ladder (routine active levels <= 11, tranche #12 stays reserve),
    breakeven TP and resting invalidation work against running average cost, a flat
    signal flattens the book reduce-only (then FLAT, never re-added to until the next
    qualified signal). The ladder is the frozen 48-config DCA rail; per-fill taker
    fee, adverse tick slippage and official funding are charged at their own time,
    and gross PnL comes from an independent price-PnL accumulator checked against net
    with a negative control.

Funding (record crypto portability: funding rates must be integrated into the net
expected return vector prior to LP optimization): every scenario return and every
portfolio-level diagnostic return uses price return MINUS the official funding carry
for that UTC day (long-only book pays positive funding). Official observations only;
missing intervals and the pre-2023-10-31 stretch contribute zero carry and the
coverage is disclosed. The funding_2x / no_funding grids stress the funding charged
in ladder PnL; the signal layer is frozen per cell exactly like every other grid.

Not reimplemented (disclosed adaptation boundary, never silently substituted): the
record's 475-equity + 1-month-Treasury cross-sectional basket and its risk-free
asset column (the registered local universe is the crypto portability basket of four
USD-M perpetuals; the source-market column is provenance only), the source's monthly
equity cadence (weekly epochs per the record's own crypto portability section), and
the source's ex-post tiered cost schedule as an objective term (costs are charged
per fill in the ladder, as the candidate body requires).

Execution: daily bars, Qlib 0.9.7 dump/readback proves the daily signal source,
official funding only at its own timestamp/mark (missing intervals are zero and
disclosed, modeled rows are never charged), and the per-cohort ladder Sharpe keeps
the pipeline's equity-return convention (rf = 0) exactly as the other direct
families do.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import itertools
import json
import math
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np
from scipy.optimize import linprog
from scipy import sparse

FAMILY_ID = "wasserstein-robust-portfolio-hyperplane-dual-lp-2026-09-02"
ENGINE_VERSION = "wasserstein_hyperplane_dro_qlib_v1"
RAW_ROOT = "/data/raw"
RESULTS_ROOT = "/results"
WORK_ROOT = "/qlib/work/wasserstein-dro-v1"
QLIB_DIR = WORK_ROOT + "/qlib-data"
CSV_ROOT = WORK_ROOT + "/csv"
INSTRUMENTS_PATH = RAW_ROOT + "/binance/usdm/instruments/usdm-perp-instruments.json"
VENV_PYTHON = "/opt/venv/bin/python"
DUMP_BIN = "/opt/qlib-tools/dump_bin.py"
MS_DAY = 86_400_000
FIELDS = ("open", "high", "low", "close", "volume")
SYMBOLS = ("BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT")
TIMEFRAME = "1d"
START_EQUITY = 30_000.0
BASE_QUOTE = 1_000.0
LEVERAGE = 10.0
MAX_ADD_LEVELS = 10          # tranche #1 + 10 scale-ins = 11 routine active levels
LADDER_LEVELS = 12           # tranche #12 stays reserve, never routinely deployed
SEED = 20260902
# Source-specified strategy axes (record: eps dial, behavioral radius; record required
# data: rolling lookback N in [20, 252] trading days). Registered value ORDER is the
# tie-break order (descending eps mirrors the record's small/moderate/large dial read
# from the conservative end; lookback ascending). Member values are research-defined
# inside the source ranges and frozen as PROJECT_PRE_REGISTERED_SEARCH_DOMAIN.
STRATEGY_AXES = {"wasserstein_epsilon": (1.0, 1e-2, 1e-4), "lookback_n": (63, 252)}


def _eps_label(eps):
    """1.0 -> 'eps1', 1e-2 -> 'eps1em2', 1e-4 -> 'eps1em4' (CSV-safe, no dots)."""
    if eps == 1.0:
        return "eps1"
    return ("eps1e%d" % int(round(math.log10(eps)))).replace("e-", "em")


CASES = tuple({"case_code": i, "wasserstein_epsilon": e, "lookback_n": n,
               "label": "%s_n%d" % (_eps_label(e), n)}
              for i, (e, n) in enumerate(itertools.product(*STRATEGY_AXES.values())))
DCA_AXES = {"spacing_pct": (0.01, 0.02, 0.03, 0.04),
            "size_multiplier": (1.0, 1.1), "breakeven_tp_pct": (0.01, 0.02, 0.03),
            "invalidation_pct": (0.05, 0.10)}
DCA_GRID = tuple(dict(zip(DCA_AXES, v)) for v in itertools.product(*DCA_AXES.values()))
GRIDS = ("historical", "oos", "full", "fee_2x", "funding_2x", "entry_delay_1_bar",
         "slippage_2ticks", "no_funding", "no_funding_full", "cost_attrition_40bps")
MIN_EPISODES_IS = 10
MIN_EPISODES_OOS = 3
MIN_NEIGHBOUR = 0.60
PHASES = {"historical": ("2022-01-01", "2025-09-30"),
          "oos": ("2025-10-01", "2026-09-11"),
          "full": ("2022-01-01", "2026-09-11")}
# Registered signal constants (record source-specified vs research-defined split).
REBALANCE_EVERY = 7          # research-defined weekly epochs (record crypto portability)
ETA = 1e-3                   # record's prescribed hyperplane tolerance
X_MIN_CLIP = -0.99           # record: box support clipped so x_min > -1
MAX_HYPERPLANES = 128        # sanity cap; a window that needs more fails closed
SIGNAL_PARAMS = {
    "wasserstein_epsilon": [1.0, 1e-2, 1e-4],
    "lookback_n": [63, 252],
    "rebalance_every_days": REBALANCE_EVERY,
    "utility": "U(y) = log(1 + y) logarithmic Kelly growth (record)",
    "ground_norm": "l1 ground norm, l_inf dual norm; long-only simplex; compact box support (record Corollary 3.7 specialization)",
    "hyperplane_certificate": "eta = 1e-3, mesh dy <= sqrt(8*eta/L_f), L_f = sup|U''| = 1/(1+y_lo)^2 on [y_lo, y_hi]",
    "box_support": "per-window sample min/max per asset, x_min clipped to > -1 at -0.99 (record)",
    "lookback_domain": "record required data: rolling lookback N in [20, 252] trading days; member values research-defined",
    "epsilon_domain": "record behavioral dial: small eps ~ 1e-4 tracks SAA, moderate ~ 1e-2 risk-adjusted, large >= 1.0 near-equal weighting",
    "funding_carry": "scenario and diagnostic returns = price return minus official funding carry for that UTC day (record crypto portability); missing intervals = zero, coverage disclosed",
    "weight_reference": "long-only simplex n=4; leg LONG iff w_i > 1/n equal-weight anchor, else FLAT (research-defined ladder mapping)",
    "execution_timing": "weights solved at epoch t's close from data through t; direction effective at t+1's open (research-defined execution assumption)",
    "record_transcription_note": "the sign convention transcribed for Theorem 3.6/Corollary 3.7 inverts the record's own eps dial and Theorem 3.13; the engine implements the sign-consistent Kantorovich dual (objective -lam*eps) that reproduces both stated anchors (eps=0 == SAA, eps >= eps_bar == maximin) - registered in round-spec before compute",
}
PIT_STATUS = "PASS_NO_LOOKAHEAD"
# Record falsification plan item 1 sweep (eps in [1e-5, 1e1]) plus the SAA anchor.
SWEEP_EPS = (1e-5, 1e-4, 1e-3, 5e-3, 1e-2, 5e-2, 1e-1, 1e0, 1e1)
INTERMEDIATE_EPS = (5e-3, 5e-2)   # record's claimed best-in-class band
SAA_EPS = 0.0                     # eps -> 0 limit: SAA through the hyperplane surrogate
SWEEP_TIER_COST = 0.0010          # record falsification item 1: after transaction costs (0.1% tier)
ITEM3_FEE = 0.0010                # record falsification item 3: 10 bps fees
ITEM3_SLIPPAGE = 0.0002           # record falsification item 3: 2 bps per trade
ITEM3_TURNOVER_THRESHOLD = 0.40   # record: monthly turnover > 40%
ITEM3_SHARPE_THRESHOLD = 0.6      # record: net Sharpe < 0.6
ITEM4_SHARPE_THRESHOLD = 0.5      # record: reject if OOS annualized Sharpe < 0.5
ITEM4_MAXDD_THRESHOLD = 30.0      # record: or max drawdown > 30%
FALSIFICATION_REGISTRY = {
    "radius_sweep": "record item 1: Wasserstein radius sensitivity sweep eps in [1e-5, 1e1]; falsified if the out-of-sample performance curve is monotonically decreasing in eps, OR if no intermediate radius eps in [5e-3, 5e-2] beats BOTH the SAA anchor (eps=0) and the equal-weight buy-and-hold benchmark after transaction costs out of sample",
    "support_boundary_misspecification": "record item 2: in volatile regimes realized returns breach the estimated historical box [x_min, x_max]; falsified if any realized scalar portfolio return evaluated by the frozen layer makes the hyperplane majorization gap Uhat_M(y) - U(y) exceed the certified eta (truncation distortion invalidates the error bound)",
    "turnover_slippage_hurdle": "record item 3: apply 10 bps fees plus 2 bps slippage per rebalance; falsified for a case when monthly turnover exceeds 40% AND net annualized Sharpe falls below 0.6 (the LP formulation would then require an explicit turnover-penalty constraint)",
    "rejection_threshold": "record item 4: reject the strategy if the walk-forward annualized Sharpe drops below 0.5 or maximum drawdown exceeds 30% over a 3-year test; evaluated on the full-window (2022-01-01..2026-09-11 = 4.67y >= 3y) walk-forward portfolio of every registered case, every decision made from trailing data only",
}
FALSIFICATION = [
    "Radius sensitivity sweep: an out-of-sample curve monotonically decreasing in eps, or no intermediate radius in [5e-3, 5e-2] beating both SAA and 1/N buy-and-hold after costs, falsifies the distributionally robust diversification hypothesis (record item 1)",
    "Support boundary misspecification: a realized scalar return whose hyperplane majorization gap exceeds eta falsifies the certified error bound under box truncation (record item 2)",
    "Turnover and execution slippage hurdle: monthly turnover > 40% together with net Sharpe < 0.6 under 10 bps fees + 2 bps slippage falsifies practical executability without a turnover penalty (record item 3)",
    "Rejection threshold: walk-forward annualized Sharpe < 0.5 or max drawdown > 30% over the >= 3-year full window rejects the strategy (record item 4)",
]
ARTIFACTS = ("state.json", "result.json", "logs/run.log", "artifacts/progress.json",
             "artifacts/raw_build.json", "artifacts/funding_coverage.json",
             "artifacts/signal_metrics.json", "artifacts/optimizer_diagnostics.json",
             "artifacts/portfolio_diagnostics.json", "artifacts/baseline_equal_weight.json",
             "artifacts/stress_effects.json", "artifacts/falsification.json",
             "artifacts/dca_layer_histogram.json", "artifacts/cohort_results.json",
             "artifacts/cohort_survivors.json", "artifacts/assertions.json",
             *(("artifacts/grid_%s.csv" % g) for g in GRIDS))


def expected_counts():
    per = len(SYMBOLS) * len(CASES) * len(DCA_GRID)
    return {"cohorts": len(SYMBOLS), "strategy_cases_per_cohort": len(CASES),
            "dca_configs_per_cohort": len(DCA_GRID),
            "base_combinations_per_cohort": len(CASES) * len(DCA_GRID),
            "case_evaluations_per_grid": per, "grid_count": len(GRIDS),
            "case_evaluations_total": per * len(GRIDS)}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def utc_ms(day):
    return int(dt.datetime.fromisoformat(day[:10]).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def stamp(ms):
    return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(value, fh, indent=2, ensure_ascii=False, allow_nan=False)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def axis(got, registered):
    return list(got) == list(registered)


def now_utc():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_spec_template(created_at=None):
    here = Path(__file__).resolve()
    return {
        "schema_version": 1, "document_kind": "run_spec",
        "contract": "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md v2.0.0",
        "family_id": FAMILY_ID, "round_id": "<family_id>-r1", "run_id": "<round_id>-u1",
        "created_at_utc": created_at or "<ISO8601Z>", "container_id": "qlib-run",
        "image_id": "qlib:0.9.7-arm64",
        "script": {"path": "/scripts/300_wasserstein_dro_run.py",
                   "sha256": sha256_file(here),
                   "deployed_host_path": "/Users/hong/workspace/qlib-apple-container/scripts/300_wasserstein_dro_run.py"},
        "engine": {"name": ENGINE_VERSION, "script": "container/scripts/300_wasserstein_dro_run.py",
                   "container": "qlib-run", "qlib_version": "0.9.7", "seed": SEED,
                   "self_check": "container/scripts/tests/test_wasserstein_dro_engine.py",
                   "self_check_sha256": sha256_file(here.with_name("tests") / "test_wasserstein_dro_engine.py")},
        "data": {"raw_root": "/data/raw/binance/usdm", "start": PHASES["full"][0],
                 "end": PHASES["full"][1], "timezone": "UTC", "symbols": list(SYMBOLS),
                 "fields": list(FIELDS),
                 "signal_timeframe": {"raw_interval": TIMEFRAME, "qlib_freq": "day"},
                 "execution_timeframe": {"raw_interval": TIMEFRAME, "qlib_freq": "day",
                                         "bars_per_day": 1}},
        "split": {"historical_start": PHASES["historical"][0], "historical_end": PHASES["historical"][1],
                  "oos_start": PHASES["oos"][0], "oos_end": PHASES["oos"][1],
                  "rule": "chronological; frozen before compute; OOS never used for selection"},
        "params": [dict(c) for c in CASES],
        "dca_domain": {**{k: list(v) for k, v in DCA_AXES.items()}, "grid": [dict(d) for d in DCA_GRID],
                       "base_quote": BASE_QUOTE, "base_quote_status": "PROJECT_PRE_REGISTERED_CONSTANT",
                       **{k + "_status": "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN" for k in DCA_AXES}},
        "selector_version": "cohort-selector-v1", "disposition_version": "cohort-disposition-v1",
        "expected": expected_counts(), "gates": {"min_episodes_is": MIN_EPISODES_IS,
                                                 "min_episodes_oos": MIN_EPISODES_OOS,
                                                 "min_neighbour_same_sign_fraction": MIN_NEIGHBOUR},
        "signal_constants": dict(SIGNAL_PARAMS),
        "costs": {"taker_fee": "canonical instrument taker_fee", "slippage_ticks": 1,
                  "slippage_robustness_ticks": 2,
                  "funding": "official observations only at timestamp/mark; missing intervals zero and disclosed"},
        "falsification": FALSIFICATION, "expected_outputs": list(ARTIFACTS),
        "notes": "direct family: family_id + round_id + run_id only; no Kanban ownership keys."}


def parameter_contract():
    """Frozen generic Strategy Family Parameter Contract (contract section 29)."""
    dca_fields = list(DCA_AXES)
    strategy_fields = ["wasserstein_epsilon", "lookback_n"]
    axes = [{"name": "wasserstein_epsilon", "kind": "atomic", "members": ["wasserstein_epsilon"],
             "registered_values": list(STRATEGY_AXES["wasserstein_epsilon"]),
             "row_fields": ["wasserstein_epsilon"]},
            {"name": "lookback_n", "kind": "atomic", "members": ["lookback_n"],
             "registered_values": list(STRATEGY_AXES["lookback_n"]),
             "row_fields": ["lookback_n"]}]
    axes += [{"name": name, "kind": "atomic", "members": [name], "registered_values": list(dom),
              "row_fields": [name]} for name, dom in DCA_AXES.items()]
    return {
        "parameter_contract_version": 1, "family_id": FAMILY_ID,
        "contract_ref": "generic Strategy Family Parameter Contract, contract v2.0.0 section 29; frozen copy lives here and is the single source of truth",
        "research_axes_ordered": axes,
        "row_fields": strategy_fields + dca_fields, "composite_map": {},
        "strategy_param_fields": strategy_fields, "dca_param_fields": dca_fields,
        "canonical_recipe": {"sort_keys": True, "separators": [",", ":"],
                             "ensure_ascii": False,
                             "numeric_rule": "JSON number finite, bool excluded"},
        "row_match_recipe": {"keys": ["symbol", "timeframe"] + strategy_fields + dca_fields,
                             "equality": "exact, numeric == float compare, rest bytewise"},
        "non_params": ["symbol", "timeframe", "case_label", "grid", "decomposition_ok"],
        "domain_cardinality": {"strategy": len(CASES), "dca": len(DCA_GRID),
                               "per_cohort": len(CASES) * len(DCA_GRID)}}


def round_spec_template(source_record_sha256=None, candidate_body_sha256=None):
    """Immutable pre-registration document (host writes it once before any compute)."""
    fam_path = Path(RESULTS_ROOT) / FAMILY_ID / "family.json"
    body_path = Path(RESULTS_ROOT) / "_handoff" / "bodies" / (FAMILY_ID + ".md")
    if candidate_body_sha256 is None:
        candidate_body_sha256 = sha256_file(body_path) if body_path.is_file() else None
    family_sha = sha256_file(fam_path) if fam_path.is_file() else None
    try:
        family_doc = json.loads(fam_path.read_text(encoding="utf-8")) if fam_path.is_file() else {}
    except ValueError:
        family_doc = {}
    handoff = family_doc.get("handoff") or {}
    return {
        "schema_version": 1, "document_kind": "round_spec",
        "contract": "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md v2.0.0",
        "family_id": FAMILY_ID, "round_id": FAMILY_ID + "-r1", "created_at_utc": now_utc(),
        "ownership_mode": "direct_hermes",
        "ownership_note": "direct family: ownership is family_id + round_id + run_id on the fixed results path. No Kanban card, board, dispatcher or task id exists for this round and none may be written into any spec or evidence file.",
        "provenance": {
            "source_record": "wiki brain quant/%s.md" % FAMILY_ID,
            "source_record_sha256": source_record_sha256,
            "source_record_status": "research-only / not-implemented / not-approved / approval_scope=research-only",
            "intake_decision": "PASS-WITH-CAVEAT (source-faithful research-only knowledge; source results never independently reproduced in this workflow; operationalization, thresholds and execution assumptions remain unvalidated)",
            "primary_source": "Chung-Han Hsieh and Rong Gan, 'Certified High-Dimensional Wasserstein Robust Portfolio Optimization', arXiv preprint arXiv:2608.07032v1 [math.OC], August 12, 2026. DOI: 10.48550/arXiv.2608.07032. Stable URL: https://arxiv.org/abs/2608.07032",
            "candidate_body_path": str(body_path),
            "candidate_body_sha256": candidate_body_sha256,
            "family_json_sha256": family_sha,
            "family_handoff_body_sha256": handoff.get("body_sha256"),
            "family_handoff_execution": handoff.get("execution"),
            "semantic_fingerprint": family_doc.get("semantic_fingerprint"),
            "frozen_bytes_rule": "the reviewed candidate body and its system lifecycle footer are read verbatim and never rewritten; this round-spec records how they are executed, it does not restate or amend them",
            "backfill_note": "backfill (card t_86d04b09) candidateized directly from the canonical Wiki record exactly once; this round performs no intake re-review and no second crypto/runnable suitability classification"},
        "parameter_contract": parameter_contract(),
        "params": [dict(c) for c in CASES],
        "parameter_domain": {
            "strategy_axes": ["wasserstein_epsilon", "lookback_n"],
            "legal_cases_per_cohort": len(CASES), "cases": [dict(c) for c in CASES],
            "provenance": "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN (both axes source-specified by the record - the Wasserstein radius dial and the rolling lookback N in [20,252] - with member values research-defined inside those source ranges)",
            "selection_unit": "strategy case x DCA config = one cell; the full 6 x 48 = 288-cell product is evaluated per cohort per grid"},
        "dca_domain": {
            **{k: list(v) for k, v in DCA_AXES.items()},
            "grid": [dict(d) for d in DCA_GRID], "config_count": len(DCA_GRID),
            "base_quote": BASE_QUOTE, "base_quote_status": "PROJECT_PRE_REGISTERED_CONSTANT",
            **{k + "_status": "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN" for k in DCA_AXES},
            "leg_semantics": "tranche #1 is the initial entry after a qualifying signal (solved weight above the equal-weight anchor); adverse-price scale-ins then expand the ladder (routine active levels <= 11, tranche #12 stays reserve); breakeven-anchored take-profit and resting invalidation are measured against the running average cost; a registered exit flattens the whole position reduce-only, and no add happens before the next qualifying signal"},
        "provenance_classes_v1_3_1": {
            "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN": ["wasserstein_epsilon", "lookback_n", "spacing_pct",
                                                     "size_multiplier", "breakeven_tp_pct", "invalidation_pct"],
            "PROJECT_PRE_REGISTERED_CONSTANT": ["base_quote"],
            "USER_FIXED": [
                "starting_equity = 30,000 USDT",
                "USDT as the only numeraire",
                "linear USD-M perpetual instrument",
                "leverage 10x",
                "12 tranches",
                "geometric size multiplier 1.1 (historical progression value; inside the size_multiplier search axis as a search candidate)",
                "tranche #12 is reserve/buffer and not routinely deployed (routine active levels max 11)",
                "initial entry plus adverse-price scale-ins",
                "reduce-only exit",
                "same-bar multi-level crossing uses deterministic conservative ordering",
                "no add after FLAT/kill"],
            "rule": "searched axes and project constants must never be described as USER_FIXED invariants; round-spec and run-spec classifications must agree"},
        "eligible_universe": {
            "cohort_definition": "instrument x timeframe of the local eligible universe",
            "cohorts": ["%s/%s" % (s, TIMEFRAME) for s in SYMBOLS],
            "local": [{"symbol": s, "timeframe": TIMEFRAME, "interval": TIMEFRAME,
                       "qlib_freq": "day", "rows_1d_in_window": 1715,
                       "first_bar_utc": "2022-01-01T00:00:00Z", "last_bar_utc": "2026-09-11T00:00:00Z",
                       "monthly_shards_read": 57} for s in SYMBOLS],
            "record_universe_provenance": "record required data names cross-sectional asset baskets (e.g. 475 S&P 500 equities + 1-month U.S. Treasury yield proxy, scalable to 1,000 assets) on daily adjusted closes - provenance and external validity reference only",
            "local_universe_rule": "system lifecycle footer: the eligible universe is the complete set of local canonical symbols on which the registered core signal/mechanism computes legally; the record's market/symbol list is provenance only, conclusions are scoped to this local universe, and the universe is never shrunk after results are seen",
            "crypto_portability": "the record itself ships a Crypto portability section (status: adapted/unproven) that names the LP port to liquid Binance USDT perpetual baskets, dynamic box support, weekly/bi-weekly rebalancing epochs and funding integration - the local USD-M perpetual universe is canonicalized from that section rather than from a new suitability screen",
            "timeframe_rule": "the record's mechanism is specified on daily adjusted closing prices, so the registered timeframe is 1d only; no intraday cohort is added or removed after results"},
        "prerequisite_evidence_phase_2b": {
            "catalog_reads": [
                {"path": "CONFIG.json", "market_type": "usdm_perp", "symbols": list(SYMBOLS),
                 "intervals": ["1d", "1w", "15m", "30m", "4h", "5m", "1h"],
                 "datasets_used": ["klines", "funding"]},
                {"path": "SCHEMA.md",
                 "klines_fields": ["open_time_ms", "close_time_ms", "open", "high", "low", "close", "volume"],
                 "funding_fields": ["symbol", "venue", "market_type", "funding_time_ms", "funding_rate",
                                    "mark_price", "rate_type", "truth_status", "funding_price_source"],
                 "instruments_fields": ["price_increment", "taker_fee", "maker_fee", "type",
                                        "quote_currency", "is_inverse"],
                 "note": "a missing bar is absent, not zero-filled (engine fails closed on any gap)"}],
            "requirement_vs_available": [
                {"requirement": "Daily total returns R_{t,i} over a rolling lookback N in [20,252] trading days (record required data)",
                 "available": "klines 1d close, 1715 contiguous daily bars per symbol 2022-01-01..2026-09-11, zero gaps (measured); lookbacks 63 and 252 both inside the record's [20,252] range",
                 "status": "present"},
                {"requirement": "Support bounds x_min, x_max estimated per rolling window (record required data)",
                 "available": "computed from the same canonical daily returns per window; x_min clipped to > -1 as the record requires; no extra data type needed",
                 "status": "present"},
                {"requirement": "Funding & borrow costs integrated into net expected returns (record crypto portability)",
                 "available": "funding jsonl.gz official rows only: 3144 official observations per symbol in-window, first_official 2023-10-31T08:00Z; 2005-2080 non-official rows ignored; intervals without official observation contribute zero carry and are disclosed",
                 "status": "present_with_disclosed_gaps"},
                {"requirement": "Instrument metadata: taker fee and price_increment (registered cost assumptions)",
                 "available": "usdm-perp-instruments.json: taker_fee 0.0005, price_increment BNBUSDT=0.010 BTCUSDT=0.10 ETHUSDT=0.01 SOLUSDT=0.0100, linear USDT perpetuals",
                 "status": "present"},
                {"requirement": "Risk-Free Rate: 1-Month Constant Maturity (FRED) integrated as an additional asset column",
                 "available": "fred-DGS1MO.jsonl.gz is absent from the local FRED pack (27 bounded series present); under the registered crypto-portability canonicalization the local basket is the four USD-M perpetuals with official funding carry and no treasury column - the source-market risk-free column is provenance only, is not part of the local basket, and no registered gate or metric depends on it (pipeline rf=0 equity-return convention)",
                 "status": "not_local_provenance_only"},
                {"requirement": "475 S&P 500 constituents + source sample calendar",
                 "available": "absent from the local canonical raw by design; the system lifecycle footer makes the record's market list provenance-only and the local universe the execution universe",
                 "status": "not_local_provenance_only"}],
            "core_signal_conclusion": "every data type the registered core signal needs (daily price series for the return matrix, rolling-window support bounds, official funding carry) exists in the canonical raw, so the full backtest runs on the local eligible universe; no TECHNICAL_INCOMPLETE is warranted and the universe is not shrunk",
            "bounded_scope": "only CONFIG.json and SCHEMA.md were read as catalogs plus direct bounded reads of the klines/funding/instrument files named above; no second data registry, no host-wide scan, no candidate-specific prerequisite checker"},
        "data_window_split": {
            "window_rule": "the record does not fix a sample calendar for its own study; the candidate body DATA WINDOW clause adopts the local canonical raw window, marked research-defined",
            "start": "2022-01-01", "end": "2026-09-11",
            "in_sample": {"start": "2022-01-01", "end": "2025-09-30"},
            "oos": {"start": "2025-10-01", "end": "2026-09-11"},
            "rule": "chronological, frozen in this immutable document before any compute; OOS is never used for selection and is read only after the historical winner cell is frozen",
            "missing_data_handling": "canonical raw has no silently filled bars (SCHEMA: a missing bar is absent); the engine fails closed on any gap, so the registered window must be fully covered"},
        "hypothesis": {
            "record_title": "Certified High-Dimensional Wasserstein Robust Portfolio Optimization: Supporting Hyperplane Majorization, Polyhedral Dual LP, and Large-Ambiguity Finite-Threshold Characterization",
            "economic_mechanism_source_reported": [
                "Adversarial distributional shift protection: optimize against the worst-case distribution in an order-1 Wasserstein ball of radius eps centered at the empirical distribution; the l1 ground metric penalizes mass migration across asset returns and protects against co-movement breakdown, non-Gaussian tails and estimation error in expected growth.",
                "Supporting hyperplane majorization: U(<w,x>) on the compact scalar range is upper-approximated by M supporting tangent hyperplanes, so the inner worst-case support minimization dualizes exactly by LP duality.",
                "Finite-threshold large-ambiguity conservatism: once eps exceeds the support diameter the ball becomes the whole simplex over the support and the allocation collapses to the closed maximin rule."],
            "signal": {
                "primal": "V*(eps) = sup_{w in W} inf_{F in B_eps(Fhat)} E^F[U(<w,X>)], U(y) = log(1+y), W subset Delta_n long-only, box support X = [x_min, x_max]",
                "hyperplanes": "tangents h_m(y) = f(y_m) + f'(y_m)(y - y_m) at an M-point partition with mesh dy <= sqrt(8*eta/L_f), eta = 1e-3 (record certificate)",
                "dual_lp": "record Corollary 3.7 box specialization, n + 1 + N + n*M scalar variables, one HiGHS solve per rebalance epoch",
                "large_ambiguity": "eps >= eps_bar = sum_i (x_max_i - x_min_i) => maximin over argmax_i x_min_i (record Theorem 3.13 / Corollary 3.15), asserted in-run",
                "leg_selection": "long-only simplex n=4; leg LONG iff solved weight w_i > 1/n (equal-weight anchor), else FLAT (research-defined ladder mapping)",
                "decision_timing": "weights solved at epoch t's close from data strictly through t, executed at the next bar's open with 1 tick adverse slippage (research-defined execution assumption)",
                "rebalancing": "weekly epochs every 7 days (record crypto portability: weekly / bi-weekly epochs for crypto)",
                "funding_integration": "scenario and diagnostic returns = price return minus official funding carry for that UTC day (record crypto portability: funding integrated into the net expected return vector prior to LP optimization); official observations only, zero and disclosed where missing",
                "pit_status": "PASS_NO_LOOKAHEAD",
                "pit_evidence": "every scenario window ends at the epoch day, the epoch grid is data-independent, and the decision is executed at the next bar's open; asserted in-run and re-tested by the self-check suffix-perturbation test"},
            "core_hypothesis_rule": "this section is the single registered hypothesis; research-defined entries may only fill execution detail (cost, slippage, entry timing alignment, member values inside source ranges) and may never change the core hypothesis, its direction or its universe",
            "direction": "long-only (record Long-Only Box Specialization: the LP reduction relies strictly on the long-only portfolio simplex); no short or market-neutral leg is registered or invented"},
        "record_transcription_reconciliation": {
            "issue": "the canonical record's transcribed Theorem 3.6 / Corollary 3.7 constraint signs (+lam*eps in the objective, -lam*<1, X_j - x_min> inside the a_j bound) invert the record's own stated dial: taken verbatim, eps -> 0 collapses to the maximin rule and the program is unbounded for eps beyond the support diameter, contradicting the record's Research interpretation (small eps tracks SAA, large eps converges to near-equal weighting) and Theorem 3.13",
            "resolution": "implement the sign-consistent Kantorovich dual (objective -lam*eps + empirical mean of the dual support values) which is the standard order-1 Wasserstein dual and reproduces BOTH stated anchors; registered before compute",
            "anchors_asserted": ["eps = 0 solves to the exact epigraph SAA through the hyperplane surrogate (independent LP cross-check, in-run assertion)",
                                 "eps >= eps_bar solves to the maximin closed form over argmax_i x_min_i (record Theorem 3.13, in-run assertion)",
                                 "hyperplane majorization gap <= eta on the certified interval for every solve (record certificate, in-run assertion)"],
            "mechanism_unchanged": "Wasserstein ambiguity ball, supporting-hyperplane majorization, polyhedral dual LP and the finite-threshold large-ambiguity characterization are all preserved; only the transcription's sign slips are corrected"},
        "costs": {
            "taker_fee": "canonical instrument taker_fee (usdm-perp-instruments.json), per fill",
            "slippage_baseline": "1 tick adverse per fill, per instrument price_increment (research-defined)",
            "slippage_robustness": "2 ticks adverse (research-defined, registered stress grid)",
            "funding": "official observations only, charged at their own timestamp and mark price; missing official interval => zero cost, disclosed; modeled rows never charged",
            "accounting": "per-fill taker fee and funding are deducted from realised equity at the fill's own time; net_pnl/ending_equity/daily marks/Sharpe/margin are all net-of-fee (v1.3.1)",
            "gross_pnl": "independent price-PnL accumulator (exit proceeds - cost basis, no fee/funding); decomposition cross-check gross - fees - funding == net within 1e-3, with a negative control (v1.3.2)",
            "attrition": "cost_attrition_40bps applies an absolute 40 bps fee override",
            "turnover": "the record evaluates costs ex-post on turnover; this family measures turnover and charged costs per cell instead of adding an objective-level turnover penalty (disclosed)"},
        "selector_disposition": {
            "selector_version": "cohort-selector-v1", "disposition_version": "cohort-disposition-v1",
            "disposition_mapping_version": "v1.4.0", "unit": "cohort (instrument x timeframe)",
            "selection_data": "historical grid only; OOS never used for selection",
            "gates": {"min_episodes_is": MIN_EPISODES_IS, "min_episodes_oos": MIN_EPISODES_OOS,
                      "min_neighbour_same_sign_fraction": MIN_NEIGHBOUR},
            "cull_reasons": ["insufficient_trades", "no_qualifying_candidate", "oos_economic",
                             "full_economic", "robustness_economic:<grids>", "parameter_neighbourhood"],
            "band_rule": "0 survivors -> REJECT (performance_claimable=false); >=1 survivor -> PASS band; every survivor advances, no ranking, survivor count is never itself a performance_claimable=false reason",
            "cross_cohort_median": "descriptive diagnostic only, marked non_gating, never a family gate",
            "runner_role": "runner disposition/verdict_recommendation are suggestions; the final verdict is written to verdict.json by the default worker (10.7)"},
        "grids": list(GRIDS),
        "coverage_gate_g1": {
            "product": "each registered grid must contain exactly cohorts x strategy cases x 48 DCA configs; the measured cell set must equal the registered product or the round is TECHNICAL_INCOMPLETE",
            "expected": expected_counts()},
        "robustness": {
            "standard_stress_grids": ["fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks"],
            "additional_grids": ["no_funding", "no_funding_full", "cost_attrition_40bps"],
            "neighbourhood": "face neighbours of the winner cell in the full joint space (wasserstein_epsilon + lookback_n + 4 DCA axes); >=60% must agree in sign with the winner's historical net PnL, historical rows only",
            "no_gate_lowering": "robustness results may never shrink the eligible universe or lower a gate"},
        "falsification": FALSIFICATION,
        "falsification_registry": dict(FALSIFICATION_REGISTRY),
        "failure_taxonomy": {
            "scientific_failure": "record falsification battery hit -> cull or family REJECT, written into verdict.json; no retuning to pass and no semantic change to mask it",
            "technical_failure": "incomplete coverage, missing prerequisite, non-real DCA accounting (any assertion false) or script bug -> TECHNICAL_INCOMPLETE or same-round new run_id remediation (hypothesis/parameter domain/split/DCA rail unchanged; max_rounds=3, max_no_progress_rounds=2, section 15)",
            "operator_failure": "operator stop -> attempt ends INCOMPLETE with failure.class=operator_stopped, same run_id never rerun (INV-15); no PASS/REJECT is produced and an operator stop is never written as a scientific conclusion",
            "rule": "the three classes may never stand in for each other"},
        "research_defined": [
            "local data window 2022-01-01..2026-09-11 and the chronological IS/OOS split",
            "strategy member values inside the record's source ranges: epsilon in {1.0, 1e-2, 1e-4} and lookback N in {63, 252} subset of [20, 252]",
            "weekly (7-day) rebalance epochs and the 0.0010 tier used by the radius sweep cost (record crypto portability / cost tier)",
            "the 1/n equal-weight anchor that maps a solved long-only weight to a ladder leg",
            "execution of the epoch decision at the next bar's open with 1 tick adverse slippage",
            "the DCA tranche ladder itself: the record specifies no accumulation/execution model, so the pipeline's project pre-registered 4-axis DCA domain is the execution rail",
            "falsification operationalizations (monotonicity + intermediate-band gate, majorization-gap threshold, monthly turnover grouping, walk-forward window used for the rejection threshold)",
            "the hyperplane count cap (128) as a fail-closed sanity guard; windows that would need more raise instead of silently degrading the certificate",
            "portfolio-level falsification probes use net returns (price return minus official funding carry) with the registered ex-post turnover cost; per-fill fees and funding remain the ladder grids' domain"],
        "not_reimplemented_disclosed": [
            "the record's 475-equity + 1-month-Treasury basket and its risk-free asset column: provenance and external-validity reference only, per the system lifecycle footer local-universe rule (DGS1MO absent locally and not needed by the registered crypto basket)",
            "monthly equity cadence: replaced by the record's own crypto-portability weekly epochs",
            "an explicit turnover-penalty term in the objective: replaced by the registered per-fill cost model with turnover reported per cell",
            "SOCP / short-selling / l2 ground metric variants: outside the record's Long-Only Box Specialization",
            "no second engine, no Manager/Service/Factory/Registry/Orchestrator/daemon/queue: one minimal Qlib script in the qlib-run container"],
        "engine": {
            "name": ENGINE_VERSION,
            "script": {"repo_path": "container/scripts/300_wasserstein_dro_run.py",
                       "container_path": "/scripts/300_wasserstein_dro_run.py",
                       "deployed_host_path": "/Users/hong/workspace/qlib-apple-container/scripts/300_wasserstein_dro_run.py",
                       "sha256": sha256_file(Path(__file__).resolve())},
            "self_check": {"repo_path": "container/scripts/tests/test_wasserstein_dro_engine.py",
                           "container_path": "/scripts/tests/test_wasserstein_dro_engine.py",
                           "sha256": sha256_file(Path(__file__).resolve().with_name("tests") / "test_wasserstein_dro_engine.py")},
            "container": "qlib-run", "image": "qlib:0.9.7-arm64", "qlib_version": "0.9.7",
            "seed": SEED,
            "compute_rule": "production compute runs only inside the qlib-run container via the existing `container exec -d qlib-run` semantics; raw is read through /data/raw read-only; no second engine, no host pandas backtest, no external coding agent"},
        "data": {"raw_root": "/data/raw/binance/usdm", "start": "2022-01-01", "end": "2026-09-11",
                 "timezone": "UTC", "symbols": list(SYMBOLS), "fields": list(FIELDS),
                 "signal_timeframe": {"raw_interval": TIMEFRAME, "qlib_freq": "day"},
                 "execution_timeframe": {"raw_interval": TIMEFRAME, "qlib_freq": "day",
                                         "bars_per_day": 1}},
        "split": {"historical_start": PHASES["historical"][0], "historical_end": PHASES["historical"][1],
                  "oos_start": PHASES["oos"][0], "oos_end": PHASES["oos"][1],
                  "rule": "chronological; frozen before compute; OOS never used for selection"},
        "signal_constants": dict(SIGNAL_PARAMS),
        "expected": expected_counts(),
        "expected_outputs": list(ARTIFACTS),
        "evidence_rules": {
            "immutable": "round-spec.json and run-spec.json are frozen before compute and never rewritten after launch",
            "attempt_path": "/results/%s/rounds/%s-r1/attempts/%s-r1-u1" % (FAMILY_ID, FAMILY_ID, FAMILY_ID),
            "sentinels": "exact-once launch; terminal evidence, checksums and reconciliation follow contract 9.4/10/11; a FAILED/INCOMPLETE/DONE attempt is never rerun (INV-15)",
            "post_survivor": "frozen survivor bundle (ranking=null) is produced only from a terminal DONE attempt with full coverage and all assertions true; evidence preservation under 28 is not a PASS gate"},
        "notes": "direct family: family_id + round_id + run_id only; no Kanban ownership keys in this spec, in run-spec.json, or in any evidence file"}


def validate_spec(spec, script_path=None, test_path=None):
    if spec.get("family_id") != FAMILY_ID or spec.get("selector_version") != "cohort-selector-v1" \
            or spec.get("disposition_version") != "cohort-disposition-v1":
        raise ValueError("wrong family/selector/disposition identity")
    rid = spec.get("round_id")
    if not (isinstance(rid, str) and re.fullmatch(re.escape(FAMILY_ID) + r"-r[1-9][0-9]*", rid)
            and isinstance(spec.get("run_id"), str)
            and re.fullmatch(re.escape(rid) + r"-u[1-9][0-9]*", spec["run_id"])):
        raise ValueError("round/run identity mismatch")
    if any(k in spec for k in ("task_id", "kanban_task_id", "kanban_board")):
        raise ValueError("direct family must not carry task/board ids")
    if not spec.get("created_at_utc"):
        raise ValueError("missing created_at_utc")
    data = spec.get("data", {})
    if data.get("raw_root") != "/data/raw/binance/usdm" or data.get("fields") != list(FIELDS) \
            or data.get("symbols") != list(SYMBOLS):
        raise ValueError("canonical local universe mismatch")
    if data.get("signal_timeframe") != {"raw_interval": TIMEFRAME, "qlib_freq": "day"} \
            or data.get("execution_timeframe") != {"raw_interval": TIMEFRAME, "qlib_freq": "day",
                                                   "bars_per_day": 1}:
        raise ValueError("signal/execution timeframe mismatch")
    if spec.get("params") != [dict(c) for c in CASES]:
        raise ValueError("strategy domain must be the exact registered case set")
    dca = spec.get("dca_domain", {})
    if any(not axis(dca.get(k, ()), v) for k, v in DCA_AXES.items()) \
            or dca.get("grid") != [dict(d) for d in DCA_GRID] or dca.get("base_quote") != BASE_QUOTE:
        raise ValueError("DCA domain incomplete/changed")
    if dca.get("base_quote_status") != "PROJECT_PRE_REGISTERED_CONSTANT" \
            or any(dca.get(k + "_status") != "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN" for k in DCA_AXES):
        raise ValueError("DCA provenance mismatch")
    if spec.get("expected") != expected_counts() or spec.get("expected_outputs") != list(ARTIFACTS) \
            or spec.get("falsification") != FALSIFICATION:
        raise ValueError("coverage/artifact/falsification registration mismatch")
    if spec.get("signal_constants") != dict(SIGNAL_PARAMS):
        raise ValueError("signal constants must stay the registered values")
    script = spec.get("script", {})
    if script.get("path") != "/scripts/300_wasserstein_dro_run.py" \
            or script.get("sha256") != sha256_file(script_path or __file__):
        raise ValueError("script bytes do not match pinned identity")
    engine = spec.get("engine", {})
    test = Path(test_path) if test_path else Path(__file__).resolve().with_name("tests") / "test_wasserstein_dro_engine.py"
    if engine.get("name") != ENGINE_VERSION or engine.get("qlib_version") != "0.9.7" \
            or engine.get("seed") != SEED or engine.get("self_check_sha256") != sha256_file(test):
        raise ValueError("engine/self-check identity mismatch")
    with open(RAW_ROOT + "/_meta/CONFIG.json", encoding="utf-8") as fh:
        catalog = json.load(fh)
    if catalog.get("market_type") != "usdm_perp" or sorted(catalog.get("symbols", [])) != list(SYMBOLS) \
            or TIMEFRAME not in set(catalog.get("intervals", [])) \
            or not {"klines", "funding"} <= set(catalog.get("datasets", {})):
        raise ValueError("canonical catalog diverged from registered universe")
    return expected_counts()


# --------------------------------------------------------------------------------------
# Canonical raw readers (identical shape to the other direct families)
# --------------------------------------------------------------------------------------
def instrument_metadata(path=INSTRUMENTS_PATH):
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    by_symbol = {r["fields"]["raw_symbol"]: r["fields"] for r in doc["instruments"]}
    out = {}
    for sym in SYMBOLS:
        f = by_symbol[sym]
        if f.get("type") != "CryptoPerpetual" or f.get("quote_currency") != "USDT" or f.get("is_inverse"):
            raise ValueError("not a linear USDT perpetual: " + sym)
        tick, fee = float(f["price_increment"]), float(f["taker_fee"])
        if not (0 < tick and 0 <= fee < 0.01):
            raise ValueError("invalid tick/fee: " + sym)
        out[sym] = {"tick": tick, "taker_fee": fee, "source_sha256": sha256_file(path)}
    return out


def raw_files(symbol, interval, start, end):
    """Canonical monthly shards whose month falls inside [start, end]."""
    root = Path(RAW_ROOT) / "binance/usdm/klines" / symbol / interval
    files = sorted(root.glob("%s-%s-*.jsonl.gz" % (symbol, interval)))
    out = []
    for f in files:
        tail = f.name.split("-" + interval + "-", 1)[-1]
        month = tail.split(".", 1)[0]
        if start[:7] <= month <= end[:7]:
            out.append(f)
    return out


def load_rows(symbol, interval, start, end, fields):
    lo, hi = utc_ms(start), utc_ms(end) + MS_DAY
    times, cols = [], {f: [] for f in fields}
    prev = None
    files = raw_files(symbol, interval, start, end)
    if not files:
        raise RuntimeError("missing %s/%s raw" % (symbol, interval))
    for path in files:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                row = json.loads(line)
                t = int(row["open_time_ms"])
                if not lo <= t < hi:
                    continue
                if prev is not None and t <= prev:
                    raise RuntimeError("unsorted/duplicate bars")
                if prev is not None and t - prev != MS_DAY:
                    raise RuntimeError("gap in canonical %s/%s/%s" % (symbol, interval, stamp(t)))
                for f in fields:
                    value = float(row[f])
                    if not math.isfinite(value):
                        raise RuntimeError("non-finite %s in %s" % (f, symbol))
                    if (value < 0) or (f != "volume" and value <= 0):
                        raise RuntimeError("invalid %s in %s" % (f, symbol))
                    cols[f].append(value)
                times.append(t)
                prev = t
    if not times or times[0] != lo or times[-1] != hi - MS_DAY:
        raise RuntimeError("registered window not fully covered by %s/%s: first=%s last=%s expected %s..%s"
                           % (symbol, interval, stamp(times[0]) if times else "none",
                              stamp(times[-1]) if times else "none", stamp(lo), stamp(hi - MS_DAY)))
    return {"open_ms": np.asarray(times, dtype=np.int64),
            **{f: np.asarray(v, dtype=np.float64) for f, v in cols.items()},
            "raw_files": [{"path": str(x), "sha256": sha256_file(x)} for x in files]}


def write_csv(symbol, panel):
    path = Path(CSV_ROOT) / ("%s.csv" % symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date", *FIELDS])
        for i, t in enumerate(panel["open_ms"]):
            writer.writerow([stamp(t), *(panel[f][i] for f in FIELDS)])
    return path


def build_qlib(daily, start, end):
    if Path(WORK_ROOT).exists():
        shutil.rmtree(WORK_ROOT)
    for symbol in SYMBOLS:
        write_csv(symbol, daily[symbol])
    cmd = [VENV_PYTHON, DUMP_BIN, "dump_all", "--data_path", CSV_ROOT, "--qlib_dir", QLIB_DIR,
           "--freq", "day", "--include_fields", ",".join(FIELDS), "--date_field_name", "date",
           "--max_workers", "1"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if proc.returncode:
        raise RuntimeError("dump_bin failed: %s / %s" % (proc.stdout[-800:], proc.stderr[-800:]))
    import qlib
    from qlib.data import D
    if qlib.__version__ != "0.9.7":
        raise RuntimeError("Qlib version mismatch")
    qlib.init(provider_uri=QLIB_DIR, region="cn", expression_cache=None, dataset_cache=None)
    readback = {}
    for symbol in SYMBOLS:
        df = D.features([symbol], ["$" + f for f in FIELDS], start_time=start,
                        end_time=end + " 23:59:59", freq="day").sort_index()
        times = np.asarray([int(x.value // 1_000_000) for x in df.index.get_level_values("datetime")],
                           dtype=np.int64)
        vals = {f: df["$" + f].to_numpy(dtype=np.float64) for f in FIELDS}
        if len(times) != len(daily[symbol]["open_ms"]) or not np.array_equal(times, daily[symbol]["open_ms"]):
            raise RuntimeError("Qlib daily readback mismatch: " + symbol)
        if any(not np.isfinite(v).all() for v in vals.values()) or not np.all((vals["open"] > 0) & (vals["close"] > 0)):
            raise RuntimeError("invalid Qlib daily readback: " + symbol)
        for f in FIELDS:
            expected = np.asarray(daily[symbol][f], dtype=np.float32).astype(np.float64)
            if np.array_equal(vals[f], expected):
                continue
            if not np.allclose(vals[f], daily[symbol][f], rtol=1e-6, atol=1e-9):
                raise RuntimeError("Qlib value mismatch beyond float32 round-trip: %s/%s" % (symbol, f))
        readback[symbol] = {"open_ms": times, **vals}
    return readback, {"qlib_version": qlib.__version__, "read_path": "qlib.data.D.features",
                      "daily_rows": {s: len(daily[s]["open_ms"]) for s in SYMBOLS},
                      "readback_tolerance": "exact float32 dump_bin round-trip, else relative 1e-6",
                      "qlib_rows_identical_to_raw": True}


def load_funding(symbol, open_ms, end_ms, root=RAW_ROOT):
    path = Path(root) / "binance/usdm/funding" / symbol / ("%s-funding.jsonl.gz" % symbol)
    events = [[] for _ in open_ms]
    report = {"file_missing": not path.is_file(), "official": 0, "charged_events": 0,
              "modeled_ignored": 0, "other_ignored": 0, "out_of_window": 0, "days_with_official": 0,
              "days_total": len(open_ms), "first_official_utc": None, "last_official_utc": None,
              "missing_intervals_are_zero_not_modeled": True}
    if not path.is_file():
        return events, report
    report["source_sha256"] = sha256_file(path)
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            t = int(row["funding_time_ms"])
            if not int(open_ms[0]) <= t < int(end_ms) + MS_DAY:
                report["out_of_window"] += 1
                continue
            if row.get("truth_status") != "official":
                report["modeled_ignored" if row.get("truth_status") in ("modeled", "modeled_funding")
                       else "other_ignored"] += 1
                continue
            rate, mark = float(row["funding_rate"]), float(row["mark_price"])
            if not (math.isfinite(rate) and math.isfinite(mark) and mark > 0):
                raise RuntimeError("invalid official funding")
            bar = int(np.searchsorted(open_ms, t, side="right") - 1)
            if not 0 <= bar < len(events):
                raise RuntimeError("funding outside panel")
            events[bar].append((t, rate * mark))
            report["official"] += 1
            report["charged_events"] += 1
            iso = dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            report["first_official_utc"] = report["first_official_utc"] or iso
            report["last_official_utc"] = iso
    report["days_with_official"] = sum(bool(x) for x in events)
    report["official_events_per_day"] = report["official"] / max(1, len(events))
    report["coverage_note"] = ("official observations only; days before first_official_utc and any "
                               "missing interval contribute zero funding cost, never a modeled row")
    return events, report


# --------------------------------------------------------------------------------------
# Wasserstein-hyperplane DRO: mesh certificate + box-specialized dual LP
# --------------------------------------------------------------------------------------
def hyperplane_mesh(y_lo, y_hi, eta=ETA):
    """Record's supporting-hyperplane partition of [y_lo, y_hi] with certificate eta.

    Mesh dy <= sqrt(8*eta/L_f) with L_f = sup|U''| on the interval guarantees
    0 <= Uhat_M - U <= eta. Fails closed when the certificate cannot be met within
    MAX_HYPERPLANES (window needs an absurdly deep support box).
    """
    if not (y_lo > -1.0):
        raise RuntimeError("box support violates x_min > -1: y_lo=%r" % (y_lo,))
    if not (y_hi >= y_lo):
        raise RuntimeError("empty scalar return range")
    l_f = 1.0 / (1.0 + y_lo) ** 2            # sup |U''| for U(y) = log(1+y), U decreasing in y
    dy_max = math.sqrt(8.0 * eta / l_f)
    width = y_hi - y_lo
    m = max(2, int(math.ceil(width / dy_max)) + 1) if width > 0 else 2
    if m > MAX_HYPERPLANES:
        raise RuntimeError("hyperplane count %d exceeds cap %d (y_lo=%r y_hi=%r)"
                           % (m, MAX_HYPERPLANES, y_lo, y_hi))
    ys = np.linspace(y_lo, y_hi, m)
    alphas = 1.0 / (1.0 + ys)                 # U'(y_m)
    betas = np.log1p(ys) - ys * alphas        # U(y_m) - alpha_m*y_m
    dense = np.linspace(y_lo, y_hi, 513)
    gap = float(np.max(np.min(np.outer(dense, alphas) + betas, axis=1) - np.log1p(dense)))
    if gap > eta + 1e-12:
        raise RuntimeError("hyperplane certificate violated: gap=%r > eta=%r" % (gap, eta))
    return {"ys": ys, "alphas": alphas, "betas": betas, "m": m, "l_f": l_f,
            "dy": (width / (m - 1)) if m > 1 else 0.0, "gap_max": gap,
            "y_lo": y_lo, "y_hi": y_hi}


def solve_wasserstein(X, eps, xmin=None, xmax=None):
    """Box-specialized Wasserstein-hyperplane dual LP (record Corollary 3.7 shape).

    maximize  -lam*eps + (1/N) sum_j a_j
    s.t.  a_j + <X_j - xmin, s^m> - alpha_m <w, X_j> <= beta_m    (j=1..N, m=1..M)
          s^m >= alpha_m w - lam*1,  s^m >= 0                     (m=1..M)
          w >= 0, 1'w = 1, lam >= 0
    variables: [w(n), lam, a(N), s(n*M)]  -> n + 1 + N + n*M (record variable count).
    """
    X = np.asarray(X, dtype=np.float64)
    n_obs, n = X.shape
    if n_obs < 2 or n < 2:
        raise RuntimeError("degenerate scenario matrix")
    if not np.isfinite(X).all():
        raise RuntimeError("non-finite scenario")
    if np.any(X <= -1.0):
        raise RuntimeError("scenario return <= -1 breaks U(y) = log(1+y)")
    xmin = X.min(axis=0) if xmin is None else np.asarray(xmin, dtype=np.float64)
    xmax = X.max(axis=0) if xmax is None else np.asarray(xmax, dtype=np.float64)
    xmin = np.maximum(xmin, X_MIN_CLIP)
    if np.any(X < xmin) or np.any(X > xmax):
        raise RuntimeError("box support does not contain the scenarios")
    mesh = hyperplane_mesh(float(xmin.min()), float(xmax.max()))
    alphas, betas, m = mesh["alphas"], mesh["betas"], mesh["m"]
    eps_bar = float(np.sum(xmax - xmin))
    nw, na, ns = n, n_obs, n * m
    total = nw + 1 + na + ns
    off_a, off_s = nw + 1, nw + 1 + na
    rows, cols, vals, rhs = [], [], [], []
    r = 0
    shifted = X - xmin[None, :]
    for mi in range(m):
        a_m = float(alphas[mi])
        for j in range(n_obs):
            for i in range(n):
                rows.append(r); cols.append(i); vals.append(-a_m * float(X[j, i]))
                rows.append(r); cols.append(off_s + mi * n + i); vals.append(float(shifted[j, i]))
            rows.append(r); cols.append(off_a + j); vals.append(1.0)
            rhs.append(float(betas[mi]))
            r += 1
    for mi in range(m):
        a_m = float(alphas[mi])
        for i in range(n):
            rows.append(r); cols.append(i); vals.append(a_m)
            rows.append(r); cols.append(nw); vals.append(-1.0)
            rows.append(r); cols.append(off_s + mi * n + i); vals.append(-1.0)
            rhs.append(0.0)
            r += 1
    a_ub = sparse.csr_matrix((vals, (rows, cols)), shape=(r, total))
    b_ub = np.asarray(rhs, dtype=np.float64)
    c = np.zeros(total)
    c[nw] = float(eps)                    # minimize lam*eps - mean(a)  ==  maximize -lam*eps + mean(a)
    c[off_a:off_a + na] = -1.0 / n_obs
    a_eq = sparse.csr_matrix((np.ones(nw), (np.zeros(nw), np.arange(nw))), shape=(1, total))
    bounds = [(0.0, None)] * nw + [(0.0, None)] + [(None, None)] * na + [(0.0, None)] * ns
    res = linprog(c, A_ub=a_ub, b_ub=b_ub, A_eq=a_eq, b_eq=[1.0], bounds=bounds, method="highs")
    if not res.success or res.x is None:
        raise RuntimeError("Wasserstein LP failed (eps=%g): %s" % (eps, res.message))
    w = np.asarray(res.x[:nw], dtype=np.float64)
    lam = float(res.x[nw])
    a = np.asarray(res.x[off_a:off_a + na], dtype=np.float64)
    if abs(float(w.sum()) - 1.0) > 1e-7 or float(w.min()) < -1e-9 or lam < -1e-9:
        raise RuntimeError("Wasserstein LP returned an infeasible vertex")
    objective = -lam * eps + float(a.mean())
    return {"w": w, "lam": lam, "objective": objective, "mesh": mesh,
            "xmin": xmin, "xmax": xmax, "eps_bar": eps_bar,
            "sum_dev": abs(float(w.sum()) - 1.0), "min_w": float(w.min())}


def solve_saa_epigraph(X):
    """Independent SAA through the same hyperplane surrogate (eps = 0 anchor).

    maximize (1/N) sum_j z_j  s.t.  z_j <= alpha_m <w, X_j> + beta_m, 1'w = 1, w >= 0.
    Two independent LPs agreeing proves the engine's eps = 0 anchor is SAA.
    """
    X = np.asarray(X, dtype=np.float64)
    n_obs, n = X.shape
    xmin = np.maximum(X.min(axis=0), X_MIN_CLIP)
    xmax = X.max(axis=0)
    mesh = hyperplane_mesh(float(xmin.min()), float(xmax.max()))
    alphas, betas, m = mesh["alphas"], mesh["betas"], mesh["m"]
    total = n + n_obs
    rows, cols, vals, rhs = [], [], [], []
    r = 0
    for mi in range(m):
        for j in range(n_obs):
            for i in range(n):
                rows.append(r); cols.append(i); vals.append(-float(alphas[mi]) * float(X[j, i]))
            rows.append(r); cols.append(n + j); vals.append(1.0)
            rhs.append(float(betas[mi]))
            r += 1
    a_ub = sparse.csr_matrix((vals, (rows, cols)), shape=(r, total))
    c = np.zeros(total)
    c[n:] = -1.0 / n_obs
    a_eq = sparse.csr_matrix((np.ones(n), (np.zeros(n), np.arange(n))), shape=(1, total))
    res = linprog(c, A_ub=a_ub, b_ub=np.asarray(rhs), A_eq=a_eq, b_eq=[1.0],
                  bounds=[(0.0, None)] * n + [(None, None)] * n_obs, method="highs")
    if not res.success or res.x is None:
        raise RuntimeError("SAA epigraph LP failed: %s" % res.message)
    w = np.asarray(res.x[:n], dtype=np.float64)
    z = np.asarray(res.x[n:], dtype=np.float64)
    if abs(float(w.sum()) - 1.0) > 1e-7 or float(w.min()) < -1e-9:
        raise RuntimeError("SAA LP returned an infeasible vertex")
    return {"w": w, "objective": float(z.mean())}


def maximin_weights(xmin):
    """Record Theorem 3.13 / Corollary 3.15 closed form: uniform over argmax_i x_min_i."""
    xmin = np.asarray(xmin, dtype=np.float64)
    best = float(xmin.max())
    mask = xmin >= best - 1e-12
    w = mask.astype(np.float64)
    return w / w.sum(), mask


def majorant_gap(y, mesh):
    """Uhat_M(y) - U(y) >= 0; the certificate says <= eta on the certified interval."""
    y = np.atleast_1d(np.asarray(y, dtype=np.float64))
    if np.any(y <= -1.0):
        raise RuntimeError("scalar return <= -1 breaks U(y) = log(1+y)")
    vals = np.min(np.outer(y, mesh["alphas"]) + mesh["betas"], axis=1)
    return vals - np.log1p(y)


def epoch_days(n_bars, lookback, every=REBALANCE_EVERY):
    """Data-independent weekly epoch grid: multiples of `every`, warm-up safe.

    t >= lookback keeps the first window inside returns[1:] (index 0 is the
    placeholder return), t <= n-2 keeps the decision executable at t+1's open.
    """
    return [t for t in range(lookback, n_bars - 1) if t % every == 0]


# --------------------------------------------------------------------------------------
# Point-in-time allocation layer
# --------------------------------------------------------------------------------------
def scenario_returns(daily, carry):
    """Net expected return matrix per asset: price return minus official funding carry."""
    out = {}
    for s in SYMBOLS:
        r = np.r_[0.0, daily[s]["close"][1:] / daily[s]["close"][:-1] - 1.0]
        if len(carry[s]) != len(r):
            raise RuntimeError("carry length mismatch for " + s)
        out[s] = r - carry[s]
    if not all(np.isfinite(v).all() for v in out.values()):
        raise RuntimeError("non-finite scenario returns")
    return out


def build_layer(daily, scen, eps, lookback, report=None):
    """Solve the registered LP at every weekly epoch for one (eps, lookback) layer."""
    ref = daily[SYMBOLS[0]]
    n = len(ref["close"])
    epochs = epoch_days(n, lookback)
    weights = np.zeros((n, len(SYMBOLS)), dtype=np.float64)
    defined = np.zeros(n, dtype=bool)
    meta = []
    worst_sum, worst_neg, max_gap, max_m = 0.0, 0.0, 0.0, 0
    max_data_index = -1
    for t in epochs:
        X = np.vstack([scen[s][t - lookback + 1:t + 1] for s in SYMBOLS]).T
        if X.shape != (lookback, len(SYMBOLS)):
            raise RuntimeError("bad scenario matrix at epoch %d" % t)
        sol = solve_wasserstein(X, eps)
        weights[t] = sol["w"]
        defined[t] = True
        max_data_index = max(max_data_index, t)
        worst_sum = max(worst_sum, sol["sum_dev"])
        worst_neg = max(worst_neg, -min(0.0, sol["min_w"]))
        max_gap = max(max_gap, sol["mesh"]["gap_max"])
        max_m = max(max_m, sol["mesh"]["m"])
        meta.append({"day_index": t, "date": stamp(int(ref["open_ms"][t])),
                     "m_hyperplanes": sol["mesh"]["m"], "mesh_gap_max": sol["mesh"]["gap_max"],
                     "y_lo": sol["mesh"]["y_lo"], "y_hi": sol["mesh"]["y_hi"],
                     "lam": sol["lam"], "eps_bar": sol["eps_bar"], "objective": sol["objective"],
                     "xmin": [float(v) for v in sol["xmin"]],
                     "xmax": [float(v) for v in sol["xmax"]],
                     "w": [float(v) for v in sol["w"]]})
    anchor = 1.0 / len(SYMBOLS)
    directions = {}
    for si, s in enumerate(SYMBOLS):
        d = np.zeros(n, dtype=np.int8)
        cur = 0
        for t in range(n):
            if defined[t]:
                cur = 1 if weights[t, si] > anchor else 0
            d[t] = cur
        directions[s] = d
    if report is not None:
        report.update({"eps": eps, "lookback_n": lookback, "epochs": len(epochs),
                       "first_epoch": epochs[0] if epochs else None,
                       "last_epoch": epochs[-1] if epochs else None,
                       "max_data_index_used": max_data_index,
                       "worst_sum_w_deviation": worst_sum, "worst_neg_weight": worst_neg,
                       "max_mesh_gap": max_gap, "max_hyperplanes": max_m,
                       "warmup_flat": bool(all(directions[s][:epochs[0]] .max(initial=0) == 0
                                               for s in SYMBOLS)) if epochs else True,
                       "direction_counts": {s: {"long": int((directions[s] == 1).sum()),
                                                "flat": int((directions[s] == 0).sum())}
                                            for s in SYMBOLS}})
    return {"weights": weights, "defined": defined, "directions": directions,
            "epochs": epochs, "meta": meta, "eps": eps, "lookback_n": lookback}


# --------------------------------------------------------------------------------------
# DCA ladder execution (candidate body DCA rail, per-fill accounting)
# --------------------------------------------------------------------------------------
def window_indices(ms, start, end):
    return int(np.searchsorted(ms, utc_ms(start))), int(np.searchsorted(ms, utc_ms(end) + MS_DAY))


def empty_metric(i0, i1):
    return {"gross_pnl": 0.0, "fees": 0.0, "funding": 0.0, "net_pnl": 0.0,
            "ending_equity": START_EQUITY, "episodes": 0, "fills": 0, "adds": 0,
            "turnover_usdt": 0.0, "sharpe": 0.0, "max_dd_pct": 0.0, "max_dd_usdt": 0.0,
            "annualized_return": 0.0, "max_effective_leverage": 0.0, "capital_utilization": 0.0,
            "tp_hits": 0, "stop_hits": 0, "margin_calls": 0, "end_exits": 0, "open_at_end": 0,
            "signal_flat_exits": 0, "rebalance_exits": 0, "layer_hist": [0] * LADDER_LEVELS,
            "decomposition_ok": True, "daily_equity": [START_EQUITY] * max(0, i1 - i0)}


def metric_block(m):
    keys = ("gross_pnl", "fees", "funding", "net_pnl", "ending_equity", "episodes", "fills",
            "adds", "turnover_usdt", "sharpe", "max_dd_pct", "max_dd_usdt", "annualized_return",
            "max_effective_leverage", "capital_utilization", "tp_hits", "stop_hits", "margin_calls",
            "end_exits", "open_at_end", "signal_flat_exits", "rebalance_exits")
    return {k: m[k] for k in keys}


def simulate(panel, funding_events, direction, dca, i0, i1, cost, instrument):
    """Run the registered ladder on daily bars for one (signal, DCA, phase) cell.

    Daily bar = one deterministic conservative ordering: open actions (signal-flat
    flatten, then tranche #1) at the open with adverse tick slippage, then that day's
    official settlements while the book is live, then the intraday rail (open-gap
    invalidation, adverse scale-ins, margin guard, invalidation, breakeven TP),
    then the window-end flatten.  Gross PnL is accumulated only from the independent
    price-PnL expression.
    """
    if i1 <= i0:
        return empty_metric(i0, i1)
    if i0 < 0 or i1 > len(panel["open"]):
        raise ValueError("window outside panel")
    tick = instrument["tick"] * int(cost.get("slip_ticks", 1))
    fee_rate = float(cost.get("fee_override", instrument["taker_fee"] * cost.get("fee_mult", 1.0)))
    if fee_rate < 0:
        raise ValueError("negative fee")
    mult = float(dca["size_multiplier"])
    spacing = float(dca["spacing_pct"])
    tp_pct = float(dca["breakeven_tp_pct"])
    inv_pct = float(dca["invalidation_pct"])
    delay = int(cost.get("entry_delay", 0))
    funding_mult = float(cost.get("funding_mult", 1.0))
    no_funding = bool(cost.get("no_funding", False))
    n_days = i1 - i0
    cash = np.zeros(n_days)
    unreal = np.zeros(n_days)
    gross = fees = funding_paid = turnover = realized = 0.0
    eps = fills = adds = tp_hits = stop_hits = margin_calls = end_exits = 0
    signal_flat_exits = rebalance_exits = 0
    max_lev = max_util = 0.0
    hist = [0] * LADDER_LEVELS
    O, H, L, C, ms = panel["open"], panel["high"], panel["low"], panel["close"], panel["open_ms"]
    qty = basis = 0.0
    entry_price = 0.0
    quote0 = 0.0
    entry_day = -1
    next_level = 1
    layers_used = 0
    held_dir = 0

    def close_position(exit_px, base, reason):
        nonlocal qty, basis, gross, fees, realized, turnover, fills, funding_paid
        nonlocal signal_flat_exits, rebalance_exits, end_exits, hist
        ep_gross = qty * exit_px - basis
        gross += ep_gross
        exit_fee = abs(qty) * abs(exit_px) * fee_rate
        realized += ep_gross - exit_fee
        fees += exit_fee
        turnover += abs(qty * exit_px)
        fills += 1
        cash[base] += ep_gross - exit_fee
        hist[0] += 1
        hist[min(LADDER_LEVELS - 1, layers_used)] += 1
        if reason == "signal_flat":
            signal_flat_exits += 1
        elif reason == "rebalance":
            rebalance_exits += 1
        elif reason == "close":
            end_exits += 1
        qty = basis = 0.0

    for di in range(i0, i1):
        base = di - i0
        sig_idx = di - 1 - delay
        target = int(direction[sig_idx]) if 0 <= sig_idx < len(direction) else 0
        open_px = float(O[di])
        if open_px <= 0:
            raise RuntimeError("nonpositive open")
        if qty != 0 and target == 0:
            # the record's allocation withdraws this leg: reduce-only flatten, then FLAT
            side = 1.0 if held_dir > 0 else -1.0
            close_position(open_px - tick * side, base, "signal_flat")
        elif qty != 0 and target != 0 and target != held_dir:
            side = 1.0 if held_dir > 0 else -1.0
            close_position(open_px - tick * side, base, "rebalance")
        if qty == 0 and target != 0:
            side = 1.0 if target > 0 else -1.0
            fill = open_px + tick * side
            equity_before = START_EQUITY + realized
            quote0 = min(BASE_QUOTE * LEVERAGE, equity_before * LEVERAGE)
            if quote0 > 0 and equity_before > quote0 / LEVERAGE:
                qty0 = target * quote0 / fill
                fee0 = quote0 * fee_rate
                if equity_before - fee0 > quote0 / LEVERAGE:
                    realized -= fee0
                    fees += fee0
                    turnover += quote0
                    fills += 1
                    cash[base] -= fee0
                    qty, basis = qty0, qty0 * fill
                    entry_price = fill
                    entry_day = di
                    next_level = 1
                    layers_used = 1
                    held_dir = target
                    eps += 1
        if qty != 0:
            side = 1.0 if qty > 0 else -1.0
            old_avg = basis / qty
            old_stop = old_avg * (1.0 - side * inv_pct)
            if not no_funding:
                for ev_t, amount in funding_events[di]:
                    if ev_t <= int(ms[di]):
                        continue          # settles at/before the open: prior book
                    charge = qty * amount * funding_mult
                    realized -= charge
                    funding_paid += charge
                    cash[base] -= charge
            if di > entry_day and ((side > 0 and open_px <= old_stop)
                                   or (side < 0 and open_px >= old_stop)):
                close_position(open_px - tick * side, base, "stop")
                stop_hits += 1
            if qty != 0:
                adverse = float(L[di]) if side > 0 else float(H[di])
                margin_equity = START_EQUITY + realized + (qty * adverse - basis)
                if margin_equity > 0:
                    max_lev = max(max_lev, abs(qty) * adverse / margin_equity)
                    max_util = max(max_util, abs(qty) * adverse / LEVERAGE / margin_equity)
                if margin_equity <= 0 or abs(qty) * adverse / LEVERAGE > margin_equity:
                    close_position(adverse - tick * side, base, "margin")
                    margin_calls += 1
            if qty != 0:
                while next_level <= MAX_ADD_LEVELS:
                    trigger = entry_price * (1.0 - side * spacing * next_level)
                    reached = float(L[di]) <= trigger if side > 0 else float(H[di]) >= trigger
                    beyond = trigger <= old_stop if side > 0 else trigger >= old_stop
                    if not reached or beyond:
                        break
                    fill = trigger + tick * side
                    quote = quote0 * mult ** next_level
                    add_qty = side * quote / fill
                    add_fee = quote * fee_rate
                    equity_at_add = START_EQUITY + realized + (qty * fill - basis) - add_fee
                    if equity_at_add <= 0 or abs(qty + add_qty) * fill / LEVERAGE > equity_at_add:
                        close_position(fill - tick * side, base, "margin")
                        margin_calls += 1
                        break
                    realized -= add_fee
                    fees += add_fee
                    turnover += quote
                    fills += 1
                    adds += 1
                    cash[base] -= add_fee
                    qty += add_qty
                    basis += add_qty * fill
                    max_lev = max(max_lev, abs(qty) * fill / equity_at_add)
                    max_util = max(max_util, abs(qty) * fill / LEVERAGE / equity_at_add)
                    next_level += 1
                    layers_used += 1
            if qty != 0:
                avg = basis / qty
                stop = avg * (1.0 - side * inv_pct)
                take = avg * (1.0 + side * tp_pct)
                pre_hit = float(L[di]) <= old_stop if side > 0 else float(H[di]) >= old_stop
                new_hit = float(L[di]) <= stop if side > 0 else float(H[di]) >= stop
                if pre_hit or new_hit:
                    reached = [x for x, crossed in ((old_stop, pre_hit), (stop, new_hit)) if crossed]
                    trigger = min(reached) if side > 0 else max(reached)  # conservative fill
                    close_position(trigger - tick * side, base, "stop")
                    stop_hits += 1
                elif (float(H[di]) >= take) if side > 0 else (float(L[di]) <= take):
                    close_position(take - tick * side, base, "tp")
                    tp_hits += 1
            if qty != 0 and di == i1 - 1:
                close_position(float(C[di]) - tick * side, base, "close")
            unreal[base] = qty * float(C[di]) - basis if qty != 0 else 0.0
    equity = START_EQUITY + np.cumsum(cash) + unreal
    net = float(realized)
    independent_cash = float(cash.sum())
    high = np.maximum.accumulate(np.r_[START_EQUITY, equity])
    dd = high[1:] - equity
    returns = np.diff(np.r_[START_EQUITY, equity]) / np.maximum(np.r_[START_EQUITY, equity[:-1]], 1e-9)
    sd = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0
    sharpe = float(np.mean(returns) / sd * math.sqrt(365)) if sd > 1e-12 else 0.0
    duration = max(1, n_days)
    annual = float((max(float(equity[-1]), 1e-9) / START_EQUITY) ** (365.0 / duration) - 1)
    return {"gross_pnl": float(gross), "fees": float(fees), "funding": float(funding_paid),
            "net_pnl": net, "ending_equity": float(equity[-1]) if len(equity) else START_EQUITY,
            "episodes": eps, "fills": fills, "adds": adds, "turnover_usdt": float(turnover),
            "sharpe": sharpe, "max_dd_pct": float(np.max(dd / np.maximum(high[1:], 1e-9)) * 100) if len(dd) else 0.0,
            "max_dd_usdt": float(np.max(dd)) if len(dd) else 0.0, "annualized_return": annual,
            "max_effective_leverage": float(max_lev), "capital_utilization": float(max_util),
            "tp_hits": tp_hits, "stop_hits": stop_hits, "margin_calls": margin_calls,
            "end_exits": end_exits, "open_at_end": 0, "signal_flat_exits": signal_flat_exits,
            "rebalance_exits": rebalance_exits, "layer_hist": hist,
            "decomposition_ok": abs(gross - fees - funding_paid - net) < 1e-3
            and abs(net - independent_cash) < 1e-3
            and abs(net - (float(equity[-1]) - START_EQUITY)) < 1e-3,
            "daily_equity": equity.tolist()}


# --------------------------------------------------------------------------------------
# Selector / disposition (cohort-selector-v1, cohort-disposition-v1)
# --------------------------------------------------------------------------------------
def cell_key(row):
    return (row["symbol"], row["timeframe"], row["wasserstein_epsilon"], row["lookback_n"],
            *(row[k] for k in DCA_AXES))


def _axis_index(axis_name, value):
    domain = STRATEGY_AXES.get(axis_name) or DCA_AXES[axis_name]
    return list(domain).index(value)


def neighbourhood(winner, historical):
    if winner["grid"] != "historical" or any(r["grid"] != "historical" for r in historical):
        raise ValueError("selector may only read historical rows")
    found = {cell_key(r): r for r in historical}
    # Frozen round-spec parameter_contract: TWO strategy axes (wasserstein_epsilon,
    # lookback_n) plus the four atomic DCA axes; face neighbours are walked in that
    # joint space.
    steps = [((2,), list(STRATEGY_AXES["wasserstein_epsilon"])),
             ((3,), list(STRATEGY_AXES["lookback_n"]))]
    steps += [((pos,), list(domain)) for pos, domain in enumerate(DCA_AXES.values(), start=4)]
    agrees = count = 0
    key = list(cell_key(winner))
    for positions, domain in steps:
        pos = positions[0]
        ix = domain.index(key[pos])
        for delta in (-1, 1):
            if 0 <= ix + delta < len(domain):
                candidate = key.copy()
                candidate[pos] = domain[ix + delta]
                row = found[tuple(candidate)]
                count += 1
                agrees += float(row["net_pnl"]) > 0
    return {"neighbours": count, "agreeing": agrees,
            "same_sign_fraction": agrees / count if count else 0.0,
            "passed": bool(count and agrees / count >= MIN_NEIGHBOUR)}


def _order_key(r):
    """Sharpe desc, net_pnl desc, then registered-index lexical tie-break."""
    return (-float(r["sharpe"]), -float(r["net_pnl"]),
            _axis_index("wasserstein_epsilon", r["wasserstein_epsilon"]),
            _axis_index("lookback_n", r["lookback_n"]),
            *(_axis_index(k, r[k]) for k in DCA_AXES))


def select_cohort(rows):
    hist = rows["historical"]
    if not hist or any(r["grid"] != "historical" for r in hist):
        raise ValueError("selector saw a non-historical row")
    best = max(int(r["episodes"]) for r in hist)
    if best < MIN_EPISODES_IS:
        return None, {"cull_reasons": ["insufficient_trades"], "best_historical_episodes": best}
    candidates = [r for r in hist if int(r["episodes"]) >= MIN_EPISODES_IS
                  and float(r["net_pnl"]) > 0 and float(r["sharpe"]) > 0]
    if not candidates:
        return None, {"cull_reasons": ["no_qualifying_candidate"], "best_historical_episodes": best}
    winner = min(candidates, key=_order_key)
    lookup = {g: next(r for r in rows[g] if cell_key(r) == cell_key(winner)) for g in GRIDS}
    reasons = []
    oos = lookup["oos"]
    if not (float(oos["net_pnl"]) > 0 and float(oos["sharpe"]) > 0
            and int(oos["episodes"]) >= MIN_EPISODES_OOS):
        reasons.append("oos_economic")
    if float(lookup["full"]["net_pnl"]) <= 0:
        reasons.append("full_economic")
    weak = [g for g in ("fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks")
            if float(lookup[g]["net_pnl"]) <= 0]
    if weak:
        reasons.append("robustness_economic:" + ",".join(weak))
    neighbors = neighbourhood(winner, hist)
    if not neighbors["passed"]:
        reasons.append("parameter_neighbourhood")
    phases = {g: metric_block(lookup[g]) for g in ("historical", "oos", "full")}
    robustness = {g: metric_block(lookup[g]) for g in
                  ("fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks")}
    detail = {"winner": {k: winner[k] for k in ("wasserstein_epsilon", "lookback_n", *DCA_AXES)},
              "winner_source_grid": "historical",
              "best_historical_episodes": best, "neighbourhood": neighbors,
              "phases": phases, "robustness": robustness,
              "metrics": {**phases, "robustness": robustness, "neighbourhood": neighbors},
              "cull_reasons": reasons}
    return (winner if not reasons else None), detail


def write_grid(path, rows):
    metric_keys = list(metric_block(empty_metric(0, 0)))
    cols = ["symbol", "timeframe", "wasserstein_epsilon", "lookback_n", "case_label", *DCA_AXES,
            "grid", *metric_keys, "decomposition_ok"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def cost_for(grid):
    return {"fee_2x": {"fee_mult": 2.0}, "funding_2x": {"funding_mult": 2.0},
            "entry_delay_1_bar": {"entry_delay": 1}, "slippage_2ticks": {"slip_ticks": 2},
            "no_funding": {"no_funding": True}, "no_funding_full": {"no_funding": True},
            "cost_attrition_40bps": {"fee_override": 0.004}}.get(grid, {})


# --------------------------------------------------------------------------------------
# Portfolio-level diagnostics: benchmarks, falsification battery
# --------------------------------------------------------------------------------------
def simple_returns(daily):
    mat = np.vstack([daily[s]["close"] for s in SYMBOLS])
    ref = daily[SYMBOLS[0]]["open_ms"]
    for s in SYMBOLS:
        if len(daily[s]["open_ms"]) != len(ref) or not np.array_equal(daily[s]["open_ms"], ref):
            raise RuntimeError("symbol calendars are not aligned: " + s)
    out = np.zeros(mat.shape)
    out[:, 1:] = mat[:, 1:] / mat[:, :-1] - 1.0
    return out


def sharpe_from_returns(rets):
    r = np.asarray(rets, dtype=np.float64)
    if len(r) < 3:
        return 0.0
    sd = float(np.std(r, ddof=1))
    return float(np.mean(r) / sd * math.sqrt(365)) if sd > 1e-12 else 0.0


def max_drawdown_pct(equity_curve):
    eq = np.asarray(equity_curve, dtype=float)
    if len(eq) < 2:
        return 0.0
    high = np.maximum.accumulate(eq)
    dd = (high - eq) / np.maximum(high, 1e-9)
    return float(np.max(dd) * 100.0)


def held_weights(layer, n_bars):
    """Weights held during day d = the decision of the last epoch <= d-1 (PIT)."""
    W = np.zeros((n_bars, len(SYMBOLS)), dtype=np.float64)
    cur = None
    epochs = set(layer["epochs"])
    for d in range(n_bars):
        if d - 1 in epochs:
            cur = layer["weights"][d - 1].copy()
        if cur is not None:
            W[d] = cur
    return W


def layer_portfolio_returns(layer, net_rets, cost_rate=0.0, start=0):
    """Net portfolio daily returns of a frozen layer: net asset returns (price minus
    official funding carry) with an explicit ex-post turnover cost (record tiers)."""
    n_bars = net_rets.shape[1]
    W = held_weights(layer, n_bars)
    rp = np.einsum("tn,tn->t", W, net_rets.T)
    turnover = np.zeros(n_bars)
    turnover[1:] = np.abs(W[1:] - W[:-1]).sum(axis=1)
    net = rp - cost_rate * turnover
    return net[start:], turnover[start:]


def buy_and_hold_ew(net_rets, start=0):
    sub = net_rets[:, start:]
    n = sub.shape[1]
    w = np.full(sub.shape[0], 1.0 / sub.shape[0])
    out = np.zeros((sub.shape[0], n))
    for t in range(n):
        out[:, t] = w
        if t + 1 < n:
            grown = w * (1.0 + sub[:, t])
            total = grown.sum()
            w = grown / total if total > 0 else w
    rp = np.einsum("in,in->n", out, sub)
    return rp


def _aggregate(details):
    flags = [v.get("falsified") for v in details.values()]
    if not flags or all(f is None for f in flags):
        return "INDETERMINATE"
    if any(f is True for f in flags):
        return "FALSIFIED"
    return "NOT_FALSIFIED"


def _window_slice(arr, i0, i1):
    return arr[i0:i1]


def falsification_report(layers, sweep, daily, net_rets, windows, funding_events):
    """All four registered record falsification items, measured locally.

    These probes are portfolio-level diagnostics over the registered allocation rule;
    they never enter the selector, never alter the eligible universe and never feed
    the ladder grids.  Every item is computable here, so no item is left unevaluated.
    """
    n_bars = net_rets.shape[1]
    out = {"items": {}, "scope": "local adapted USD-M perp universe, cross-sectional allocation",
           "selection_leak": "none: the probes read the frozen allocation layers only",
           "sweep_epsilon": list(SWEEP_EPS), "saa_anchor_epsilon": SAA_EPS,
           "intermediate_band": list(INTERMEDIATE_EPS)}
    # --- item 1: Wasserstein radius sensitivity sweep ------------------------------
    item1 = {}
    for lookback in STRATEGY_AXES["lookback_n"]:
        rows = {}
        starts = {}
        for eps in (SAA_EPS,) + tuple(SWEEP_EPS):
            layer = sweep[(eps, lookback)]
            start = layer["epochs"][0]
            starts[eps] = start
            series, _ = layer_portfolio_returns(layer, net_rets, SWEEP_TIER_COST, start=start)
            rows[eps] = {"sharpe": sharpe_from_returns(series),
                         "cum_return": float(np.prod(1.0 + series) - 1.0)}
        # equal-weight buy-and-hold benchmark from the same first epoch (zero cost)
        s0 = starts[SAA_EPS]
        ew = buy_and_hold_ew(net_rets, start=s0)
        ew_bh = {"sharpe": sharpe_from_returns(ew), "cum_return": float(np.prod(1.0 + ew) - 1.0)}
        # out-of-sample slice relative to the shared start
        oos_i0, oos_i1 = windows["oos"]
        oos_rel = (max(oos_i0, s0) - s0, oos_i1 - s0)
        oos_sweep = {}
        for eps in (SAA_EPS,) + tuple(SWEEP_EPS):
            layer = sweep[(eps, lookback)]
            series, _ = layer_portfolio_returns(layer, net_rets, SWEEP_TIER_COST, start=s0)
            sl = series[oos_rel[0]:oos_rel[1]]
            oos_sweep[eps] = {"sharpe": sharpe_from_returns(sl),
                              "cum_return": float(np.prod(1.0 + sl) - 1.0)}
        ew_oos = ew[oos_rel[0]:oos_rel[1]]
        ew_oos_row = {"sharpe": sharpe_from_returns(ew_oos),
                      "cum_return": float(np.prod(1.0 + ew_oos) - 1.0)}
        sharpes = [oos_sweep[e]["sharpe"] for e in SWEEP_EPS]
        monotone = all(sharpes[i] >= sharpes[i + 1] - 1e-9 for i in range(len(sharpes) - 1))
        saa_oos = oos_sweep[SAA_EPS]
        intermediate = {("%g" % e): {**oos_sweep[e],
                                     "beats_saa": oos_sweep[e]["sharpe"] > saa_oos["sharpe"],
                                     "beats_ew_bh": oos_sweep[e]["sharpe"] > ew_oos_row["sharpe"]}
                        for e in INTERMEDIATE_EPS}
        beats_both = any(v["beats_saa"] and v["beats_ew_bh"] for v in intermediate.values())
        item1["lookback_%d" % lookback] = {
            "oos_sweep": {"%g" % e: oos_sweep[e] for e in (SAA_EPS,) + tuple(SWEEP_EPS)},
            "full_sweep": {"%g" % e: rows[e] for e in (SAA_EPS,) + tuple(SWEEP_EPS)},
            "oos_saa": saa_oos, "oos_equal_weight_bh": ew_oos_row,
            "oos_monotonically_decreasing": bool(monotone),
            "intermediate_beats_both": bool(beats_both), "intermediate": intermediate,
            "falsified": bool(monotone or not beats_both)}
    out["items"]["radius_sweep"] = {
        "registered_rule": FALSIFICATION_REGISTRY["radius_sweep"],
        "status": _aggregate(item1), "cases": item1,
        "cost": "ex-post turnover cost %s per unit turnover (record 0.1%% tier)" % SWEEP_TIER_COST,
        "benchmark": "SAA anchor (eps=0) and equal-weight buy-and-hold from the layer's first epoch",
        "window": "out-of-sample (2025-10-01..2026-09-11), research-defined operating rule"}
    # --- item 2: support boundary misspecification ---------------------------------
    item2 = {}
    for case in CASES:
        layer = layers[(case["wasserstein_epsilon"], case["lookback_n"])]
        epochs = layer["epochs"]
        worst_gap = 0.0
        worst_in_gap = 0.0
        breach_days = 0
        eval_days = 0
        worst_breach = 0.0
        for idx, t in enumerate(epochs):
            end = epochs[idx + 1] if idx + 1 < len(epochs) else n_bars
            meta = layer["meta"][idx]
            mesh = hyperplane_mesh(meta["y_lo"], meta["y_hi"])
            w = np.asarray(meta["w"], dtype=np.float64)
            xmin = np.asarray(meta["xmin"], dtype=np.float64)
            xmax = np.asarray(meta["xmax"], dtype=np.float64)
            for d in range(t + 1, end):
                x = np.array([net_rets[si, d] for si in range(len(SYMBOLS))])
                y = float(w @ x)
                gap = float(majorant_gap(y, mesh)[0])
                worst_gap = max(worst_gap, gap)
                eval_days += 1
                outside = bool(np.any(x < xmin - 1e-12) or np.any(x > xmax + 1e-12))
                if outside:
                    breach_days += 1
                    worst_breach = max(worst_breach, float(np.max(np.maximum(xmin - x, x - xmax))))
                else:
                    worst_in_gap = max(worst_in_gap, gap)
        item2[case["label"]] = {"evaluated_days": eval_days, "breach_days": breach_days,
                                "worst_breach": worst_breach,
                                "max_majorant_gap": worst_gap,
                                "max_majorant_gap_in_support": worst_in_gap,
                                "eta": ETA,
                                "falsified": bool(worst_gap > ETA + 1e-9)}
    out["items"]["support_boundary_misspecification"] = {
        "registered_rule": FALSIFICATION_REGISTRY["support_boundary_misspecification"],
        "status": _aggregate(item2), "cases": item2,
        "evaluation": "realized net return vector after each epoch evaluated by that epoch's frozen hyperplane mesh",
        "window": "all epochs in the registered window"}
    # --- item 3: turnover & execution slippage hurdle -------------------------------
    item3 = {}
    fee_slip = ITEM3_FEE + ITEM3_SLIPPAGE
    for case in CASES:
        layer = layers[(case["wasserstein_epsilon"], case["lookback_n"])]
        s0 = layer["epochs"][0]
        series, turnover = layer_portfolio_returns(layer, net_rets, fee_slip, start=s0)
        # monthly turnover: group each rebalance-day L1 turnover by calendar month
        by_month = {}
        W = held_weights(layer, n_bars)
        for d in layer["epochs"]:
            if d <= 0:
                continue
            turn = float(np.abs(W[d] - W[d - 1]).sum())
            if turn <= 0:
                continue
            key = stamp(int(daily[SYMBOLS[0]]["open_ms"][d]))[:7]
            by_month[key] = by_month.get(key, 0.0) + turn
        monthly = float(np.mean(list(by_month.values()))) if by_month else 0.0
        sharpe = sharpe_from_returns(series)
        item3[case["label"]] = {"monthly_turnover": monthly, "net_sharpe": sharpe,
                                "fee_plus_slippage": fee_slip,
                                "falsified": bool(monthly > ITEM3_TURNOVER_THRESHOLD
                                                  and sharpe < ITEM3_SHARPE_THRESHOLD)}
    out["items"]["turnover_slippage_hurdle"] = {
        "registered_rule": FALSIFICATION_REGISTRY["turnover_slippage_hurdle"],
        "status": _aggregate(item3), "cases": item3,
        "turnover_definition": "Turnover_t = sum_i |w_t,i - w_{t-1,i}| (the record's own definition), aggregated per calendar month",
        "window": "full window"}
    # --- item 4: rejection threshold (>= 3y walk-forward) ---------------------------
    item4 = {}
    i0, i1 = windows["full"]
    for case in CASES:
        layer = layers[(case["wasserstein_epsilon"], case["lookback_n"])]
        s0 = layer["epochs"][0]
        series, _ = layer_portfolio_returns(layer, net_rets, SWEEP_TIER_COST, start=s0)
        years = (i1 - max(i0, s0)) / 365.0
        sharpe = sharpe_from_returns(series)
        eq = np.cumprod(1.0 + series)
        dd = max_drawdown_pct(np.r_[1.0, eq])
        item4[case["label"]] = {"years_walk_forward": round(years, 2),
                                "annualized_sharpe": sharpe, "max_dd_pct": dd,
                                "oos_sharpe": sharpe_from_returns(
                                    layer_portfolio_returns(layer, net_rets, SWEEP_TIER_COST,
                                                            start=s0)[0][
                                        max(windows["oos"][0], s0) - s0:windows["oos"][1] - s0]),
                                "falsified": bool(sharpe < ITEM4_SHARPE_THRESHOLD
                                                  or dd > ITEM4_MAXDD_THRESHOLD)}
    out["items"]["rejection_threshold"] = {
        "registered_rule": FALSIFICATION_REGISTRY["rejection_threshold"],
        "status": _aggregate(item4), "cases": item4,
        "window": "full window walk-forward 2022-01-01..2026-09-11 (4.67y >= 3y), every decision from trailing data only",
        "thresholds": {"sharpe": ITEM4_SHARPE_THRESHOLD, "max_dd_pct": ITEM4_MAXDD_THRESHOLD}}
    hits = sorted(k for k, v in out["items"].items() if v.get("status") == "FALSIFIED")
    out["falsification_hits"] = hits
    out["indeterminate_items"] = sorted(k for k, v in out["items"].items()
                                        if v.get("status") == "INDETERMINATE")
    out["battery_status"] = "FAIL" if hits else (
        "NOT_FALSIFIED_WITH_INDETERMINATE" if out["indeterminate_items"] else "NOT_FALSIFIED")
    out["scoped_note"] = ("the four registered record items are measured on the cross-sectional "
                          "allocation layers; a hit is reported as a family-level science failure "
                          "per the body FAILURE TAXONOMY and is never converted into a pass")
    return out


def equal_weight_report(net_rets, windows):
    report = {"definition": "daily rebalanced 1/N over the four-symbol local universe (net returns)",
              "sharpe": {}, "annualized": {}}
    for g, (a, b) in windows.items():
        series = net_rets[:, a:b].mean(axis=0)
        report["sharpe"][g] = sharpe_from_returns(series)
        eq = np.cumprod(1.0 + series)
        report["annualized"][g] = float(eq[-1] ** (365.0 / max(1, b - a)) - 1)
    return report


# --------------------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------------------
def run(spec, attempt_dir):
    counts = validate_spec(spec)
    path = Path(attempt_dir)
    expected = Path(RESULTS_ROOT) / FAMILY_ID / "rounds" / spec["round_id"] / "attempts" / spec["run_id"]
    if path != expected or any((path / x).exists() for x in ("DONE", "FAILED", "INCOMPLETE", "result.json")):
        raise ValueError("attempt path/immutability violation")
    artifacts, logs = path / "artifacts", path / "logs"
    artifacts.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    log_fh = open(logs / "run.log", "w", encoding="utf-8")

    def log(msg):
        line = "[%s] %s" % (dt.datetime.now(dt.timezone.utc).isoformat(), msg)
        print(line, flush=True)
        log_fh.write(line + "\n")
        log_fh.flush()

    state = {"schema_version": 1, "family_id": FAMILY_ID, "round_id": spec["round_id"],
             "run_id": spec["run_id"], "stage": "RUNNING_QLIB"}
    atomic_json(path / "state.json", state)
    progress = {"schema_version": 1, "family_id": FAMILY_ID, "round_id": spec["round_id"],
                "run_id": spec["run_id"], "stage": "RUNNING_QLIB", "case_evaluations": 0,
                "expected_case_evaluations": counts["case_evaluations_total"]}

    def write_progress():
        progress["updated_at_utc"] = now_utc()
        progress["runtime_seconds"] = round(time.monotonic() - started, 3)
        atomic_json(artifacts / "progress.json", progress)

    write_progress()
    try:
        instruments = instrument_metadata()
        daily = {s: load_rows(s, TIMEFRAME, *PHASES["full"], FIELDS) for s in SYMBOLS}
        _, build = build_qlib(daily, *PHASES["full"])
        atomic_json(artifacts / "raw_build.json",
                    {"qlib": build, "daily_files": {s: daily[s]["raw_files"] for s in SYMBOLS}})
        funding = {s: load_funding(s, daily[s]["open_ms"], daily[s]["open_ms"][-1]) for s in SYMBOLS}
        atomic_json(artifacts / "funding_coverage.json", {s: funding[s][1] for s in SYMBOLS})
        rets = simple_returns(daily)
        windows = {k: window_indices(daily[SYMBOLS[0]]["open_ms"], *v)
                   for k, v in PHASES.items()}
        log("building the point-in-time allocation layers (Wasserstein-hyperplane LP solves)")

        # funding carry as a return: official rate sums per UTC day (signal input)
        carry = {s: load_funding_carry(s, daily[s]["open_ms"], daily[s]["open_ms"][-1]) for s in SYMBOLS}
        carry_report = {s: {"days_with_carry": int((carry[s] != 0).sum()),
                            "max_abs_daily_carry": float(np.max(np.abs(carry[s]))),
                            "zero_before_first_official": True} for s in SYMBOLS}
        scen = scenario_returns(daily, carry)

        layers = {}
        layer_reports = {}
        for case in CASES:
            key = (case["wasserstein_epsilon"], case["lookback_n"])
            if key in layers:
                continue
            rep = {}
            layers[key] = build_layer(daily, scen, *key, report=rep)
            layer_reports["eps=%g_n=%d" % key] = rep
        # falsification sweep layers: registered (eps, lookback) reused, extras solved here
        sweep = {}
        for lookback in STRATEGY_AXES["lookback_n"]:
            for eps in (SAA_EPS,) + tuple(SWEEP_EPS):
                key = (eps, lookback)
                if key in layers:
                    sweep[key] = layers[key]
                    continue
                rep = {}
                sweep[key] = build_layer(daily, scen, eps, lookback, report=rep)
                layer_reports["sweep_eps=%g_n=%d" % key] = rep
            write_progress()

        # anchor probes: eps = 0 == SAA epigraph, eps >= eps_bar == maximin closed form
        anchors = {"saa": [], "maximin": []}
        probe_layer = sweep[(SAA_EPS, STRATEGY_AXES["lookback_n"][0])]
        for idx, t in enumerate(probe_layer["epochs"][:2]):
            meta = probe_layer["meta"][idx]
            X = np.vstack([scen[s][t - probe_layer["lookback_n"] + 1:t + 1]
                           for s in SYMBOLS]).T
            lp = solve_wasserstein(X, SAA_EPS)
            saa = solve_saa_epigraph(X)
            anchors["saa"].append({"date": meta["date"], "lookback_n": probe_layer["lookback_n"],
                                   "lp_objective": lp["objective"], "saa_objective": saa["objective"],
                                   "objective_gap": abs(lp["objective"] - saa["objective"])})
        for lookback in STRATEGY_AXES["lookback_n"]:
            layer = sweep[(SAA_EPS, lookback)]
            t = layer["epochs"][0]
            X = np.vstack([scen[s][t - lookback + 1:t + 1] for s in SYMBOLS]).T
            xmin = np.maximum(X.min(axis=0), X_MIN_CLIP)
            big = float(np.sum(X.max(axis=0) - xmin)) * 1.5
            lp = solve_wasserstein(X, big)
            closed, mask = maximin_weights(xmin)
            anchors["maximin"].append({"lookback_n": lookback, "eps_used": big,
                                       "w": [float(v) for v in lp["w"]],
                                       "closed_form": [float(v) for v in closed],
                                       "argmax_assets": [SYMBOLS[i] for i in range(len(SYMBOLS)) if mask[i]],
                                       "mass_on_argmax": float(lp["w"][mask].sum())})
        sig_report = {"layers": layer_reports, "anchors": anchors,
                      "epochs_rule": "t %% %d == 0, lookback <= t <= n-2 (data-independent)" % REBALANCE_EVERY,
                      "direction_rule": SIGNAL_PARAMS["weight_reference"]}
        signal_metrics = {}
        for case in CASES:
            layer = layers[(case["wasserstein_epsilon"], case["lookback_n"])]
            for s in SYMBOLS:
                d = layer["directions"][s]
                si = SYMBOLS.index(s)
                signal_metrics["%s/%s" % (case["label"], s)] = {
                    "wasserstein_epsilon": case["wasserstein_epsilon"],
                    "lookback_n": case["lookback_n"],
                    "direction_counts": {"long": int((d == 1).sum()), "flat": int((d == 0).sum())},
                    "weight_mean": float(np.mean(layer["weights"][layer["defined"], si])),
                    "pit_status": PIT_STATUS,
                    "pit_disclosure": "epoch weights are solved from a scenario window that ends at the epoch day; the epoch grid is data-independent and the decision executes at the next bar's open, so no day trades on its own close",
                    "anchor": 1.0 / len(SYMBOLS)}
        signal_metrics["_layer"] = {k: v for k, v in layer_reports.items()}
        atomic_json(artifacts / "signal_metrics.json", signal_metrics)
        net_rets = rets - np.vstack([carry[s] for s in SYMBOLS])
        ew_report = equal_weight_report(net_rets, {k: v for k, v in windows.items()})
        atomic_json(artifacts / "baseline_equal_weight.json", ew_report)

        # ----------------------------------------------------------------------------------
        grid_rows = {g: [] for g in GRIDS}
        results, survivors = [], []
        hist_total = [0] * LADDER_LEVELS
        for s in SYMBOLS:
            rows = {g: [] for g in GRIDS}
            inst = instruments[s]
            for case in CASES:
                layer = layers[(case["wasserstein_epsilon"], case["lookback_n"])]
                direction = layer["directions"][s]
                for dca in DCA_GRID:
                    for grid in GRIDS:
                        phase = "historical" if grid in ("historical", "no_funding") \
                            else "oos" if grid == "oos" else "full"
                        metric = simulate(daily[s], funding[s][0], direction,
                                          dca, *windows[phase], cost_for(grid), inst)
                        row = {"symbol": s, "timeframe": TIMEFRAME,
                               "wasserstein_epsilon": case["wasserstein_epsilon"],
                               "lookback_n": case["lookback_n"],
                               "case_label": case["label"], **dca, "grid": grid,
                               **metric_block(metric), "decomposition_ok": metric["decomposition_ok"]}
                        rows[grid].append(row)
                        grid_rows[grid].append(row)
                        progress["case_evaluations"] += 1
                        if progress["case_evaluations"] % 500 == 0:
                            write_progress()
                        if grid == "full":
                            for i, val in enumerate(metric["layer_hist"]):
                                hist_total[i] += val
            selected, info = select_cohort(rows)
            record = {"cohort": s + "/" + TIMEFRAME, "symbol": s,
                      "outcome": "SURVIVOR" if selected is not None else "CULLED", **info}
            results.append(record)
            if selected is not None:
                survivors.append(record)
            log("cohort %s outcome=%s cull=%s" % (record["cohort"], record["outcome"],
                                                  ",".join(info["cull_reasons"]) or "none"))
            write_progress()
        expected_keys = {(s, TIMEFRAME, c["wasserstein_epsilon"], c["lookback_n"], *(d[k] for k in DCA_AXES))
                         for s in SYMBOLS for c in CASES for d in DCA_GRID}
        coverage = {g: {cell_key(r) for r in grid_rows[g]} == expected_keys
                    and len(grid_rows[g]) == len(expected_keys) for g in GRIDS}
        base_full = sum(float(r["net_pnl"]) for r in grid_rows["full"])
        stress_delta = {g: sum(float(r["net_pnl"]) for r in grid_rows[g]) - base_full
                        for g in ("fee_2x", "funding_2x", "slippage_2ticks", "cost_attrition_40bps")}
        traded = any(float(r["turnover_usdt"]) > 0 for r in grid_rows["full"])
        official_events = {s: funding[s][1]["official"] for s in SYMBOLS}
        delay_delta = sum(float(r["net_pnl"]) for r in grid_rows["entry_delay_1_bar"]) - base_full

        def cell_delta(a, b, tol=1e-9):
            return any(abs(float(x["net_pnl"]) - float(y["net_pnl"])) > tol
                       for x, y in zip(grid_rows[a], grid_rows[b]))

        cells_with_funding = [i for i, r in enumerate(grid_rows["full"])
                              if abs(float(r["funding"])) > 1e-9]
        falsification = falsification_report(layers, sweep, daily, net_rets, windows,
                                             {s: funding[s][0] for s in SYMBOLS})
        atomic_json(artifacts / "falsification.json", falsification)
        for rec in survivors:
            rec["falsification_battery"] = falsification["battery_status"]
        # portfolio-level diagnostics for the registered cases and benchmarks
        portfolio = {"rf_note": "pipeline rf=0 equity-return convention; the record's 1-month T-bill column belongs to the source-market basket and is not part of the registered local universe",
                     "windows": {}, "cases": {}}
        for gname, (a, b) in windows.items():
            portfolio["windows"].setdefault(gname, {})["ewp"] = {
                "sharpe": sharpe_from_returns(net_rets[:, a:b].mean(axis=0)),
                "cum_return": float(np.prod(1.0 + net_rets[:, a:b].mean(axis=0)) - 1.0)}
        for case in CASES:
            layer = layers[(case["wasserstein_epsilon"], case["lookback_n"])]
            s0 = layer["epochs"][0]
            series, _ = layer_portfolio_returns(layer, net_rets, SWEEP_TIER_COST, start=s0)
            entry = {"first_epoch": stamp(int(daily[SYMBOLS[0]]["open_ms"][s0])),
                     "annualized_sharpe": sharpe_from_returns(series),
                     "cum_return": float(np.prod(1.0 + series) - 1.0),
                     "max_dd_pct": max_drawdown_pct(np.r_[1.0, np.cumprod(1.0 + series)])}
            for gname, (a, b) in windows.items():
                rel = (max(a, s0) - s0, b - s0)
                sl = series[rel[0]:rel[1]]
                entry[gname] = {"sharpe": sharpe_from_returns(sl),
                                "cum_return": float(np.prod(1.0 + sl) - 1.0),
                                "max_dd_pct": max_drawdown_pct(np.r_[1.0, np.cumprod(1.0 + sl)])}
            portfolio["cases"][case["label"]] = entry
        portfolio["note"] = ("portfolio diagnostics use net returns (price return minus official "
                             "funding carry) with the registered ex-post turnover cost")
        atomic_json(artifacts / "portfolio_diagnostics.json", portfolio)
        all_meta = [m for key in layers for m in layers[key]["meta"]]
        atomic_json(artifacts / "optimizer_diagnostics.json", {
            "layer_report": sig_report,
            "lp_solves": {"registered_layers": len(layers), "sweep_layers": len(sweep),
                          "epochs_total": sum(len(v["meta"]) for v in sweep.values())},
            "hyperplanes": {"min_m": min(m["m_hyperplanes"] for m in all_meta),
                            "max_m": max(m["m_hyperplanes"] for m in all_meta),
                            "max_gap": max(m["mesh_gap_max"] for m in all_meta), "eta": ETA,
                            "cap": MAX_HYPERPLANES},
            "box_support": {"clip": X_MIN_CLIP, "eps_bar_rule": "sum_i (x_max_i - x_min_i) per epoch"},
            "cases": [{"label": c["label"], "wasserstein_epsilon": c["wasserstein_epsilon"],
                       "lookback_n": c["lookback_n"],
                       "mean_lam": float(np.mean([m["lam"] for m in
                                                  layers[(c["wasserstein_epsilon"], c["lookback_n"])]["meta"]])),
                       "mean_objective": float(np.mean([m["objective"] for m in
                                                        layers[(c["wasserstein_epsilon"], c["lookback_n"])]["meta"]]))}
                      for c in CASES],
            "constraints": "sum(w)=1, w >= 0, lam >= 0, s^m >= 0 (record long-only box specialization)",
            "solver": "scipy linprog HiGHS (sparse constraints), feasibility-checked"})
        anchor_checks = [a["objective_gap"] for a in anchors["saa"]] + \
                        [abs(a["mass_on_argmax"] - 1.0) for a in anchors["maximin"]]
        assertions = {
            "qlib_readback_0_9_7": build["qlib_version"] == "0.9.7"
            and build["qlib_rows_identical_to_raw"],
            "coverage_complete": all(coverage.values()),
            "case_evaluations_total_exact": sum(map(len, grid_rows.values()))
            == counts["case_evaluations_total"],
            "independent_gross_net_decomposition": all(r["decomposition_ok"]
                                                       for g in GRIDS for r in grid_rows[g]),
            "dca_histogram_reconciles": hist_total[0] == sum(int(r["episodes"])
                                                             for r in grid_rows["full"]),
            "cost_stress_effective": (not traded) or all(stress_delta[g] < 0 for g in
                                                         ("fee_2x", "slippage_2ticks",
                                                          "cost_attrition_40bps")),
            "funding_2x_effective": not cells_with_funding or all(
                abs(float(grid_rows["funding_2x"][i]["net_pnl"])
                    - float(grid_rows["full"][i]["net_pnl"])) > 1e-9
                for i in cells_with_funding),
            "entry_delay_1_bar_effective": (not traded) or cell_delta("entry_delay_1_bar", "full"),
            "official_funding_only_no_modeled_charges": all(
                funding[s][1]["charged_events"] == funding[s][1]["official"]
                and funding[s][1]["missing_intervals_are_zero_not_modeled"] is True
                for s in SYMBOLS),
            "funding_carry_official_only": all(
                int((carry[s] != 0).sum()) <= funding[s][1]["days_with_official"]
                and all(funding[s][0][i] or carry[s][i] == 0.0
                        for i in range(len(carry[s])))
                for s in SYMBOLS),
            "strategy_constants_match_record": (
                STRATEGY_AXES["wasserstein_epsilon"] == (1.0, 1e-2, 1e-4)
                and STRATEGY_AXES["lookback_n"] == (63, 252)
                and [c["label"] for c in CASES] == [("%s_n%d" % (_eps_label(e), n))
                                                    for e, n in itertools.product(
                                                        *STRATEGY_AXES.values())]
                and ETA == 1e-3 and REBALANCE_EVERY == 7
                and SIGNAL_PARAMS["wasserstein_epsilon"] == [1.0, 1e-2, 1e-4]),
            "point_in_time_no_lookahead": (
                all(rep["max_data_index_used"] <= len(daily[SYMBOLS[0]]["close"]) - 2
                    and rep["epochs"] > 0 and rep["last_epoch"] <= len(daily[SYMBOLS[0]]["close"]) - 2
                    for rep in layer_reports.values())
                and all(rep.get("warmup_flat") for rep in layer_reports.values())),
            "optimizer_feasibility": all(
                rep["worst_sum_w_deviation"] <= 1e-6 and rep["worst_neg_weight"] <= 1e-9
                for rep in layer_reports.values()),
            "hyperplane_certificate_uniform": all(
                rep["max_mesh_gap"] <= ETA + 1e-12 for rep in layer_reports.values()),
            "weekly_epochs_registered": all(
                all(t % REBALANCE_EVERY == 0 for t in layers[key]["epochs"]) for key in layers),
            "saa_limit_matches_direct_saa": all(a["objective_gap"] <= 1e-6 for a in anchors["saa"]),
            "large_ambiguity_matches_maximin": all(a["mass_on_argmax"] >= 1.0 - 1e-6
                                                   for a in anchors["maximin"]),
            "falsification_battery_evaluated": all(
                v.get("status") in ("FALSIFIED", "NOT_FALSIFIED", "INDETERMINATE")
                for v in falsification["items"].values())
            and sorted(falsification["items"]) == sorted(FALSIFICATION_REGISTRY),
            "selector_winner_is_historical_row": all(x.get("winner_source_grid") == "historical"
                                                     for x in results if x.get("winner")),
        }
        assert max(anchor_checks) < 10.0  # sanity: anchor probes produced finite numbers
        atomic_json(artifacts / "stress_effects.json", {
            "baseline_grid": "full", "net_pnl_delta": {**stress_delta,
                                                       "entry_delay_1_bar": delay_delta},
            "any_fill_in_full_grid": traded,
            "noop_reason": None if traded else "no fills in full grid"})
        atomic_json(artifacts / "cohort_results.json", results)
        atomic_json(artifacts / "cohort_survivors.json", survivors)
        atomic_json(artifacts / "dca_layer_histogram.json",
                    {"level_%02d" % i: v for i, v in enumerate(hist_total)})
        for g in GRIDS:
            write_grid(artifacts / ("grid_%s.csv" % g), grid_rows[g])
        atomic_json(artifacts / "assertions.json", assertions)
        hits = falsification.get("falsification_hits", [])
        verdict = "REJECT" if not survivors else "PASS"
        result = {"schema_version": 1, "family_id": FAMILY_ID, "round_id": spec["round_id"],
                  "run_id": spec["run_id"], "engine": ENGINE_VERSION,
                  "script_sha256": sha256_file(__file__), "qlib_version": build["qlib_version"],
                  "status": "ARTIFACT_READY", "coverage_complete": all(coverage.values()),
                  "coverage_by_grid": coverage,
                  "assertions_all_true": all(v for v in assertions.values()),
                  "assertion_failures": [k for k, v in assertions.items() if not v],
                  "assertions": assertions,
                  "case_evaluations_total": sum(map(len, grid_rows.values())),
                  "expected_case_evaluations": counts["case_evaluations_total"],
                  "cohort_count": len(results), "cohort_survivor_count": len(survivors),
                  "cohort_survivors": [x["cohort"] for x in survivors],
                  "official_funding_events": official_events,
                  "disposition": "REJECT / NO_SURVIVOR" if not survivors
                  else ("SURVIVOR_FOUND" if len(survivors) == 1 else "MULTIPLE_SURVIVORS"),
                  "falsification_hits": hits,
                  "falsification_indeterminate": falsification.get("indeterminate_items", []),
                  "falsification_advice": {
                      "rule": "record falsification battery hit => cull or family REJECT (body "
                              "FAILURE TAXONOMY); verdict_recommendation is the v1.4.0 "
                              "disposition band only and must not be read as science advice",
                      "battery_status": falsification.get("battery_status"),
                      "advice": "REJECT" if hits else None},
                  "verdict_recommendation": verdict,
                  "verdict_recommendation_basis": "disposition band v1.4.0 (0 survivors = "
                                                  "REJECT, >=1 = PASS); the final verdict is "
                                                  "written to verdict.json by default (10.7)",
                  "pit_status": PIT_STATUS,
                  "claimability_evidence": {
                      "basis": "performance_claimable is decided in verdict.json under contract "
                               "9.6, not recommended here",
                      "missing_conditions": [],
                      "pit_note": "epoch weights are solved from windows ending at the epoch day "
                                  "on a data-independent grid and executed at the next bar's open; "
                                  "the construction assertion and the self-check perturbation test "
                                  "both gate this"},
                  "selector_version": "cohort-selector-v1", "disposition_version": "cohort-disposition-v1",
                  "disposition_mapping_version": "v1.4.0", "grid_kinds": GRIDS,
                  "funding_coverage": {s: funding[s][1] for s in SYMBOLS},
                  "funding_carry_coverage": carry_report,
                  "stress_net_delta": stress_delta,
                  "signal_layer_report": {k: v for k, v in layer_reports.items()},
                  "falsification_summary": {k: v.get("status")
                                            for k, v in falsification["items"].items()},
                  "runtime_seconds": time.monotonic() - started}
        if result["case_evaluations_total"] != counts["case_evaluations_total"]:
            result["assertions_all_true"] = False
            result["assertion_failures"].append("case_evaluations_total")
        if not result["assertions_all_true"]:
            result["verdict_recommendation"] = "TECHNICAL_INCOMPLETE"
        atomic_json(path / "result.json", result)
        state["stage"] = "ARTIFACT_READY"
        atomic_json(path / "state.json", state)
        progress["stage"] = "ARTIFACT_READY"
        write_progress()
        log("ARTIFACT_READY cohorts=%d survivors=%d rows=%d falsification=%s assertions=%s"
            % (len(results), len(survivors), result["case_evaluations_total"],
               ",".join(hits) or "none",
               "all_true" if result["assertions_all_true"] else str(result["assertion_failures"])))
        return result
    except Exception as exc:
        state.update(stage="FAILED_SCRIPT", error="%s: %s" % (type(exc).__name__, exc))
        atomic_json(path / "state.json", state)
        progress["stage"] = "FAILED_SCRIPT"
        write_progress()
        log("FAILED %s: %s" % (type(exc).__name__, exc))
        raise
    finally:
        log_fh.close()


def load_funding_carry(symbol, open_ms, end_ms, root=RAW_ROOT):
    """Daily sum of official funding RATES (a return) per bar; missing days = zero.

    Independent of the cost-side loader: this is the signal-side carry the record's
    crypto portability section requires inside the net expected return vector.
    """
    path = Path(root) / "binance/usdm/funding" / symbol / ("%s-funding.jsonl.gz" % symbol)
    carry = np.zeros(len(open_ms), dtype=np.float64)
    if not path.is_file():
        return carry
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("truth_status") != "official":
                continue
            t = int(row["funding_time_ms"])
            if not int(open_ms[0]) <= t < int(end_ms) + MS_DAY:
                continue
            rate = float(row["funding_rate"])
            if not math.isfinite(rate):
                raise RuntimeError("non-finite official funding rate")
            bar = int(np.searchsorted(open_ms, t, side="right") - 1)
            if not 0 <= bar < len(carry):
                raise RuntimeError("funding outside panel")
            carry[bar] += rate
    return carry


def emit_template(kind):
    if kind == "run-spec":
        doc = run_spec_template(created_at=now_utc())
        doc["round_id"] = FAMILY_ID + "-r1"
        doc["run_id"] = doc["round_id"] + "-u1"
    elif kind == "round-spec":
        doc = round_spec_template(source_record_sha256=os.environ.get("SOURCE_RECORD_SHA256"))
    else:
        raise SystemExit("unknown template kind: %s" % kind)
    print(json.dumps(doc, indent=2, ensure_ascii=False, allow_nan=False))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-spec")
    parser.add_argument("--attempt-dir")
    parser.add_argument("--emit-template", choices=("run-spec", "round-spec"))
    args = parser.parse_args(argv)
    if args.emit_template:
        return emit_template(args.emit_template)
    if not args.run_spec:
        raise SystemExit("--run-spec is required (or --emit-template)")
    with open(args.run_spec, encoding="utf-8") as fh:
        spec = json.load(fh)
    result = run(spec, args.attempt_dir or str(Path(args.run_spec).parent))
    print(json.dumps({"status": result["status"],
                      "case_evaluations_total": result["case_evaluations_total"],
                      "assertions_all_true": result["assertions_all_true"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
