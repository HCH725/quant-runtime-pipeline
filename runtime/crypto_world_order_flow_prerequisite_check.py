#!/usr/bin/env python3
"""Prerequisite-gate read-back checker for the family

    crypto-world-order-flow-cross-sectional-quintile-weekly-2026-08-31

Card t_619d69e1 terminalized this family as TECHNICAL_INCOMPLETE because the
record's required data - signed buyer-initiated / seller-initiated volume by
fiat denomination aggregated across many venues (CryptoCompare), CoinMarketCap
daily USD prices, an 84-coin-or-analogue cross-section with market-capitalization
and stablecoin classification, and trading-availability history - is absent from
the canonical raw. The raw holds four BINANCE USD-M perpetual contracts whose
klines carry the six-field OHLCV shape only (no quote_volume, no trade count, no
taker-buy split), so the signal itself - signed order flow - cannot be formed.

This script exists so an independent reader can re-derive that determination from
the live filesystem instead of trusting prose:

  * it re-measures the canonical raw (venue/market directories, contract set,
    cross-section size, the exact kline row field set, fiat-channel and
    aggressor-flow name probes, vendor datasets, market structure datasets,
    availability history, the stored weekly-bar convention and the perpetual
    add-on surfaces) - the measurement side;
  * it re-reads the round's immutable artifacts and asserts they state exactly
    the contract-mandated terminal values (verdict, layer, class, yield decision,
    zero attempts, null run_id) and that the registered universe is still the
    record's own 84-coin-or-documented-analogue requirement with a registered
    five-quintile sort, not a shrunk one;
  * it asserts nothing was ever submitted (no attempt directory, no terminal
    sentinel) so a "0 attempts" claim cannot quietly become a fabricated run;
  * it asserts the DCA registration still carries the contract 7.2 v1.3.1
    provenance classes plus the complete 48-cell product.

Read-only: it never writes inside the results tree or the raw tree. `--self-test`
builds tampered copies in fresh temp directories and asserts the checker refuses
them (non-vacuousness control), and `--raw-fixture-control` builds a temp raw tree
carrying what this record would need (a spot market, a second venue, a wider
cross-section, kline rows that actually carry aggressor-side and quote-volume
fields, multi-fiat flow references, vendor datasets, market-cap/stablecoin
reference data and a Saturday-anchored weekly bar) and asserts the raw-side checks
flip to FAIL. Both temp trees are removed afterwards.

Usage:
    python3 runtime/crypto_world_order_flow_prerequisite_check.py [--json]
    python3 runtime/crypto_world_order_flow_prerequisite_check.py --self-test
    python3 runtime/crypto_world_order_flow_prerequisite_check.py --raw-fixture-control

Exit codes: 0 = all checks PASS, 1 = at least one FAIL, 2 = usage error.
"""
import argparse
import datetime
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

FAMILY = "crypto-world-order-flow-cross-sectional-quintile-weekly-2026-08-31"
ROUND = FAMILY + "-r1"
TASK = "t_619d69e1"
DEFAULT_RESULTS = "/Volumes/ExpansionDrive/qlib-results"
DEFAULT_RAW = "/Volumes/ExpansionDrive/market-data-raw"
EXPECTED_SYMBOLS = ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
# The record normalizes a five-quintile cross-sectional sort: P1..P5 need at
# least one name per bucket, so fewer than five coins cannot carry it at all.
QUINTILE_COUNT = 5
# The published 2026 article's balanced panel is 84 coins (the EFMA working paper
# says 82); either way the registered universe is a wide cross-section of names.
PANEL_SIZE = 84
# The exact kline row shape the raw stores: six OHLCV fields plus the convenience
# close_time_ms. No aggressor-side split, no quote volume, no trade count.
KLINE_ROW_FIELDS = ["close", "close_time_ms", "high", "low", "open", "open_time_ms",
                    "volume"]
# Basename tokens that would have to exist for this record's required data.
FLOW_TOKENS = ("taker", "aggressor", "buy_volume", "sell_volume", "buyvolume", "sellvolume",
               "signed", "orderflow", "order_flow", "netflow", "buy_base", "sell_base",
               "buyer", "seller", "buyer_initiated", "seller_initiated")
FIAT_TOKENS = ("eur", "gbp", "jpy", "chf", "cad", "aud", "nzd", "nok", "sek", "krw", "fiat")
VENDOR_TOKENS = ("coinmarketcap", "cryptocompare", "ccdata", "kaiko", "coingecko", "messari")
MARKET_STRUCTURE_TOKENS = ("market_cap", "marketcap", "mcap", "circulating", "supply",
                           "stablecoin", "stable_coin")
AVAILABILITY_TOKENS = ("listing", "delisting", "onboard", "universe", "membership",
                       "availability", "trading_history")
LISTING_FIELD_TOKENS = ("onboarddate", "listingdate", "listed_at", "launchtime", "listing",
                        "delisting", "delist", "deliverydate", "expiry")
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


def _iso_utc(ms):
    return datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc).isoformat()


def _first_row(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                return json.loads(line)
    return {}


def _first_and_last_rows(path):
    rows = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        rows = [json.loads(x) for x in f if x.strip()]
    return rows[0], rows[-1]


def measure_raw(raw):
    """Independent re-measurement of the canonical raw. No artifact is trusted."""
    m = {}
    m["binance_market_dirs"] = _dirs(os.path.join(raw, "binance"))
    all_paths = _walk_paths(raw, 4)
    m["paths_named_spot"] = [p for p in all_paths if os.path.basename(p).lower() == "spot"]
    m["non_binance_paths"] = [p for p in _walk_paths(raw, 3)
                              if p != "." and not p.startswith("binance")
                              and not p.startswith("_")]
    cfg_path = os.path.join(raw, "_meta", "CONFIG.json")
    cfg = _load_json(cfg_path) if os.path.exists(cfg_path) else {}
    m["market_type"] = cfg.get("market_type")
    m["venue"] = cfg.get("venue")
    m["symbols"] = sorted(cfg.get("symbols") or [])
    m["config_intervals"] = sorted(cfg.get("intervals") or [])
    m["cross_section_size"] = len(m["symbols"])

    kl = os.path.join(raw, "binance", "usdm", "klines")
    m["klines_dataset_dirs"] = _dirs(kl)
    m["klines_intervals"] = _dirs(os.path.join(kl, "BTCUSDT"))
    d1 = sorted(os.listdir(os.path.join(kl, "BTCUSDT", "1d"))) if os.path.isdir(
        os.path.join(kl, "BTCUSDT", "1d")) else []
    w1 = sorted(os.listdir(os.path.join(kl, "BTCUSDT", "1w"))) if os.path.isdir(
        os.path.join(kl, "BTCUSDT", "1w")) else []
    if d1:
        first, last = _first_and_last_rows(os.path.join(kl, "BTCUSDT", "1d", d1[0]))
        m["klines_1d_first_open_utc"] = _iso_utc(first["open_time_ms"])
        m["klines_1d_last_open_utc"] = _iso_utc(last["open_time_ms"])
    if w1:
        wfirst = _first_row(os.path.join(kl, "BTCUSDT", "1w", w1[0]))
        m["klines_1w_first_open_utc"] = _iso_utc(wfirst["open_time_ms"])
        m["klines_1w_first_open_weekday"] = datetime.datetime.fromtimestamp(
            wfirst["open_time_ms"] / 1000, tz=datetime.timezone.utc).strftime("%a")
    if d1:
        m["klines_row_field_set"] = sorted(_first_row(
            os.path.join(kl, "BTCUSDT", "1d", d1[0])).keys())

    funding_dir = os.path.join(raw, "binance", "usdm", "funding")
    m["funding_symbol_dirs"] = _dirs(funding_dir)
    fund_file = os.path.join(funding_dir, "BTCUSDT", "BTCUSDT-funding.jsonl.gz")
    if os.path.exists(fund_file):
        with gzip.open(fund_file, "rt", encoding="utf-8") as f:
            row = json.loads(f.readline())
        m["funding_row_keys"] = sorted(row.keys())
        m["funding_row_mark_price_present"] = "mark_price" in row
        m["funding_truth_status_field_present"] = "truth_status" in row

    inst_path = os.path.join(raw, "binance", "usdm", "instruments",
                             "usdm-perp-instruments.json")
    inst_text = open(inst_path, encoding="utf-8").read() if os.path.exists(inst_path) else ""
    inst = json.loads(inst_text or "{}").get("instruments", [])
    m["instrument_types"] = sorted({i["fields"]["type"] for i in inst})
    m["instrument_count"] = len(inst)
    m["instrument_field_names"] = sorted({k for i in inst for k in i["fields"]})
    m["instrument_quote_currencies"] = sorted({i["fields"].get("quote_currency")
                                               for i in inst if i["fields"].get("quote_currency")})
    m["listing_history_field_present"] = any(t in inst_text.lower() for t in LISTING_FIELD_TOKENS)

    schema_path = os.path.join(raw, "_meta", "SCHEMA.md")
    schema_text = open(schema_path, encoding="utf-8").read() if os.path.exists(schema_path) else ""
    schema_flat = schema_text.replace("*", "")
    m["schema_documents_missing_fields"] = (
        "quote_volume" in schema_flat and "not" in schema_flat
        and "taker-buy splits" in schema_flat)

    names = _all_entries(raw, 4)
    m["flow_tokens_found"] = sorted({t for n in names for t in FLOW_TOKENS if t in n})
    m["fiat_tokens_found"] = sorted({t for n in names for t in FIAT_TOKENS if t in n})
    m["vendor_tokens_found"] = sorted({t for n in names for t in VENDOR_TOKENS if t in n})
    m["market_structure_tokens_found"] = sorted({t for n in names
                                                 for t in MARKET_STRUCTURE_TOKENS if t in n})
    m["availability_tokens_found"] = sorted({t for n in names
                                             for t in AVAILABILITY_TOKENS if t in n})

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
    if os.path.exists(inst_path):
        m["meta_sha256"]["binance/usdm/instruments/usdm-perp-instruments.json"] = \
            _sha256_file(inst_path)
    m["note"] = ("single venue (BINANCE USD-M perpetual), 4 fixed contracts, klines whose "
                 "rows carry the six-field OHLCV shape only (no taker/aggressor split, no "
                 "quote_volume, no trade count, documented in _meta/SCHEMA.md); no fiat "
                 "denomination channel, no CryptoCompare/CoinMarketCap-style vendor dataset, "
                 "no market-cap or stablecoin reference data, no trading-availability history, "
                 "no spot market and no second venue; the stored 1w bars open on Monday, not "
                 "on the record's Saturday 00:00 GMT week boundary")
    return m


def run_checks(results_root, raw_root):
    checks = []
    add = lambda cid, ok, detail: checks.append({"id": cid, "status": "PASS" if ok else "FAIL",
                                                 "detail": detail})
    raw = measure_raw(raw_root)
    round_dir = os.path.join(results_root, FAMILY, "rounds", ROUND)

    # C1 - the raw holds exactly one market type for one venue, and no spot market.
    md = raw["binance_market_dirs"] or []
    add("C1", md == ["usdm"] and raw["venue"] == "BINANCE" and raw["market_type"] == "usdm_perp"
        and raw["paths_named_spot"] == [] and raw["non_binance_paths"] == [],
        "binance market dirs=%s venue=%s market_type=%s paths_named_spot=%s "
        "non_binance_paths=%s" % (md, raw["venue"], raw["market_type"],
                                  raw["paths_named_spot"], raw["non_binance_paths"]))

    # C2 - the registered four USD-M perpetual contracts are the only instruments present.
    same = (raw["klines_dataset_dirs"] == EXPECTED_SYMBOLS
            and raw["funding_symbol_dirs"] == EXPECTED_SYMBOLS
            and raw["symbols"] == EXPECTED_SYMBOLS
            and raw["instrument_types"] == ["CryptoPerpetual"]
            and raw["instrument_count"] == len(EXPECTED_SYMBOLS))
    add("C2", bool(same),
        "klines=%s funding=%s config=%s types=%s count=%s" % (
            raw["klines_dataset_dirs"], raw["funding_symbol_dirs"], raw["symbols"],
            raw["instrument_types"], raw["instrument_count"]))

    # C3 - the decisive one: kline rows carry only the six-field OHLCV shape, so the
    #      record's signed aggressor-side flow cannot be formed, and the raw's own
    #      schema documents that quote_volume / trade count / taker-buy splits are absent.
    fields_ok = raw.get("klines_row_field_set") == KLINE_ROW_FIELDS
    add("C3", bool(fields_ok and raw["schema_documents_missing_fields"]
                   and raw["flow_tokens_found"] == []),
        "kline_row_fields=%s schema_documents_missing_fields=%s flow_tokens_found=%s"
        % (raw.get("klines_row_field_set"), raw["schema_documents_missing_fields"],
           raw["flow_tokens_found"]))

    # C4 - the cross-section cannot carry the registered five-quintile sort, and the
    #      universe-construction inputs (market cap, stablecoin classification,
    #      trading-availability history) do not exist.
    n = raw["cross_section_size"]
    add("C4", 0 < n < QUINTILE_COUNT
        and raw["market_structure_tokens_found"] == []
        and raw["availability_tokens_found"] == [],
        "cross_section=%d < quintiles=%d and < record panel=%d; market_structure_tokens=%s "
        "availability_tokens=%s" % (n, QUINTILE_COUNT, PANEL_SIZE,
                                    raw["market_structure_tokens_found"],
                                    raw["availability_tokens_found"]))

    # C5 - no fiat denomination channel: the only quote currency is USDT and no fiat
    #      token exists anywhere under the raw root.
    add("C5", raw["fiat_tokens_found"] == []
        and raw["instrument_quote_currencies"] == ["USDT"],
        "fiat_tokens_found=%s quote_currencies=%s" % (raw["fiat_tokens_found"],
                                                      raw["instrument_quote_currencies"]))

    # C6 - no multi-venue / vendor order-flow dataset (CryptoCompare, CoinMarketCap, ...).
    add("C6", raw["vendor_tokens_found"] == [],
        "vendor_tokens_found=%s" % raw["vendor_tokens_found"])

    # C7 - the stored weekly bars are Monday-anchored, i.e. not the record's
    #      Saturday 00:00 - Friday 23:59 GMT week; the registered weekly window is
    #      not native to any stored series.
    add("C7", raw.get("klines_1w_first_open_weekday") == "Mon",
        "1w first open=%s weekday=%s (registered week starts Saturday 00:00 GMT)"
        % (raw.get("klines_1w_first_open_utc"), raw.get("klines_1w_first_open_weekday")))

    # ---- artifact side
    spec_path = os.path.join(round_dir, "round-spec.json")
    verdict_path = os.path.join(round_dir, "verdict.json")
    spec = _load_json(spec_path) if os.path.exists(spec_path) else {}
    verdict = _load_json(verdict_path) if os.path.exists(verdict_path) else {}

    # C8 - the registration still states the record's own universe (the 84-coin balanced
    #      panel or a documented point-in-time analogue) with its five-quintile sort, is
    #      not the local four contracts, and records the signal input as unavailable.
    gate = spec.get("prerequisite_gate") or {}
    univ = spec.get("universe_registration") or {}
    sig = spec.get("signal_semantics") or {}
    ok8 = (
        spec.get("family_id") == FAMILY and spec.get("round_id") == ROUND
        and spec.get("kanban_task_id") == TASK
        and gate.get("outcome") == "PREREQUISITE_MISSING"
        and gate.get("verdict") == "TECHNICAL_INCOMPLETE"
        and gate.get("required_data_available") is False
        and gate.get("attempts_launched") == 0
        and univ.get("quintile_count_registered") == QUINTILE_COUNT
        and univ.get("balanced_panel_size_registered") == PANEL_SIZE
        and univ.get("cross_section_size_available") == raw["cross_section_size"]
        and univ.get("universe_shrunk_to_local_contracts") is False
        and "84" in str(univ.get("universe_requirement", ""))
        and sig.get("signed_order_flow_available") is False
        and (spec.get("expected") or {}).get("status") == "not_computable"
        and (spec.get("strategy_domain") or {}).get("status") == "not_registered"
        and (spec.get("launch") or {}).get("attempts_launched") == 0
    )
    add("C8", ok8, "round-spec ids/gate=%s required_available=%s quintiles=%s panel=%s "
        "cross_section_registered=%s shrunk=%s flow_available=%s"
        % (gate.get("outcome"), gate.get("required_data_available"),
           univ.get("quintile_count_registered"), univ.get("balanced_panel_size_registered"),
           univ.get("cross_section_size_available"),
           univ.get("universe_shrunk_to_local_contracts"),
           sig.get("signed_order_flow_available")))

    # C9 - the terminal verdict states the contract-mandated values.
    fail = verdict.get("failure") or {}
    yld = verdict.get("yield") or {}
    ok9 = (
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
    add("C9", ok9, "verdict=%s run_id=%s layer=%s class=%s yield=%s run_ids=%s" % (
        verdict.get("verdict"), verdict.get("run_id"), fail.get("layer"), fail.get("class"),
        yld.get("yield_decision"), verdict.get("evidence_run_ids")))

    # C10 - nothing was ever submitted: no attempt directory, no terminal sentinel.
    attempts_dir = os.path.join(round_dir, "attempts")
    sentinels = []
    for dp, dn, fn in os.walk(round_dir):
        for f in fn:
            if f in ("DONE", "FAILED", "INCOMPLETE"):
                sentinels.append(os.path.join(dp, f))
    add("C10", not os.path.exists(attempts_dir) and sentinels == [],
        "attempts_dir_exists=%s terminal_sentinels=%s" % (os.path.exists(attempts_dir),
                                                          sentinels))

    # C11 - ownership, and the round directory holds only the two immutable artifacts.
    fam_path = os.path.join(results_root, FAMILY, "family.json")
    fam = _load_json(fam_path) if os.path.exists(fam_path) else {}
    files = sorted(f for f in os.listdir(round_dir)) if os.path.isdir(round_dir) else None
    add("C11", fam.get("kanban_task_id") == TASK
        and files == ["round-spec.json", "verdict.json"],
        "family.kanban_task_id=%s round_dir_files=%s" % (fam.get("kanban_task_id"), files))

    # C12 - the DCA registration keeps its contract 7.2 v1.3.1 provenance classes and its
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
    add("C12", not probs, "dca provenance/grid+bounded invariants: %s"
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
                "universe_requirement": "BINANCE USD-M perpetual, "
                                        "BNBUSDT/BTCUSDT/ETHUSDT/SOLUSDT",
                "universe_shrunk_to_local_contracts": True})),
        "required_data_available_flipped_true": lambda d: _tamper_spec(d, lambda s: s[
            "prerequisite_gate"].update({
                "required_data_available": True, "outcome": "PREREQUISITE_PRESENT",
                "missing": []})),
        "signed_flow_flipped_available": lambda d: _tamper_spec(d, lambda s: s[
            "signal_semantics"].update({"signed_order_flow_available": True})),
        "searched_axis_mislabelled_user_fixed": lambda d: _tamper_spec(
            d, lambda s: s["dca_domain"].update({"spacing_pct_status": "USER_FIXED"})),
        "dca_grid_truncated_to_47": lambda d: _tamper_spec(
            d, lambda s: s["dca_domain"]["grid"].pop()),
    }
    results = []
    ok = True
    for name, mutate in variants.items():
        tmp = tempfile.mkdtemp(prefix="xq-orderflow-prereq-selftest-")
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


def _rewrite_rows(path, mutate):
    rows = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        rows = [json.loads(x) for x in f if x.strip()]
    rows = [mutate(r) for r in rows]
    with gzip.GzipFile(path, "wb", mtime=0) as gz:
        for r in rows:
            gz.write((json.dumps(r, sort_keys=True) + "\n").encode("utf-8"))


def raw_fixture_control(results_root, raw_root):
    """Measurement-side non-vacuousness control.

    Build a temp raw tree that carries what this record would need: a spot market, a
    second venue, a wider cross-section, kline rows that actually hold aggressor-side
    and quote-volume fields, a multi-fiat flow reference, vendor datasets, market-cap
    and stablecoin reference data, availability history and a Saturday-anchored weekly
    bar. The raw-side checks must flip to FAIL.
    """
    tmp = tempfile.mkdtemp(prefix="xq-orderflow-prereq-rawfix-")
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
        # kline rows that actually carry aggressor-side / quote-volume fields
        kl = os.path.join(fixture, "binance", "usdm", "klines", "BTCUSDT")
        d1 = sorted(os.listdir(os.path.join(kl, "1d")))[0]
        _rewrite_rows(os.path.join(kl, "1d", d1), lambda r: dict(
            r, taker_buy_base="1.0", taker_sell_base="1.0", quote_volume="1.0",
            trade_count=1))
        # market-cap / stablecoin / availability reference data and a fiat flow channel
        for d in ("market_cap", "stablecoin", "listing_history", "fiat_flow",
                  "cryptocompare", "coinmarketcap"):
            os.makedirs(os.path.join(fixture, "_ref", d))
            with open(os.path.join(fixture, "_ref", d, "reference.json"), "w",
                      encoding="utf-8") as f:
                f.write("{}\n")
        # a Saturday-anchored weekly bar (the record's week starts Saturday 00:00 GMT)
        w1 = sorted(os.listdir(os.path.join(kl, "1w")))[0]
        saturday_ms = 1640995200000  # 2022-01-01T00:00:00Z, a Saturday
        _rewrite_rows(os.path.join(kl, "1w", w1), lambda r: dict(r, open_time_ms=saturday_ms))
        # listing-history style instrument fields
        inst = os.path.join(fixture, "binance", "usdm", "instruments",
                            "usdm-perp-instruments.json")
        doc = _load_json(inst)
        for item in doc["instruments"]:
            item["fields"]["onboardDate"] = 1598252400000
            item["fields"]["listingDate"] = 1598252400000
        with open(inst, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=1)
        res = run_checks(results_root, fixture)
        failed = [c["id"] for c in res["checks"] if c["status"] == "FAIL"]
        expected = {"C1", "C2", "C3", "C4", "C5", "C6", "C7"}
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
