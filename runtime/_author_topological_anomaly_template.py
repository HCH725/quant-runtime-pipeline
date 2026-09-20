#!/usr/bin/env python3
"""Author/check the frozen topological-anomaly templates.

The scientific constants live in topological_anomaly_counts.py; this small authoring check keeps
future edits from silently changing the registered matrix or data window.
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import parameter_contract as pc  # noqa: E402
import topological_anomaly_counts as C  # noqa: E402

ROUND = ROOT / "runtime" / "templates" / "topological_anomaly_round_spec.template.json"
RUN = ROOT / "runtime" / "templates" / "topological_anomaly_run_spec.template.json"


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def check():
    problems = []
    expected = C.expected_counts()
    for path, kind in ((ROUND, "round_spec"), (RUN, "run_spec")):
        obj = load(path)
        if obj.get("family_id") != C.FAMILY_ID:
            problems.append("%s family_id mismatch" % kind)
        if obj.get("semantic_fingerprint") != "sha256:95ae7d754fe1b954ad4c0ba446d9a4a3d6d038533b706c509677e636e27b6aff":
            problems.append("%s semantic fingerprint mismatch" % kind)
        problems.extend("%s: %s" % (kind, p) for p in pc.validate_contract(obj.get("parameter_contract")))
        problems.extend("%s: %s" % (kind, p) for p in pc.validate_round_spec_contract(obj))
        exp = obj.get("expected", {})
        if exp.get("cohorts") != expected["cohorts"]:
            problems.append("%s expected cohorts mismatch" % kind)
        if exp.get("case_evaluations_total") != expected["case_evaluations_total"]:
            problems.append("%s expected total mismatch" % kind)
        data = obj.get("data", {})
        if data.get("raw_root") != "/data/raw/binance/usdm":
            problems.append("%s raw_root mismatch" % kind)
        if data.get("start") != C.DATA_START or data.get("end") != C.DATA_END:
            problems.append("%s data window mismatch" % kind)
        dca = obj.get("dca_domain", {})
        if dca.get("max_layers") != C.MAX_LAYERS or dca.get("leverage") != C.LEVERAGE:
            problems.append("%s leverage/max_layers mismatch" % kind)
        registered_grids = obj.get("grids", obj.get("robustness_plan", {}).get("grids"))
        if registered_grids != list(C.GRID_KINDS):
            problems.append("%s grid registration mismatch" % kind)
        split = obj.get("split", {})
        if {k: split.get(k) for k in ("historical_start", "historical_end", "oos_start", "oos_end")} != {
                "historical_start": C.HISTORICAL_START, "historical_end": C.HISTORICAL_END,
                "oos_start": C.OOS_START, "oos_end": C.OOS_END}:
            problems.append("%s split mismatch" % kind)
        if kind == "round_spec" and obj.get("robustness_plan", {}).get("grids") != list(C.GRID_KINDS):
            problems.append("round_spec robustness grid registration mismatch")
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", default=True)
    args = ap.parse_args(argv)
    problems = check()
    if problems:
        for problem in problems:
            print("FAIL: " + problem)
        return 1
    print(json.dumps({"ok": True, "family_id": C.FAMILY_ID,
                      "counts": C.expected_counts(), "templates": [str(ROUND), str(RUN)]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
