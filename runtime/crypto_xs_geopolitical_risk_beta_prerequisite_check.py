#!/usr/bin/env python3
"""Prerequisite-gate read-back checker for the family

    crypto-cross-sectional-geopolitical-risk-beta-premium-weekly-2026-08-31

Card t_cc8b557f terminalized this family as TECHNICAL_INCOMPLETE because the
record's required data is not in the canonical raw:

  * the record's universe is a **point-in-time CoinMarketCap cross-section of
    1,980 cryptocurrencies** (active AND dead coins) with market capitalization
    >= USD 1m and >= 60 days of trading history, ranked weekly into five
    quintiles - the raw holds four BINANCE USD-M perpetual contracts;
  * the sorting variable's regressor is the **daily percentage change of the
    Caldara-Iacoviello Geopolitical Risk (GPR) index** - no GPR series (and no
    GPR-named dataset) exists on this machine;
  * market capitalization and trading volume (source eligibility filters AND the
    value-weighted variant) do not exist: no market-cap dataset, no
    quote-volume/turnover field anywhere in the stored row shapes;
  * the record's registered sample runs **2014-02-03..2021-12-12** while the
    canonical raw starts 2022-01-01, i.e. the registered sample window has ZERO
    overlap with the raw;
  * there is no U.S. risk-free rate / T-bill proxy and no crypto market / size /
    momentum factor return series, both of which the registered 21-day beta
    regression needs as regressors.

Running the local four-contract perpetual panel instead would change the
registered universe, the registered sample period and the registered regressor
set. The card forbids that ("不得以近似資料、替代市場或改寫 hypothesis 硬跑" /
"不得縮減 universe 以硬造可執行性"), so nothing was launched.

This script exists so an independent reader can re-derive that determination from
the live filesystem instead of trusting prose:

  * it re-measures the canonical raw (market directories, instrument set, kline
    row shape and UTC boundaries, weekly bar anchor, funding shape, name probes
    for the missing GPR / market-cap / turnover / risk-free / factor datasets,
    and the stored schema's own statements) - the measurement side;
  * it re-reads the round's immutable artifacts and asserts they state exactly
    the contract-mandated terminal values (verdict, layer, class, yield decision,
    zero attempts, null run_id) and that the registered universe is still the
    record's own CoinMarketCap-wide cross-section, not a local four-contract
    fallback;
  * it asserts nothing was ever submitted (no attempt directory, no terminal
    sentinel) so a "0 attempts" claim cannot quietly become a fabricated run;
  * it asserts the DCA registration still carries the contract 7.2 v1.3.1
    provenance classes plus the complete 48-cell product.

Read-only: it never writes inside the results tree or the raw tree. `--self-test`
builds tampered copies in fresh temp directories and asserts the checker refuses
them (non-vacuousness control). `--raw-fixture-control` builds a temp raw tree
carrying what this record would need (a second venue, a spot market, a broad
symbol set, a GPR index reference dataset, market-cap / turnover / risk-free /
factor / point-in-time-membership reference trees and a CoinMarketCap-named
reference dataset) and asserts the raw-side checks flip to FAIL. `--host-scan`
re-runs the house-wide search for a GPR index series. Every temp tree is removed
afterwards.

Usage:
    python3 runtime/crypto_xs_geopolitical_risk_beta_prerequisite_check.py [--json]
    python3 runtime/crypto_xs_geopolitical_risk_beta_prerequisite_check.py --self-test
    python3 runtime/crypto_xs_geopolitical_risk_beta_prerequisite_check.py --raw-fixture-control
    python3 runtime/crypto_xs_geopolitical_risk_beta_prerequisite_check.py --host-scan

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

FAMILY = "crypto-cross-sectional-geopolitical-risk-beta-premium-weekly-2026-08-31"
ROUND = FAMILY + "-r1"
TASK = "t_cc8b557f"
DEFAULT_RESULTS = "/Volumes/ExpansionDrive/qlib-results"
DEFAULT_RAW = "/Volumes/ExpansionDrive/market-data-raw"
EXPANSION = "/Volumes/ExpansionDrive"
HOME = os.path.expanduser("~")
EXPECTED_SYMBOLS = ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
# The record's registered cross-section: 1,980 CoinMarketCap coins after the
# source-specified filters (active and dead coins), ranked weekly into quintiles.
RECORD_UNIVERSE_SIZE = 1980
QUINTILE_COUNT = 5
# Bare structural minimum for a five-quintile sort (each quintile non-empty).
QUINTILE_MIN_NAMES = 5
RECORD_SAMPLE_START = "2014-02-03T00:00:00+00:00"
RECORD_SAMPLE_END = "2021-12-12T00:00:00+00:00"
# The exact kline row shape the raw stores: six OHLCV fields plus the convenience
# close_time_ms. No quote volume, no market cap, no trade count, no taker-buy splits.
KLINE_ROW_FIELDS = ["close", "close_time_ms", "high", "low", "open", "open_time_ms",
                    "volume"]
# Name tokens that would have to exist for this record's required data.
GPR_STRICT_TOKENS = ("gpr_index", "gpr_daily", "gprd", "caldara", "iacoviello",
                     "geopolitical_risk_index", "gpr_export", "data_gpr")
GPR_LOOSE_TOKENS = ("gpr", "geopolitical", "policy_uncertainty", "uncertainty_index",
                    "world_uncertainty", "risk_index", "epu")
MARKET_CAP_TOKENS = ("market_cap", "marketcap", "mcap", "coinmarketcap", "coin_market_cap",
                     "circulating_supply", "total_supply", "coingecko")
PRICE_SOURCE_TOKENS = ("cmc", "spot", "aggregated", "reference_price", "twap",
                       "close_price_daily", "cmc_daily", "index_price")
UNIVERSE_TOKENS = ("universe", "membership", "point_in_time", "pit_", "survivor",
                   "cross_section", "cross-sectional", "eligible", "dead_coin", "delisted")
LISTING_TOKENS = ("listing", "listed", "delist", "onboard", "trading_start", "first_trade",
                  "existence", "dead")
TURNOVER_TOKENS = ("turnover", "dollar_volume", "quote_volume", "traded_value", "volume_usd",
                   "adv_", "notional_value")
RISKFREE_TOKENS = ("risk_free", "riskfree", "t_bill", "tbill", "treasury", "dgs3mo", "dgs1mo",
                   "fred_", "sofr", "libor", "interest_rate", "usd_rate")
FACTOR_TOKENS = ("factor_return", "mkt_factor", "size_factor", "mom_factor", "smb", "hml",
                 "wml", "fama", "french_")
# instrument-export field names that would identify a listing/supply/market-cap surface
INSTRUMENT_UNIVERSE_FIELDS = ("onboard", "listing", "listed", "delivery", "expiry",
                              "supply", "market_cap", "circulating")
INVARIANT_TOKENS = ("30,000", "numeraire", "leverage 10x", "12 tranches", "tranche #12",
                    "reduce-only", "flat/kill")
MISSING_DATA_MATRIX_ITEMS = (
    "universe_point_in_time_coinmarketcap_wide", "cross_section_breadth_for_quintile_sort",
    "daily_crypto_close_returns", "market_cap_for_eligibility_and_value_weighting",
    "trading_volume_for_eligibility", "us_risk_free_rate_tbill_proxy",
    "daily_gpr_index_and_percentage_changes", "crypto_market_size_momentum_factor_returns",
    "timestamp_alignment_gpr_publication_convention", "target_sample_2014_2021",
    "weekly_point_in_time_membership_and_ranks",
    "shortability_borrow_or_perpetual_availability", "falsification_battery_benchmarks",
    "transaction_cost_convention")
DECISIVE_ITEMS = ("universe_point_in_time_coinmarketcap_wide",
                  "daily_gpr_index_and_percentage_changes", "target_sample_2014_2021",
                  "market_cap_for_eligibility_and_value_weighting")


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

    first, last = {}, {}
    for sym in (m["klines_dataset_dirs"] or []):
        d = os.path.join(kl, sym, "1d")
        if not os.path.isdir(d):
            continue
        fs = sorted(f for f in os.listdir(d) if f.endswith(".jsonl.gz"))
        if not fs:
            continue
        first[sym] = _rows(os.path.join(d, fs[0]))[0]["open_time_ms"]
        last[sym] = _rows(os.path.join(d, fs[-1]))[-1]["open_time_ms"]
    m["klines_1d_first_by_symbol"] = {k: _iso(v) for k, v in first.items()}
    m["klines_1d_last_by_symbol"] = {k: _iso(v) for k, v in last.items()}
    m["raw_1d_window_utc"] = ([_iso(min(first.values())), _iso(max(last.values()))]
                              if first else None)
    if first:
        r0 = datetime.fromtimestamp(min(first.values()) / 1000, tz=timezone.utc)
        r1 = datetime.fromtimestamp(max(last.values()) / 1000, tz=timezone.utc)
        rec0 = datetime.fromisoformat(RECORD_SAMPLE_START)
        rec1 = datetime.fromisoformat(RECORD_SAMPLE_END)
        lo, hi = max(rec0, r0), min(rec1, r1)
        m["record_sample_window_utc"] = [rec0.isoformat(), rec1.isoformat()]
        m["sample_window_overlap_days"] = max(0, (hi - lo).days)
        m["sample_window_overlap_none"] = hi <= lo

    dw = sorted(f for f in os.listdir(os.path.join(kl, "BTCUSDT", "1w"))
                if f.endswith(".jsonl.gz"))
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
    flat = schema.replace("*", "").replace("`", "")
    low = flat.lower()
    m["schema_mentions_gpr_strict"] = sorted({t for t in GPR_STRICT_TOKENS if t in low})
    m["schema_mentions_gpr_loose"] = sorted({t for t in GPR_LOOSE_TOKENS if t in low})
    m["schema_mentions_market_cap"] = "market cap" in low or "market_cap" in low
    m["schema_mentions_risk_free"] = any(t in low for t in RISKFREE_TOKENS)
    m["schema_documents_missing_fields"] = ("quote_volume" in flat
                                            and "trade count and taker-buy splits are" in flat)
    m["schema_single_venue"] = "Binance USD-M perpetual futures, UTC" in flat
    m["schema_no_silent_gapfill"] = "No gaps are silently filled" in flat

    names, total = _all_entries(raw, 8)
    m["raw_entry_name_count"] = total
    joined = "\n".join(names)
    m["gpr_strict_tokens_found"] = sorted({t for t in GPR_STRICT_TOKENS if t in joined})
    m["gpr_loose_tokens_found"] = sorted({t for t in GPR_LOOSE_TOKENS if t in joined})
    m["market_cap_tokens_found"] = sorted({t for t in MARKET_CAP_TOKENS if t in joined})
    m["price_source_tokens_found"] = sorted({t for t in PRICE_SOURCE_TOKENS if t in joined})
    m["universe_tokens_found"] = sorted({t for t in UNIVERSE_TOKENS if t in joined})
    m["listing_tokens_found"] = sorted({t for t in LISTING_TOKENS if t in joined})
    m["turnover_tokens_found"] = sorted({t for t in TURNOVER_TOKENS if t in joined})
    m["riskfree_tokens_found"] = sorted({t for t in RISKFREE_TOKENS if t in joined})
    m["factor_tokens_found"] = sorted({t for t in FACTOR_TOKENS if t in joined})

    m["quintile_count"] = QUINTILE_COUNT
    m["quintile_min_names"] = QUINTILE_MIN_NAMES
    m["cross_section_ranking_structurally_possible"] = (
        (m["cross_section_size"] or 0) >= QUINTILE_MIN_NAMES)

    m["meta_sha256"] = {}
    for rel in ("CONFIG.json", "SCHEMA.md", "INSTRUMENTS_EXPORT.json", "STATE.json"):
        p = os.path.join(raw, "_meta", rel)
        if os.path.exists(p):
            m["meta_sha256"][rel] = _sha256_file(p)
    m["note"] = ("single venue (BINANCE USD-M perpetual), four fixed survivor contracts: klines "
                 "whose rows carry the six-field OHLCV shape only, plus that venue's funding. No "
                 "GPR / geopolitical-risk index series, no market capitalization, no turnover "
                 "field, no point-in-time CoinMarketCap cross-section, no risk-free rate proxy "
                 "and no crypto factor returns - so the record's 1,980-coin cross-section, its "
                 "21-day beta regression regressors and its 2014-2021 sample cannot be built.")
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

    # C2 - the four USD-M perpetual contracts are the only local instruments: the
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

    # C3 - the decisive one for the universe: the record's registered cross-section is a
    #      point-in-time CoinMarketCap panel of 1,980 coins, while the local cross-section
    #      is four perpetual contracts - below even the bare five-name minimum a
    #      five-quintile sort needs.
    add("C3", raw["cross_section_size"] == len(EXPECTED_SYMBOLS)
        and raw["cross_section_ranking_structurally_possible"] is False
        and raw["cross_section_size"] < QUINTILE_MIN_NAMES
        and raw["universe_tokens_found"] == []
        and raw["listing_tokens_found"] == [],
        "local_cross_section=%s quintiles=%s quintile_min_names=%s ranking_possible=%s "
        "record_universe_size=%s universe_name_tokens=%s listing_name_tokens=%s"
        % (raw["cross_section_size"], QUINTILE_COUNT, QUINTILE_MIN_NAMES,
           raw["cross_section_ranking_structurally_possible"], RECORD_UNIVERSE_SIZE,
           raw["universe_tokens_found"], raw["listing_tokens_found"]))

    # C4 - the sorting variable's regressor does not exist: no GPR / geopolitical-risk
    #      index series under the raw tree, and the stored schema mentions none. This is
    #      DECISIVE - without ΔGPR the registered beta cannot be estimated at all.
    add("C4", raw["gpr_strict_tokens_found"] == []
        and raw["gpr_loose_tokens_found"] == []
        and raw["schema_mentions_gpr_strict"] == []
        and raw["schema_mentions_gpr_loose"] == []
        and raw["price_source_tokens_found"] == [],
        "gpr_strict_name_probe=%s gpr_loose_name_probe=%s schema_gpr_strict=%s "
        "schema_gpr_loose=%s price_source_tokens=%s"
        % (raw["gpr_strict_tokens_found"], raw["gpr_loose_tokens_found"],
           raw["schema_mentions_gpr_strict"], raw["schema_mentions_gpr_loose"],
           raw["price_source_tokens_found"]))

    # C5 - market capitalization and trading volume are absent. The record needs both for
    #      source-style eligibility AND for the value-weighted variant, and the stored
    #      kline rows carry six OHLCV fields only (no quote volume / turnover).
    add("C5", raw["market_cap_tokens_found"] == []
        and raw["turnover_tokens_found"] == []
        and raw["instrument_universe_field_hits"] == []
        and raw["klines_row_field_set"] == KLINE_ROW_FIELDS
        and raw["schema_documents_missing_fields"] is True
        and raw["schema_mentions_market_cap"] is False,
        "market_cap_tokens=%s turnover_tokens=%s instrument_supply_or_cap_fields=%s "
        "kline_row_fields=%s schema_documents_missing_fields=%s"
        % (raw["market_cap_tokens_found"], raw["turnover_tokens_found"],
           raw["instrument_universe_field_hits"], raw["klines_row_field_set"],
           raw["schema_documents_missing_fields"]))

    # C6 - the two remaining regressor families are absent: no U.S. risk-free / T-bill
    #      proxy (needed for the registered daily EXCESS returns) and no crypto market /
    #      size / momentum factor return series (needed as regression controls).
    add("C6", raw["riskfree_tokens_found"] == [] and raw["factor_tokens_found"] == []
        and raw["schema_mentions_risk_free"] is False,
        "riskfree_tokens=%s factor_tokens=%s schema_mentions_risk_free=%s"
        % (raw["riskfree_tokens_found"], raw["factor_tokens_found"],
           raw["schema_mentions_risk_free"]))

    # C7 - the record's registered sample (2014-02-03..2021-12-12) has zero overlap with
    #      the canonical raw's measured daily window.
    add("C7", raw.get("sample_window_overlap_none") is True
        and raw.get("sample_window_overlap_days") == 0
        and (raw.get("raw_1d_window_utc") or [""])[0] >= "2022-01-01",
        "record_sample=%s raw_window=%s overlap_days=%s"
        % (raw.get("record_sample_window_utc"), raw.get("raw_1d_window_utc"),
           raw.get("sample_window_overlap_days")))

    # ---- artifact side
    spec_path = os.path.join(round_dir, "round-spec.json")
    verdict_path = os.path.join(round_dir, "verdict.json")
    spec = _load_json(spec_path) if os.path.exists(spec_path) else {}
    verdict = _load_json(verdict_path) if os.path.exists(verdict_path) else {}

    # C8 - the registration still states the record's own CoinMarketCap-wide cross-section
    #      and its registered regressors (it was NOT shrunk to the locally available four
    #      contracts) and records the missing surfaces as unavailable.
    gate = spec.get("prerequisite_gate") or {}
    univ = spec.get("universe_registration") or {}
    sig = spec.get("signal_semantics") or {}
    meas = gate.get("measured_available") or {}
    matrix = {i.get("item"): i for i in (gate.get("required_data_matrix") or [])
              if isinstance(i, dict)}
    probes = meas.get("name_probe_hits") or {}
    ok8 = (
        spec.get("family_id") == FAMILY and spec.get("round_id") == ROUND
        and spec.get("kanban_task_id") == TASK
        and gate.get("outcome") == "PREREQUISITE_MISSING"
        and gate.get("verdict") == "TECHNICAL_INCOMPLETE"
        and gate.get("required_data_available") is False
        and gate.get("attempts_launched") == 0
        and set(MISSING_DATA_MATRIX_ITEMS) <= set(matrix)
        and all(matrix[k].get("status") for k in MISSING_DATA_MATRIX_ITEMS)
        and all(matrix[k].get("status") == "ABSENT" for k in DECISIVE_ITEMS)
        and matrix["cross_section_breadth_for_quintile_sort"].get("status")
        == "STRUCTURALLY_INSUFFICIENT"
        and matrix["daily_gpr_index_and_percentage_changes"].get("status") == "ABSENT"
        and matrix["us_risk_free_rate_tbill_proxy"].get("status") == "ABSENT"
        and matrix["crypto_market_size_momentum_factor_returns"].get("status") == "ABSENT"
        and matrix["target_sample_2014_2021"].get("status") == "ABSENT"
        and matrix["universe_point_in_time_coinmarketcap_wide"].get("status") == "ABSENT"
        and univ.get("universe_shrunk_to_local_list") is False
        and univ.get("target_universe_required_size") == RECORD_UNIVERSE_SIZE
        and univ.get("quintile_count") == QUINTILE_COUNT
        and univ.get("quintile_sort_minimum_names") == QUINTILE_MIN_NAMES
        and univ.get("cross_section_available") == len(EXPECTED_SYMBOLS)
        and univ.get("cross_section_ranking_structurally_possible") is False
        and univ.get("gpr_dataset_available") is False
        and univ.get("market_cap_selection_data_available") is False
        and univ.get("turnover_data_available") is False
        and univ.get("risk_free_data_available") is False
        and univ.get("factor_returns_available") is False
        and univ.get("point_in_time_membership_available") is False
        and univ.get("delisted_assets_present") is False
        and univ.get("target_price_source_available") is False
        and univ.get("sample_window_overlap_days") == 0
        and univ.get("weekly_sampling_required") == "weekly (quintile rebalance)"
        and sig.get("cross_section_ranking_structurally_possible") is False
        and sig.get("cross_section_available") == len(EXPECTED_SYMBOLS)
        and sig.get("quintile_count") == QUINTILE_COUNT
        and sig.get("direction") == "long-short (bottom beta_GPR quintile long / top " \
                                     "beta_GPR quintile short)"
        and (spec.get("expected") or {}).get("status") == "not_computable"
        and (spec.get("strategy_domain") or {}).get("status") == "not_registered"
        and (spec.get("launch") or {}).get("attempts_launched") == 0
        and meas.get("cross_section_size") == len(EXPECTED_SYMBOLS)
        and all(v == [] for v in probes.values()) and bool(probes)
    )
    add("C8", bool(ok8),
        "round-spec ids/gate=%s required_available=%s matrix_items=%d decisive_absent=%s "
        "required_size=%s available=%s ranking_possible=%s shrunk=%s probes_empty=%s "
        "direction=%s"
        % (gate.get("outcome"), gate.get("required_data_available"), len(matrix),
           all(matrix.get(k, {}).get("status") == "ABSENT" for k in DECISIVE_ITEMS),
           univ.get("target_universe_required_size"), univ.get("cross_section_available"),
           univ.get("cross_section_ranking_structurally_possible"),
           univ.get("universe_shrunk_to_local_list"),
           all(v == [] for v in probes.values()) if probes else None, sig.get("direction")))

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
                "target_universe_required_size": 4,
                "cross_section_ranking_structurally_possible": True})),
        "required_data_available_flipped_true": lambda d: _tamper_spec(d, lambda s: s[
            "prerequisite_gate"].update({
                "required_data_available": True, "outcome": "PREREQUISITE_PRESENT",
                "missing": []})),
        "gpr_dataset_claimed_present": lambda d: _tamper_spec(d, lambda s: s[
            "universe_registration"].update({"gpr_dataset_available": True})),
        "market_cap_claim_present": lambda d: _tamper_spec(d, lambda s: s[
            "universe_registration"].update({"market_cap_selection_data_available": True})),
        "matrix_gpr_item_flipped_to_present": lambda d: _tamper_spec(d, lambda s: _matrix_set(
            s, "daily_gpr_index_and_percentage_changes", "PRESENT")),
        "sample_window_overlap_claimed": lambda d: _tamper_spec(d, lambda s: s[
            "universe_registration"].update({"sample_window_overlap_days": 1200})),
        "searched_axis_mislabelled_user_fixed": lambda d: _tamper_spec(
            d, lambda s: s["dca_domain"].update({"spacing_pct_status": "USER_FIXED"})),
        "dca_grid_truncated_to_47": lambda d: _tamper_spec(
            d, lambda s: s["dca_domain"]["grid"].pop()),
    }
    results = []
    ok = True
    for name, mutate in variants.items():
        tmp = tempfile.mkdtemp(prefix="xq-gprbeta-prereq-selftest-")
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

    Build a temp raw tree that carries what this record would need: a second venue, a spot
    market on the same venue, a much broader symbol set, a GPR index reference dataset, and
    market-cap / turnover / risk-free / factor / point-in-time-membership / CoinMarketCap
    reference datasets. The raw-side checks must flip to FAIL.
    """
    tmp = tempfile.mkdtemp(prefix="xq-gprbeta-prereq-rawfix-")
    try:
        fixture = os.path.join(tmp, "raw")
        shutil.copytree(raw_root, fixture,
                        ignore=shutil.ignore_patterns(".DS_Store", "__pycache__"))
        # a second venue and a spot market on the same venue
        os.makedirs(os.path.join(fixture, "okx", "swap"))
        os.makedirs(os.path.join(fixture, "binance", "spot"))
        # a broader symbol set with a pre-2022 daily bar (above the five-name quintile floor)
        extra = ["ADAUSDT", "DOGEUSDT", "LTCUSDT", "XRPUSDT", "XLMUSDT", "TRXUSDT"]
        for i, sym in enumerate(extra):
            d = os.path.join(fixture, "binance", "usdm", "klines", sym, "1d")
            os.makedirs(d)
            start = 1391385600000 if i == 0 else 1640995200000  # 2014-02-03 for ADAUSDT
            _kw_gz(os.path.join(d, "%s-1d-%s.jsonl.gz" % (sym, "2014-02" if i == 0 else "2022-01")),
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
            "gpr_index": "caldara_iacoviello_gpr_daily.jsonl.gz",
            "market_cap": "market_cap_daily.jsonl.gz",
            "turnover": "quote_volume_daily.jsonl.gz",
            "universe_membership": "universe_membership_daily.jsonl.gz",
            "risk_free": "us_tbill_risk_free_daily.jsonl.gz",
            "factor_returns": "crypto_mkt_size_mom_factor_returns.jsonl.gz",
            "coinmarketcap": "coinmarketcap_daily_closes.jsonl.gz",
        }
        for d, f in ref.items():
            os.makedirs(os.path.join(fixture, "_ref", d))
            _kw_gz(os.path.join(fixture, "_ref", d, f),
                   {"symbol": "BTC", "source": "GPR", "value": "1.0"})
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


def host_scan():
    """House-wide search for a GPR / geopolitical-risk index series (read-only).

    The canonical raw is the only market-data source; this reports every
    data-shaped hit so the evidence can state exactly what exists.
    """
    roots = [os.path.join(HOME, "workspace"), EXPANSION, "/Volumes/ResearchData",
             os.path.join(HOME, ".hermes"), os.path.join(HOME, "workspace", "qlib-apple-container")]
    exts = (".csv", ".parquet", ".jsonl", ".gz", ".bin", ".feather", ".h5", ".npy", ".json",
            ".txt", ".xls", ".xlsx", ".db")
    strict_hits, loose_hits, scanned, skipped = [], [], [], []
    for root in roots:
        if not os.path.isdir(root):
            skipped.append(root)
            continue
        scanned.append(root)
        for dp, dn, fn in os.walk(root):
            dn[:] = [d for d in dn if not d.startswith(".") and d not in
                     ("node_modules", "__pycache__", ".git", "venvs", "site-packages")]
            for f in fn:
                if not f.lower().endswith(exts):
                    continue
                fl = f.lower()
                if any(t in fl for t in GPR_STRICT_TOKENS):
                    strict_hits.append(os.path.join(dp, f))
                elif any(t in fl for t in GPR_LOOSE_TOKENS):
                    loose_hits.append(os.path.join(dp, f))
    return {"roots_scanned": scanned, "skipped_roots": skipped,
            "gpr_index_series_name_hits": sorted(set(strict_hits)),
            "loose_name_hits": sorted(set(loose_hits)),
            "note": ("canonical raw is the only market-data source; a loose-name hit is "
                     "reported for disclosure and is not a GPR index series unless it "
                     "matches the strict probe")}


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
