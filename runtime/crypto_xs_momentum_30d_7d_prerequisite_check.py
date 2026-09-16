#!/usr/bin/env python3
"""Prerequisite-gate read-back checker for the family

    crypto-cross-sectional-momentum-30d-top-quintile-7d-2026-08-31

Card t_629b9ae1 terminalized this family as TECHNICAL_INCOMPLETE because the
record's required data/market (a point-in-time, survivorship-free, multi-venue
spot universe) is absent from the canonical raw. This script exists so that an
independent reader can re-derive that determination from the live filesystem
instead of trusting prose:

  * it re-measures the canonical raw (venue directories, market type, symbol
    set, listing/venue-classification fields, spot paths) - the measurement side;
  * it re-reads the round's immutable artifacts and asserts they state exactly
    the contract-mandated terminal values (verdict, layer, class, yield
    decision, zero attempts) - the artifact side;
  * it asserts nothing was ever submitted (no attempt directory, no terminal
    sentinel) so a "0 attempts" claim cannot quietly become a fabricated run.

Read-only: it never writes inside the results tree. `--self-test` builds a
tampered copy in a fresh temp directory and asserts the checker refuses it
(non-vacuousness control); the temp copy is removed afterwards.

Usage:
    python3 runtime/crypto_xs_momentum_30d_7d_prerequisite_check.py [--json]
    python3 runtime/crypto_xs_momentum_30d_7d_prerequisite_check.py --self-test

Exit codes: 0 = all checks PASS, 1 = at least one FAIL, 2 = usage error.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile

FAMILY = "crypto-cross-sectional-momentum-30d-top-quintile-7d-2026-08-31"
ROUND = FAMILY + "-r1"
TASK = "t_629b9ae1"
DEFAULT_RESULTS = "/Volumes/ExpansionDrive/qlib-results"
DEFAULT_RAW = "/Volumes/ExpansionDrive/market-data-raw"
EXPECTED_SYMBOLS = ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
MIN_CROSS_SECTION_FOR_QUINTILES = 5

CHECK_IDS = ("C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9")


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _dirs(path, skip_dotfiles=True):
    if not os.path.isdir(path):
        return None
    out = sorted(os.listdir(path))
    if skip_dotfiles:
        out = [x for x in out if not x.startswith(".")]
    return out


def _walk_paths(root, max_depth=3):
    out = []
    for dp, dn, fn in os.walk(root):
        rel = os.path.relpath(dp, root)
        if rel.count(os.sep) >= max_depth:
            dn[:] = []
            continue
        out.append(rel)
    return out


def measure_raw(raw):
    """Independent re-measurement of the canonical raw. No artifact is trusted."""
    m = {}
    m["binance_market_dirs"] = _dirs(os.path.join(raw, "binance"))
    m["paths_named_spot"] = [p for p in _walk_paths(raw, 4)
                             if os.path.basename(p).lower() == "spot"]
    m["non_binance_paths"] = [p for p in _walk_paths(raw, 3)
                              if p != "." and not p.startswith("binance")
                              and not p.startswith("_")]
    cfg_path = os.path.join(raw, "_meta", "CONFIG.json")
    cfg = _load_json(cfg_path) if os.path.exists(cfg_path) else {}
    m["market_type"] = cfg.get("market_type")
    m["venue"] = cfg.get("venue")
    m["symbols"] = sorted(cfg.get("symbols") or [])
    m["klines_dataset_dirs"] = _dirs(os.path.join(raw, "binance", "usdm", "klines"))
    m["funding_symbol_dirs"] = _dirs(os.path.join(raw, "binance", "usdm", "funding"))
    inst_path = os.path.join(raw, "binance", "usdm", "instruments",
                             "usdm-perp-instruments.json")
    inst_text = open(inst_path, encoding="utf-8").read() if os.path.exists(inst_path) else ""
    m["instrument_types"] = sorted({i["fields"]["type"]
                                    for i in json.loads(inst_text or "{}").get("instruments", [])})
    m["listing_history_field_present"] = any(
        k in inst_text for k in ("onboardDate", "listingDate", "listed_at", "launchTime"))
    m["venue_classification_field_present"] = any(
        k in inst_text for k in ("cefi", "defi", "venue_class", "exchange_class"))
    return m


def run_checks(results_root, raw_root):
    checks = []
    add = lambda cid, ok, detail: checks.append({"id": cid, "status": "PASS" if ok else "FAIL",
                                                 "detail": detail})
    raw = measure_raw(raw_root)
    round_dir = os.path.join(results_root, FAMILY, "rounds", ROUND)

    # C1 - the raw holds exactly one market type for one venue.
    md = raw["binance_market_dirs"] or []
    add("C1", md == ["usdm"] and raw["venue"] == "BINANCE",
        "binance market dirs=%s venue=%s market_type=%s" % (md, raw["venue"], raw["market_type"]))

    # C2 - the registered 4 major USD-M perpetual contracts are the only ones present.
    same = (raw["klines_dataset_dirs"] == EXPECTED_SYMBOLS
            and raw["funding_symbol_dirs"] == EXPECTED_SYMBOLS
            and raw["symbols"] == EXPECTED_SYMBOLS
            and raw["instrument_types"] == ["CryptoPerpetual"])
    add("C2", bool(same),
        "klines=%s funding=%s config=%s types=%s" % (
            raw["klines_dataset_dirs"], raw["funding_symbol_dirs"], raw["symbols"],
            raw["instrument_types"]))

    # C3 - no cross-section large enough for a quintile sort.
    n = len(raw["symbols"])
    add("C3", 0 < n < MIN_CROSS_SECTION_FOR_QUINTILES,
        "symbols=%d < %d (quintile sort impossible on a single-digit single-venue perp list)"
        % (n, MIN_CROSS_SECTION_FOR_QUINTILES))

    # C4 - no second venue and no spot market anywhere under the raw root.
    add("C4", raw["non_binance_paths"] == [] and raw["paths_named_spot"] == [],
        "non_binance_paths=%s paths_named_spot=%s" % (raw["non_binance_paths"],
                                                      raw["paths_named_spot"]))

    # C5 - the fields the record requires for universe eligibility are absent.
    add("C5", not raw["listing_history_field_present"]
        and not raw["venue_classification_field_present"],
        "listing_history_field_present=%s venue_classification_field_present=%s"
        % (raw["listing_history_field_present"], raw["venue_classification_field_present"]))

    # ---- artifact side
    spec_path = os.path.join(round_dir, "round-spec.json")
    verdict_path = os.path.join(round_dir, "verdict.json")
    spec = _load_json(spec_path) if os.path.exists(spec_path) else {}
    verdict = _load_json(verdict_path) if os.path.exists(verdict_path) else {}

    # C6 - the registration still states the record's universe (not a shrunk one).
    gate = spec.get("prerequisite_gate") or {}
    univ = spec.get("universe_registration") or {}
    ok6 = (
        spec.get("family_id") == FAMILY and spec.get("round_id") == ROUND
        and spec.get("kanban_task_id") == TASK
        and gate.get("outcome") == "PREREQUISITE_MISSING"
        and gate.get("verdict") == "TECHNICAL_INCOMPLETE"
        and gate.get("attempts_launched") == 0
        and univ.get("instrument_market_type") == "spot cryptocurrencies"
        and univ.get("minimum_exchange_listings") == 3
        and univ.get("cefi_listing_required") is True
        and univ.get("point_in_time_evaluation") is True
        and univ.get("survivorship_free_inclusion") is True
        and (spec.get("expected") or {}).get("status") == "not_computable"
        and (spec.get("strategy_domain") or {}).get("status") == "not_registered"
        and (spec.get("launch") or {}).get("attempts_launched") == 0
    )
    add("C6", ok6, "round-spec ids/gate=%s universe=%s/%s listings>=%s" % (
        gate.get("outcome"), univ.get("instrument_market_type"), univ.get("venue_scope"),
        univ.get("minimum_exchange_listings")))

    # C7 - the terminal verdict states the contract-mandated values.
    fail = verdict.get("failure") or {}
    yld = verdict.get("yield") or {}
    ok7 = (
        verdict.get("verdict") == "TECHNICAL_INCOMPLETE"
        and verdict.get("performance_claimable") is False
        and verdict.get("family_id") == FAMILY and verdict.get("round_id") == ROUND
        and verdict.get("kanban_task_id") == TASK
        and fail.get("layer") == "card-local"
        and fail.get("class") == "data_window_invalid"
        and yld.get("yield_decision") == "STOP_TECHNICAL_INCOMPLETE"
        and (verdict.get("attempts") or {}).get("launched") == 0
        and not verdict.get("evidence_run_ids")
    )
    add("C7", ok7, "verdict=%s layer=%s class=%s yield=%s run_ids=%s" % (
        verdict.get("verdict"), fail.get("layer"), fail.get("class"),
        yld.get("yield_decision"), verdict.get("evidence_run_ids")))

    # C8 - nothing was ever submitted: no attempt directory, no terminal sentinel.
    attempts_dir = os.path.join(round_dir, "attempts")
    sentinels = []
    for dp, dn, fn in os.walk(round_dir):
        for f in fn:
            if f in ("DONE", "FAILED", "INCOMPLETE"):
                sentinels.append(os.path.join(dp, f))
    add("C8", not os.path.exists(attempts_dir) and sentinels == [],
        "attempts_dir_exists=%s terminal_sentinels=%s" % (os.path.exists(attempts_dir),
                                                          sentinels))

    # C9 - ownership, and the round directory holds only the two immutable artifacts.
    fam_path = os.path.join(results_root, FAMILY, "family.json")
    fam = _load_json(fam_path) if os.path.exists(fam_path) else {}
    files = sorted(f for f in os.listdir(round_dir)) if os.path.isdir(round_dir) else None
    add("C9", fam.get("kanban_task_id") == TASK
        and files == ["round-spec.json", "verdict.json"],
        "family.kanban_task_id=%s round_dir_files=%s" % (fam.get("kanban_task_id"), files))

    return {"family_id": FAMILY, "round_id": ROUND, "task_id": TASK,
            "results_root": results_root, "raw_root": raw_root,
            "measured_raw": raw,
            "checks": checks,
            "overall": "PASS" if all(c["status"] == "PASS" for c in checks) else "FAIL"}


def self_test(results_root, raw_root):
    """Non-vacuousness control: the checker must refuse a tampered copy.

    Builds each tamper variant in a fresh temp results root and asserts rc != 0.
    """
    variants = {
        "verdict_tampered_to_PASS": lambda d: _tamper(d, lambda v: v.update(
            {"verdict": "PASS"})),
        "layer_flipped_to_shared": lambda d: _tamper(d, lambda v: v["failure"].update(
            {"layer": "shared-layer"})),
        "attempt_fabricated": lambda d: _fabricate_attempt(d),
    }
    results = []
    ok = True
    for name, mutate in variants.items():
        tmp = tempfile.mkdtemp(prefix="xq-prereq-selftest-")
        try:
            dst = os.path.join(tmp, FAMILY)
            os.makedirs(os.path.join(dst, "rounds"))
            shutil.copy(os.path.join(results_root, FAMILY, "family.json"),
                        os.path.join(dst, "family.json"))
            shutil.copytree(os.path.join(results_root, FAMILY, "rounds", ROUND),
                            os.path.join(dst, "rounds", ROUND))
            mutate(os.path.join(dst, "rounds", ROUND))
            res = run_checks(tmp, raw_root)
            refused = [c["id"] for c in res["checks"] if c["status"] == "FAIL"]
            results.append({"variant": name, "refused": bool(refused), "failed_checks": refused})
            ok = ok and bool(refused)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return {"self_test": results, "overall": "PASS" if ok else "FAIL"}


def _tamper(round_dir, mutate):
    p = os.path.join(round_dir, "verdict.json")
    doc = _load_json(p)
    mutate(doc)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)


def _fabricate_attempt(round_dir):
    adir = os.path.join(round_dir, "attempts", ROUND + "-u1")
    os.makedirs(adir)
    with open(os.path.join(adir, "DONE"), "w", encoding="utf-8") as f:
        f.write("{}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="prerequisite-gate read-back checker (%s)" % FAMILY)
    ap.add_argument("--results-root", default=DEFAULT_RESULTS)
    ap.add_argument("--raw-root", default=DEFAULT_RAW)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.results_root) or not os.path.isdir(args.raw_root):
        print("usage error: results/raw root not mounted", file=sys.stderr)
        return 2

    if args.self_test:
        out = self_test(args.results_root, args.raw_root)
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0 if out["overall"] == "PASS" else 1

    out = run_checks(args.results_root, args.raw_root)
    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        for c in out["checks"]:
            print("%-4s %-4s %s" % (c["id"], c["status"], c["detail"]))
        print("overall:", out["overall"])
    return 0 if out["overall"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
