#!/usr/bin/env python3
"""Build the Strategy D v1 SMOKE run-spec (card t_50c28da5).

Not a production artifact: a 3-month-window copy of the frozen run-spec template that exercises
the whole engine end to end (all 7 cases x 48 DCA x 12 grids, summarize, assertions, artifacts)
so the production launch is informed by a measured throughput instead of an estimate.  It lands
under /results/_validation (a reserved, scanner-skipped namespace) and never under a family
directory.
"""
import json
import os
import sys

REPO = "/Users/hong/workspace/quant-runtime-pipeline"
RESULTS = "/Volumes/ExpansionDrive/qlib-results"
DEPLOY_ENGINE = "/Users/hong/workspace/qlib-apple-container/scripts/40_strategy_d_run.py"
RUN_T = os.path.join(REPO, "runtime/templates/strategy_d_v1_run_spec.template.json")
OUT_DIR = os.path.join(RESULTS, "_validation/sd-v1-smoke/attempts/sd-v1-smoke-u1")


def sha256_file(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def main():
    sys.path.insert(0, os.path.join(REPO, "runtime"))
    import strategy_d_v1_counts as counts
    spec = json.load(open(RUN_T))
    spec["round_id"] = "sd-v1-smoke"
    spec["run_id"] = "sd-v1-smoke-u1"
    spec["task_id"] = "t_50c28da5"
    spec["created_at_utc"] = "2026-09-15T07:30:00Z"
    spec["round_spec_path"] = "/results/_validation/sd-v1-smoke/round-spec.json"
    spec["data"]["start"] = "2022-01-01"
    spec["data"]["end"] = "2022-03-31"
    spec["data"]["historical_start"] = "2022-01-01"
    spec["data"]["historical_end"] = "2022-02-28"
    spec["data"]["oos_start"] = "2022-03-01"
    spec["data"]["oos_end"] = "2022-03-31"
    spec["script"] = {"path": "/scripts/40_strategy_d_run.py",
                      "sha256": sha256_file(DEPLOY_ENGINE),
                      "deployed_from": "host deploy dir (smoke run only)"}
    spec["engine_selfcheck"] = {
        "script": "/scripts/tests/test_strategy_d_engine.py",
        "sha256": sha256_file("/Users/hong/workspace/qlib-apple-container/scripts/tests/"
                              "test_strategy_d_engine.py"),
        "must_run_before_launch": True}
    spec["notes"] = ("SMOKE RUN (3-month window, not a production attempt): exercises the whole "
                     "engine and the artifact set to measure throughput.")
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(os.path.join(OUT_DIR, "logs"), exist_ok=True)
    path = os.path.join(OUT_DIR, "run-spec.json")
    with open(path, "w") as fh:
        fh.write(json.dumps(spec, indent=2, ensure_ascii=False) + "\n")
    print("wrote", path)
    print("script sha pinned:", spec["script"]["sha256"])
    print("expected:", json.dumps(spec["expected"], indent=1)[:400])


if __name__ == "__main__":
    main()
