#!/usr/bin/env python3
"""Prerequisite-gate read-back checker for the family

    crypto-cross-sectional-low-volatility-premium-post-2017-2026-09-01

Card t_444f837a terminalized this family as TECHNICAL_INCOMPLETE because the
record's required data is not in the canonical raw:

  * the record's universe is a **point-in-time cryptocurrency listing and
    delisting cross-section** whose assets are priced daily so that a trailing
    realized-volatility estimate can be formed per asset and ranked across the
    cross-section (long lower-volatility / short-or-compare higher-volatility);
    the canonical raw holds four BINANCE USD-M perpetual *survivor* contracts
    with no membership, listing or delisting surface;
  * the record requires **spot close prices or the exact market-type prices used
    by the source** - the raw is USD-M perpetual only (no spot, no index, no
    second venue), and the record's own portability section says a spot
    implementation is conceptually closest while a perpetual adaptation is
    adapted / unproven;
  * **point-in-time market capitalization and circulating supply** (size
    controls and the value-weighting variant) do not exist anywhere on this
    machine, and neither do **turnover / spread / Amihud-style illiquidity**
    measures: the stored kline rows carry six OHLCV fields, and the schema
    documents that quote volume, trade count and taker-buy splits were never
    imported;
  * **listing age and asset metadata** (stablecoins, wrapped assets,
    migrations, redenominations, stale prices) do not exist: instrument fields
    carry no listing/onboarding date and no supply field;
  * the perpetual-adaptation extras the record asks for (point-in-time contract
    availability, mark/index prices outside funding rows, open interest,
    liquidation constraints) are absent too. The host does hold *derived*
    USD-M contract-lifecycle research evidence (a1-* work dirs: listing and
    delisting boundary events from official notices), but its own daily
    point-in-time cell ledger for the target window is registered
    `membership_status: unknown / supported: false` for every cell and it
    carries no prices at all - it is not a priced cross-section.

Running the local four-contract perpetual panel instead would change the
registered universe, the registered price source and the registered portfolio
formation. The card forbids that ("不得以近似資料、替代市場或改寫 hypothesis 硬跑" /
"不得縮減 universe 以硬造可執行性"), so nothing was launched.

This script exists so an independent reader can re-derive that determination from
the live filesystem instead of trusting prose:

  * it re-measures the canonical raw (market directories, instrument set, kline
    row shape and UTC boundaries, weekly anchor, funding shape, name probes for
    the missing coin-level universe / spot / market-cap / turnover / listing /
    microstructure / perpetual-extra datasets, and the stored schema's own
    statements) - the measurement side;
  * it re-measures the host's point-of-truth work dirs (the a1-* USD-M
    contract-lifecycle evidence) and asserts that their daily membership cells
    are unsupported, i.e. they cannot stand in for a priced cross-section;
  * it re-measures every other local store that touches the record's requirements
    (the phase7 12-symbol USD-M survivor panel and its own point-in-time
    disclaimer, the phase3/4/5 BTC-ETH-only panels, the ml4t derived four-contract
    feature panels, and the daily-brief CMC bridge) and asserts that none of them
    carries a market-cap series, a spot multi-asset panel, a supported
    point-in-time membership surface or dated history; every host-scan hit is
    classified and any unclassified hit stays visible;
  * it re-reads the round's immutable artifacts and asserts they state exactly
    the contract-mandated terminal values (verdict, layer, class, yield decision,
    zero attempts, null run_id) and that the registered universe is still the
    record's own point-in-time coin cross-section, not a local four-contract
    fallback;
  * it asserts nothing was ever submitted (no attempt directory, no terminal
    sentinel) so a "0 attempts" claim cannot quietly become a fabricated run;
  * it asserts the DCA registration still carries the contract 7.2 v1.3.1
    provenance classes plus the complete 48-cell product.

Read-only: it never writes inside the results tree or the raw tree. `--self-test`
builds tampered copies in fresh temp directories and asserts the checker refuses
them (non-vacuousness control). `--raw-fixture-control` builds a temp raw tree
carrying what this record would need (a second venue, a spot market, a broad
symbol set, and coin-cross-section / spot / market-cap / turnover / listing /
membership / perpetual-extra reference trees) and asserts the raw-side checks flip
to FAIL. `--host-scan` re-runs the house-wide search for a priced spot
coin-cross-section, market-cap or point-in-time membership dataset. Every temp
tree is removed afterwards.

Usage:
    python3 runtime/crypto_xs_low_volatility_premium_prerequisite_check.py [--json]
    python3 runtime/crypto_xs_low_volatility_premium_prerequisite_check.py --self-test
    python3 runtime/crypto_xs_low_volatility_premium_prerequisite_check.py --raw-fixture-control
    python3 runtime/crypto_xs_low_volatility_premium_prerequisite_check.py --host-scan

Exit codes: 0 = all checks PASS, 1 = at least one FAIL, 2 = usage error.
"""
import argparse
import csv
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

FAMILY = "crypto-cross-sectional-low-volatility-premium-post-2017-2026-09-01"
ROUND = FAMILY + "-r1"
TASK = "t_444f837a"
DEFAULT_RESULTS = "/Volumes/ExpansionDrive/qlib-results"
DEFAULT_RAW = "/Volumes/ExpansionDrive/market-data-raw"
EXPANSION = "/Volumes/ExpansionDrive"
HOME = os.path.expanduser("~")
RECORD_REL = "quant/crypto-cross-sectional-low-volatility-premium-post-2017-2026-09-01.md"
EXPECTED_SYMBOLS = ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
# The exact kline row shape the raw stores: six OHLCV fields plus the convenience
# close_time_ms. No quote volume, no market cap, no trade count, no taker-buy splits.
KLINE_ROW_FIELDS = ["close", "close_time_ms", "high", "low", "open", "open_time_ms",
                    "volume"]
# The record's own statements (registered verbatim in the round-spec): the source
# studies a "post-2017 market" cross-section, its exact sample dates are
# underspecified, and it needs a point-in-time listing/delisting universe priced
# daily. The window therefore falls back to the card's research-defined rule.
RECORD_SAMPLE_DATES_UNDERSPECIFIED = True
REGISTERED_WINDOW_RESEARCH_DEFINED = ["2022-01-01", "2026-09-11"]
IN_SAMPLE_RESEARCH_DEFINED = ["2022-01-01", "2025-09-30"]
OOS_RESEARCH_DEFINED = ["2025-10-01", "2026-09-11"]
# The derived USD-M contract-lifecycle work dirs on the host (one venue, contract
# level, boundary events; NOT a priced coin cross-section).
A1_MEMBERSHIP_DIR = os.path.join(HOME, "workspace", "a1-1-phase9-pit-membership-20260830")
A1_LIFECYCLE_DIR = os.path.join(HOME, "workspace", "a1-usdm-pit-lifecycle-20260830")

# Name tokens that would have to exist for this record's required data.
UNIVERSE_TOKENS = ("universe", "membership", "point_in_time", "pit_", "cross_section",
                   "cross-sectional", "eligible", "dead_coin", "delisted")
LISTING_TOKENS = ("listing", "listed", "delist", "onboard", "trading_start", "first_trade",
                  "existence", "dead")
PRICE_SOURCE_TOKENS = ("cmc", "spot", "aggregated", "reference_price", "twap",
                       "index_price", "close_price_daily")
MARKET_CAP_TOKENS = ("market_cap", "marketcap", "mcap", "coinmarketcap", "circulating_supply",
                     "total_supply", "coingecko")
TURNOVER_TOKENS = ("turnover", "quote_volume", "dollar_volume", "traded_value", "volume_usd",
                   "adv_", "notional_value")
MICROSTRUCTURE_TOKENS = ("amihud", "spread", "bid_ask", "order_book")
PERP_EXTRA_TOKENS = ("open_interest", "liquidation", "mark_index", "contract_availability")
ASSET_METADATA_TOKENS = ("stablecoin", "wrapped", "redenomination", "migration", "peg")
# instrument-export field names that would identify a listing/supply/cap surface
INSTRUMENT_UNIVERSE_FIELDS = ("onboard", "listing", "listed", "delivery", "expiry",
                              "supply", "market_cap", "circulating")
INSTRUMENT_COST_FIELDS = ("maker_fee", "taker_fee", "price_increment", "lot_size")
INVARIANT_TOKENS = ("30,000", "numeraire", "leverage 10x", "12 tranches", "tranche #12",
                    "reduce-only", "flat/kill")
MISSING_DATA_MATRIX_ITEMS = (
    "universe_point_in_time_coin_cross_section_listing_delisting",
    "cross_section_breadth_for_volatility_sorted_portfolios",
    "spot_close_or_source_market_type_prices",
    "market_cap_and_circulating_supply_size_controls",
    "daily_returns_for_trailing_realized_volatility_windows",
    "turnover_spread_amihud_illiquidity_measures",
    "listing_age_and_asset_metadata",
    "market_wide_crypto_return_factor_or_btc_return",
    "utc_venue_timestamp_convention",
    "perpetual_funding_series",
    "perpetual_point_in_time_contract_availability",
    "perpetual_mark_index_open_interest_liquidation_constraints",
    "falsification_battery_benchmarks",
    "transaction_cost_convention")
DECISIVE_ITEMS = ("universe_point_in_time_coin_cross_section_listing_delisting",
                  "cross_section_breadth_for_volatility_sorted_portfolios",
                  "spot_close_or_source_market_type_prices",
                  "market_cap_and_circulating_supply_size_controls")
BREADTH_ITEM = "cross_section_breadth_for_volatility_sorted_portfolios"
ABSENT_ZERO_ITEMS = ("universe_point_in_time_coin_cross_section_listing_delisting",
                     "spot_close_or_source_market_type_prices",
                     "market_cap_and_circulating_supply_size_controls",
                     "turnover_spread_amihud_illiquidity_measures",
                     "listing_age_and_asset_metadata",
                     "perpetual_point_in_time_contract_availability",
                     "perpetual_mark_index_open_interest_liquidation_constraints")
LOCAL_ONLY_ITEMS = ("daily_returns_for_trailing_realized_volatility_windows",
                    "market_wide_crypto_return_factor_or_btc_return")
PRESENT_ITEMS = ("utc_venue_timestamp_convention", "perpetual_funding_series",
                 "transaction_cost_convention")
NOT_CONSTRUCTIBLE_ITEMS = ("falsification_battery_benchmarks",)
DIRECTION_REGISTERED = ("long lower-realized-volatility assets / short or compare against "
                        "higher-realized-volatility assets")


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


def _all_entries(root, max_depth=8):
    """Every file/directory basename token under the raw root (for name probes).

    Depth 8 is required to reach datasets nested as klines/<SYMBOL>/<interval>/.
    """
    names, total = [], 0
    for dp, dn, fn in os.walk(root):
        rel = os.path.relpath(dp, root)
        if rel.count(os.sep) >= max_depth:
            dn[:] = []
            continue
        names.extend(dn)
        names.extend(fn)
        total += len(dn) + len(fn)
    return sorted({n.lower() for n in names if not n.startswith(".")}), total


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


def _tokens_found(names, tokens):
    joined = "\n".join(names)
    return sorted({t for t in tokens if t in joined})


def measure_raw(raw):
    """Independent re-measurement of the canonical raw. No artifact is trusted."""
    m = {}
    m["binance_market_dirs"] = _dirs(os.path.join(raw, "binance"))
    ud = os.path.join(raw, "binance", "usdm")
    m["binance_usdm_subdirs"] = _dirs(ud)
    m["raw_paths"] = sorted(p for p in _walk_paths(raw, 3) if p != ".")
    m["paths_named_other_market"] = [
        p for p in _walk_paths(raw, 4)
        if os.path.basename(p).lower() in ("spot", "margin", "options", "inverse", "coinm",
                                           "delivery", "futures", "quarter", "index")]
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

    kl = os.path.join(ud, "klines")
    m["klines_dataset_dirs"] = _dirs(kl)
    m["klines_interval_set"] = _dirs(os.path.join(kl, "BTCUSDT"))
    d1 = sorted(f for f in os.listdir(os.path.join(kl, "BTCUSDT", "1d"))
                if f.endswith(".jsonl.gz"))
    if d1:
        rows1 = _rows(os.path.join(kl, "BTCUSDT", "1d", d1[0]))
        opens = [r["open_time_ms"] for r in rows1]
        m["klines_row_field_set"] = sorted(rows1[-1].keys())
        m["klines_1d_first_open_utc"] = _iso(opens[0])
        m["klines_1d_first_open_mod_86400_seconds"] = [o % 86400000 // 1000 for o in opens[:3]]
        m["klines_1d_open_step_seconds"] = sorted(Counter(
            (opens[i + 1] - opens[i]) // 1000 for i in range(len(opens) - 1)).keys())[:4]

    first, last, rows_per_symbol = {}, {}, {}
    for sym in (m["klines_dataset_dirs"] or []):
        d = os.path.join(kl, sym, "1d")
        if not os.path.isdir(d):
            continue
        fs = sorted(f for f in os.listdir(d) if f.endswith(".jsonl.gz"))
        if not fs:
            continue
        first[sym] = _rows(os.path.join(d, fs[0]))[0]["open_time_ms"]
        last[sym] = _rows(os.path.join(d, fs[-1]))[-1]["open_time_ms"]
        rows_per_symbol[sym] = sum(len(_rows(os.path.join(d, f))) for f in fs)
    m["klines_1d_first_by_symbol"] = {k: _iso(v) for k, v in first.items()}
    m["klines_1d_last_by_symbol"] = {k: _iso(v) for k, v in last.items()}
    m["klines_1d_rows_by_symbol"] = rows_per_symbol
    m["raw_1d_window_utc"] = ([_iso(min(first.values())), _iso(max(last.values()))]
                              if first else None)
    m["cross_section_size"] = len(m["klines_dataset_dirs"] or [])

    dw = sorted(f for f in os.listdir(os.path.join(kl, "BTCUSDT", "1w"))
                if f.endswith(".jsonl.gz"))
    if dw:
        rows_w = _rows(os.path.join(kl, "BTCUSDT", "1w", dw[0]))
        first_open = rows_w[0]["open_time_ms"]
        m["klines_1w_first_open_utc"] = _iso(first_open)
        m["klines_1w_first_open_weekday"] = datetime.fromtimestamp(
            first_open / 1000, tz=timezone.utc).strftime("%a")

    fu_root = os.path.join(ud, "funding")
    m["funding_symbol_dirs"] = _dirs(fu_root)
    p = os.path.join(fu_root, "BTCUSDT", "BTCUSDT-funding.jsonl.gz")
    if os.path.exists(p):
        rs = _rows(p)
        m["funding_row_keys"] = sorted(rs[-1].keys())
        m["funding_venues"] = sorted({str(r.get("venue")) for r in rs})
        m["funding_first_time_utc"] = _iso(rs[0]["funding_time_ms"])
        m["funding_last_time_utc"] = _iso(rs[-1]["funding_time_ms"])
        m["funding_mark_price_present_on_last_row"] = rs[-1].get("mark_price") is not None

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
    m["instrument_cost_field_hits"] = sorted(
        f for f in m["instrument_field_names"] if f in INSTRUMENT_COST_FIELDS)

    schema_path = os.path.join(raw, "_meta", "SCHEMA.md")
    schema = open(schema_path, encoding="utf-8").read() if os.path.exists(schema_path) else ""
    flat = schema.replace("*", "").replace("`", "")
    low = flat.lower()
    m["schema_mentions_market_cap"] = "market cap" in low or "market_cap" in low
    m["schema_mentions_spot"] = "spot" in low
    m["schema_single_venue"] = "Binance USD-M perpetual futures, UTC" in flat
    m["schema_documents_missing_fields"] = ("quote_volume" in flat
                                            and "trade count and taker-buy splits are" in flat)
    m["schema_no_silent_gapfill"] = "No gaps are silently filled" in flat
    m["schema_utc_declared"] = "UTC" in flat

    names, total = _all_entries(raw, 8)
    m["raw_entry_name_count"] = total
    m["universe_tokens_found"] = _tokens_found(names, UNIVERSE_TOKENS)
    m["listing_tokens_found"] = _tokens_found(names, LISTING_TOKENS)
    m["price_source_tokens_found"] = _tokens_found(names, PRICE_SOURCE_TOKENS)
    m["market_cap_tokens_found"] = _tokens_found(names, MARKET_CAP_TOKENS)
    m["turnover_tokens_found"] = _tokens_found(names, TURNOVER_TOKENS)
    m["microstructure_tokens_found"] = _tokens_found(names, MICROSTRUCTURE_TOKENS)
    m["perp_extra_tokens_found"] = _tokens_found(names, PERP_EXTRA_TOKENS)
    m["asset_metadata_tokens_found"] = _tokens_found(names, ASSET_METADATA_TOKENS)

    m["meta_sha256"] = {}
    for rel in ("CONFIG.json", "SCHEMA.md", "INSTRUMENTS_EXPORT.json", "STATE.json"):
        p = os.path.join(raw, "_meta", rel)
        if os.path.exists(p):
            m["meta_sha256"][rel] = _sha256_file(p)
    m["note"] = ("single venue (BINANCE USD-M perpetual), four fixed survivor contracts: klines "
                 "whose rows carry the six-field OHLCV shape only, plus that venue's funding. "
                 "No spot or index market, no coin-level point-in-time cross-section, no "
                 "market capitalization or circulating supply, no turnover/spread/Amihud "
                 "measure, no listing-age or asset metadata, no mark/index series outside the "
                 "funding rows, no open interest and no liquidation constraints - so the "
                 "record's priced point-in-time coin cross-section cannot be built.")
    return m


def measure_points_of_truth():
    """Measure the host's derived USD-M contract-lifecycle evidence (read-only).

    These are the only local datasets that even touch the record's
    listing/delisting requirement; the measurement records what they actually are
    so the determination can state it precisely instead of claiming absence
    everywhere.
    """
    out = {}
    cells_path = os.path.join(A1_MEMBERSHIP_DIR, "membership_cells.jsonl")
    out["a1_membership_dir_present"] = os.path.isdir(A1_MEMBERSHIP_DIR)
    if os.path.exists(cells_path):
        rows = _rows(cells_path)
        out["a1_membership_cells_rows"] = len(rows)
        out["a1_membership_cells_supported_true"] = sum(
            1 for r in rows if r.get("supported") is True)
        out["a1_membership_cells_status_values"] = sorted(
            {str(r.get("membership_status")) for r in rows})
        out["a1_membership_cells_symbols"] = sorted(
            {r.get("venue_symbol") for r in rows if r.get("venue_symbol")})
        dates = sorted(r.get("decision_date") for r in rows if r.get("decision_date"))
        out["a1_membership_cells_date_window"] = [dates[0], dates[-1]] if dates else None
    lifecycle_path = os.path.join(A1_LIFECYCLE_DIR, "lifecycle_events.csv")
    out["a1_lifecycle_dir_present"] = os.path.isdir(A1_LIFECYCLE_DIR)
    if os.path.exists(lifecycle_path):
        with open(lifecycle_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        out["a1_lifecycle_events_rows"] = len(rows)
        out["a1_lifecycle_event_types"] = sorted(
            {str(r.get("event_type")) for r in rows if r.get("event_type")})
        out["a1_lifecycle_symbols"] = sorted(
            {str(r.get("venue_symbol")) for r in rows if r.get("venue_symbol")})
        out["a1_lifecycle_symbol_count"] = len(out["a1_lifecycle_symbols"])
    out["prices_present"] = False
    out["note"] = ("derived USD-M perpetual *contract-lifecycle* research evidence for one "
                   "venue: listing/delisting boundary events from official notices, plus a "
                   "daily point-in-time cell ledger whose own status is "
                   "membership_status=unknown / supported=false for every cell (the evidence "
                   "could not backfill daily membership for the target window). It carries no "
                   "prices, returns, market caps or liquidity measures, so it is not the "
                   "record's priced coin-level cross-section.")
    return out


def measure_record():
    """Structural read of the canonical record (read-only, no hash equality claim)."""
    p = os.path.join(HOME, ".hermes", "wiki", RECORD_REL)
    if not os.path.exists(p):
        return {"record_present": False, "record_path": p}
    text = open(p, encoding="utf-8").read()
    m = {"record_present": True, "record_path": p, "record_sha256": _sha256_file(p),
         "record_bytes": len(text.encode())}
    m["source_as_of_underspecified_marker"] = "source_as_of: \"underspecified in public abstract" in text
    m["provenance_dates_underspecified_statement"] = (
        "are **underspecified** in the public abstract reviewed in this Scout cycle" in text)
    m["intake_decision_pass_with_caveat"] = "Decision: **PASS-WITH-CAVEAT**" in text
    m["required_data_bullets"] = text.split("## Required data")[1].split("## Execution")[0].count("\n- ")
    fals = text.split("## Falsification plan")[1].split("A valid replication")[0]
    m["falsification_items"] = sum(
        1 for line in fals.splitlines() if line.strip()[:2].rstrip(".").isdigit())
    lims = text.split("## Limitations")[1].split("## Implementation status")[0]
    m["limitations_items"] = sum(1 for line in lims.splitlines() if line.startswith("- "))
    m["post_2017_market_statement"] = "post-2017" in text
    return m


def _names_with(root, tokens, max_depth=5):
    hits = []
    for dp, dn, fn in os.walk(root):
        rel = os.path.relpath(dp, root)
        if rel.count(os.sep) >= max_depth:
            dn[:] = []
            continue
        for n in list(dn) + list(fn):
            if any(t in n.lower() for t in tokens):
                hits.append(os.path.join(dp, n))
    return sorted(set(hits))


def measure_other_stores():
    """Measure every other local store that touches the record's requirements.

    Read-only. These are the only datasets on the host that could conceivably stand in
    for the record's priced point-in-time coin cross-section; each one's measured
    properties are recorded so the determination can say exactly why it does not.
    """
    out = {}
    p7 = os.path.join(HOME, "workspace", "phase7-alpha-research")
    out["phase7_present"] = os.path.isdir(p7)
    if out["phase7_present"]:
        kdir = os.path.join(p7, "raw", "binance_klines")
        out["phase7_kline_symbols"] = sorted(f[:-5] for f in os.listdir(kdir)
                                             if f.endswith(".json")) if os.path.isdir(kdir) else []
        man_path = os.path.join(p7, "raw", "universe_manifest.json")
        if os.path.exists(man_path):
            man = _load_json(man_path)
            out["phase7_manifest_endpoint"] = man.get("endpoint")
            out["phase7_manifest_window"] = [man.get("start_date_utc"), man.get("end_date_utc")]
            out["phase7_manifest_status_values"] = sorted(
                {str(s.get("status_at_manifest")) for s in man.get("symbols", [])})
            out["phase7_manifest_onboard_dates_present"] = all(
                s.get("onboard_date_ms") for s in man.get("symbols", []))
            out["phase7_manifest_symbols"] = sorted(s.get("symbol") for s in man.get("symbols", []))
        prov = os.path.join(p7, "data_provenance.md")
        text = open(prov, encoding="utf-8").read() if os.path.exists(prov) else ""
        out["phase7_provenance_disclaims_pit_universe"] = ("不等於 point-in-time universe" in text)
        out["phase7_provenance_disclaims_survivorship_free"] = ("不能宣稱無 survivorship bias" in text)
        out["phase7_market_cap_like_files"] = _names_with(
            p7, ("market_cap", "marketcap", "mcap", "circulating", "coingecko", "coinmarketcap"))
        out["phase7_spot_like_files"] = _names_with(p7, ("spot_", "_spot", "spot-"))
        rc = os.path.join(p7, "data", "derived", "real_daily.csv")
        out["phase7_real_daily_rows"] = (sum(1 for _ in open(rc, encoding="utf-8")) - 1
                                         if os.path.exists(rc) else None)
        out["phase7_real_daily_has_quote_volume"] = (
            "quote_volume" in open(rc, encoding="utf-8").readline() if os.path.exists(rc) else False)

    p5 = os.path.join(HOME, "workspace", "phase5-crypto-derivatives")
    out["phase5_present"] = os.path.isdir(p5)
    if out["phase5_present"]:
        ddir = os.path.join(p5, "data")
        files = sorted(f for f in os.listdir(ddir) if f.endswith(".json")) if os.path.isdir(ddir) else []
        out["phase5_data_files"] = len(files)
        out["phase5_asset_tokens"] = sorted({t for t in ("btc", "eth", "bnb", "sol", "xrp", "ada")
                                             if any(t in f.lower() for f in files)})
        spot = os.path.join(ddir, "klines_spot_btc_1d.json")
        if os.path.exists(spot):
            try:
                out["phase5_spot_btc_daily_rows"] = len(_load_json(spot))
            except (ValueError, OSError):
                out["phase5_spot_btc_daily_rows"] = None
        out["phase5_market_cap_like_files"] = _names_with(p5, ("market_cap", "marketcap", "mcap",
                                                               "circulating", "coingecko"))

    p4 = os.path.join(HOME, "workspace", "phase4-market-microstructure")
    out["phase4_present"] = os.path.isdir(p4)
    if out["phase4_present"]:
        c = os.path.join(p4, "data", "merged_daily_ohlcv.csv")
        header = open(c, encoding="utf-8").readline().strip() if os.path.exists(c) else ""
        cols = [x.strip() for x in header.split(",")]
        out["phase4_asset_tokens"] = sorted({x.split("_")[0] for x in cols if "_" in x})
        out["phase4_rows"] = (sum(1 for _ in open(c, encoding="utf-8")) - 1
                              if os.path.exists(c) else None)

    p3 = os.path.join(HOME, "workspace", "phase3-portfolio-risk")
    out["phase3_present"] = os.path.isdir(p3)
    if out["phase3_present"]:
        c = os.path.join(p3, "data", "merged_daily.csv")
        header = open(c, encoding="utf-8").readline().strip() if os.path.exists(c) else ""
        out["phase3_asset_tokens"] = sorted({x.split("_")[0] for x in header.split(",") if "_" in x})
        out["phase3_rows"] = (sum(1 for _ in open(c, encoding="utf-8")) - 1
                              if os.path.exists(c) else None)

    p9 = os.path.join(HOME, "workspace", "phase9-cross-sectional-factors")
    out["phase9_present"] = os.path.isdir(p9)
    if out["phase9_present"]:
        out["phase9_data_like_files"] = _names_with(
            p9, (".csv", ".parquet", ".jsonl", ".feather", ".h5", ".db"))

    p10 = os.path.join(HOME, "workspace", "phase10-pit-bitemporal")
    out["phase10_present"] = os.path.isdir(p10)
    if out["phase10_present"]:
        lc = os.path.join(p10, "data", "real_lifecycle_evidence.csv")
        out["phase10_lifecycle_rows"] = (sum(1 for _ in open(lc, encoding="utf-8")) - 1
                                         if os.path.exists(lc) else None)
        out["phase10_market_cap_like_files"] = _names_with(
            p10, ("market_cap", "marketcap", "mcap", "circulating", "coingecko"))

    ml4t = os.path.join(HOME, "workspace", "ml4t-real-evidence-remediation")
    out["ml4t_present"] = os.path.isdir(ml4t)
    if out["ml4t_present"]:
        man_path = os.path.join(ml4t, "p3", "P3_PANEL_MANIFEST.json")
        out["ml4t_p3_panel_manifest_present"] = os.path.exists(man_path)
        if os.path.exists(man_path):
            man = _load_json(man_path)
            cols = [str(c).lower() for c in (man.get("columns") or [])]
            out["ml4t_p3_panel_rows"] = man.get("panel_rows")
            out["ml4t_p3_panel_asset_tokens"] = sorted({t for t in ("btc", "eth", "bnb", "sol")
                                                        if any(c.startswith(t) for c in cols)})

    brief = os.path.join(EXPANSION, "daily-crypto-brief")
    out["daily_brief_present"] = os.path.isdir(brief)
    if out["daily_brief_present"]:
        odir = os.path.join(brief, "output")
        ofiles = sorted(os.listdir(odir)) if os.path.isdir(odir) else []
        out["daily_brief_output_files"] = len(ofiles)
        out["daily_brief_dated_history_files"] = [f for f in ofiles
                                                  if len(f) >= 10 and f[:4].isdigit()]
        out["daily_brief_latest_has_market_cap"] = (
            "market_cap" in open(os.path.join(odir, "latest.json"), encoding="utf-8").read()
            if os.path.exists(os.path.join(odir, "latest.json")) else False)
        out["daily_brief_git_repo_present"] = os.path.isdir(os.path.join(brief, ".git"))

    out["coin_level_point_in_time_cross_section_available"] = False
    out["priced_spot_multi_asset_panel_available"] = False
    out["market_cap_panel_available"] = False
    out["note"] = ("no local store is the record's priced point-in-time coin cross-section: the "
                   "phase7 panel is a 12-symbol single-venue USD-M perpetual survivor list whose own "
                   "provenance disclaims point-in-time completeness; phase3/4/5 carry BTC/ETH only; "
                   "a1/phase10 are price-free contract-lifecycle metadata; the daily-brief CMC bridge "
                   "holds per-run current snapshots, not a history panel")
    return out


def run_checks(results_root, raw_root):
    checks = []
    add = lambda cid, ok, detail: checks.append({"id": cid, "status": "PASS" if ok else "FAIL",
                                                 "detail": detail})
    raw = measure_raw(raw_root)
    pot = measure_points_of_truth()
    rec = measure_record()
    osts = measure_other_stores()
    round_dir = os.path.join(results_root, FAMILY, "rounds", ROUND)

    # C1 - one market type for one venue, and no other market directory at all
    #      (no spot, no dated futures, no second venue).
    md = raw["binance_market_dirs"] or []
    add("C1", md == ["usdm"] and raw["venue"] == "BINANCE" and raw["market_type"] == "usdm_perp"
        and raw["paths_named_other_market"] == [] and raw["non_binance_paths"] == [],
        "binance market dirs=%s venue=%s market_type=%s other_market_paths=%s "
        "non_binance_paths=%s" % (md, raw["venue"], raw["market_type"],
                                  raw["paths_named_other_market"], raw["non_binance_paths"]))

    # C2 - the four USD-M perpetual contracts are the only local instruments: the
    #      raw's unit of cross-section is exactly a four-name survivor list.
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

    # C3 - DECISIVE, the universe: the record registers a point-in-time coin-level
    #      listing/delisting cross-section (active AND dead assets) priced daily.
    #      The raw holds four survivor contracts with no membership surface, and
    #      the host's derived USD-M contract-lifecycle evidence cannot stand in
    #      for it (its daily cells are all supported=false and it has no prices).
    pot_ok = (pot.get("a1_membership_cells_supported_true", 0) == 0
              and pot.get("prices_present") is False)
    add("C3", raw["cross_section_size"] == len(EXPECTED_SYMBOLS)
        and raw["universe_tokens_found"] == []
        and raw["listing_tokens_found"] == []
        and raw["instrument_universe_field_hits"] == []
        and pot_ok,
        "local_cross_section=%s symbols=%s universe_name_tokens=%s listing_name_tokens=%s "
        "instrument_universe_fields=%s | a1_daily_membership_supported_true=%s "
        "a1_cells=%s lifecycle_events=%s lifecycle_symbols=%s"
        % (raw["cross_section_size"], raw["symbols"], raw["universe_tokens_found"],
           raw["listing_tokens_found"], raw["instrument_universe_field_hits"],
           pot.get("a1_membership_cells_supported_true"),
           pot.get("a1_membership_cells_rows"), pot.get("a1_lifecycle_events_rows"),
           pot.get("a1_lifecycle_symbol_count")))

    # C4 - DECISIVE, the price source: the record requires spot closes or the
    #      exact market-type prices used by the source, and says a spot
    #      implementation is conceptually closest while a perpetual adaptation is
    #      adapted / unproven. The raw is USD-M perpetual only.
    add("C4", raw["paths_named_other_market"] == []
        and raw["price_source_tokens_found"] == []
        and raw["klines_row_field_set"] == KLINE_ROW_FIELDS
        and raw["schema_single_venue"] is True,
        "other_market_paths=%s price_source_name_tokens=%s kline_row_fields=%s "
        "schema_single_venue=%s"
        % (raw["paths_named_other_market"], raw["price_source_tokens_found"],
           raw["klines_row_field_set"], raw["schema_single_venue"]))

    # C5 - DECISIVE, size controls: point-in-time market capitalization and
    #      circulating supply do not exist (also the value-weighted variant's input).
    add("C5", raw["market_cap_tokens_found"] == []
        and raw["instrument_universe_field_hits"] == []
        and raw["schema_mentions_market_cap"] is False,
        "market_cap_name_tokens=%s instrument_supply_or_cap_fields=%s "
        "schema_mentions_market_cap=%s"
        % (raw["market_cap_tokens_found"], raw["instrument_universe_field_hits"],
           raw["schema_mentions_market_cap"]))

    # C6 - turnover / spread / Amihud-style illiquidity measures are absent; the
    #      stored kline rows carry base-asset volume only and the schema documents
    #      the never-imported fields (quote volume, trade count, taker-buy splits).
    add("C6", raw["turnover_tokens_found"] == []
        and raw["microstructure_tokens_found"] == []
        and raw["klines_row_field_set"] == KLINE_ROW_FIELDS
        and raw["schema_documents_missing_fields"] is True,
        "turnover_name_tokens=%s microstructure_name_tokens=%s kline_row_fields=%s "
        "schema_documents_missing_fields=%s"
        % (raw["turnover_tokens_found"], raw["microstructure_tokens_found"],
           raw["klines_row_field_set"], raw["schema_documents_missing_fields"]))

    # C7 - listing age and asset metadata (stablecoins, wrapped assets,
    #      migrations, redenominations, stale prices) do not exist: no name token,
    #      no instrument field carrying a listing/onboarding date.
    add("C7", raw["listing_tokens_found"] == []
        and raw["asset_metadata_tokens_found"] == []
        and raw["instrument_universe_field_hits"] == [],
        "listing_name_tokens=%s asset_metadata_name_tokens=%s instrument_fields=%s"
        % (raw["listing_tokens_found"], raw["asset_metadata_tokens_found"],
           raw["instrument_universe_field_hits"]))

    # C8 - the perpetual-adaptation extras are absent: no mark/index series
    #      outside the funding rows, no open interest, no liquidation constraints,
    #      and no usable point-in-time contract-availability series (the a1
    #      boundary evidence is not one: all daily cells are unsupported).
    add("C8", raw["perp_extra_tokens_found"] == []
        and "mark_price" in (raw["funding_row_keys"] or [])
        and raw["klines_row_field_set"] == KLINE_ROW_FIELDS
        and pot.get("a1_membership_cells_supported_true", 0) == 0,
        "perp_extra_name_tokens=%s mark_price_only_in_funding_rows=%s kline_row_fields=%s "
        "a1_daily_membership_supported_true=%s"
        % (raw["perp_extra_tokens_found"],
           "mark_price" in (raw["funding_row_keys"] or []), raw["klines_row_field_set"],
           pot.get("a1_membership_cells_supported_true")))

    # C9 - what IS usable is still registered as present, and the window rule is
    #      the card's research-defined fallback (the record's exact sample dates
    #      are underspecified, so no record sample window is asserted).
    utc_ok = (raw["klines_1d_first_open_mod_86400_seconds"] == [0, 0, 0]
              and raw["klines_1d_open_step_seconds"] == [86400])
    add("C9", utc_ok
        and raw["funding_symbol_dirs"] == EXPECTED_SYMBOLS
        and raw["funding_venues"] == ["BINANCE"]
        and set(INSTRUMENT_COST_FIELDS) <= set(raw["instrument_field_names"])
        and RECORD_SAMPLE_DATES_UNDERSPECIFIED is True
        and rec.get("source_as_of_underspecified_marker") is True
        and rec.get("provenance_dates_underspecified_statement") is True,
        "utc_day_anchors=%s step=%s funding_venues=%s cost_fields=%s | record_underspecified="
        "%s/%s"
        % (raw["klines_1d_first_open_mod_86400_seconds"], raw["klines_1d_open_step_seconds"],
           raw["funding_venues"], raw["instrument_cost_field_hits"],
           rec.get("source_as_of_underspecified_marker"),
           rec.get("provenance_dates_underspecified_statement")))

    # ---- artifact side
    spec_path = os.path.join(round_dir, "round-spec.json")
    verdict_path = os.path.join(round_dir, "verdict.json")
    spec = _load_json(spec_path) if os.path.exists(spec_path) else {}
    verdict = _load_json(verdict_path) if os.path.exists(verdict_path) else {}

    # C10 - the registration still states the record's own point-in-time coin
    #       cross-section and its registered price source (it was NOT shrunk to the
    #       locally available four contracts) and records the missing surfaces as
    #       unavailable.
    gate = spec.get("prerequisite_gate") or {}
    univ = spec.get("universe_registration") or {}
    sig = spec.get("signal_semantics") or {}
    data = spec.get("data") or {}
    meas = gate.get("measured_available") or {}
    matrix = {i.get("item"): i for i in (gate.get("required_data_matrix") or [])
              if isinstance(i, dict)}
    probes = meas.get("name_probe_hits") or {}
    ok10 = (
        spec.get("family_id") == FAMILY and spec.get("round_id") == ROUND
        and spec.get("kanban_task_id") == TASK
        and gate.get("outcome") == "PREREQUISITE_MISSING"
        and gate.get("verdict") == "TECHNICAL_INCOMPLETE"
        and gate.get("required_data_available") is False
        and gate.get("attempts_launched") == 0
        and set(MISSING_DATA_MATRIX_ITEMS) <= set(matrix)
        and all(matrix[k].get("status") for k in MISSING_DATA_MATRIX_ITEMS)
        and all(matrix[k].get("status") == "ABSENT" for k in ABSENT_ZERO_ITEMS)
        and all(matrix[k].get("status") == "LOCAL_LIST_ONLY" for k in LOCAL_ONLY_ITEMS)
        and all(matrix[k].get("status") == "PRESENT" for k in PRESENT_ITEMS)
        and all(matrix[k].get("status") == "NOT_CONSTRUCTIBLE"
                for k in NOT_CONSTRUCTIBLE_ITEMS)
        and matrix[BREADTH_ITEM].get("status") == "STRUCTURALLY_INSUFFICIENT"
        and all(matrix[k].get("decisive") is True for k in DECISIVE_ITEMS)
        and univ.get("universe_shrunk_to_local_list") is False
        and univ.get("cross_section_symbols") == EXPECTED_SYMBOLS
        and univ.get("cross_section_available") == len(EXPECTED_SYMBOLS)
        and univ.get("cross_section_breadth_status") == "STRUCTURALLY_INSUFFICIENT"
        and univ.get("point_in_time_membership_available") is False
        and univ.get("listing_status_history_available") is False
        and univ.get("delisted_assets_present") is False
        and univ.get("spot_price_source_available") is False
        and univ.get("market_cap_data_available") is False
        and univ.get("turnover_data_available") is False
        and univ.get("liquidity_measure_available") is False
        and univ.get("listing_metadata_available") is False
        and univ.get("target_price_source_registered") == (
            "spot closes (or the exact market-type prices used by the source; "
            "underspecified in the reviewed public abstract)")
        and data.get("registered_sample", {}).get("status") == "research-defined"
        and data.get("record_sample_dates_underspecified") is True
        and data.get("in_sample_declared") == IN_SAMPLE_RESEARCH_DEFINED
        and data.get("oos_declared") == OOS_RESEARCH_DEFINED
        and sig.get("direction") == DIRECTION_REGISTERED
        and sig.get("cross_section_available") == len(EXPECTED_SYMBOLS)
        and sig.get("cross_section_ranking_breadth_status") == "STRUCTURALLY_INSUFFICIENT"
        and (spec.get("expected") or {}).get("status") == "not_computable"
        and (spec.get("strategy_domain") or {}).get("status") == "not_registered"
        and (spec.get("launch") or {}).get("attempts_launched") == 0
        and meas.get("cross_section_size") == len(EXPECTED_SYMBOLS)
        and all(v == [] for v in probes.values()) and bool(probes)
    )
    add("C10", bool(ok10),
        "round-spec ids/gate=%s required_available=%s matrix_items=%d decisive_status=%s "
        "breadth=%s required_price_source=%s available=%s shrunk=%s window_status=%s "
        "record_underspecified=%s in_sample=%s oos=%s probes_empty=%s"
        % (gate.get("outcome"), gate.get("required_data_available"), len(matrix),
           {k: matrix.get(k, {}).get("status") for k in DECISIVE_ITEMS},
           matrix.get(BREADTH_ITEM, {}).get("status"), univ.get("target_price_source_registered"),
           univ.get("cross_section_available"), univ.get("universe_shrunk_to_local_list"),
           data.get("registered_sample", {}).get("status"),
           data.get("record_sample_dates_underspecified"), data.get("in_sample_declared"),
           data.get("oos_declared"), all(v == [] for v in probes.values()) if probes else None))

    # C11 - the terminal verdict states the contract-mandated values.
    fail = verdict.get("failure") or {}
    yld = verdict.get("yield") or {}
    ok11 = (
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
    add("C11", ok11, "verdict=%s run_id=%s layer=%s class=%s yield=%s run_ids=%s" % (
        verdict.get("verdict"), verdict.get("run_id"), fail.get("layer"), fail.get("class"),
        yld.get("yield_decision"), verdict.get("evidence_run_ids")))

    # C12 - nothing was ever submitted: no attempt directory, no terminal sentinel.
    attempts_dir = os.path.join(round_dir, "attempts")
    sentinels = []
    for dp, dn, fn in os.walk(round_dir):
        for f in fn:
            if f in ("DONE", "FAILED", "INCOMPLETE"):
                sentinels.append(os.path.join(dp, f))
    add("C12", not os.path.exists(attempts_dir) and sentinels == [],
        "attempts_dir_exists=%s terminal_sentinels=%s" % (os.path.exists(attempts_dir),
                                                          sentinels))

    # C13 - ownership, and the round directory holds only the two immutable artifacts.
    fam_path = os.path.join(results_root, FAMILY, "family.json")
    fam = _load_json(fam_path) if os.path.exists(fam_path) else {}
    files = sorted(f for f in os.listdir(round_dir)) if os.path.isdir(round_dir) else None
    add("C13", fam.get("kanban_task_id") == TASK
        and files == ["round-spec.json", "verdict.json"],
        "family.kanban_task_id=%s round_dir_files=%s" % (fam.get("kanban_task_id"), files))

    # C14 - the DCA registration keeps its contract 7.2 v1.3.1 provenance classes and
    #       its complete 48-cell product (a searched axis is never declared as a
    #       user-fixed invariant).
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
    add("C14", not probs, "dca provenance/grid+bounded invariants: %s"
        % (probs if probs else "ok"))

    # C15 - the other local stores that could conceivably touch this record's
    #       requirements are not the registered cross-section: whatever is present is
    #       single-venue / narrow / price-free, and none carries a market-cap series or
    #       a supported point-in-time membership surface.
    probs15, present_stores = [], []
    if osts["phase7_present"]:
        present_stores.append("phase7")
        if osts.get("phase7_manifest_status_values") != ["TRADING"]:
            probs15.append("phase7 manifest carries a non-TRADING symbol status: %s"
                           % osts.get("phase7_manifest_status_values"))
        if not osts.get("phase7_provenance_disclaims_pit_universe"):
            probs15.append("phase7 provenance no longer disclaims a point-in-time universe")
        if osts.get("phase7_market_cap_like_files"):
            probs15.append("phase7 tree holds market-cap-like files: %s"
                           % osts["phase7_market_cap_like_files"])
        if not (osts.get("phase7_kline_symbols") or []):
            probs15.append("phase7 kline store is empty")
    if osts["phase5_present"]:
        present_stores.append("phase5")
        if osts.get("phase5_asset_tokens") not in (["btc"], ["btc", "eth"]):
            probs15.append("phase5 asset tokens changed: %s" % osts.get("phase5_asset_tokens"))
        if osts.get("phase5_market_cap_like_files"):
            probs15.append("phase5 tree holds market-cap-like files")
    if osts["phase4_present"]:
        present_stores.append("phase4")
        if osts.get("phase4_asset_tokens") not in (["btc"], ["btc", "eth"]):
            probs15.append("phase4 asset tokens changed: %s" % osts.get("phase4_asset_tokens"))
    if osts["phase10_present"]:
        present_stores.append("phase10")
        if osts.get("phase10_market_cap_like_files"):
            probs15.append("phase10 tree holds market-cap-like files")
    if osts["ml4t_present"]:
        present_stores.append("ml4t")
        if osts.get("ml4t_p3_panel_asset_tokens") and osts["ml4t_p3_panel_asset_tokens"] != [
                "bnb", "btc", "eth", "sol"]:
            probs15.append("ml4t panel asset tokens changed: %s"
                           % osts.get("ml4t_p3_panel_asset_tokens"))
    if osts["daily_brief_present"]:
        present_stores.append("daily_brief")
        if osts.get("daily_brief_dated_history_files"):
            probs15.append("daily-brief output holds dated history files: %s"
                           % osts["daily_brief_dated_history_files"][:5])
    if not present_stores:
        probs15.append("no other local store was measurable (check would be vacuous)")
    ncs = (univ.get("non_canonical_stores_disclosed") or {})
    p7_spec = ncs.get("phase7_alpha_research") or {}
    if p7_spec:
        if p7_spec.get("symbols") != osts.get("phase7_manifest_symbols"):
            probs15.append("round-spec phase7 symbol list drifts from the live manifest")
        if p7_spec.get("manifest_status_values") != osts.get("phase7_manifest_status_values"):
            probs15.append("round-spec phase7 status values drift from the live manifest")
    for name, store in ncs.items():
        if isinstance(store, dict) and store.get("used_as_input") is not False:
            probs15.append("non_canonical_stores_disclosed.%s does not declare used_as_input=false"
                           % name)
    add("C15", not probs15,
        "stores_present=%s phase7_symbols=%d phase7_statuses=%s phase7_pit_disclaimer=%s "
        "phase7_market_cap_files=%s phase5_assets=%s phase4_assets=%s phase10_lifecycle_rows=%s "
        "ml4t_panel_rows=%s brief_history_files=%s problems=%s"
        % (present_stores, len(osts.get("phase7_kline_symbols") or []),
           osts.get("phase7_manifest_status_values"), osts.get("phase7_provenance_disclaims_pit_universe"),
           osts.get("phase7_market_cap_like_files"), osts.get("phase5_asset_tokens"),
           osts.get("phase4_asset_tokens"), osts.get("phase10_lifecycle_rows"),
           osts.get("ml4t_p3_panel_rows"), osts.get("daily_brief_dated_history_files"),
           probs15 if probs15 else "ok"))

    return {"family_id": FAMILY, "round_id": ROUND, "task_id": TASK,
            "results_root": results_root, "raw_root": raw_root,
            "measured_raw": raw,
            "measured_points_of_truth": pot,
            "measured_record": rec,
            "measured_other_stores": osts,
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
                "cross_section_breadth_status": "SUFFICIENT"})),
        "required_data_available_flipped_true": lambda d: _tamper_spec(d, lambda s: s[
            "prerequisite_gate"].update({
                "required_data_available": True, "outcome": "PREREQUISITE_PRESENT",
                "missing": []})),
        "spot_price_source_claimed_present": lambda d: _tamper_spec(d, lambda s: s[
            "universe_registration"].update({"spot_price_source_available": True})),
        "market_cap_claimed_present": lambda d: _tamper_spec(d, lambda s: s[
            "universe_registration"].update({"market_cap_data_available": True})),
        "listing_metadata_claimed_present": lambda d: _tamper_spec(d, lambda s: s[
            "universe_registration"].update({"listing_metadata_available": True})),
        "matrix_universe_item_flipped_to_present": lambda d: _tamper_spec(d, lambda s: _matrix_set(
            s, "universe_point_in_time_coin_cross_section_listing_delisting", "PRESENT")),
        "breadth_claimed_sufficient": lambda d: _tamper_spec(d, lambda s: _matrix_set(
            s, BREADTH_ITEM, "SUFFICIENT")),
        "window_claimed_record_faithful": lambda d: _tamper_spec(d, lambda s: s[
            "data"].update({"record_sample_dates_underspecified": False})),
        "searched_axis_mislabelled_user_fixed": lambda d: _tamper_spec(
            d, lambda s: s["dca_domain"].update({"spacing_pct_status": "USER_FIXED"})),
        "dca_grid_truncated_to_47": lambda d: _tamper_spec(
            d, lambda s: s["dca_domain"]["grid"].pop()),
    }
    results = []
    ok = True
    for name, mutate in variants.items():
        tmp = tempfile.mkdtemp(prefix="xq-lowvol-prereq-selftest-")
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


def _kw_gz(path, row):
    with gzip.GzipFile(path, "wb", mtime=0) as gz:
        gz.write(json.dumps(row).encode() + b"\n")


def raw_fixture_control(results_root, raw_root):
    """Measurement-side non-vacuousness control.

    Build a temp raw tree that carries what this record would need: a second venue, a
    spot market on the same venue, a much broader symbol set, and priced-cross-section /
    spot / market-cap / turnover / listing / membership / perpetual-extra reference
    datasets. The raw-side checks must flip to FAIL.
    """
    tmp = tempfile.mkdtemp(prefix="xq-lowvol-prereq-rawfix-")
    try:
        fixture = os.path.join(tmp, "raw")
        shutil.copytree(raw_root, fixture,
                        ignore=shutil.ignore_patterns(".DS_Store", "__pycache__"))
        # a second venue and a spot market on the same venue
        os.makedirs(os.path.join(fixture, "okx", "swap"))
        os.makedirs(os.path.join(fixture, "binance", "spot"))
        # a broader symbol set (above the four-name survivor list)
        extra = ["ADAUSDT", "DOGEUSDT", "LTCUSDT", "XRPUSDT", "XLMUSDT", "TRXUSDT"]
        for i, sym in enumerate(extra):
            d = os.path.join(fixture, "binance", "usdm", "klines", sym, "1d")
            os.makedirs(d)
            start = 1640995200000  # 2022-01-01 UTC
            _kw_gz(os.path.join(d, "%s-1d-2022-01.jsonl.gz" % sym),
                   {"open_time_ms": start, "close_time_ms": start + 86399999, "open": "1.0",
                    "high": "1.0", "low": "1.0", "close": "1.0", "volume": "1.0"})
            os.makedirs(os.path.join(fixture, "binance", "usdm", "funding", sym))
        cfg_path = os.path.join(fixture, "_meta", "CONFIG.json")
        cfg = _load_json(cfg_path)
        cfg["symbols"] = sorted(cfg["symbols"] + extra)
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=1)
        # the datasets this record's method would need, as reference trees
        ref = {
            "universe_membership": "universe_membership_daily.jsonl.gz",
            "listing_status": "listing_status_history.jsonl.gz",
            "spot_closes": "spot_daily_closes.jsonl.gz",
            "market_cap": "market_cap_daily.jsonl.gz",
            "circulating_supply": "circulating_supply_daily.jsonl.gz",
            "turnover": "quote_volume_daily.jsonl.gz",
            "amihud": "amihud_illiquidity_daily.jsonl.gz",
            "open_interest": "open_interest_daily.jsonl.gz",
            "liquidation": "liquidation_constraints.jsonl.gz",
            "coinmarketcap": "coinmarketcap_daily_closes.jsonl.gz",
        }
        for d, f in ref.items():
            os.makedirs(os.path.join(fixture, "_ref", d))
            _kw_gz(os.path.join(fixture, "_ref", d, f),
                   {"symbol": "BTC", "source": "spot", "value": "1.0"})
        res = run_checks(results_root, fixture)
        failed = [c["id"] for c in res["checks"] if c["status"] == "FAIL"]
        expected = {"C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"}
        return {"raw_fixture_control": {"failed_checks": sorted(failed),
                                        "expected": sorted(expected),
                                        "detail": {c["id"]: c["detail"] for c in res["checks"]
                                                   if c["id"] in expected},
                                        "overall": res["overall"]},
                "overall": "PASS" if expected <= set(failed) else "FAIL"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _classify_hit(path):
    """Classify a host-scan hit. Returns a reason string, or None if unclassified."""
    low = path.lower()
    if "spotcheck" in low or "spot-check" in low:
        return "spelling collision ('spotcheck' contains the token 'spot')"
    if "market_capital" in low:
        return "token collision ('market_capital' inside a Dr.-Profit holdout fixture name)"
    if "dr-profit-distillation" in low or "dr-profit-investment-cognition" in low:
        return ("Dr.-Profit investment-cognition fixtures (Telegram-derived state/episode JSON), "
                "not market data")
    if "a1-1-phase9-pit-membership" in low or "a1-usdm-pit-lifecycle" in low:
        return ("derived USD-M contract-lifecycle evidence (price-free; its daily cells are all "
                "registered supported=false)")
    if "phase10-pit-bitemporal" in low:
        return "derived bitemporal lifecycle evidence for the same venue (metadata only, no prices)"
    if "klines_spot_btc_1d" in low:
        return "single-asset BTC spot daily series (phase5 derivatives study), not a cross-section"
    if "phase5-crypto-derivatives" in low:
        return "BTC/ETH derivatives series (phase5 study), two assets only"
    if "phase7-alpha-research" in low:
        return ("single-venue USD-M 12-symbol survivor panel (see measured_other_stores.phase7); "
                "its own provenance disclaims point-in-time completeness")
    if "phase13-production-ops" in low:
        return "OMS process-lifecycle replay file, not market data"
    if "gateway.lifecycle" in low or "profile_backup" in low:
        return "Hermes gateway process lifecycle files"
    if "quant-runtime-pipeline" in low:
        return "this board's own runtime/evidence artifacts (repo-internal)"
    if "hermes-capability-development" in low:
        return "another board's attachment fixture"
    if "ml4t-real-evidence-remediation" in low:
        return "derived feature panels built from the four local contracts"
    if "phase4-market-microstructure" in low or "phase3-portfolio-risk" in low:
        return "BTC/ETH-only merged daily panels"
    if "HERMES_CUADRIVER_READINESS" in path:
        return "Dr.-Profit readiness-session fixture"
    if "daily-crypto-brief" in low:
        return "current-vintage CMC brief snapshots (per-run, no history panel)"
    return None


def host_scan():
    """House-wide search for a priced spot coin-cross-section / market-cap /
    point-in-time membership dataset (read-only, bounded depth)."""
    roots = [(os.path.join(HOME, "workspace"), 6), (EXPANSION, 5), ("/Volumes/ResearchData", 5),
             (os.path.join(HOME, ".hermes"), 6),
             (os.path.join(HOME, "workspace", "qlib-apple-container"), 6)]
    skip_dirs = {"node_modules", "__pycache__", ".git", "venvs", "site-packages", ".venv",
                 "Photos Library.photoslibrary", "build-cache", ".fseventsd", ".Trash",
                 "migration-backups", "backups", "tmp", ".pytest_cache", "logs"}
    exts = (".csv", ".parquet", ".jsonl", ".gz", ".bin", ".feather", ".h5", ".npy", ".json",
            ".txt", ".xls", ".xlsx", ".db")
    strict_hits, loose_hits, helper_hits, scanned, skipped = [], [], [], [], []
    for root, max_depth in roots:
        if not os.path.isdir(root):
            skipped.append(root)
            continue
        scanned.append(root)
        for dp, dn, fn in os.walk(root):
            rel = os.path.relpath(dp, root)
            if rel.count(os.sep) >= max_depth:
                dn[:] = []
                continue
            dn[:] = [d for d in dn if not d.startswith(".") and d not in skip_dirs]
            for f in fn:
                if not f.lower().endswith(exts):
                    continue
                fl = f.lower()
                path = os.path.join(dp, f)
                if any(t in fl for t in MARKET_CAP_TOKENS):
                    strict_hits.append(path)
                elif any(t in fl for t in PRICE_SOURCE_TOKENS):
                    strict_hits.append(path)
                elif any(t in fl for t in UNIVERSE_TOKENS + LISTING_TOKENS):
                    loose_hits.append(path)
                elif any(t in fl for t in ("lifecycle", "membership")):
                    helper_hits.append(path)
    all_hits = sorted(set(strict_hits) | set(loose_hits) | set(helper_hits))
    classified = {}
    for p in all_hits:
        reason = _classify_hit(p)
        classified.setdefault(reason, []).append(p)
    unclassified = sorted(classified.pop(None, []))
    return {"roots_scanned": scanned, "skipped_roots": skipped,
            "market_cap_or_spot_cross_section_name_hits": sorted(set(strict_hits)),
            "universe_or_listing_name_hits": sorted(set(loose_hits)),
            "lifecycle_or_membership_helper_hits": sorted(set(helper_hits))[:40],
            "hit_count": len(all_hits),
            "classification": {k: sorted(v) for k, v in sorted(classified.items())},
            "class_count": len(classified),
            "unclassified": unclassified,
            "note": ("canonical raw is the only market-data source; the a1-*/phase10 dirs hold "
                     "derived USD-M contract-lifecycle boundary evidence plus a daily cell ledger "
                     "whose cells are all supported=false and carry no prices; the phase7 panel is "
                     "a 12-symbol single-venue USD-M survivor list that disclaims point-in-time "
                     "completeness - none is a priced point-in-time coin cross-section")}


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


def _matrix_set(spec, item, status):
    for row in spec["prerequisite_gate"]["required_data_matrix"]:
        if row.get("item") == item:
            row["status"] = status


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
    ap.add_argument("--host-scan", action="store_true")
    args = ap.parse_args(argv)

    if args.host_scan:
        out = host_scan()
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

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
