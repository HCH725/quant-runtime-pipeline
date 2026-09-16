#!/usr/bin/env python3
"""Prerequisite-gate read-back checker for the family

    crypto-futures-cross-sectional-basis-high-low-1d-2026-08-31

Card t_d0abc072 terminalized this family as TECHNICAL_INCOMPLETE because the
record's required data/market (OKEx/OKX spot plus current-quarter dated futures
for 12 named crypto assets, with point-in-time listing history, contract
identity/expiry and roll timestamps) is absent from the canonical raw. This
script exists so that an independent reader can re-derive that determination
from the live filesystem instead of trusting prose:

  * it re-measures the canonical raw (venue directories, market type, symbol
    set, spot paths, dated/quarterly market dirs, instrument types, expiry and
    roll fields, listing/venue-classification fields) - the measurement side;
  * it re-reads the round's immutable artifacts and asserts they state exactly
    the contract-mandated terminal values (verdict, layer, class, yield
    decision, zero attempts, null run_id) and that the registered universe is
    still the record's own (OKX spot + current-quarter dated futures over the
    12 named assets), not a shrunk one;
  * it asserts nothing was ever submitted (no attempt directory, no terminal
    sentinel) so a "0 attempts" claim cannot quietly become a fabricated run;
  * it asserts the DCA registration still carries the contract 7.2 v1.3.1
    provenance classes plus the complete 48-cell product.

Read-only: it never writes inside the results tree. `--self-test` builds
tampered copies in fresh temp directories and asserts the checker refuses them
(non-vacuousness control), and `--raw-fixture-control` builds a temp raw tree
with an added spot market, a dated-futures market and a second venue and
asserts the raw-side checks flip to FAIL. Both temp trees are removed
afterwards.

Usage:
    python3 runtime/crypto_futures_xs_basis_prerequisite_check.py [--json]
    python3 runtime/crypto_futures_xs_basis_prerequisite_check.py --self-test
    python3 runtime/crypto_futures_xs_basis_prerequisite_check.py --raw-fixture-control

Exit codes: 0 = all checks PASS, 1 = at least one FAIL, 2 = usage error.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile

# Provenance classes and cartesian-product helper are reused from the existing
# registered validator rather than re-implemented here (contract 7.2 v1.3.1).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import strategy_a_v2_counts as _sav2
except ImportError:  # pragma: no cover - only if the sibling module is missing
    _sav2 = None

FAMILY = "crypto-futures-cross-sectional-basis-high-low-1d-2026-08-31"
ROUND = FAMILY + "-r1"
TASK = "t_d0abc072"
DEFAULT_RESULTS = "/Volumes/ExpansionDrive/qlib-results"
DEFAULT_RAW = "/Volumes/ExpansionDrive/market-data-raw"
EXPECTED_SYMBOLS = ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
RECORD_ASSETS = ["ADA", "BCH", "BSV", "BTC", "DOT", "EOS", "ETC", "ETH",
                 "LINK", "LTC", "TRX", "XRP"]
DATED_MARKET_DIR_TOKENS = ("delivery", "futures", "quarter", "dated", "swap_quarterly")
EXPIRY_FIELD_TOKENS = ("expiry", "expire", "deliverydate", "delivery_date", "contract_type",
                       "roll", "onboarddate", "listingdate", "listed_at", "launchtime")
INVARIANT_TOKENS = ("30000", "numeraire", "leverage 10x", "12 tranches", "tranche #12",
                    "reduce-only", "flat/kill")
CHECK_IDS = ("C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9", "C10", "C11")


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
    all_paths = _walk_paths(raw, 4)
    m["paths_named_spot"] = [p for p in all_paths if "spot" in os.path.basename(p).lower()]
    m["dated_market_paths"] = [p for p in all_paths
                               if any(t in os.path.basename(p).lower() for t in DATED_MARKET_DIR_TOKENS)]
    m["non_binance_paths"] = [p for p in _walk_paths(raw, 3)
                              if p != "." and not p.startswith("binance")
                              and not p.startswith("_")]
    m["okx_paths"] = [p for p in all_paths if "okx" in p.lower() or "okex" in p.lower()]
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
    m["instrument_contract_id_field_present"] = any(
        k in inst_text for k in ("expiry", "expire", "deliveryDate", "contract_type"))
    m["listing_history_field_present"] = any(
        k in inst_text for k in ("onboardDate", "listingDate", "listed_at", "launchTime"))
    m["roll_timestamp_field_present"] = any(k in inst_text for k in ("roll", "rollover"))
    m["raw_text_expiry_tokens"] = sorted({t for t in EXPIRY_FIELD_TOKENS
                                          if t in (inst_text + json.dumps(cfg)).lower()})
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

    # C3 - no spot market anywhere under the raw root (the record's signal needs a spot close).
    add("C3", raw["paths_named_spot"] == [],
        "paths_named_spot=%s" % raw["paths_named_spot"])

    # C4 - no dated/current-quarter futures market and no contract identity/expiry field.
    add("C4", raw["dated_market_paths"] == []
        and raw["instrument_types"] == ["CryptoPerpetual"]
        and not raw["instrument_contract_id_field_present"],
        "dated_market_paths=%s types=%s contract_id_field=%s"
        % (raw["dated_market_paths"], raw["instrument_types"],
           raw["instrument_contract_id_field_present"]))

    # C5 - the raw holds no OKX/OKEx data and no second venue.
    add("C5", raw["okx_paths"] == [] and raw["non_binance_paths"] == [],
        "okx_paths=%s non_binance_paths=%s" % (raw["okx_paths"], raw["non_binance_paths"]))

    # C6 - the fields the record requires for contract/universe identity are absent.
    add("C6", not raw["listing_history_field_present"]
        and not raw["roll_timestamp_field_present"],
        "listing_history_field_present=%s roll_timestamp_field_present=%s"
        % (raw["listing_history_field_present"], raw["roll_timestamp_field_present"]))

    # ---- artifact side
    spec_path = os.path.join(round_dir, "round-spec.json")
    verdict_path = os.path.join(round_dir, "verdict.json")
    spec = _load_json(spec_path) if os.path.exists(spec_path) else {}
    verdict = _load_json(verdict_path) if os.path.exists(verdict_path) else {}

    # C7 - the registration still states the record's own universe (venue, both market
    #      types, the 12 named assets, listing dates, roll convention) - not a shrunk one.
    gate = spec.get("prerequisite_gate") or {}
    univ = spec.get("universe_registration") or {}
    ok7 = (
        spec.get("family_id") == FAMILY and spec.get("round_id") == ROUND
        and spec.get("kanban_task_id") == TASK
        and gate.get("outcome") == "PREREQUISITE_MISSING"
        and gate.get("verdict") == "TECHNICAL_INCOMPLETE"
        and gate.get("attempts_launched") == 0
        and univ.get("instrument_market_type") == "spot plus current-quarter dated futures"
        and "OKX" in str(univ.get("venue", ""))
        and univ.get("universe_assets") == RECORD_ASSETS
        and univ.get("listing_dates_respected") is True
        and univ.get("point_in_time_listed_contracts") is True
        and "16:00 UTC+8" in str(univ.get("roll_convention", ""))
        and (spec.get("expected") or {}).get("status") == "not_computable"
        and (spec.get("strategy_domain") or {}).get("status") == "not_registered"
        and (spec.get("launch") or {}).get("attempts_launched") == 0
    )
    add("C7", ok7, "round-spec ids/gate=%s venue=%s market_type=%s assets=%d roll=%s" % (
        gate.get("outcome"), univ.get("venue"), univ.get("instrument_market_type"),
        len(univ.get("universe_assets") or []), str(univ.get("roll_convention"))[:24]))

    # C8 - the terminal verdict states the contract-mandated values.
    fail = verdict.get("failure") or {}
    yld = verdict.get("yield") or {}
    ok8 = (
        verdict.get("verdict") == "TECHNICAL_INCOMPLETE"
        and verdict.get("performance_claimable") is False
        and verdict.get("family_id") == FAMILY and verdict.get("round_id") == ROUND
        and verdict.get("kanban_task_id") == TASK
        and verdict.get("run_id") is None
        and fail.get("layer") == "card-local"
        and fail.get("class") == "data_window_invalid"
        and yld.get("yield_decision") == "STOP_TECHNICAL_INCOMPLETE"
        and (verdict.get("attempts") or {}).get("launched") == 0
        and not verdict.get("evidence_run_ids")
    )
    add("C8", ok8, "verdict=%s run_id=%s layer=%s class=%s yield=%s run_ids=%s" % (
        verdict.get("verdict"), verdict.get("run_id"), fail.get("layer"), fail.get("class"),
        yld.get("yield_decision"), verdict.get("evidence_run_ids")))

    # C9 - nothing was ever submitted: no attempt directory, no terminal sentinel.
    attempts_dir = os.path.join(round_dir, "attempts")
    sentinels = []
    for dp, dn, fn in os.walk(round_dir):
        for f in fn:
            if f in ("DONE", "FAILED", "INCOMPLETE"):
                sentinels.append(os.path.join(dp, f))
    add("C9", not os.path.exists(attempts_dir) and sentinels == [],
        "attempts_dir_exists=%s terminal_sentinels=%s" % (os.path.exists(attempts_dir),
                                                          sentinels))

    # C10 - ownership, and the round directory holds only the two immutable artifacts.
    fam_path = os.path.join(results_root, FAMILY, "family.json")
    fam = _load_json(fam_path) if os.path.exists(fam_path) else {}
    files = sorted(f for f in os.listdir(round_dir)) if os.path.isdir(round_dir) else None
    add("C10", fam.get("kanban_task_id") == TASK
        and files == ["round-spec.json", "verdict.json"],
        "family.kanban_task_id=%s round_dir_files=%s" % (fam.get("kanban_task_id"), files))

    # C11 - the DCA registration keeps its contract 7.2 v1.3.1 provenance classes and its
    #       complete 48-cell product (a searched axis is never declared as a user-fixed invariant).
    probs = []
    dca = spec.get("dca_domain") or {}
    invariants = list(spec.get("user_fixed_invariants") or [])
    if _sav2 is None:
        probs.append("runtime/strategy_a_v2_counts.py is not importable")
    else:
        for axis in _sav2.DCA_AXES:
            values = list(dca.get(axis) or [])
            cls = _sav2.provenance_class(dca.get(axis + "_status"))
            if len(values) > 1 and cls != _sav2.PROJECT_SEARCH:
                probs.append("%s is searched over %r but classified %r" % (axis, values, cls))
        if _sav2.provenance_class(dca.get("base_quote_status")) != _sav2.PROJECT_CONSTANT:
            probs.append("base_quote_status=%r is not %s"
                         % (dca.get("base_quote_status"), _sav2.PROJECT_CONSTANT))
        cells = _sav2.product([list(dca.get(a) or []) for a in _sav2.DCA_AXES])
        declared = [tuple(g.get(a) for a in _sav2.DCA_AXES) for g in (dca.get("grid") or [])]
        if (len(cells) != 48 or len(declared) != len(cells)
                or sorted(set(declared)) != sorted(set(cells))
                or dca.get("config_count") != 48):
            probs.append("grid is not the complete 48-cell product (declared=%d product=%d "
                         "config_count=%r)" % (len(declared), len(cells), dca.get("config_count")))
        for key in _sav2.USER_FIXED_FORBIDDEN_KEYS:
            if any(str(s).lower().startswith(key.lower()) for s in invariants):
                probs.append("user_fixed_invariants declares the searched axis %r" % key)
    blob = " | ".join(str(s) for s in invariants).lower()
    for token in INVARIANT_TOKENS:
        if token not in blob:
            probs.append("registered invariant token missing: %s" % token)
    add("C11", not probs, "dca provenance/grid+bounded invariants: %s"
        % (probs if probs else "ok"))

    return {"family_id": FAMILY, "round_id": ROUND, "task_id": TASK,
            "results_root": results_root, "raw_root": raw_root,
            "measured_raw": raw,
            "checks": checks,
            "overall": "PASS" if all(c["status"] == "PASS" for c in checks) else "FAIL"}


def _copy_tree(results_root, tmp):
    dst = os.path.join(tmp, FAMILY)
    os.makedirs(os.path.join(dst, "rounds"))
    shutil.copy(os.path.join(results_root, FAMILY, "family.json"),
                os.path.join(dst, "family.json"))
    shutil.copytree(os.path.join(results_root, FAMILY, "rounds", ROUND),
                    os.path.join(dst, "rounds", ROUND))
    return os.path.join(dst, "rounds", ROUND)


def self_test(results_root, raw_root):
    """Non-vacuousness control: the checker must refuse every tampered copy."""
    variants = {
        "verdict_tampered_to_PASS": lambda d: _tamper(d, lambda v: v.update(
            {"verdict": "PASS"})),
        "layer_flipped_to_shared": lambda d: _tamper(d, lambda v: v["failure"].update(
            {"layer": "shared-layer"})),
        "run_id_fabricated": lambda d: _tamper(d, lambda v: v.update(
            {"run_id": ROUND + "-u1"})),
        "attempt_fabricated": lambda d: _fabricate_attempt(d),
        "universe_shrunk_to_the_local_perps": lambda d: _tamper_spec(d, lambda s: s[
            "universe_registration"].update({
                "instrument_market_type": "linear USD-M perpetual",
                "venue": "BINANCE", "universe_assets": ["BNBUSDT", "BTCUSDT", "ETHUSDT",
                                                         "SOLUSDT"]})),
        "searched_axis_mislabelled_user_fixed": lambda d: _tamper_spec(
            d, lambda s: s["dca_domain"].update({"spacing_pct_status": "USER_FIXED"})),
        "dca_grid_truncated_to_47": lambda d: _tamper_spec(
            d, lambda s: s["dca_domain"]["grid"].pop()),
    }
    results = []
    ok = True
    for name, mutate in variants.items():
        tmp = tempfile.mkdtemp(prefix="xq-basis-prereq-selftest-")
        try:
            rdir = _copy_tree(results_root, tmp)
            mutate(rdir)
            res = run_checks(tmp, raw_root)
            refused = [c["id"] for c in res["checks"] if c["status"] == "FAIL"]
            results.append({"variant": name, "refused": bool(refused), "failed_checks": refused})
            ok = ok and bool(refused)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return {"self_test": results, "overall": "PASS" if ok else "FAIL"}


def raw_fixture_control(results_root, raw_root):
    """Measurement-side non-vacuousness control.

    Build a temp raw tree that keeps the real config/instruments and adds the
    markets this card's record would need (spot, a dated-futures market, a
    second venue). The raw-side checks must flip to FAIL.
    """
    tmp = tempfile.mkdtemp(prefix="xq-basis-prereq-rawfix-")
    try:
        fixture = os.path.join(tmp, "raw")
        shutil.copytree(raw_root, fixture,
                        ignore=shutil.ignore_patterns(".DS_Store", "__pycache__"))
        os.makedirs(os.path.join(fixture, "binance", "spot", "klines", "BTCUSDT"))
        os.makedirs(os.path.join(fixture, "binance", "delivery", "BTCUSDT_260327"))
        os.makedirs(os.path.join(fixture, "okx", "swap"))
        inst = os.path.join(fixture, "binance", "usdm", "instruments",
                            "usdm-perp-instruments.json")
        doc = _load_json(inst)
        extra = {"fields": dict(doc["instruments"][0]["fields"]),
                 "python_type": "CryptoFuture"}
        extra["fields"].update({"id": "BTCUSDT_260327.BINANCE", "raw_symbol": "BTCUSDT_260327",
                                "type": "CryptoFuture", "expiry": "2026-03-27"})
        doc["instruments"].append(extra)
        with open(inst, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1)
        res = run_checks(results_root, fixture)
        failed = [c["id"] for c in res["checks"] if c["status"] == "FAIL"]
        expected = {"C1", "C3", "C4", "C5"}
        return {"raw_fixture_control": {"failed_checks": sorted(failed),
                                        "expected": sorted(expected),
                                        "detail": {c["id"]: c["detail"] for c in res["checks"]
                                                   if c["id"] in expected},
                                        "overall": res["overall"]},
                "overall": "PASS" if expected <= set(failed) else "FAIL"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _tamper(round_dir, mutate):
    p = os.path.join(round_dir, "verdict.json")
    doc = _load_json(p)
    mutate(doc)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2, ensure_ascii=False)


def _tamper_spec(round_dir, mutate):
    p = os.path.join(round_dir, "round-spec.json")
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
    ap.add_argument("--raw-fixture-control", action="store_true")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.results_root) or not os.path.isdir(args.raw_root):
        print("usage error: results/raw root not mounted", file=sys.stderr)
        return 2

    if args.self_test:
        out = self_test(args.results_root, args.raw_root)
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0 if out["overall"] == "PASS" else 1

    if args.raw_fixture_control:
        out = raw_fixture_control(args.results_root, args.raw_root)
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
