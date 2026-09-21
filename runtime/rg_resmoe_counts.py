#!/usr/bin/env python3
"""Frozen RG-ResMoE registered-domain counts and parameter contract."""
import hashlib
import itertools

FAMILY_ID = "cross-sectional-volatility-regime-gated-residual-mixture-of-experts-2026-09-02"
TASK_ID = "t_0fba9abc"
BOARD = "quant-strategy-research"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")
TIMEFRAMES = (
    {"raw_interval": "5m", "qlib_freq": "5min", "bars_per_day": 288},
    {"raw_interval": "15m", "qlib_freq": "15min", "bars_per_day": 96},
    {"raw_interval": "30m", "qlib_freq": "30min", "bars_per_day": 48},
    {"raw_interval": "1h", "qlib_freq": "60min", "bars_per_day": 24},
    {"raw_interval": "4h", "qlib_freq": "240min", "bars_per_day": 6},
    {"raw_interval": "1d", "qlib_freq": "day", "bars_per_day": 1},
    {"raw_interval": "1w", "qlib_freq": "week", "bars_per_day": 1},
)
STRATEGY_CASES = ({"model_code": 0, "model": "rg_resmoe", "label": "rg_resmoe"},)
DCA_AXES = {"spacing_pct": (0.01, 0.02, 0.03, 0.04),
            "size_multiplier": (1.0, 1.1),
            "breakeven_tp_pct": (0.01, 0.02, 0.03),
            "invalidation_pct": (0.05, 0.10)}
GRID_KINDS = ("historical", "oos", "full", "fee_2x", "funding_2x", "entry_delay_1_bar",
              "slippage_2ticks", "no_funding", "no_funding_full", "cost_attrition_40bps")
DATA = {"start": "2022-01-01", "end": "2026-09-11", "historical_start": "2022-01-01",
        "historical_end": "2025-09-30", "oos_start": "2025-10-01", "oos_end": "2026-09-11"}
START_EQUITY = 30000.0
BASE_QUOTE = 1000.0
LEVERAGE = 10.0
MAX_LAYERS = 11
TAKER_FEE = 0.0005
SLIPPAGE_TICKS = 1
PRICE_TICK = {"BTCUSDT": 0.10, "ETHUSDT": 0.01, "SOLUSDT": 0.001, "BNBUSDT": 0.01}


def dca_cases():
    keys = tuple(DCA_AXES)
    return [dict(zip(keys, values)) for values in itertools.product(*(DCA_AXES[k] for k in keys))]


def cohorts():
    return [{"symbol": s, "timeframe": t["raw_interval"], "qlib_freq": t["qlib_freq"],
             "bars_per_day": t["bars_per_day"]} for t in TIMEFRAMES for s in SYMBOLS]


def expected_counts():
    c, s, d, g = len(cohorts()), len(STRATEGY_CASES), len(dca_cases()), len(GRID_KINDS)
    return {"cohorts": c, "strategy_cases_per_cohort": s, "dca_configs_per_cohort": d,
            "cases_per_cohort": s * d, "case_evaluations_per_grid": c * s * d,
            "grid_count": g, "case_evaluations_total": c * s * d * g}


def fingerprint(text):
    return "sha256:" + hashlib.sha256(str(text).encode()).hexdigest()


def parameter_contract():
    axes = [{"name": "model_code", "kind": "atomic", "members": ["model_code"],
             "registered_values": [0], "row_fields": ["model_code"]}]
    axes += [{"name": k, "kind": "atomic", "members": [k], "registered_values": list(v), "row_fields": [k]}
             for k, v in DCA_AXES.items()]
    row_fields = ["model_code"] + list(DCA_AXES)
    return {"parameter_contract_version": 1, "family_id": FAMILY_ID,
            "contract_ref": "rg-resmoe-v1 frozen round-spec parameter contract",
            "research_axes_ordered": axes, "row_fields": row_fields, "composite_map": {},
            "strategy_param_fields": ["model_code"], "dca_param_fields": list(DCA_AXES),
            "canonical_recipe": {"sort_keys": True, "separators": [",", ":"], "ensure_ascii": False,
                                 "numeric_rule": "JSON finite number; bool excluded"},
            "row_match_recipe": {"keys": ["symbol", "timeframe"] + row_fields,
                                 "equality": "exact, numeric == float compare; labels bytewise"},
            "non_params": ["symbol", "timeframe", "grid", "metrics", "diagnostics", "timestamps"],
            "domain_cardinality": {"strategy": len(STRATEGY_CASES), "dca": len(dca_cases()),
                                   "per_cohort": len(STRATEGY_CASES) * len(dca_cases())}}


if __name__ == "__main__":
    import json
    print(json.dumps({"family_id": FAMILY_ID, **expected_counts()}, indent=2))
