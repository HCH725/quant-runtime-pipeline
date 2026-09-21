#!/usr/bin/env python3
"""Write/check the frozen RG-ResMoE final verdict from attempt artifacts only."""
import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from rg_resmoe_verdict_inputs import derive

FAMILY_ID = "cross-sectional-volatility-regime-gated-residual-mixture-of-experts-2026-09-02"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return "sha256:" + h.hexdigest()


def atomic_json(path, value):
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(value, fh, indent=2, ensure_ascii=False, allow_nan=False)
        fh.write("\n"); fh.flush(); os.fsync(fh.fileno())
    os.replace(tmp, path)


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("attempt_dir"); ap.add_argument("--out"); ap.add_argument("--check", action="store_true"); ap.add_argument("--json", action="store_true"); args = ap.parse_args(argv)
    attempt = Path(args.attempt_dir).resolve()
    if not (attempt / "state.json").is_file():
        print("REFUSED: missing state.json", file=sys.stderr); return 1
    evidence = derive(str(attempt))
    if not evidence["ok"]:
        for problem in evidence["problems"]: print("REFUSED: " + problem, file=sys.stderr)
        return 1
    result = evidence["result"]
    if result.get("family_id") != FAMILY_ID:
        print("REFUSED: family mismatch", file=sys.stderr); return 1
    source_names = ["run-spec.json", "result.json", "state.json", "artifacts/assertions.json", "artifacts/cohort_results.json", "artifacts/cohort_survivors.json", "artifacts/dca_layer_histogram.json", "artifacts/signal_layer.json", "artifacts/family_falsification.json"]
    source_names += ["artifacts/grid_%s.csv" % g for g in evidence["coverage"]["grids"]]
    source = {name: sha256(attempt / name) for name in source_names}
    count = evidence["survivor_count"]
    verdict = "PASS" if count else "REJECT"
    full = evidence["aggregates"].get("full", {})
    body = {"schema_version": 1, "document_kind": "final_verdict", "family_id": FAMILY_ID, "round_id": result.get("round_id"), "run_id": result.get("run_id"), "task_id": result.get("task_id"), "kanban_board": result.get("kanban_board"), "verdict": verdict, "disposition": result.get("disposition"), "cohort_survivor_count": count, "cohort_survivors": result.get("cohort_survivors"), "coverage_complete": bool(evidence["coverage"]["rows_total"] == evidence["coverage"]["expected_total"]), "performance_claimable": bool(count), "assertions": evidence["assertions"], "coverage": evidence["coverage"], "aggregates": evidence["aggregates"], "winner_evidence": evidence["winners"], "summary_metrics": {"ending_equity_usdt_aggregate": 30000.0 + float(full.get("sum_net_pnl", 0.0)), "net_pnl_usdt_aggregate": float(full.get("sum_net_pnl", 0.0)), "gross_pnl_usdt_aggregate": float(full.get("sum_gross_pnl", 0.0)), "fees_usdt_aggregate": float(full.get("sum_fees", 0.0)), "funding_usdt_aggregate": float(full.get("sum_funding", 0.0)), "turnover_usdt_aggregate": float(full.get("sum_turnover_usdt", 0.0)), "trade_count_aggregate": int(full.get("sum_episodes", 0)), "fill_count_aggregate": int(full.get("sum_fills", 0)), "max_effective_leverage_registered": 10.0, "capital_utilization_definition": "per-row rail metric; aggregate artifact preserves per-cell values"}, "research_boundary": "PASS/REJECT is the preregistered local-crypto adaptation result only; it does not reproduce the source equity panel, does not adopt the model, and is not paper/testnet/live authorization.", "source_attempt_dir": str(attempt), "source_artifacts": source, "generated_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    out = Path(args.out).resolve() if args.out else attempt.parent.parent / "verdict.json"
    frozen_keys = ("family_id", "round_id", "run_id", "verdict", "disposition", "cohort_survivors", "source_artifacts")
    if args.check:
        if not out.is_file(): print("REFUSED: missing verdict %s" % out, file=sys.stderr); return 1
        existing = json.load(open(out, encoding="utf-8"))
        for key in frozen_keys:
            if existing.get(key) != body.get(key): print("REFUSED: verdict field changed: %s" % key, file=sys.stderr); return 1
        action = "check_clean"
    elif out.exists():
        existing = json.load(open(out, encoding="utf-8"))
        if any(existing.get(k) != body.get(k) for k in frozen_keys): print("REFUSED: verdict exists with different frozen content", file=sys.stderr); return 1
        action = "already_identical"
    else:
        atomic_json(out, body); action = "written"
    response = {"ok": True, "action": action, "verdict_path": str(out), "verdict": verdict, "survivor_count": count, "survivors": result.get("cohort_survivors")}
    print(json.dumps(response, indent=2) if args.json else "verdict=%s survivors=%d path=%s action=%s" % (verdict, count, out, action))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
