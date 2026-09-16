#!/usr/bin/env python3
"""Prerequisite-gate read-back checker for the family

    crypto-cross-sectional-same-weekday-seasonality-20w-daily-2026-08-31

Card t_62361906 terminalized this family as TECHNICAL_INCOMPLETE because the
record's required data is a *point-in-time cryptocurrency cross-section*: a
point-in-time universe (membership + listing status, delisted/failed assets
retained), daily closes, daily market capitalization, daily dollar volume /
turnover, and an explicit timezone/daily-boundary convention that is TESTED
against at least one alternative venue-native boundary. The canonical raw holds
four fixed BINANCE USD-M perpetual contracts (BNBUSDT/BTCUSDT/ETHUSDT/SOLUSDT)
of one venue - exactly the "today's survivor list" the record forbids
substituting - so the eligible cross-section the registered signal ranks cannot
be formed (4 assets < 5 quintile buckets < the source's 20-per-day minimum).

This script exists so an independent reader can re-derive that determination
from the live filesystem instead of trusting prose:

  * it re-measures the canonical raw (market directories, instrument set, kline
    row shape and UTC boundary, funding shape, name probes for the missing
    universe / market-cap / turnover / listing datasets, and the stored schema's
    own statements) - the measurement side;
  * it re-reads the one machine-local point-in-time membership product and
    asserts it is not usable (all cells unknown/unsupported, DATA_BLOCKED);
  * it re-reads the round's immutable artifacts and asserts they state exactly
    the contract-mandated terminal values (verdict, layer, class, yield
    decision, zero attempts, null run_id) and that the registered universe is
    still the record's own point-in-time cross-section, not a four-contract
    fallback;
  * it asserts nothing was ever submitted (no attempt directory, no terminal
    sentinel) so a "0 attempts" claim cannot quietly become a fabricated run;
  * it asserts the DCA registration still carries the contract 7.2 v1.3.1
    provenance classes plus the complete 48-cell product.

Read-only: it never writes inside the results tree or the raw tree. `--self-test`
builds tampered copies in fresh temp directories and asserts the checker refuses
them (non-vacuousness control), and `--raw-fixture-control` builds a temp raw
tree carrying what this record would need (a broader symbol set, a second venue,
a spot market, index prices, market-cap / turnover / universe-membership
reference datasets) and asserts the raw-side checks flip to FAIL. Both temp trees
are removed afterwards.

Usage:
    python3 runtime/crypto_xs_same_weekday_seasonality_prerequisite_check.py [--json]
    python3 runtime/crypto_xs_same_weekday_seasonality_prerequisite_check.py --self-test
    python3 runtime/crypto_xs_same_weekday_seasonality_prerequisite_check.py --raw-fixture-control

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
from collections import Counter
from datetime import datetime, timezone

# Provenance classes and the cartesian-product helper are reused from the
# existing registered validator rather than re-implemented here (contract 7.2 v1.3.1).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import strategy_a_v2_counts as _sav2
except ImportError:  # pragma: no cover - only if the sibling module is missing
    _sav2 = None

FAMILY = "crypto-cross-sectional-same-weekday-seasonality-20w-daily-2026-08-31"
ROUND = FAMILY + "-r1"
TASK = "t_62361906"
DEFAULT_RESULTS = "/Volumes/ExpansionDrive/qlib-results"
DEFAULT_RAW = "/Volumes/ExpansionDrive/market-data-raw"
PIT_PRODUCT = "/Users/hong/workspace/a1-1-phase9-pit-membership-20260830"
EXPECTED_SYMBOLS = ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
# The exact kline row shape the raw stores: six OHLCV fields plus the convenience
# close_time_ms. No quote volume, no trade count, no taker-buy splits.
KLINE_ROW_FIELDS = ["close", "close_time_ms", "high", "low", "open", "open_time_ms",
                    "volume"]
# Name tokens that would have to exist for this record's required data.
UNIVERSE_TOKENS = ("universe", "membership", "point_in_time", "pit_", "survivor",
                   "cross_section", "cross-sectional", "eligible")
LISTING_TOKENS = ("listing", "listed", "delist", "onboard", "trading_start", "first_trade",
                  "existence")
MARKET_CAP_TOKENS = ("market_cap", "marketcap", "mcap", "coinmarketcap", "coin_market_cap",
                     "circulating_supply", "total_supply", "supply")
TURNOVER_TOKENS = ("turnover", "dollar_volume", "quote_volume", "traded_value", "volume_usd",
                   "adv_", "notional_value")
# instrument-export field names that would identify a listing/supply/market-cap surface
INSTRUMENT_UNIVERSE_FIELDS = ("onboard", "listing", "listed", "delivery", "expiry",
                              "supply", "market_cap", "circulating")
INVARIANT_TOKENS = ("30,000", "numeraire", "leverage 10x", "12 tranches", "tranche #12",
                    "reduce-only", "flat/kill")
MISSING_DATA_MATRIX_ITEMS = (
    "universe_point_in_time", "daily_closes", "market_cap", "turnover_dollar_volume",
    "timestamps_boundary", "existence_listing_status", "filters_point_in_time",
    "delisting_handling", "venue_boundary_sensitivity", "cross_section_breadth")
QUINTILE_BUCKETS = 5
SOURCE_MIN_ASSETS_PER_DAY = 20


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


def _walk_paths(root, max_depth=4):
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


def _rows(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def measure_pit_product(root=PIT_PRODUCT):
    """Re-measure the machine-local point-in-time membership product."""
    out = {"path": root, "exists": os.path.isdir(root)}
    if not out["exists"]:
        return out
    cells_path = os.path.join(root, "membership_cells.jsonl")
    status, supported = Counter(), Counter()
    n = 0
    if os.path.exists(cells_path):
        with open(cells_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                c = json.loads(line)
                n += 1
                status[str(c.get("membership_status"))] += 1
                supported[str(c.get("supported"))] += 1
    out["cells_rows"] = n
    out["membership_status_histogram"] = dict(status)
    out["supported_histogram"] = dict(supported)
    census = os.path.join(root, "coverage_census.json")
    if os.path.exists(census):
        out["classification"] = _load_json(census).get("classification")
        out["aggregate"] = _load_json(census).get("aggregate")
    out["all_unknown"] = bool(n) and set(status) <= {"unknown"}
    out["none_supported"] = bool(n) and set(supported) <= {"False"}
    out["usable"] = bool(n) and not (out["all_unknown"] and out["none_supported"])
    return out


def measure_raw(raw):
    """Independent re-measurement of the canonical raw. No artifact is trusted."""
    m = {}
    m["binance_market_dirs"] = _dirs(os.path.join(raw, "binance"))
    m["binance_usdm_subdirs"] = _dirs(os.path.join(raw, "binance", "usdm"))
    m["raw_paths"] = sorted(p for p in _walk_paths(raw, 3) if p != ".")
    m["paths_named_other_market"] = [p for p in _walk_paths(raw, 4)
                                     if os.path.basename(p).lower()
                                     in ("spot", "margin", "options", "inverse", "coinm",
                                         "delivery", "futures", "quarter")]
    m["non_binance_paths"] = [p for p in _walk_paths(raw, 3)
                              if p != "." and not p.startswith("binance")
                              and not p.startswith("_")]
    cfg_path = os.path.join(raw, "_meta", "CONFIG.json")
    cfg = _load_json(cfg_path) if os.path.exists(cfg_path) else {}
    m["venue"] = cfg.get("venue")
    m["market_type"] = cfg.get("market_type")
    m["symbols"] = sorted(cfg.get("symbols") or [])
    m["config_intervals"] = sorted(cfg.get("intervals") or [])
    m["klines_end_rule"] = cfg.get("klines_end_rule")

    kl = os.path.join(raw, "binance", "usdm", "klines")
    ud = os.path.join(raw, "binance", "usdm")
    m["klines_dataset_dirs"] = _dirs(kl)
    m["klines_interval_set"] = _dirs(os.path.join(kl, "BTCUSDT"))
    d1 = sorted(os.listdir(os.path.join(kl, "BTCUSDT", "1d"))) if os.path.isdir(
        os.path.join(kl, "BTCUSDT", "1d")) else []
    d1 = [f for f in d1 if f.endswith(".jsonl.gz")]
    if d1:
        rows1 = _rows(os.path.join(kl, "BTCUSDT", "1d", d1[0]))
        opens = [r["open_time_ms"] for r in rows1]
        m["klines_row_field_set"] = sorted(rows1[-1].keys())
        m["klines_1d_first_open_utc"] = _iso(opens[0])
        m["klines_1d_first_open_mod_86400_seconds"] = [o % 86400000 // 1000 for o in opens[:3]]
        m["klines_1d_open_step_seconds"] = sorted(Counter(
            (opens[i + 1] - opens[i]) // 1000 for i in range(len(opens) - 1)).keys())[:4]
        last_rows = _rows(os.path.join(kl, "BTCUSDT", "1d", d1[-1]))
        m["klines_1d_last_open_utc"] = _iso(last_rows[-1]["open_time_ms"])
    dw = sorted(os.listdir(os.path.join(kl, "BTCUSDT", "1w"))) if os.path.isdir(
        os.path.join(kl, "BTCUSDT", "1w")) else []
    dw = [f for f in dw if f.endswith(".jsonl.gz")]
    if dw:
        rows_w = _rows(os.path.join(kl, "BTCUSDT", "1w", dw[0]))
        first_open = rows_w[0]["open_time_ms"]
        m["klines_1w_first_open_utc"] = _iso(first_open)
        m["klines_1w_first_open_weekday"] = datetime.fromtimestamp(
            first_open / 1000, tz=timezone.utc).strftime("%a")
    m["cross_section_size"] = len(m["klines_dataset_dirs"] or [])

    fu_root = os.path.join(ud, "funding")
    m["funding_symbol_dirs"] = _dirs(fu_root)
    p = os.path.join(fu_root, "BTCUSDT", "BTCUSDT-funding.jsonl.gz")
    if os.path.exists(p):
        rs = _rows(p)
        m["funding_row_keys"] = sorted(rs[-1].keys())
        m["funding_venues"] = sorted({str(r.get("venue")) for r in rs})

    inst_path = os.path.join(ud, "instruments", "usdm-perp-instruments.json")
    inst = _load_json(inst_path).get("instruments", []) if os.path.exists(inst_path) else []
    m["instrument_count"] = len(inst)
    m["instrument_types"] = sorted({i["fields"].get("type") for i in inst})
    m["instrument_quote_currencies"] = sorted({i["fields"].get("quote_currency")
                                               for i in inst if i["fields"].get("quote_currency")})
    m["instrument_settlement_currencies"] = sorted({i["fields"].get("settlement_currency")
                                                    for i in inst
                                                    if i["fields"].get("settlement_currency")})
    m["instrument_field_names"] = sorted({k for i in inst for k in i["fields"]})
    m["instrument_universe_field_hits"] = sorted(
        f for f in m["instrument_field_names"]
        if any(t in f.lower() for t in INSTRUMENT_UNIVERSE_FIELDS))

    schema_path = os.path.join(raw, "_meta", "SCHEMA.md")
    schema = open(schema_path, encoding="utf-8").read() if os.path.exists(schema_path) else ""
    flat = schema.replace("*", "")
    m["schema_utc_documented"] = ("open_time_ms` is the bar open time in epoch ms, UTC" in schema
                                  or "open_time_ms is the bar open time in epoch ms, UTC" in flat)
    m["schema_documents_missing_fields"] = ("quote_volume" in flat
                                            and "trade count and taker-buy splits are" in flat)
    m["schema_single_venue"] = "Binance USD-M perpetual futures, UTC" in schema
    m["schema_coverage_limit"] = ("Known coverage limit" in schema
                                  and "no gaps are silently filled" in schema.lower())
    m["schema_no_silent_gapfill"] = "No gaps are silently filled" in schema

    names = _all_entries(raw, 4)
    joined = "\n".join(names)
    m["universe_tokens_found"] = sorted({t for t in UNIVERSE_TOKENS if t in joined})
    m["listing_tokens_found"] = sorted({t for t in LISTING_TOKENS if t in joined})
    m["market_cap_tokens_found"] = sorted({t for t in MARKET_CAP_TOKENS if t in joined})
    m["turnover_tokens_found"] = sorted({t for t in TURNOVER_TOKENS if t in joined})

    m["meta_sha256"] = {}
    for rel in ("CONFIG.json", "SCHEMA.md", "INSTRUMENTS_EXPORT.json"):
        p = os.path.join(raw, "_meta", rel)
        if os.path.exists(p):
            m["meta_sha256"][rel] = _sha256_file(p)
    m["pit_product"] = measure_pit_product()
    m["note"] = ("single venue (BINANCE USD-M perpetual), four fixed survivor contracts: "
                 "klines whose rows carry the six-field OHLCV shape only, plus that venue's "
                 "funding. No point-in-time universe, no market capitalization and no "
                 "dollar-volume/turnover series - so the record's eligible cross-section (and "
                 "with it the SEAS quintile long-short spread) cannot be constructed.")
    return m


def run_checks(results_root, raw_root):
    checks = []
    add = lambda cid, ok, detail: checks.append({"id": cid, "status": "PASS" if ok else "FAIL",
                                                 "detail": detail})
    raw = measure_raw(raw_root)
    round_dir = os.path.join(results_root, FAMILY, "rounds", ROUND)

    # C1 - one market type for one venue, and no other market directory at all
    #      (no spot, no dated futures, no second venue).
    md = raw["binance_market_dirs"] or []
    add("C1", md == ["usdm"] and raw["venue"] == "BINANCE" and raw["market_type"] == "usdm_perp"
        and raw["paths_named_other_market"] == [] and raw["non_binance_paths"] == [],
        "binance market dirs=%s venue=%s market_type=%s other_market_paths=%s "
        "non_binance_paths=%s" % (md, raw["venue"], raw["market_type"],
                                  raw["paths_named_other_market"], raw["non_binance_paths"]))

    # C2 - the four USD-M perpetual contracts are the only instruments present: the
    #      raw's universe is exactly a four-name survivor list.
    same = (raw["klines_dataset_dirs"] == EXPECTED_SYMBOLS
            and raw["funding_symbol_dirs"] == EXPECTED_SYMBOLS
            and raw["symbols"] == EXPECTED_SYMBOLS
            and raw["instrument_types"] == ["CryptoPerpetual"]
            and raw["instrument_count"] == len(EXPECTED_SYMBOLS)
            and raw["instrument_quote_currencies"] == ["USDT"])
    add("C2", bool(same), "klines=%s funding=%s config=%s types=%s count=%s quote=%s"
        % (raw["klines_dataset_dirs"], raw["funding_symbol_dirs"], raw["symbols"],
           raw["instrument_types"], raw["instrument_count"],
           raw["instrument_quote_currencies"]))

    # C3 - the decisive one: there is no point-in-time universe locally - neither a
    #      membership/listing surface under the raw root nor a usable machine-local
    #      membership product.
    pit = raw["pit_product"]
    pit_ok = (not pit.get("exists")) or (pit.get("cells_rows") and pit.get("all_unknown")
                                         and pit.get("none_supported")
                                         and pit.get("classification") == "DATA_BLOCKED")
    add("C3", raw["universe_tokens_found"] == [] and raw["listing_tokens_found"] == []
        and raw["instrument_universe_field_hits"] == [] and bool(pit_ok),
        "universe_tokens=%s listing_tokens=%s instrument_listing_fields=%s pit_product=%s "
        "(the record requires a point-in-time universe; the raw is a fixed survivor list)"
        % (raw["universe_tokens_found"], raw["listing_tokens_found"],
           raw["instrument_universe_field_hits"],
           {"exists": pit.get("exists"), "cells_rows": pit.get("cells_rows"),
            "all_unknown": pit.get("all_unknown"), "none_supported": pit.get("none_supported"),
            "classification": pit.get("classification")}))

    # C4 - market capitalization is absent (needed by the value-weighted variant and
    #      the size filters named by falsification item 2).
    add("C4", raw["market_cap_tokens_found"] == []
        and not any("supply" in f.lower() or "cap" in f.lower()
                    for f in raw["instrument_field_names"]),
        "market_cap_tokens=%s instrument_supply_or_cap_fields=%s"
        % (raw["market_cap_tokens_found"],
           [f for f in raw["instrument_field_names"]
            if "supply" in f.lower() or "cap" in f.lower()]))

    # C5 - dollar volume / turnover is not a field of the stored data: the kline rows
    #      are the six-field OHLCV shape and the schema documents the gap.
    add("C5", raw["turnover_tokens_found"] == []
        and raw.get("klines_row_field_set") == KLINE_ROW_FIELDS
        and raw["schema_documents_missing_fields"] is True,
        "turnover_tokens=%s kline_row_fields=%s schema_documents_missing_fields=%s"
        % (raw["turnover_tokens_found"], raw.get("klines_row_field_set"),
           raw["schema_documents_missing_fields"]))

    # C6 - daily closes DO exist with an explicit UTC boundary (recorded as present),
    #      measured on the live files rather than restated from the config.
    add("C6", raw.get("klines_1d_first_open_mod_86400_seconds") == [0, 0, 0]
        and raw.get("klines_1d_open_step_seconds") == [86400]
        and raw.get("klines_1d_first_open_utc") == "2022-01-01T00:00:00+00:00"
        and raw.get("klines_1w_first_open_weekday") == "Mon"
        and raw["schema_utc_documented"] is True
        and raw["schema_single_venue"] is True
        and "UTC" in str(raw.get("klines_end_rule")),
        "1d_first_open=%s mod86400=%s step=%s 1w_weekday=%s schema_utc=%s end_rule=%s"
        % (raw.get("klines_1d_first_open_utc"), raw.get("klines_1d_first_open_mod_86400_seconds"),
           raw.get("klines_1d_open_step_seconds"), raw.get("klines_1w_first_open_weekday"),
           raw["schema_utc_documented"], raw.get("klines_end_rule")))

    # C7 - the breadth is structurally insufficient: 4 assets cannot fill five
    #      quintile buckets, let alone the source's >=20-assets-per-day minimum.
    add("C7", raw["cross_section_size"] == len(EXPECTED_SYMBOLS)
        and raw["cross_section_size"] < QUINTILE_BUCKETS <= SOURCE_MIN_ASSETS_PER_DAY,
        "cross_section_size=%s quintile_buckets=%d source_min_assets_per_day=%d"
        % (raw["cross_section_size"], QUINTILE_BUCKETS, SOURCE_MIN_ASSETS_PER_DAY))

    # ---- artifact side
    spec_path = os.path.join(round_dir, "round-spec.json")
    verdict_path = os.path.join(round_dir, "verdict.json")
    spec = _load_json(spec_path) if os.path.exists(spec_path) else {}
    verdict = _load_json(verdict_path) if os.path.exists(verdict_path) else {}

    # C8 - the registration still states the record's own point-in-time cross-section
    #      (it was NOT shrunk to the locally available four-contract list) and records
    #      the missing surfaces as unavailable.
    gate = spec.get("prerequisite_gate") or {}
    univ = spec.get("universe_registration") or {}
    sig = spec.get("signal_semantics") or {}
    meas = gate.get("measured_available") or {}
    matrix = {i.get("item"): i for i in (gate.get("required_data_matrix") or [])
              if isinstance(i, dict)}
    probes = meas.get("name_probe_hits") or {}
    pit_m = meas.get("pit_membership_product") or {}
    ok8 = (
        spec.get("family_id") == FAMILY and spec.get("round_id") == ROUND
        and spec.get("kanban_task_id") == TASK
        and gate.get("outcome") == "PREREQUISITE_MISSING"
        and gate.get("verdict") == "TECHNICAL_INCOMPLETE"
        and gate.get("required_data_available") is False
        and gate.get("attempts_launched") == 0
        and set(MISSING_DATA_MATRIX_ITEMS) <= set(matrix)
        and all(matrix[k].get("status") for k in MISSING_DATA_MATRIX_ITEMS)
        and matrix["universe_point_in_time"].get("status") == "ABSENT"
        and matrix["market_cap"].get("status") == "ABSENT"
        and matrix["turnover_dollar_volume"].get("status") == "ABSENT_AS_A_FIELD"
        and matrix["cross_section_breadth"].get("status") == "STRUCTURALLY_INSUFFICIENT"
        and univ.get("universe_shrunk_to_local_list") is False
        and univ.get("cross_section_available") == len(EXPECTED_SYMBOLS)
        and univ.get("cross_section_required_minimum") == SOURCE_MIN_ASSETS_PER_DAY
        and univ.get("quintile_buckets") == QUINTILE_BUCKETS
        and univ.get("quintile_sort_structurally_possible") is False
        and univ.get("point_in_time_membership_available") is False
        and univ.get("listing_status_history_available") is False
        and univ.get("market_cap_available") is False
        and univ.get("dollar_volume_turnover_available") is False
        and univ.get("delisted_assets_present") is False
        and univ.get("boundary_definitions_required") == 2
        and univ.get("boundary_definitions_available") == 1
        and sig.get("quintile_sort_structurally_possible") is False
        and sig.get("cross_section_available") == len(EXPECTED_SYMBOLS)
        and sig.get("cross_section_required_minimum") == SOURCE_MIN_ASSETS_PER_DAY
        and (spec.get("expected") or {}).get("status") == "not_computable"
        and (spec.get("strategy_domain") or {}).get("status") == "not_registered"
        and (spec.get("launch") or {}).get("attempts_launched") == 0
        and meas.get("cross_section_size") == len(EXPECTED_SYMBOLS)
        and all(v == [] for v in probes.values()) and bool(probes)
        and pit_m.get("classification") == "DATA_BLOCKED"
        and (pit_m.get("aggregate") or {}).get("supported_cells") == 0
    )
    add("C8", bool(ok8),
        "round-spec ids/gate=%s required_available=%s cross_section=%s/%s quintile_possible=%s "
        "shrunk=%s pit_class=%s matrix_items=%d probes_empty=%s"
        % (gate.get("outcome"), gate.get("required_data_available"),
           univ.get("cross_section_available"), univ.get("cross_section_required_minimum"),
           univ.get("quintile_sort_structurally_possible"),
           univ.get("universe_shrunk_to_local_list"), pit_m.get("classification"),
           len(matrix), all(v == [] for v in probes.values()) if probes else None))

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
    #       complete 48-cell product (a searched axis is never declared as a user-fixed
    #       invariant).
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
        "universe_shrunk_to_the_local_four_contract_list": lambda d: _tamper_spec(d, lambda s: s[
            "universe_registration"].update({
                "universe_shrunk_to_local_list": True,
                "cross_section_required_minimum": 4,
                "quintile_sort_structurally_possible": True})),
        "required_data_available_flipped_true": lambda d: _tamper_spec(d, lambda s: s[
            "prerequisite_gate"].update({
                "required_data_available": True, "outcome": "PREREQUISITE_PRESENT",
                "missing": []})),
        "market_cap_status_flipped_present": lambda d: _tamper_spec(d, lambda s: s[
            "prerequisite_gate"]["required_data_matrix"][2].update({"status": "PRESENT"})),
        "pit_product_claimed_usable": lambda d: _tamper_spec(d, lambda s: s[
            "prerequisite_gate"]["measured_available"]["pit_membership_product"].update(
                {"classification": "SUPPORTED"})),
        "searched_axis_mislabelled_user_fixed": lambda d: _tamper_spec(
            d, lambda s: s["dca_domain"].update({"spacing_pct_status": "USER_FIXED"})),
        "dca_grid_truncated_to_47": lambda d: _tamper_spec(
            d, lambda s: s["dca_domain"]["grid"].pop()),
    }
    results = []
    ok = True
    for name, mutate in variants.items():
        tmp = tempfile.mkdtemp(prefix="xq-seasonality-prereq-selftest-")
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

    Build a temp raw tree that carries what this record would need: a second venue, a spot
    market on the same venue, a broader symbol set, index prices, and market-cap /
    turnover / universe-membership reference datasets. The raw-side checks must flip to FAIL.
    """
    tmp = tempfile.mkdtemp(prefix="xq-seasonality-prereq-rawfix-")
    try:
        fixture = os.path.join(tmp, "raw")
        shutil.copytree(raw_root, fixture,
                        ignore=shutil.ignore_patterns(".DS_Store", "__pycache__"))
        # a second venue and a spot market on the same venue
        os.makedirs(os.path.join(fixture, "okx", "swap"))
        os.makedirs(os.path.join(fixture, "binance", "spot"))
        # a broader symbol set (the record's cross-section starts at 20/day)
        extra = ["APTUSDT", "ARBUSDT", "ATOMUSDT", "DOTUSDT", "FILUSDT", "INJUSDT",
                 "NEARUSDT", "OPUSDT", "SUIUSDT", "TONUSDT", "TRXUSDT", "WIFUSDT",
                 "AAVEUSDT", "APT2USDT", "SEIUSDT", "TIAUSDT", "UNIUSDT", "PEPEUSDT",
                 "ORDIUSDT", "FTMUSDT"]
        for sym in extra:
            d = os.path.join(fixture, "binance", "usdm", "klines", sym, "1d")
            os.makedirs(d)
            with gzip.GzipFile(os.path.join(d, "%s-1d-2022-01.jsonl.gz" % sym),
                               "wb", mtime=0) as gz:
                gz.write(json.dumps({"open_time_ms": 1640995200000, "close_time_ms": 1641081599999,
                                     "open": "1.0", "high": "1.0", "low": "1.0",
                                     "close": "1.0", "volume": "1.0"}).encode() + b"\n")
            os.makedirs(os.path.join(fixture, "binance", "usdm", "funding", sym))
        # index / reference prices for the CEX leg
        os.makedirs(os.path.join(fixture, "binance", "usdm", "index_price"))
        with gzip.GzipFile(os.path.join(fixture, "binance", "usdm", "index_price",
                                       "index_price.jsonl.gz"), "wb", mtime=0) as gz:
            gz.write(json.dumps({"index_price": "1.0"}).encode() + b"\n")
        # the datasets this record would need, as reference trees
        ref = {
            "market_cap": "market_cap_daily.jsonl.gz",
            "turnover": "quote_volume_daily.jsonl.gz",
            "universe_membership": "universe_membership_daily.jsonl.gz",
            "listing_history": "listing_history.jsonl.gz",
        }
        for d, f in ref.items():
            os.makedirs(os.path.join(fixture, "_ref", d))
            with gzip.GzipFile(os.path.join(fixture, "_ref", d, f), "wb", mtime=0) as gz:
                gz.write(json.dumps({"symbol": "BTCUSDT", d: "1.0"}).encode() + b"\n")
        res = run_checks(results_root, fixture)
        failed = [c["id"] for c in res["checks"] if c["status"] == "FAIL"]
        expected = {"C1", "C2", "C3", "C4", "C5"}
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
