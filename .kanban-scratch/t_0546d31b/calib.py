#!/usr/bin/env python3
"""Calibration: universes of A/C/D/E and measured runtimes."""
import glob, json, os, re

RES = "/Volumes/ExpansionDrive/qlib-results"
for fam in ("close-vs-sma-mean-reversion-long-flat-v2", "funding-decile-extreme-contrarian-btc-v1",
            "utc-clock-hour-seasonality-perp-panel-v1", "copula-cmi-pairs-relative-value-perp-v1"):
    for rs in sorted(glob.glob(f"{RES}/{fam}/rounds/*/round-spec.json")):
        d = json.load(open(rs))
        u = d.get("eligible_universe", {})
        keep = {k: v for k, v in u.items()
                if k in ("symbols", "timeframes", "cohort_count", "cohort_definition",
                         "universe_shrinkage_disclosure", "excluded")}
        print(f"--- {fam}")
        print("   ", json.dumps(keep, ensure_ascii=False)[:1200])
        e = d.get("expected", {})
        print("    expected:", json.dumps({k: v for k, v in e.items() if not isinstance(v, (dict, list))}, ensure_ascii=False))
    for rj in sorted(glob.glob(f"{RES}/{fam}/rounds/*/attempts/*/result.json")):
        d = json.load(open(rj))
        print(f"    result {os.path.basename(os.path.dirname(rj))}:",
              json.dumps({k: d.get(k) for k in ("runtime_seconds", "case_evaluations_total",
                                                "expected_case_evaluations", "coverage_complete",
                                                "engine_version", "assertions_all_true")}, ensure_ascii=False))
