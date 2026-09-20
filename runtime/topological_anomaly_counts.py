#!/usr/bin/env python3
"""Registered counts for the cross-sectional topological anomaly family.

The module is deliberately boring: it is the executable ledger used by preflight, the
runner, and the host-side verdict re-derivation.  Changing an axis here without changing
the frozen round-spec is a contract violation.
"""
import itertools

FAMILY_ID = "cross-sectional-topological-anomaly-score-intraday-equity-return-predictability-2026-09-02"
TASK_ID = "t_0a6aba33"
BOARD = "quant-strategy-research"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")
TIMEFRAMES = (
    {"raw_interval": "5m", "qlib_freq": "5min", "bars_per_day": 288},
    {"raw_interval": "15m", "qlib_freq": "15min", "bars_per_day": 96},
    {"raw_interval": "30m", "qlib_freq": "30min", "bars_per_day": 48},
    {"raw_interval": "1h", "qlib_freq": "60min", "bars_per_day": 24},
)
STRATEGY_CASES = (
    {"method_code": 0, "method": "ballmapper", "label": "ballmapper"},
    {"method_code": 1, "method": "decoder_conditional_vae", "label": "decoder_conditional_vae"},
    {"method_code": 2, "method": "function_on_function", "label": "function_on_function"},
)
DCA_AXES = {
    "spacing_pct": (0.01, 0.02, 0.03, 0.04),
    "size_multiplier": (1.0, 1.1),
    "breakeven_tp_pct": (0.01, 0.02, 0.03),
    "invalidation_pct": (0.05, 0.10),
}
GRID_KINDS = ("historical", "oos", "full", "fee_2x", "funding_2x",
              "entry_delay_1_bar", "slippage_2ticks", "no_funding",
              "no_funding_full", "cost_attrition_40bps")
FULL_WINDOW_GRIDS = ("full", "fee_2x", "funding_2x", "entry_delay_1_bar",
                     "slippage_2ticks", "no_funding_full", "cost_attrition_40bps")
DATA_START = "2022-01-01"
DATA_END = "2026-09-11"
HISTORICAL_START = "2022-01-01"
HISTORICAL_END = "2025-09-30"
OOS_START = "2025-10-01"
OOS_END = "2026-09-11"
WARMUP_BARS_FACTOR = 5
HORIZON_BARS_FACTOR = 2
MIN_EPISODES_IS = 30
MIN_EPISODES_OOS = 10
MIN_NEIGHBOR_SAME_SIGN = 0.60
START_EQUITY = 30000.0
BASE_QUOTE = 1000.0
LEVERAGE = 10.0
MAX_LAYERS = 11
TAKER_FEE = 0.0005
SLIPPAGE_TICKS = 1
PRICE_TICK = {"BTCUSDT": 0.10, "ETHUSDT": 0.01, "SOLUSDT": 0.001, "BNBUSDT": 0.01}


def strategy_cases():
    return list(STRATEGY_CASES)


def dca_cases():
    keys = tuple(DCA_AXES)
    return [dict(zip(keys, values)) for values in itertools.product(*(DCA_AXES[k] for k in keys))]


def cohorts():
    # Frequency-major order matches the runner's panel build and keeps every artifact's cohort
    # order stable without an additional sort pass.
    return [{"symbol": symbol, "timeframe": tf["raw_interval"], "qlib_freq": tf["qlib_freq"],
             "bars_per_day": tf["bars_per_day"]}
            for tf in TIMEFRAMES for symbol in SYMBOLS]


def expected_counts():
    cohort_count = len(cohorts())
    strategy_count = len(STRATEGY_CASES)
    dca_count = len(dca_cases())
    per_grid = cohort_count * strategy_count * dca_count
    return {"cohorts": cohort_count, "strategy_cases_per_cohort": strategy_count,
            "dca_configs_per_cohort": dca_count, "cases_per_cohort": strategy_count * dca_count,
            "case_evaluations_per_grid": per_grid,
            "grid_count": len(GRID_KINDS), "case_evaluations_total": per_grid * len(GRID_KINDS)}


def parameter_contract():
    axes = [
        {"name": "method_code", "kind": "atomic", "members": ["method_code"],
         "registered_values": [0, 1, 2], "row_fields": ["method_code"]},
        {"name": "spacing_pct", "kind": "atomic", "members": ["spacing_pct"],
         "registered_values": list(DCA_AXES["spacing_pct"]), "row_fields": ["spacing_pct"]},
        {"name": "size_multiplier", "kind": "atomic", "members": ["size_multiplier"],
         "registered_values": list(DCA_AXES["size_multiplier"]), "row_fields": ["size_multiplier"]},
        {"name": "breakeven_tp_pct", "kind": "atomic", "members": ["breakeven_tp_pct"],
         "registered_values": list(DCA_AXES["breakeven_tp_pct"]), "row_fields": ["breakeven_tp_pct"]},
        {"name": "invalidation_pct", "kind": "atomic", "members": ["invalidation_pct"],
         "registered_values": list(DCA_AXES["invalidation_pct"]), "row_fields": ["invalidation_pct"]},
    ]
    row_fields = ["method_code"] + list(DCA_AXES)
    return {
        "parameter_contract_version": 1, "family_id": FAMILY_ID,
        "contract_ref": "topological-anomaly-v3 frozen round-spec parameter contract",
        "research_axes_ordered": axes, "row_fields": row_fields, "composite_map": {},
        "strategy_param_fields": ["method_code"], "dca_param_fields": list(DCA_AXES),
        "canonical_recipe": {"sort_keys": True, "separators": [",", ":"],
                              "ensure_ascii": False, "numeric_rule": "JSON finite number; bool excluded"},
        "row_match_recipe": {"keys": ["symbol", "timeframe"] + row_fields,
                              "equality": "exact, numeric == float compare; labels bytewise"},
        "non_params": ["symbol", "timeframe", "grid", "metrics", "diagnostics", "timestamps"],
        "domain_cardinality": {"strategy": len(STRATEGY_CASES), "dca": len(dca_cases()),
                               "per_cohort": len(STRATEGY_CASES) * len(dca_cases())},
    }


if __name__ == "__main__":
    import json
    print(json.dumps(expected_counts(), indent=2))
