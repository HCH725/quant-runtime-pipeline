#!/usr/bin/env python3
"""Build the Strategy E preflight (smoke-scope) run-spec from the frozen template.

NOT a registered attempt: the window is shortened so the whole real-data code path (raw ->
qlib bins -> per-cycle copula fit/GOF -> leg-aware episodes -> artifacts) can be exercised in
minutes before the registered full-window launch.  Output lives under
/results/_preflight/ (outside the family tree) and is disclosed as a probe.
"""
import hashlib
import json
import os
import time

HOST_SCRIPTS = "/Users/hong/workspace/qlib-apple-container/scripts"
RUNNER = os.path.join(HOST_SCRIPTS, "50_strategy_e_run.py")
SELFCHECK = os.path.join(HOST_SCRIPTS, "tests", "test_strategy_e_engine.py")
TEMPLATE = ("/Users/hong/workspace/quant-runtime-pipeline/runtime/templates/"
            "strategy_e_v1_run_spec.template.json")
OUT_DIR = "/Volumes/ExpansionDrive/qlib-results/_preflight/se-v1-smoke"
FAMILY = "copula-cmi-pairs-relative-value-perp-v1"
ROUND = "copula-cmi-pairs-relative-value-perp-v1-r1-preflight"
RUN = "copula-cmi-pairs-relative-value-perp-v1-r1-preflight-1"

WINDOW = {"start": "2025-08-01", "end": "2025-12-31",
          "historical_start": "2025-08-01", "historical_end": "2025-09-30",
          "oos_start": "2025-10-01", "oos_end": "2025-12-31"}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def walk(obj, mapping):
    if isinstance(obj, str):
        for k, v in mapping.items():
            obj = obj.replace(k, v)
        return obj
    if isinstance(obj, list):
        return [walk(v, mapping) for v in obj]
    if isinstance(obj, dict):
        return {k: walk(v, mapping) for k, v in obj.items()}
    return obj


with open(TEMPLATE) as fh:
    spec = json.load(fh)

runner_sha = sha256_file(RUNNER)
selfcheck_sha = sha256_file(SELFCHECK)
created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
spec = walk(spec, {"{{round_id}}": ROUND, "{{run_id}}": RUN, "{{task_id}}": "t_57ecd99e",
                   "{{created_at_utc}}": created,
                   "{{script_sha256}}": runner_sha,
                   "{{engine_selfcheck_sha256}}": selfcheck_sha,
                   "{{round_spec_path}}": "/results/%s/rounds/%s/round-spec.json"
                                          % (FAMILY, "copula-cmi-pairs-relative-value-perp-v1-r1")})
spec["document_kind"] = ("PREFLIGHT probe run-spec (NOT a registered attempt): the frozen E v1 "
                         "run-spec template with a shortened data window and its own split, used "
                         "once to exercise the real-data path before the registered launch")
spec["template_instantiation"]["status"] = ("PREFLIGHT ONLY - not a registered attempt; the "
                                            "registered launch uses the frozen template")
spec["data"].update(WINDOW)
os.makedirs(os.path.join(OUT_DIR, "attempt-1"), exist_ok=True)
out = os.path.join(OUT_DIR, "attempt-1", "run-spec.json")
with open(out, "w") as fh:
    fh.write(json.dumps(spec, indent=2, ensure_ascii=False) + "\n")
print("wrote", out)
print("runner_sha", runner_sha)
print("selfcheck_sha", selfcheck_sha)
print("window", json.dumps(WINDOW, sort_keys=True))
