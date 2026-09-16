#!/usr/bin/env python3
"""Prerequisite-gate read-back checker for the family

    crypto-cross-sectional-last-day-return-reversal-liquidity-conditioned-2026-08-31

Card t_c62c6881 terminalized this family as TECHNICAL_INCOMPLETE because the
record's required data (a point-in-time, survivorship-free cryptocurrency
cross-section with market-capitalization / liquidity conditioning measures,
listing and delisting dates and stale-price flags) is absent from the canonical
raw. This script exists so an independent reader can re-derive that
determination from the live filesystem instead of trusting prose:

  * it re-measures the canonical raw (venue/market directories, contract set,
    cross-section size, market-cap / liquidity / turnover datasets, listing and
    stale-price fields, universe-membership history, perp add-on surfaces:
    funding, mark/index pricing, contract availability) - the measurement side;
  * it re-reads the round's immutable artifacts and asserts they state exactly
    the contract-mandated terminal values (verdict, layer, class, yield
    decision, zero attempts, null run_id) and that the registered universe is
    still the record's own point-in-time survivorship-free cross-section with a
    required liquid/large vs illiquid/small regime split, not a shrunk one;
  * it asserts nothing was ever submitted (no attempt directory, no terminal
    sentinel) so a "0 attempts" claim cannot quietly become a fabricated run;
  * it asserts the DCA registration still carries the contract 7.2 v1.3.1
    provenance classes plus the complete 48-cell product.

Read-only: it never writes inside the results tree. `--self-test` builds
tampered copies in fresh temp directories and asserts the checker refuses them
(non-vacuousness control), and `--raw-fixture-control` builds a temp raw tree
carrying what this record would need (a spot market, a wider cross-section,
market-cap and liquidity reference datasets, listing-history fields, a
mark/index price series, a second venue) and asserts the raw-side checks flip to
FAIL. Both temp trees are removed afterwards.

Usage:
    python3 runtime/crypto_xs_reversal_prerequisite_check.py [--json]
    python3 runtime/crypto_xs_reversal_prerequisite_check.py --self-test
    python3 runtime/crypto_xs_reversal_prerequisite_check.py --raw-fixture-control

Exit codes: 0 = all checks PASS, 1 = at least one FAIL, 2 = usage error.
"""
import argparse
import gzip
import hashlib
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

FAMILY = "crypto-cross-sectional-last-day-return-reversal-liquidity-conditioned-2026-08-31"
ROUND = FAMILY + "-r1"
TASK = "t_c62c6881"
DEFAULT_RESULTS = "/Volumes/ExpansionDrive/qlib-results"
DEFAULT_RAW = "/Volumes/ExpansionDrive/market-data-raw"
EXPECTED_SYMBOLS = ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
# The record's hypothesis must be tested separately in liquid/large and
# illiquid/small subsets (signal step 5). Two non-empty segments therefore need
# a cross-section far wider than the four local majors; 20 is a conservative
# floor for "two segments of at least ten names".
MIN_SEGMENTED_CROSS_SECTION = 20
# Dataset / field names that would have to exist for the record's required data.
MARKET_CAP_TOKENS = ("market_cap", "marketcap", "mcap", "circulating", "supply")
LIQUIDITY_TOKENS = ("turnover", "quote_volume", "traded_value", "liquidity", "volume_usd")
LISTING_TOKENS = ("onboarddate", "listingdate", "listed_at", "launchtime", "listing",
                  "delisting", "delist")
STALE_TOKENS = ("stale", "stale_price", "no_trade", "last_trade_age")
MARK_INDEX_TOKENS = ("mark_price", "index_price", "markprice", "indexprice")
INVARIANT_TOKENS = ("30000", "numeraire", "leverage 10x", "12 tranches", "tranche #12",
                    "reduce-only", "flat/kill")


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


def _all_entries(root, max_depth=4):
    """Every file/directory basename token under the raw root (for name probes)."""
    names = []
    for dp, dn, fn in os.walk(root):
        rel = os.path.relpath(dp, root)
        if rel.count(os.sep) >= max_depth:
            dn[:] = []
            continue
        names.extend(dn)
        names.extend(fn)
    return sorted({n.lower() for n in names if not n.startswith(".")})


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def measure_raw(raw):
    """Independent re-measurement of the canonical raw. No artifact is trusted."""
    m = {}
    m["binance_market_dirs"] = _dirs(os.path.join(raw, "binance"))
    all_paths = _walk_paths(raw, 4)
    m["paths_named_spot"] = [p for p in all_paths
                             if os.path.basename(p).lower() == "spot"]
    m["non_binance_paths"] = [p for p in _walk_paths(raw, 3)
                              if p != "." and not p.startswith("binance")
                              and not p.startswith("_")]
    cfg_path = os.path.join(raw, "_meta", "CONFIG.json")
    cfg = _load_json(cfg_path) if os.path.exists(cfg_path) else {}
    m["market_type"] = cfg.get("market_type")
    m["venue"] = cfg.get("venue")
    m["symbols"] = sorted(cfg.get("symbols") or [])
    m["config_intervals"] = sorted(cfg.get("intervals") or [])
    m["trade_instrument"] = "spot" if m["paths_named_spot"] else "perpetual"
    m["cross_section_size"] = len(m["symbols"])
    m["klines_dataset_dirs"] = _dirs(os.path.join(raw, "binance", "usdm", "klines"))
    m["klines_intervals"] = _dirs(os.path.join(raw, "binance", "usdm", "klines",
                                               "BTCUSDT"))
    m["funding_symbol_dirs"] = _dirs(os.path.join(raw, "binance", "usdm", "funding"))
    inst_path = os.path.join(raw, "binance", "usdm", "instruments",
                             "usdm-perp-instruments.json")
    inst_text = open(inst_path, encoding="utf-8").read() if os.path.exists(inst_path) else ""
    inst = json.loads(inst_text or "{}").get("instruments", [])
    m["instrument_types"] = sorted({i["fields"]["type"] for i in inst})
    m["instrument_count"] = len(inst)
    m["listing_history_field_present"] = any(k in inst_text.lower() for k in LISTING_TOKENS)
    schema_path = os.path.join(raw, "_meta", "SCHEMA.md")
    schema_text = open(schema_path, encoding="utf-8").read() if os.path.exists(schema_path) else ""
    schema_flat = schema_text.replace("*", "")
    m["quote_volume_documented_absent"] = ("quote_volume" in schema_flat
                                           and "not present" in schema_flat)
    names = _all_entries(raw, 4)
    m["market_cap_dataset_present"] = any(t in n for n in names for t in MARKET_CAP_TOKENS)
    m["liquidity_or_turnover_dataset_present"] = any(t in n for n in names
                                                     for t in LIQUIDITY_TOKENS)
    m["stale_price_flag_field_present"] = any(t in n for n in names for t in STALE_TOKENS)
    m["universe_membership_history_present"] = any(
        n in ("universe", "universe.json", "membership.json", "listings.json") for n in names)
    m["mark_index_dataset_present"] = any(t in n for n in names for t in MARK_INDEX_TOKENS)
    # mark price is stored inside funding rows (source: funding history), not as a series
    funding_first = os.path.join(raw, "binance", "usdm", "funding", "BTCUSDT",
                                 "BTCUSDT-funding.jsonl.gz")
    first = ""
    if os.path.exists(funding_first):
        with gzip.open(funding_first, "rt", encoding="utf-8") as f:
            first = f.readline()
    m["funding_row_mark_price_present"] = '"mark_price"' in first
    m["funding_truth_status_field_present"] = "truth_status" in first
    state_path = os.path.join(raw, "_meta", "STATE.json")
    state = _load_json(state_path) if os.path.exists(state_path) else {}
    m["state_cursors"] = {
        "klines_BTCUSDT_1d_ms": (state.get("klines") or {}).get("BTCUSDT/1d"),
        "funding_BTCUSDT_ms": (state.get("funding") or {}).get("BTCUSDT"),
        "last_run_utc": state.get("last_run_utc"),
    }
    m["meta_sha256"] = {}
    for rel in ("CONFIG.json", "INSTRUMENTS_EXPORT.json", "SCHEMA.md"):
        p = os.path.join(raw, "_meta", rel)
        if os.path.exists(p):
            m["meta_sha256"][rel] = _sha256_file(p)
    inst_sha = _sha256_file(inst_path) if os.path.exists(inst_path) else None
    if inst_sha:
        m["meta_sha256"]["binance/usdm/instruments/usdm-perp-instruments.json"] = inst_sha
    m["note"] = ("single venue (BINANCE USD-M perpetual), 4 fixed contracts, OHLCV-only "
                 "klines (no quote_volume / trade count / taker splits); no spot market, "
                 "no market-cap or liquidity/turnover dataset, no listing/delisting dates, "
                 "no stale-price flags, no universe-membership history, no mark/index price "
                 "series, no second venue")
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
        "binance market dirs=%s venue=%s market_type=%s" % (md, raw["venue"],
                                                            raw["market_type"]))

    # C2 - the registered 4 major USD-M perpetual contracts are the only ones present.
    same = (raw["klines_dataset_dirs"] == EXPECTED_SYMBOLS
            and raw["funding_symbol_dirs"] == EXPECTED_SYMBOLS
            and raw["symbols"] == EXPECTED_SYMBOLS
            and raw["instrument_types"] == ["CryptoPerpetual"])
    add("C2", bool(same),
        "klines=%s funding=%s config=%s types=%s" % (
            raw["klines_dataset_dirs"], raw["funding_symbol_dirs"], raw["symbols"],
            raw["instrument_types"]))

    # C3 - the cross-section cannot carry the record's registered regime split, and no
    #      size/liquidity measure exists to form the liquid/large vs illiquid/small buckets.
    n = raw["cross_section_size"]
    add("C3", 0 < n < MIN_SEGMENTED_CROSS_SECTION
        and not raw["market_cap_dataset_present"]
        and not raw["liquidity_or_turnover_dataset_present"]
        and raw["quote_volume_documented_absent"],
        "cross_section=%d < %d; market_cap_dataset=%s liquidity_dataset=%s "
        "quote_volume_documented_absent=%s"
        % (n, MIN_SEGMENTED_CROSS_SECTION, raw["market_cap_dataset_present"],
           raw["liquidity_or_turnover_dataset_present"],
           raw["quote_volume_documented_absent"]))

    # C4 - no spot market, no second venue, no other market/dated directory.
    add("C4", raw["non_binance_paths"] == [] and raw["paths_named_spot"] == [],
        "non_binance_paths=%s paths_named_spot=%s" % (raw["non_binance_paths"],
                                                      raw["paths_named_spot"]))

    # C5 - listing/delisting, universe-membership history and stale-price flags are absent.
    add("C5", not raw["listing_history_field_present"]
        and not raw["stale_price_flag_field_present"]
        and not raw["universe_membership_history_present"],
        "listing_history_field_present=%s stale_price_flag_field_present=%s "
        "universe_membership_history_present=%s"
        % (raw["listing_history_field_present"], raw["stale_price_flag_field_present"],
           raw["universe_membership_history_present"]))

    # C6 - perpetual add-on surfaces the record requires: funding is present, but there is
    #      no mark/index price series and no contract-availability (listing) history.
    add("C6", raw["funding_row_mark_price_present"]
        and not raw["mark_index_dataset_present"]
        and not raw["listing_history_field_present"],
        "funding_mark_price_in_rows=%s mark_index_dataset_present=%s "
        "contract_availability(listing)_present=%s"
        % (raw["funding_row_mark_price_present"], raw["mark_index_dataset_present"],
           raw["listing_history_field_present"]))

    # ---- artifact side
    spec_path = os.path.join(round_dir, "round-spec.json")
    verdict_path = os.path.join(round_dir, "verdict.json")
    spec = _load_json(spec_path) if os.path.exists(spec_path) else {}
    verdict = _load_json(verdict_path) if os.path.exists(verdict_path) else {}

    # C7 - the registration still states the record's own universe (point-in-time,
    #      survivorship-free cross-section with a required regime split) - not a shrunk one.
    gate = spec.get("prerequisite_gate") or {}
    univ = spec.get("universe_registration") or {}
    seg = univ.get("regime_segmentation_required") or {}
    ok7 = (
        spec.get("family_id") == FAMILY and spec.get("round_id") == ROUND
        and spec.get("kanban_task_id") == TASK
        and gate.get("outcome") == "PREREQUISITE_MISSING"
        and gate.get("verdict") == "TECHNICAL_INCOMPLETE"
        and gate.get("required_data_available") is False
        and gate.get("attempts_launched") == 0
        and univ.get("point_in_time_universe") is True
        and univ.get("survivorship_free_inclusion") is True
        and seg.get("liquid_large") is True and seg.get("illiquid_small") is True
        and univ.get("cross_section_size_available") == raw["cross_section_size"]
        and "3,600" in str(univ.get("source_scale", ""))
        and (spec.get("expected") or {}).get("status") == "not_computable"
        and (spec.get("strategy_domain") or {}).get("status") == "not_registered"
        and (spec.get("launch") or {}).get("attempts_launched") == 0
    )
    add("C7", ok7, "round-spec ids/gate=%s required_available=%s point_in_time=%s "
        "survivorship_free=%s regime_split=%s cross_section_registered=%s"
        % (gate.get("outcome"), gate.get("required_data_available"),
           univ.get("point_in_time_universe"), univ.get("survivorship_free_inclusion"),
           seg.get("liquid_large") and seg.get("illiquid_small"),
           univ.get("cross_section_size_available")))

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
        "universe_shrunk_to_the_local_four_perps": lambda d: _tamper_spec(d, lambda s: s[
            "universe_registration"].update({
                "point_in_time_universe": False, "survivorship_free_inclusion": False,
                "source_scale": "BINANCE USD-M perpetual, BNBUSDT/BTCUSDT/ETHUSDT/SOLUSDT",
                "regime_segmentation_required": {"liquid_large": False, "illiquid_small": False}})),
        "required_data_available_flipped_true": lambda d: _tamper_spec(d, lambda s: s[
            "prerequisite_gate"].update({
                "required_data_available": True, "outcome": "PREREQUISITE_PRESENT",
                "missing": []})),
        "searched_axis_mislabelled_user_fixed": lambda d: _tamper_spec(
            d, lambda s: s["dca_domain"].update({"spacing_pct_status": "USER_FIXED"})),
        "dca_grid_truncated_to_47": lambda d: _tamper_spec(
            d, lambda s: s["dca_domain"]["grid"].pop()),
    }
    results = []
    ok = True
    for name, mutate in variants.items():
        tmp = tempfile.mkdtemp(prefix="xq-reversal-prereq-selftest-")
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
    surfaces this card's record would need: a spot market, a wider cross-section,
    market-cap and liquidity/turnover reference datasets, listing-history fields,
    a mark/index price series and a second venue. The raw-side checks must flip
    to FAIL.
    """
    tmp = tempfile.mkdtemp(prefix="xq-reversal-prereq-rawfix-")
    try:
        fixture = os.path.join(tmp, "raw")
        shutil.copytree(raw_root, fixture,
                        ignore=shutil.ignore_patterns(".DS_Store", "__pycache__"))
        # a spot market and a second venue
        os.makedirs(os.path.join(fixture, "binance", "spot", "klines", "BTCUSDT"))
        os.makedirs(os.path.join(fixture, "okx", "swap"))
        # a wider cross-section
        for sym in ("ADAUSDT", "LINKUSDT", "TRXUSDT"):
            os.makedirs(os.path.join(fixture, "binance", "usdm", "klines", sym))
        # market-cap and liquidity reference datasets
        for d in ("market_cap", "turnover"):
            os.makedirs(os.path.join(fixture, "_ref", d))
            with open(os.path.join(fixture, "_ref", d, "reference.json"), "w",
                      encoding="utf-8") as f:
                f.write("{}\n")
        # a mark/index price series
        os.makedirs(os.path.join(fixture, "binance", "usdm", "mark_price", "BTCUSDT"))
        # listing-history / contract-availability fields on the instrument export
        inst = os.path.join(fixture, "binance", "usdm", "instruments",
                            "usdm-perp-instruments.json")
        doc = _load_json(inst)
        for item in doc["instruments"]:
            item["fields"]["onboardDate"] = 1598252400000
            item["fields"]["deliveryDate"] = None
        with open(inst, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1)
        res = run_checks(results_root, fixture)
        failed = [c["id"] for c in res["checks"] if c["status"] == "FAIL"]
        expected = {"C2", "C3", "C4", "C5", "C6"}
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
        f.write("{}\n")


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
