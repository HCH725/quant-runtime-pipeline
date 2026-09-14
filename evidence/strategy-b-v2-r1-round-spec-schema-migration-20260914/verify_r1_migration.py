#!/usr/bin/env python3
"""Independent post-migration verifier for the r1 round-spec schema migration (card t_67481d49).

Reads the evidence record written by `migrate_r1_round_spec.py`, then re-derives every claim
from the CURRENT on-disk state — the migrated round-spec, the repo template, the u1/u2 attempts
and the launch gate itself.  Read-only: it never writes to /results and never mutates the repo.

usage:
  python3 verify_r1_migration.py [--evidence <evidence.json>] [--results-root <dir>] [--json]
exit: 0 = all checks pass, 1 = at least one check failed, 2 = usage error
"""
import argparse
import hashlib
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO_ROOT, "runtime"))
import parameter_contract as pc  # noqa: E402
import preflight  # noqa: E402
import strategy_b_v2_counts as counts  # noqa: E402

MIGRATE = os.path.join(HERE, "migrate_r1_round_spec.py")
TEMPLATE = os.path.join(REPO_ROOT, "runtime", "templates", "strategy_b_v2_round_spec.template.json")
DEFAULT_EVIDENCE = os.path.join(REPO_ROOT, "evidence",
                                "strategy-b-v2-r1-round-spec-schema-migration-20260914.json")
DEFAULT_RESULTS = "/Volumes/ExpansionDrive/qlib-results"
FAMILY = "ema-crossover-walkforward-momentum-long-short-v2"
ROUND = FAMILY + "-r1"


def sha256_file(path):
    with open(path, "rb") as fh:
        return "sha256:" + hashlib.sha256(fh.read()).hexdigest()


def load_migrator():
    spec = importlib.util.spec_from_file_location("migrate_r1", MIGRATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    ap = argparse.ArgumentParser(description="verify the r1 schema migration from durable state")
    ap.add_argument("--evidence", default=DEFAULT_EVIDENCE)
    ap.add_argument("--results-root", default=DEFAULT_RESULTS)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    record = json.load(open(args.evidence))
    round_dir = os.path.join(args.results_root, FAMILY, "rounds", ROUND)
    round_spec_path = os.path.join(round_dir, "round-spec.json")
    attempts = os.path.join(round_dir, "attempts")
    spec = json.load(open(round_spec_path))
    template = json.load(open(TEMPLATE))

    checks = []

    def expect(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})
        return ok

    expect("record_action_migrated", record.get("action") == "migrated", record.get("action"))
    expect("round_spec_sha_equals_recorded_after",
           sha256_file(round_spec_path) == record.get("after_sha256"),
           sha256_file(round_spec_path))
    expect("record_before_sha_was_the_blocker_sha",
           record.get("before_sha256")
           == "sha256:a3dd33a95e3fb307c91f5e07f5b486b3c51e8be4bbc214ae85d654a169bcaf22",
           record.get("before_sha256"))
    contract = spec.get("parameter_contract")
    expect("round_spec_carries_parameter_contract", isinstance(contract, dict))
    expect("validate_contract_zero_problems", pc.validate_contract(contract) == [],
           pc.validate_contract(contract))
    expect("validate_round_spec_contract_zero_problems",
           pc.validate_round_spec_contract(spec) == [], pc.validate_round_spec_contract(spec))
    expect("contract_equals_template_contract", contract == template.get("parameter_contract"))
    expect("contract_equals_recorded_generated_contract",
           contract == record.get("generated_contract"))
    expect("contract_is_the_generation_of_the_registered_domains",
           contract == load_migrator().generate_contract(spec))
    expect("family_id_consistent", contract.get("family_id") == spec.get("family_id")
           == FAMILY, contract.get("family_id"))
    expect("cardinality_120_48_5760",
           contract.get("domain_cardinality") == {"strategy": 120, "dca": 48, "per_cohort": 5760},
           contract.get("domain_cardinality"))

    # the real launch gate now resolves for the real attempt dir
    u2_attempt = os.path.join(attempts, ROUND + "-u2")
    expect("preflight_launch_gate_accepts_the_migrated_round_spec",
           preflight.round_spec_contract_problem(u2_attempt) is None,
           preflight.round_spec_contract_problem(u2_attempt))

    # negative control: the pre-migration shape still fails closed
    stripped = dict(spec)
    del stripped["parameter_contract"]
    expect("pre_migration_shape_still_fails_closed",
           bool(pc.validate_round_spec_contract(stripped)),
           pc.validate_round_spec_contract(stripped)[:1])

    # counts against the migrated round-spec and the frozen u2 run-spec
    run_spec = json.load(open(os.path.join(u2_attempt, "run-spec.json")))
    computed, count_problems, extra = counts.check(spec, run_spec)
    expect("counts_check_zero_problems", count_problems == [], count_problems[:2])
    expect("counts_fingerprint_match",
           extra["fingerprint_declared"] == extra["fingerprint_computed"],
           extra["fingerprint_declared"])

    # u1/u2 immutability against the pins recorded by the migration itself
    pins = (record.get("sibling_immutability") or {}).get("attempt_pins_after") or {}
    sample = 0
    for rel, want in sorted(pins.items()):
        path = os.path.join(attempts, rel)
        if expect("attempt_pin_%s" % rel.replace("/", "_"), sha256_file(path) == want, rel):
            sample += 1
    expect("attempt_pins_all_matched", sample == len(pins), "%d/%d" % (sample, len(pins)))
    expect("family_json_unchanged",
           sha256_file(os.path.join(args.results_root, FAMILY, "family.json"))
           == (record.get("sibling_immutability") or {}).get("family_json_sha256_after"))

    # sibling tree manifest digest (34 files besides the round-spec itself)
    manifest = load_migrator().tree_manifest(round_dir, exclude=("round-spec.json",))
    manifest_sha = "sha256:" + hashlib.sha256(
        json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    expect("sibling_file_count", len(manifest) == 34, str(len(manifest)))

    failed = [c for c in checks if not c["ok"]]
    payload = {"ok": not failed, "checks": checks, "failed": [c["check"] for c in failed],
               "sibling_manifest_sha256": manifest_sha, "attempt_pins_checked": len(pins)}
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for c in checks:
            print("%-4s %s %s" % ("OK" if c["ok"] else "FAIL", c["check"], c["detail"]))
        print("r1 migration verification: %s (%d checks, %d failed) manifest=%s"
              % ("PASS" if not failed else "FAIL", len(checks), len(failed), manifest_sha))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
