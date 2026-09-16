#!/usr/bin/env python3
"""Prerequisite-gate read-back checker for the family

    crypto-cex-dex-cross-venue-funding-spread-carry-2026-08-31

Card t_0d9309a8 terminalized this family as TECHNICAL_INCOMPLETE because the
record's required data is a *venue pair*: "perpetual futures / perpetual swaps
for the same underlying on at least two venues" with, for EVERY venue, the
funding rate, funding timestamps/interval/sign convention, mark and
index/reference price, executable bid/ask, liquidity surfaces, margin and
liquidation rules, and (for the DEX leg) gas, oracle mechanics, settlement and
protocol state. The canonical raw holds exactly one venue - BINANCE USD-M
perpetual, four contracts, OHLCV klines plus that venue's funding - so the
registered signal `s(i,t) = f_DEX(i,t) - f_CEX(i,t)` cannot be formed at all.

This script exists so an independent reader can re-derive that determination
from the live filesystem instead of trusting prose:

  * it re-measures the canonical raw (venue/market directories, instrument set,
    funding fields/cadence/venue identity, kline row shape, price fields,
    liquidity surfaces, margin/liquidation metadata, DEX-specific surfaces, and
    the stored schema's own statements) - the measurement side;
  * it re-reads the round's immutable artifacts and asserts they state exactly
    the contract-mandated terminal values (verdict, layer, class, yield
    decision, zero attempts, null run_id) and that the registered universe is
    still the record's own two-venue requirement, not a single-venue fallback;
  * it asserts nothing was ever submitted (no attempt directory, no terminal
    sentinel) so a "0 attempts" claim cannot quietly become a fabricated run;
  * it asserts the DCA registration still carries the contract 7.2 v1.3.1
    provenance classes plus the complete 48-cell product.

Read-only: it never writes inside the results tree or the raw tree. `--self-test`
builds tampered copies in fresh temp directories and asserts the checker refuses
them (non-vacuousness control), and `--raw-fixture-control` builds a temp raw
tree carrying what this record would need (a DEX venue with hourly funding, index
/reference prices, an order book, gas/oracle/tier references) and asserts the
raw-side checks flip to FAIL. Both temp trees are removed afterwards.

Usage:
    python3 runtime/crypto_cex_dex_funding_spread_prerequisite_check.py [--json]
    python3 runtime/crypto_cex_dex_funding_spread_prerequisite_check.py --self-test
    python3 runtime/crypto_cex_dex_funding_spread_prerequisite_check.py --raw-fixture-control

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

# Provenance classes and the cartesian-product helper are reused from the
# existing registered validator rather than re-implemented here (contract 7.2 v1.3.1).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import strategy_a_v2_counts as _sav2
except ImportError:  # pragma: no cover - only if the sibling module is missing
    _sav2 = None

FAMILY = "crypto-cex-dex-cross-venue-funding-spread-carry-2026-08-31"
ROUND = FAMILY + "-r1"
TASK = "t_0d9309a8"
DEFAULT_RESULTS = "/Volumes/ExpansionDrive/qlib-results"
DEFAULT_RAW = "/Volumes/ExpansionDrive/market-data-raw"
EXPECTED_SYMBOLS = ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
# The exact kline row shape the raw stores: six OHLCV fields plus the convenience
# close_time_ms. No aggressor-side split, no quote volume, no trade count.
KLINE_ROW_FIELDS = ["close", "close_time_ms", "high", "low", "open", "open_time_ms",
                    "volume"]
# Name tokens that would have to exist for this record's required data. The
# generic token "dex" is deliberately NOT probed: it is a substring of "index".
DEX_VENUE_TOKENS = ("dydx", "hyperliquid", "gmx", "paradex", "vertex_perp", "aevo",
                    "drift_protocol", "perpdex", "perp_dex", "dex_venue")
INDEX_PRICE_TOKENS = ("index_price", "indexprice", "reference_price", "spot_price",
                      "oracle_price")
ORDER_BOOK_TOKENS = ("orderbook", "order_book", "l2_book", "book_snapshot", "top_of_book",
                     "depth_snapshot", "bid_ask")
LIQUIDITY_TOKENS = ("slippage", "market_impact", "participation", "liquidity_depth",
                    "adv_notional")
DEX_COST_TOKENS = ("gas_fee", "gas_cost", "l1_fee", "tx_fee", "settlement_finality",
                   "protocol_status")
MARGIN_TOKENS = ("margin_tier", "maintenance_tier", "liquidation_fee", "collateral_asset",
                 "margin_mode", "partial_liquidation")
CEX_OPS_TOKENS = ("outage", "fee_tier", "transfer_constraint", "api_status")
SECOND_VENUE_TOKENS = ("okx", "okex", "bybit", "bitget", "coinbase", "kraken")
INVARIANT_TOKENS = ("30,000", "numeraire", "leverage 10x", "12 tranches", "tranche #12",
                    "reduce-only", "flat/kill")
MISSING_DATA_MATRIX_ITEMS = (
    "instrument_two_venue_perpetual", "universe_cex_leg", "venues_cex",
    "venues_dex", "funding_per_venue", "price_fields", "volatility", "liquidity",
    "margin_liquidation", "dex_specific", "cex_specific", "timestamp",
    "missing_data_policy")


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


def measure_raw(raw):
    """Independent re-measurement of the canonical raw. No artifact is trusted."""
    m = {}
    m["binance_market_dirs"] = _dirs(os.path.join(raw, "binance"))
    m["binance_usdm_subdirs"] = _dirs(os.path.join(raw, "binance", "usdm"))
    m["raw_paths"] = sorted(p for p in _walk_paths(raw, 3) if p != ".")
    m["paths_named_other_market"] = [p for p in _walk_paths(raw, 4)
                                     if os.path.basename(p).lower()
                                     in ("spot", "margin", "options", "inverse",
                                         "coinm", "delivery")]
    m["non_binance_paths"] = [p for p in _walk_paths(raw, 3)
                              if p != "." and not p.startswith("binance")
                              and not p.startswith("_")]
    cfg_path = os.path.join(raw, "_meta", "CONFIG.json")
    cfg = _load_json(cfg_path) if os.path.exists(cfg_path) else {}
    m["venue"] = cfg.get("venue")
    m["market_type"] = cfg.get("market_type")
    m["symbols"] = sorted(cfg.get("symbols") or [])
    m["config_intervals"] = sorted(cfg.get("intervals") or [])

    kl = os.path.join(raw, "binance", "usdm", "klines")
    ud = os.path.join(raw, "binance", "usdm")
    m["klines_dataset_dirs"] = _dirs(kl)
    m["klines_intervals"] = _dirs(os.path.join(kl, "BTCUSDT"))
    d1 = sorted(os.listdir(os.path.join(kl, "BTCUSDT", "1d"))) if os.path.isdir(
        os.path.join(kl, "BTCUSDT", "1d")) else []
    if d1:
        m["klines_row_field_set"] = sorted(
            _rows(os.path.join(kl, "BTCUSDT", "1d", d1[-1]))[-1].keys())

    fu_root = os.path.join(ud, "funding")
    m["funding_symbol_dirs"] = _dirs(fu_root)
    m["funding_per_symbol"] = {}
    for sym in (m["funding_symbol_dirs"] or []):
        p = os.path.join(fu_root, sym, "%s-funding.jsonl.gz" % sym)
        if not os.path.exists(p):
            continue
        rs = _rows(p)
        ts = [r["funding_time_ms"] for r in rs]
        gaps = Counter((ts[i + 1] - ts[i]) // 1000 for i in range(len(ts) - 1))
        m["funding_per_symbol"][sym] = {
            "rows": len(rs),
            "row_keys": sorted(rs[-1].keys()),
            "venues": sorted({str(r.get("venue")) for r in rs}),
            "cadence_seconds_top": [g for g, _ in gaps.most_common(3)],
            "truth_status": dict(Counter(r.get("truth_status") for r in rs)),
            "mark_price_present": all("mark_price" in r for r in rs),
            "index_price_present": any("index_price" in r for r in rs),
            "funding_time_present": all("funding_time_ms" in r for r in rs),
        }
    first = (m["funding_per_symbol"] or {}).get("BTCUSDT", {})
    m["funding_row_keys"] = first.get("row_keys")
    m["funding_venues"] = first.get("venues")
    m["funding_cadence_seconds_top"] = first.get("cadence_seconds_top")
    m["funding_truth_status"] = first.get("truth_status")
    m["funding_mark_price_present"] = first.get("mark_price_present")
    m["funding_index_price_present"] = first.get("index_price_present")

    inst_path = os.path.join(ud, "instruments", "usdm-perp-instruments.json")
    inst = _load_json(inst_path).get("instruments", []) if os.path.exists(inst_path) else []
    m["instrument_count"] = len(inst)
    m["instrument_types"] = sorted({i["fields"].get("type") for i in inst})
    m["instrument_field_names"] = sorted({k for i in inst for k in i["fields"]})
    m["instrument_quote_currencies"] = sorted({i["fields"].get("quote_currency")
                                               for i in inst if i["fields"].get("quote_currency")})
    # every perp instrument export anywhere under the raw root (a second venue would
    # necessarily ship its own instrument definitions)
    exports = {}
    for dp, dn, fn in os.walk(raw):
        for f in fn:
            if not f.endswith(".json"):
                continue
            p = os.path.join(dp, f)
            try:
                doc = _load_json(p)
            except (ValueError, OSError):
                continue
            if isinstance(doc, dict) and isinstance(doc.get("instruments"), list):
                exports[os.path.relpath(p, raw)] = sorted({
                    i.get("fields", {}).get("quote_currency")
                    for i in doc["instruments"] if isinstance(i, dict)})
    m["perp_instrument_exports"] = exports
    m["perp_instrument_export_count"] = len(exports)
    m["perp_instrument_export_quote_currencies"] = sorted(
        {q for v in exports.values() for q in v})

    schema_path = os.path.join(raw, "_meta", "SCHEMA.md")
    schema = open(schema_path, encoding="utf-8").read() if os.path.exists(schema_path) else ""
    flat = schema.replace("*", "")
    m["schema_documents_missing_fields"] = ("quote_volume" in flat
                                            and "taker-buy splits" in flat)
    m["schema_single_venue"] = "Binance USD-M perpetual futures" in schema
    m["schema_no_silent_gapfill"] = "No gaps are silently filled" in schema
    m["schema_truth_status"] = "truth_status" in schema

    names = _all_entries(raw, 4)
    joined = "\n".join(names)
    m["dex_venue_tokens_found"] = sorted({t for t in DEX_VENUE_TOKENS if t in joined})
    m["index_price_tokens_found"] = sorted({t for t in INDEX_PRICE_TOKENS if t in joined})
    m["order_book_tokens_found"] = sorted({t for t in ORDER_BOOK_TOKENS if t in joined})
    m["liquidity_tokens_found"] = sorted({t for t in LIQUIDITY_TOKENS if t in joined})
    m["dex_cost_tokens_found"] = sorted({t for t in DEX_COST_TOKENS if t in joined})
    m["margin_tokens_found"] = sorted({t for t in MARGIN_TOKENS if t in joined})
    m["cex_ops_tokens_found"] = sorted({t for t in CEX_OPS_TOKENS if t in joined})
    m["second_venue_tokens_found"] = sorted({t for t in SECOND_VENUE_TOKENS if t in joined})
    del ud

    m["meta_sha256"] = {}
    for rel in ("CONFIG.json", "SCHEMA.md", "INSTRUMENTS_EXPORT.json"):
        p = os.path.join(raw, "_meta", rel)
        if os.path.exists(p):
            m["meta_sha256"][rel] = _sha256_file(p)
    if os.path.exists(inst_path):
        m["meta_sha256"]["binance/usdm/instruments/usdm-perp-instruments.json"] = \
            _sha256_file(inst_path)
    m["note"] = ("single venue (BINANCE USD-M perpetual), 4 fixed contracts: klines whose "
                 "rows carry the six-field OHLCV shape only, plus that one venue's funding "
                 "(8-hour cadence, mark_price present, no index price). No second venue "
                 "exists - no DEX (dYdX v4 or any other), no second CEX - so the registered "
                 "signal s(i,t) = f_DEX(i,t) - f_CEX(i,t) cannot be formed; no index/"
                 "reference price, no bid/ask or order book, no liquidity/depth/impact "
                 "surface, no maintenance-margin tiers or liquidation rules, and no "
                 "DEX-specific (gas/oracle/settlement/protocol) data of any kind.")
    return m


def run_checks(results_root, raw_root):
    checks = []
    add = lambda cid, ok, detail: checks.append({"id": cid, "status": "PASS" if ok else "FAIL",
                                                 "detail": detail})
    raw = measure_raw(raw_root)
    round_dir = os.path.join(results_root, FAMILY, "rounds", ROUND)

    # C1 - one market type for one venue, and no other market directory at all.
    md = raw["binance_market_dirs"] or []
    add("C1", md == ["usdm"] and raw["venue"] == "BINANCE" and raw["market_type"] == "usdm_perp"
        and raw["paths_named_other_market"] == [] and raw["non_binance_paths"] == [],
        "binance market dirs=%s venue=%s market_type=%s other_market_paths=%s "
        "non_binance_paths=%s" % (md, raw["venue"], raw["market_type"],
                                  raw["paths_named_other_market"],
                                  raw["non_binance_paths"]))

    # C2 - the four USD-M perpetual contracts are the only instruments present, and
    #      the raw ships exactly ONE perp instrument export (a second venue would
    #      necessarily bring its own).
    same = (raw["klines_dataset_dirs"] == EXPECTED_SYMBOLS
            and raw["funding_symbol_dirs"] == EXPECTED_SYMBOLS
            and raw["symbols"] == EXPECTED_SYMBOLS
            and raw["instrument_types"] == ["CryptoPerpetual"]
            and raw["instrument_count"] == len(EXPECTED_SYMBOLS)
            and raw["instrument_quote_currencies"] == ["USDT"]
            and raw["perp_instrument_export_count"] == 1
            and raw["perp_instrument_export_quote_currencies"] == ["USDT"])
    add("C2", bool(same),
        "klines=%s funding=%s config=%s types=%s count=%s quote=%s exports=%s(%d)" % (
            raw["klines_dataset_dirs"], raw["funding_symbol_dirs"], raw["symbols"],
            raw["instrument_types"], raw["instrument_count"],
            raw["instrument_quote_currencies"], raw["perp_instrument_exports"],
            raw["perp_instrument_export_count"]))

    # C3 - the decisive one: there is no second venue, so the registered signal
    #      s(i,t) = f_DEX(i,t) - f_CEX(i,t) has no right-hand leg to subtract.
    add("C3", raw["dex_venue_tokens_found"] == [] and raw["second_venue_tokens_found"] == []
        and raw["non_binance_paths"] == [] and raw["funding_venues"] == ["BINANCE"],
        "dex_venue_tokens=%s second_venue_tokens=%s non_binance_paths=%s "
        "funding_venues=%s (the record needs at least two venues: one CEX, one DEX)"
        % (raw["dex_venue_tokens_found"], raw["second_venue_tokens_found"],
           raw["non_binance_paths"], raw["funding_venues"]))

    # C4 - the funding data that does exist belongs to ONE venue: exact funding
    #      timestamps, 8-hour cadence, mark price, official/modeled truth split -
    #      and exactly one venue value, i.e. the spread cannot be formed.
    keys = set(raw["funding_row_keys"] or [])
    needed = {"funding_time_ms", "funding_rate", "mark_price", "rate_type", "truth_status",
              "venue"}
    cadence_ok = bool(raw["funding_cadence_seconds_top"]
                      and raw["funding_cadence_seconds_top"][0] in (28799, 28800))
    add("C4", needed <= keys and raw["funding_venues"] == ["BINANCE"] and cadence_ok
        and bool(raw["funding_truth_status"]),
        "funding_row_keys=%s funding_venues=%s cadence_seconds_top=%s truth_status=%s"
        % (sorted(keys), raw["funding_venues"], raw["funding_cadence_seconds_top"],
           raw["funding_truth_status"]))

    # C5 - price fields: mark price exists for the CEX leg, but there is no index
    #      /reference price and no executable bid/ask or trade prices; the kline
    #      rows are the six-field OHLCV shape only (documented in _meta/SCHEMA.md).
    add("C5", raw["funding_mark_price_present"] is True
        and raw["funding_index_price_present"] is False
        and raw["index_price_tokens_found"] == []
        and raw["order_book_tokens_found"] == []
        and raw.get("klines_row_field_set") == KLINE_ROW_FIELDS
        and raw["schema_documents_missing_fields"] is True,
        "mark_price=%s index_price=%s index_tokens=%s book_tokens=%s kline_row_fields=%s "
        "schema_documents_missing_fields=%s" % (
            raw["funding_mark_price_present"], raw["funding_index_price_present"],
            raw["index_price_tokens_found"], raw["order_book_tokens_found"],
            raw.get("klines_row_field_set"), raw["schema_documents_missing_fields"]))

    # C6 - no liquidity surface on either side: no top-of-book spread, depth,
    #      participation or impact dataset, and no DEX cost/oracle/settlement data.
    add("C6", raw["liquidity_tokens_found"] == [] and raw["dex_cost_tokens_found"] == [],
        "liquidity_tokens=%s dex_cost_tokens=%s" % (raw["liquidity_tokens_found"],
                                                    raw["dex_cost_tokens_found"]))

    # C7 - margin/liquidation: the instrument export carries scalars only
    #      (margin_init/margin_maint); no tiers, liquidation fee, collateral asset,
    #      margin mode or partial-liquidation behaviour, and no CEX ops data.
    fields = set(raw["instrument_field_names"] or [])
    add("C7", {"margin_init", "margin_maint"} <= fields
        and raw["margin_tokens_found"] == [] and raw["cex_ops_tokens_found"] == []
        and raw["schema_single_venue"] is True,
        "scalar_margin_fields=%s margin_tokens=%s cex_ops_tokens=%s schema_single_venue=%s"
        % (sorted(fields & {"margin_init", "margin_maint"}), raw["margin_tokens_found"],
           raw["cex_ops_tokens_found"], raw["schema_single_venue"]))

    # ---- artifact side
    spec_path = os.path.join(round_dir, "round-spec.json")
    verdict_path = os.path.join(round_dir, "verdict.json")
    spec = _load_json(spec_path) if os.path.exists(spec_path) else {}
    verdict = _load_json(verdict_path) if os.path.exists(verdict_path) else {}

    # C8 - the registration still states the record's own two-venue universe (it was
    #      NOT shrunk to the one locally available venue) and records the absent
    #      right-hand leg as unavailable.
    gate = spec.get("prerequisite_gate") or {}
    univ = spec.get("universe_registration") or {}
    sig = spec.get("signal_semantics") or {}
    matrix = {i.get("item"): i for i in (gate.get("required_data_matrix") or [])
              if isinstance(i, dict)}
    ok8 = (
        spec.get("family_id") == FAMILY and spec.get("round_id") == ROUND
        and spec.get("kanban_task_id") == TASK
        and gate.get("outcome") == "PREREQUISITE_MISSING"
        and gate.get("verdict") == "TECHNICAL_INCOMPLETE"
        and gate.get("required_data_available") is False
        and gate.get("attempts_launched") == 0
        and set(MISSING_DATA_MATRIX_ITEMS) <= set(matrix)
        and all(matrix[k].get("status") for k in MISSING_DATA_MATRIX_ITEMS)
        and matrix["venues_dex"].get("status") == "ABSENT"
        and matrix["instrument_two_venue_perpetual"].get("status") == "ABSENT"
        and univ.get("venues_required") == 2
        and univ.get("venues_available") == 1
        and univ.get("dex_venue_available") is False
        and univ.get("venue_pairs_available") == 0
        and univ.get("universe_shrunk_to_local_venue") is False
        and "two venues" in str(univ.get("instrument_requirement", ""))
        and sig.get("two_venue_funding_spread_available") is False
        and sig.get("second_venue_funding_available") is False
        and (spec.get("expected") or {}).get("status") == "not_computable"
        and (spec.get("strategy_domain") or {}).get("status") == "not_registered"
        and (spec.get("launch") or {}).get("attempts_launched") == 0
    )
    add("C8", ok8, "round-spec ids/gate=%s required_available=%s venues=%s/%s dex=%s pairs=%s "
        "shrunk=%s spread_available=%s matrix_items=%d"
        % (gate.get("outcome"), gate.get("required_data_available"), univ.get("venues_available"),
           univ.get("venues_required"), univ.get("dex_venue_available"),
           univ.get("venue_pairs_available"), univ.get("universe_shrunk_to_local_venue"),
           sig.get("two_venue_funding_spread_available"), len(matrix)))

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
        "universe_shrunk_to_the_local_single_venue": lambda d: _tamper_spec(d, lambda s: s[
            "universe_registration"].update({
                "instrument_requirement": "BINANCE USD-M perpetual, "
                                          "BNBUSDT/BTCUSDT/ETHUSDT/SOLUSDT",
                "universe_shrunk_to_local_venue": True,
                "venues_required": 1, "venue_pairs_available": 1})),
        "dex_leg_struck_from_the_registration": lambda d: _tamper_spec(d, lambda s: s[
            "prerequisite_gate"]["required_data_matrix"][3].update({"status": "PRESENT"})),
        "required_data_available_flipped_true": lambda d: _tamper_spec(d, lambda s: s[
            "prerequisite_gate"].update({
                "required_data_available": True, "outcome": "PREREQUISITE_PRESENT",
                "missing": []})),
        "spread_flipped_available": lambda d: _tamper_spec(d, lambda s: s[
            "signal_semantics"].update({"two_venue_funding_spread_available": True,
                                        "second_venue_funding_available": True})),
        "searched_axis_mislabelled_user_fixed": lambda d: _tamper_spec(
            d, lambda s: s["dca_domain"].update({"spacing_pct_status": "USER_FIXED"})),
        "dca_grid_truncated_to_47": lambda d: _tamper_spec(
            d, lambda s: s["dca_domain"]["grid"].pop()),
    }
    results = []
    ok = True
    for name, mutate in variants.items():
        tmp = tempfile.mkdtemp(prefix="xq-cexdex-prereq-selftest-")
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

    Build a temp raw tree that carries what this record would need: a DEX venue
    with hourly funding, index/reference prices, an order book, gas/oracle/
    settlement references, maintenance-margin tiers and a second CEX venue. The
    raw-side checks must flip to FAIL.
    """
    tmp = tempfile.mkdtemp(prefix="xq-cexdex-prereq-rawfix-")
    try:
        fixture = os.path.join(tmp, "raw")
        shutil.copytree(raw_root, fixture,
                        ignore=shutil.ignore_patterns(".DS_Store", "__pycache__"))
        # a DEX venue with hourly funding for the same underlyings
        dex = os.path.join(fixture, "dydx", "v4", "funding")
        for sym in ("BTC-USD", "ETH-USD", "SOL-USD"):
            os.makedirs(os.path.join(dex, sym))
            with gzip.GzipFile(os.path.join(dex, sym, "%s-funding-1h.jsonl.gz" % sym),
                               "wb", mtime=0) as gz:
                gz.write(json.dumps({"symbol": sym, "venue": "DYDX_V4",
                                     "funding_time_ms": 1704067200000,
                                     "funding_rate": "0.00001"}).encode() + b"\n")
        # a second CEX venue
        os.makedirs(os.path.join(fixture, "okx", "swap"))
        # the DEX venue's own instrument definitions (a second venue ships its own)
        os.makedirs(os.path.join(fixture, "dydx", "v4", "instruments"))
        with open(os.path.join(fixture, "dydx", "v4", "instruments",
                               "dydx-perp-instruments.json"), "w", encoding="utf-8") as f:
            json.dump({"instruments": [{"id": "BTC-USD-PERP.DYDX_V4",
                                        "fields": {"type": "CryptoPerpetual",
                                                   "quote_currency": "USDC",
                                                   "settlement_currency": "USDC"}}]}, f, indent=1)
        # index / reference prices and an order book for the CEX leg
        os.makedirs(os.path.join(fixture, "binance", "usdm", "index_price"))
        os.makedirs(os.path.join(fixture, "binance", "usdm", "orderbook"))
        for rel, key in (("binance/usdm/index_price/index_price.jsonl.gz", "index_price"),
                         ("binance/usdm/orderbook/book_snapshot_bid_ask.jsonl.gz", "bid")):
            with gzip.GzipFile(os.path.join(fixture, rel), "wb", mtime=0) as gz:
                gz.write(json.dumps({key: "1.0"}).encode() + b"\n")
        # DEX-cost / oracle / settlement / margin-tier reference data
        for d in ("gas_fee", "oracle", "settlement_finality", "margin_tier",
                  "liquidation_fee", "collateral_asset", "margin_mode", "outage"):
            os.makedirs(os.path.join(fixture, "_ref", d))
            with open(os.path.join(fixture, "_ref", d, "%s-reference.json" % d), "w",
                      encoding="utf-8") as f:
                f.write("{}\n")
        res = run_checks(results_root, fixture)
        failed = [c["id"] for c in res["checks"] if c["status"] == "FAIL"]
        expected = {"C1", "C2", "C3", "C5", "C6", "C7"}
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
