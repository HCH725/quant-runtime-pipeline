#!/usr/bin/env python3
"""Author the frozen same-day open/close round/run spec templates exactly once."""
import hashlib
import importlib.util, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import parameter_contract as pc

FAMILY_ID = "same-day-open-to-close-directional-spy-walk-forward-2026-09-02"
BODY = Path("/Users/hong/workspace/quant-runtime-pipeline/evidence/_spy_candidate_body.md")
RECORD = Path("/Users/hong/.hermes/wiki/quant/same-day-open-to-close-directional-spy-walk-forward-2026-09-02.md")
ROUND = HERE / "templates" / "spy_open_close_round_spec.template.json"
RUN = HERE / "templates" / "spy_open_close_run_spec.template.json"

body_text = BODY.read_text()
record_text = RECORD.read_text()
fam = json.loads(Path("/Volumes/ExpansionDrive/qlib-results/same-day-open-to-close-directional-spy-walk-forward-2026-09-02/family.json").read_text())

def excerpt(text, anchor, end):
    a = text.index(anchor)
    b = text.index(end, a + len(anchor))
    return text[a:b].rstrip()

# Exact frozen-body sections; source record supplies the full untruncated list.
required_data = excerpt(record_text, "- **Instrument:**", "## Execution assumptions")
portability = excerpt(record_text, "**Adapted / Unproven.**", "## Limitations")
falsification = excerpt(record_text, "1. **Out-of-Sample Horizon Test", "## Crypto portability")
signal_block = excerpt(record_text, "### Formation timestamp", "## Required data")
limitations = excerpt(record_text, "- **Single-Instrument Primary Focus:**", "## Implementation status")
assert len(required_data.splitlines()) == 7, required_data
assert len(falsification.splitlines()) == 4, falsification
for text in (body_text, record_text):
    for needle in (required_data.splitlines()[0], signal_block.splitlines()[0], limitations.splitlines()[0]):
        assert needle in text, (needle, text[:100])

sha = lambda p: "sha256:" + hashlib.sha256(Path(p).read_bytes()).hexdigest()
RUNNER = "/Users/hong/workspace/quant-runtime-pipeline/container/scripts/260_spy_open_close_run.py"
TEST = "/Users/hong/workspace/quant-runtime-pipeline/container/scripts/tests/test_spy_open_close_engine.py"

axes = [
    {"name": "model_threshold_case", "kind": "composite", "members": ["case_code", "tau_pct"],
     "registered_values": [[0, 0.0], [1, 0.5], [2, 1.0], [3, 1.5], [4, 2.0],
                            [5, 2.5], [6, 3.0], [7, 3.5], [8, 0.0]],
     "row_fields": ["case_code", "tau_pct"]},
    {"name": "spacing_pct", "kind": "atomic", "members": ["spacing_pct"],
     "registered_values": [0.01, 0.02, 0.03, 0.04], "row_fields": ["spacing_pct"]},
    {"name": "size_multiplier", "kind": "atomic", "members": ["size_multiplier"],
     "registered_values": [1.0, 1.1], "row_fields": ["size_multiplier"]},
    {"name": "breakeven_tp_pct", "kind": "atomic", "members": ["breakeven_tp_pct"],
     "registered_values": [0.01, 0.02, 0.03], "row_fields": ["breakeven_tp_pct"]},
    {"name": "invalidation_pct", "kind": "atomic", "members": ["invalidation_pct"],
     "registered_values": [0.05, 0.10], "row_fields": ["invalidation_pct"]},
]
row_fields = ["case_code", "tau_pct", "spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct"]
contract = {"parameter_contract_version": 1, "family_id": FAMILY_ID,
    "contract_ref": "same-day open-to-close direct-family frozen round-spec parameter contract",
    "research_axes_ordered": axes, "row_fields": row_fields, "composite_map": {"model_threshold_case": ["case_code", "tau_pct"]},
    "strategy_param_fields": ["case_code", "tau_pct"],
    "dca_param_fields": ["spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct"],
    "canonical_recipe": {"sort_keys": True, "separators": [",", ":"], "ensure_ascii": False,
                         "numeric_rule": "JSON finite number; bool excluded"},
    "row_match_recipe": {"keys": ["symbol", "timeframe", *row_fields], "equality": "exact numeric / bytewise labels"},
    "non_params": ["symbol", "timeframe", "case_label", "model_kind", "grid", "metrics", "diagnostics", "timestamps"],
    "domain_cardinality": {"strategy": 9, "dca": 48, "per_cohort": 432}}

cases = []
tau = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5]   # source grid in PERCENT
for i in range(9):
    cases.append({"case_code": i, "model_kind": "regression" if i < 8 else "logistic",
                  "tau_pct": tau[i] if i < 8 else 0.0,
                  "label": ("rf_tau_" if i < 8 else "logistic_") +
                  (format(tau[i], ".1f").replace(".", "p") if i < 8 else "direct")})

# Frozen-domain self-checks (pure python; host has no sklearn). Drift against the
# engine itself is caught by container/scripts/tests/test_spy_open_close_engine.py,
# which runs validate_spec() on these exact template bytes inside qlib-run.
assert tau == [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5], "source tau grid must stay in percent"
assert len(cases) == 9 and cases[-1] == {"case_code": 8, "model_kind": "logistic",
                                         "tau_pct": 0.0, "label": "logistic_direct"}
assert [c["tau_pct"] for c in cases[:8]] == tau
assert [c["label"] for c in cases[:8]] == ["rf_tau_%s" % format(t, ".1f").replace(".", "p") for t in tau]
assert row_fields == ["case_code", "tau_pct", "spacing_pct", "size_multiplier",
                      "breakeven_tp_pct", "invalidation_pct"]
assert [a["name"] for a in axes] == ["model_threshold_case", "spacing_pct", "size_multiplier",
                                     "breakeven_tp_pct", "invalidation_pct"]

round_spec = {
 "schema_version": 1, "document_kind": "round_spec",
 "contract": "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md v2.0.0",
 "family_id": FAMILY_ID, "round_id": "{{round_id}}",
 "semantic_fingerprint": fam["semantic_fingerprint"], "status": "registered",
 "provenance": {"source_record": "quant/same-day-open-to-close-directional-spy-walk-forward-2026-09-02.md",
   "source_url": "https://arxiv.org/abs/2608.26106", "source_as_of": "2026-09-02",
   "source_status": "research-only / not-implemented / not-approved (intake PASS-WITH-CAVEAT)",
   "family_handoff_body_sha256": fam["handoff"]["body_sha256"],
   "implementation_boundary": "local adapted full backtest on the complete canonical USD-M daily universe; no SPY or equity-screen result is claimed"},
 "universe": {"source_universe": "SPY on NYSE Arca plus a 541-stock U.S. equity cross-section",
   "symbols": ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"], "signal_timeframe": "1d",
   "execution_timeframe": "5m", "portability": "adapted/unproven (source-record crypto portability canonicalized)",
   "selection": "complete local eligible universe: every canonical USD-M symbol with the registered 1d signal and 5m execution bars; no post-hoc shrink"},
 "data": {"venue": "Binance USD-M perpetual canonical raw archive", "raw_root": "/data/raw/binance/usdm",
   "start": "2022-01-01", "end": "2026-09-11", "timezone": "UTC",
   "fields": ["open", "high", "low", "close", "volume"],
   "signal_timeframe": {"raw_interval": "1d", "qlib_freq": "day"},
   "execution_timeframe": {"raw_interval": "5m", "qlib_freq": "5min", "bars_per_day": 288},
   "point_in_time": "expanding prefix fit strictly before day t; day-t open observed, no day-t high/low/close in features",
   "missing_data": "fail closed on any registered-window gap; no fill or resample",
   "measured_raw_scan": "registered window 2022-01-01..2026-09-11: 1d=1715 rows and 5m=493920 rows = 1715 x 288 per symbol, zero gaps/unsorted bars, zero non-finite or non-positive price fields",
   "volume_edge_case": "research-defined data reality: 20 zero-volume 5m bars per symbol on 2022-05-01 UTC are legitimate no-trade intervals with valid prices and are kept; only price fields are required strictly positive"},
 "signal_registration": {"required_data_verbatim": required_data,
   "signal_verbatim": signal_block,
   "mechanism": "opening price plus two lagged open/close pairs forecast same-day close; regression direction and direct logistic classification are registered cases",
   "features": ["O(t-2)", "C(t-2)", "O(t-1)", "C(t-1)", "O(t)"],
   "target": "close(t); regression direction is predicted close(t) >= close(t-1); logistic directly predicts that binary outcome",
   "pipeline": "expanding walk-forward, fit only through t-1, refit every 5 days (research-defined compute schedule), source-reported Random Forest for regression and source-reported Logistic Regression for classification; XGBoost is not installed in the registered image, so the source's explicitly authorized Random Forest branch is used rather than silently substituting another booster",
   "formation": "after each UTC daily open", "holding": "flatten in the same UTC session; no overnight inventory",
   "threshold_conditioning": "regression trade iff predicted absolute percentage move >= tau; source grid [0,0.5,1,1.5,2,2.5,3,3.5]%",
   "causality": "no feature or fit uses day-t high/low/close; OOS is read-only"},
 "parameter_domain": {"strategy_axes": ["case_code", "tau_pct"], "strategy_cases": cases,
   "legal_cases_per_cohort": 9,
   "selection_rule": "cohort-selector-v1 historical-only; frozen winner travels unchanged to OOS/full/robustness/neighborhood"},
 "dca_domain": {
   "spacing_pct": [0.01, 0.02, 0.03, 0.04], "size_multiplier": [1.0, 1.1],
   "breakeven_tp_pct": [0.01, 0.02, 0.03], "invalidation_pct": [0.05, 0.10],
   "config_count": 48, "base_quote": 1000.0,
   "grid": [dict(zip(["spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct"], v))
            for v in __import__("itertools").product([0.01, 0.02, 0.03, 0.04], [1.0, 1.1],
                                                      [0.01, 0.02, 0.03], [0.05, 0.10])],
   "max_active_layers": 11, "leverage": 10.0,
   "provenance_class": {"spacing_pct": "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN",
     "size_multiplier": "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN",
     "breakeven_tp_pct": "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN",
     "invalidation_pct": "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN",
     "base_quote": "PROJECT_PRE_REGISTERED_CONSTANT"},
   "user_fixed_invariants": ["starting_equity = 30000 USDT", "USDT sole numeraire", "linear USD-M perpetual",
     "leverage 10x", "12 tranches", "tranche #12 reserve/buffer, routine active levels max 11",
     "initial entry plus adverse-price scale-ins", "reduce-only exit",
     "same-bar multi-level crossing deterministic conservative ordering", "no add after FLAT/kill"]},
 "robustness_plan": {"grids": ["historical","oos","full","fee_2x","funding_2x","entry_delay_1_bar","slippage_2ticks","no_funding","no_funding_full","cost_attrition_40bps"],
   "required_stress_grids": ["fee_2x","funding_2x","entry_delay_1_bar","slippage_2ticks"],
   "falsification_verbatim": falsification,
   "execution_delay_mapping": "research-defined: registered 5m bars approximate 1/5/15/30-minute source delays; exact SPY minute prints are absent and the reader stays INDETERMINATE",
   "baseline": "all nine registered cases evaluated; no post-hoc case removal"},
 "registered_family_level_falsification": {
   "note": "The record's four items are preserved verbatim. Three source-specific subtests are disclosed INDETERMINATE rather than silently converted into PASS.",
   "items": [
     "OOS horizon: local adapted OOS accuracy vs 52.5%; market/window differs from source SPY 2024-03-16..2026",
     "50-stock median-AUC basket: INDETERMINATE, equity cross-section absent",
     "exact 1/5/15/30-minute delay: INDETERMINATE for exact timing, local 5m delay accuracy measured separately",
     "shuffled-open: measured by a seeded permutation of day-t open within the lagged feature"]},
 "gates": {"min_episodes_is": 10, "min_episodes_oos": 3, "min_neighbour_same_sign_fraction": 0.6,
   "oos_positive_net_pnl": True, "stress_positive_net_pnl": True},
 "split": {"historical_start": "2022-01-01", "historical_end": "2025-09-30",
   "oos_start": "2025-10-01", "oos_end": "2026-09-11",
   "rule": "chronological; frozen before compute; OOS never selects parameters"},
 "costs": {"taker_fee": "canonical instrument taker_fee", "slippage_ticks": 1,
   "slippage_robustness_ticks": 2, "slippage_basis": "instrument price_increment, adverse on every fill",
   "funding": "official observations only at actual timestamp and mark price; missing intervals zero and disclosed; modeled rows never charged"},
 "expected": {"cohorts": 4, "strategy_cases_per_cohort": 9, "dca_configs_per_cohort": 48,
   "base_combinations_per_cohort": 432, "case_evaluations_per_grid": 1728, "grid_count": 10,
   "case_evaluations_total": 17280},
 "parameter_contract": contract,
 "limitations_verbatim": limitations,
 "research_defined": [
   "UTC 00:00-24:00 crypto session for the source's 09:30-16:00 cash session",
   "5m execution bars and 1-bar delay rail in place of source exact minute prints",
   "model refit cadence every 5 days to bound compute while every fit remains prefix-only",
   "local long/flat rail; shorting is not registered by the source illustration"],
 "falsification": [
   "OOS directional accuracy below 52.5% fails the local adapted horizon test",
   "equity cross-section test is INDETERMINATE until a point-in-time equity universe exists",
   "source exact minute-delay test is INDETERMINATE; local 5m delay accuracy is measured",
   "permuted-open directional accuracy statistically equivalent to true-open accuracy falsifies causal gap structure"]}

run_spec = {
 "schema_version": 1, "document_kind": "run_spec",
 "contract": "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md v2.0.0",
 "family_id": FAMILY_ID, "round_id": "{{round_id}}", "run_id": "{{run_id}}",
 "semantic_fingerprint": fam["semantic_fingerprint"], "status": "registered",
 "created_at_utc": "{{created_at_utc}}", "container_id": "qlib-run", "image_id": "qlib:0.9.7-arm64",
 "script": {"path": "/scripts/260_spy_open_close_run.py", "sha256": "{{script_sha256}}",
   "deployed_host_path": "/Users/hong/workspace/qlib-apple-container/scripts/260_spy_open_close_run.py"},
 "engine": {"name": "spy_open_close_qlib_v1", "script": "container/scripts/260_spy_open_close_run.py",
   "container": "qlib-run", "qlib_version": "0.9.7", "seed": 260928,
   "self_check": "container/scripts/tests/test_spy_open_close_engine.py",
   "self_check_sha256": "{{engine_selfcheck_sha256}}"},
 "data": {"raw_root": "/data/raw/binance/usdm", "start": "2022-01-01", "end": "2026-09-11",
   "timezone": "UTC", "symbols": ["BNBUSDT","BTCUSDT","ETHUSDT","SOLUSDT"],
   "fields": ["open","high","low","close","volume"],
   "signal_timeframe": {"raw_interval": "1d", "qlib_freq": "day"},
   "execution_timeframe": {"raw_interval": "5m", "qlib_freq": "5min", "bars_per_day": 288}},
 "split": {"historical_start": "2022-01-01", "historical_end": "2025-09-30",
   "oos_start": "2025-10-01", "oos_end": "2026-09-11",
   "rule": "chronological; frozen model fit prefix-only; OOS is read-only"},
 "params": cases,
 "dca_domain": {"spacing_pct": [0.01,0.02,0.03,0.04], "size_multiplier": [1.0,1.1],
   "breakeven_tp_pct": [0.01,0.02,0.03], "invalidation_pct": [0.05,0.10],
   "config_count": 48, "base_quote": 1000.0,
   "grid": [dict(zip(["spacing_pct","size_multiplier","breakeven_tp_pct","invalidation_pct"], v))
            for v in __import__("itertools").product([0.01,0.02,0.03,0.04],[1.0,1.1],
                                                      [0.01,0.02,0.03],[0.05,0.10])],
   "max_active_layers": 11, "leverage": 10.0,
   "base_quote_status": "PROJECT_PRE_REGISTERED_CONSTANT",
   **{k + "_status": "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN"
      for k in ("spacing_pct","size_multiplier","breakeven_tp_pct","invalidation_pct")}},
 "grids": round_spec["robustness_plan"]["grids"], "selector_version": "cohort-selector-v1",
 "disposition_version": "cohort-disposition-v1", "expected": round_spec["expected"],
 "parameter_contract": contract,
 "signal_constants": {"train_warmup": 252, "refit_every": 5, "seed": 260928},
 "gates": round_spec["gates"], "costs": round_spec["costs"],
 "falsification": round_spec["falsification"],
 "expected_outputs": ["state.json","result.json","logs/run.log","artifacts/progress.json","artifacts/raw_build.json",
   "artifacts/funding_coverage.json","artifacts/assertions.json","artifacts/stress_effects.json","artifacts/signal_metrics.json",
   "artifacts/falsification.json","artifacts/dca_layer_histogram.json","artifacts/cohort_results.json",
   "artifacts/cohort_survivors.json",
   *["artifacts/grid_%s.csv" % g for g in round_spec["robustness_plan"]["grids"]]],
 "notes": "direct family: no task_id, kanban_task_id or kanban_board keys; ownership is family_id+round_id+run_id."}

for label, obj in (("round", round_spec), ("run", run_spec)):
    problems = pc.validate_contract(obj.get("parameter_contract"))
    assert not problems, (label, problems)
assert pc.validate_round_spec_contract(round_spec) == []
for key in ("required_data_verbatim", "signal_verbatim", "limitations_verbatim"):
    assert round_spec["signal_registration"].get(key) is not None or key == "limitations_verbatim"
assert len(round_spec["signal_registration"]["required_data_verbatim"].splitlines()) == 7
assert len(round_spec["robustness_plan"]["falsification_verbatim"].splitlines()) == 4
assert len(round_spec["limitations_verbatim"].splitlines()) == 5
assert len(round_spec["dca_domain"]["grid"]) == 48
assert len(run_spec["dca_domain"]["grid"]) == 48

ROUND.write_text(json.dumps(round_spec, indent=2, ensure_ascii=False) + "\n")
RUN.write_text(json.dumps(run_spec, indent=2, ensure_ascii=False) + "\n")
print(json.dumps({"ok": True, "round": str(ROUND), "run": str(RUN),
  "round_sha256": sha(ROUND), "run_sha256": sha(RUN),
  "runner_sha256": sha(RUNNER), "test_sha256": sha(TEST),
  "required_items": len(round_spec["signal_registration"]["required_data_verbatim"].splitlines()),
  "falsification_items": len(round_spec["robustness_plan"]["falsification_verbatim"].splitlines())}, indent=2))
