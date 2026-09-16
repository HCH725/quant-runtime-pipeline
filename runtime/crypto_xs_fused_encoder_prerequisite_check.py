#!/usr/bin/env python3
"""Prerequisite-gate read-back checker for the family

    crypto-cross-sectional-momentum-fused-encoder-2026-08-31

Card t_51c8c704 terminalized this family as TECHNICAL_INCOMPLETE because the
record's required data is not in the canonical raw:

  * the record fixes a 10-crypto target universe selected by market
    capitalization at end-Dec-2019 (BTC, ETH, DOGE, DGB, LTC, XLM, XRP, XMR, XEM,
    DASH) - the raw holds two of them (BTC, ETH) plus two assets the record never
    registers (BNB, SOL);
  * the record's method is a FUSED/TRANSFER encoder whose source encoder is
    pre-trained on the BIS FX dataset (30 currency pairs, May-2000..Dec-2021) -
    there is no FX data anywhere on this machine;
  * the record's target sample is 2016-01-01..2021-12-31 while the canonical raw
    starts 2022-01-01, i.e. the registered sample window has ZERO overlap with
    the raw;
  * the registered price source is CoinMarketCap daily closing prices downsampled
    to Wednesday weeks; the raw is one venue's USD-M perpetual klines (Monday-
    anchored weekly bars, no quote volume, no market capitalization).

Running the local four-contract perpetual panel instead would change the
registered universe, the registered price source and the registered sample
period, and would delete the transfer identity that the record's falsification
battery tests. The card forbids that ("不得以近似資料、替代市場或改寫 hypothesis
硬跑" / "不得縮減 universe 以硬造可執行性"), so nothing was launched.

This script exists so an independent reader can re-derive that determination from
the live filesystem instead of trusting prose:

  * it re-measures the canonical raw (market directories, instrument set, kline
    row shape and UTC boundaries, weekly bar anchor, funding shape, name probes
    for the missing universe / FX source / market-cap / price-source datasets,
    and the stored schema's own statements) - the measurement side;
  * it re-reads the round's immutable artifacts and asserts they state exactly
    the contract-mandated terminal values (verdict, layer, class, yield decision,
    zero attempts, null run_id) and that the registered universe is still the
    record's own 10-asset cross-section, not a local four-contract fallback;
  * it asserts nothing was ever submitted (no attempt directory, no terminal
    sentinel) so a "0 attempts" claim cannot quietly become a fabricated run;
  * it asserts the DCA registration still carries the contract 7.2 v1.3.1
    provenance classes plus the complete 48-cell product.

Read-only: it never writes inside the results tree or the raw tree. `--self-test`
builds tampered copies in fresh temp directories and asserts the checker refuses
them (non-vacuousness control). `--raw-fixture-control` builds a temp raw tree
carrying what this record would need (a second venue, a spot market, the eight
missing record assets with a pre-2022 daily window, an FX reference dataset,
market-cap / turnover / universe-membership reference trees and a
CoinMarketCap-named reference dataset) and asserts the raw-side checks flip to
FAIL. `--host-scan` re-runs the house-wide search for the record's universe and
for FX data. Every temp tree is removed afterwards.

Usage:
    python3 runtime/crypto_xs_fused_encoder_prerequisite_check.py [--json]
    python3 runtime/crypto_xs_fused_encoder_prerequisite_check.py --self-test
    python3 runtime/crypto_xs_fused_encoder_prerequisite_check.py --raw-fixture-control
    python3 runtime/crypto_xs_fused_encoder_prerequisite_check.py --host-scan

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

FAMILY = "crypto-cross-sectional-momentum-fused-encoder-2026-08-31"
ROUND = FAMILY + "-r1"
TASK = "t_51c8c704"
DEFAULT_RESULTS = "/Volumes/ExpansionDrive/qlib-results"
DEFAULT_RAW = "/Volumes/ExpansionDrive/market-data-raw"
EXPANSION = "/Volumes/ExpansionDrive"
HOME = os.path.expanduser("~")
EXPECTED_SYMBOLS = ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
# The record's fixed target universe (10 assets selected by market cap at end-Dec-2019).
RECORD_UNIVERSE = ["BTC", "ETH", "DOGE", "DGB", "LTC", "XLM", "XRP", "XMR", "XEM", "DASH"]
UNIVERSE_PRESENT_EXPECTED = ["BTC", "ETH"]
UNIVERSE_ABSENT_EXPECTED = ["DASH", "DGB", "DOGE", "LTC", "XEM", "XLM", "XMR", "XRP"]
RECORD_SAMPLE_START = "2016-01-01T00:00:00+00:00"
RECORD_SAMPLE_END = "2021-12-31T00:00:00+00:00"
# The exact kline row shape the raw stores: six OHLCV fields plus the convenience
# close_time_ms. No quote volume, no trade count, no taker-buy splits.
KLINE_ROW_FIELDS = ["close", "close_time_ms", "high", "low", "open", "open_time_ms",
                    "volume"]
# Name tokens that would have to exist for this record's required data.
FX_TOKENS = ("bis", "fx", "forex", "currency", "currencies", "eurusd", "usdjpy",
             "gbpusd", "audusd", "usdchf", "usdcad", "nzdusd", "exchange_rate",
             "fx_pair", "ecb", "dxy", "usd_index")
MARKET_CAP_TOKENS = ("market_cap", "marketcap", "mcap", "coinmarketcap", "coin_market_cap",
                     "circulating_supply", "total_supply", "coingecko")
PRICE_SOURCE_TOKENS = ("cmc", "spot", "aggregated", "reference_price", "twap",
                       "close_price_daily", "cmc_daily", "index_price")
UNIVERSE_TOKENS = ("universe", "membership", "point_in_time", "pit_", "survivor",
                   "cross_section", "cross-sectional", "eligible")
LISTING_TOKENS = ("listing", "listed", "delist", "onboard", "trading_start", "first_trade",
                  "existence")
TURNOVER_TOKENS = ("turnover", "dollar_volume", "quote_volume", "traded_value", "volume_usd",
                   "adv_", "notional_value")
# instrument-export field names that would identify a listing/supply/market-cap surface
INSTRUMENT_UNIVERSE_FIELDS = ("onboard", "listing", "listed", "delivery", "expiry",
                              "supply", "market_cap", "circulating")
INVARIANT_TOKENS = ("30,000", "numeraire", "leverage 10x", "12 tranches", "tranche #12",
                    "reduce-only", "flat/kill")
MISSING_DATA_MATRIX_ITEMS = (
    "target_universe_10_fixed", "market_cap_universe_selection", "daily_closing_prices_cmc",
    "target_sample_2016_2021", "weekly_wednesday_sampling", "source_dataset_bis_fx_30_pairs",
    "source_encoder_pretraining_representation", "volatility_normalized_return_inputs",
    "cross_section_breadth_for_ranking", "transaction_cost_convention",
    "falsification_benchmarks_and_transfer_control")
CROSS_SECTION_REQUIRED_MINIMUM = 10


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
    d1 = sorted(f for f in (os.listdir(os.path.join(kl, "BTCUSDT", "1d"))
                            if os.path.isdir(os.path.join(kl, "BTCUSDT", "1d")) else [])
                if f.endswith(".jsonl.gz"))
    if d1:
        rows1 = _rows(os.path.join(kl, "BTCUSDT", "1d", d1[0]))
        opens = [r["open_time_ms"] for r in rows1]
        m["klines_row_field_set"] = sorted(rows1[-1].keys())
        m["klines_1d_first_open_utc"] = _iso(opens[0])
        m["klines_1d_first_open_mod_86400_seconds"] = [o % 86400000 // 1000 for o in opens[:3]]
        m["klines_1d_open_step_seconds"] = sorted(Counter(
            (opens[i + 1] - opens[i]) // 1000 for i in range(len(opens) - 1)).keys())[:4]
        m["klines_1d_last_open_utc"] = _iso(_rows(os.path.join(kl, "BTCUSDT", "1d",
                                                              d1[-1]))[-1]["open_time_ms"])
    # earliest / latest daily bar across every local dataset (the raw's own window)
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
    r0 = datetime.fromtimestamp(min(first.values()) / 1000, tz=timezone.utc) if first else None
    r1 = datetime.fromtimestamp(max(last.values()) / 1000, tz=timezone.utc) if last else None
    rec0 = datetime.fromisoformat(RECORD_SAMPLE_START)
    rec1 = datetime.fromisoformat(RECORD_SAMPLE_END)
    if r0 and r1:
        lo, hi = max(rec0, r0), min(rec1, r1)
        m["record_sample_window_utc"] = [rec0.isoformat(), rec1.isoformat()]
        m["sample_window_overlap_days"] = max(0, (hi - lo).days)
        m["sample_window_overlap_none"] = hi <= lo

    dw = sorted(f for f in (os.listdir(os.path.join(kl, "BTCUSDT", "1w"))
                            if os.path.isdir(os.path.join(kl, "BTCUSDT", "1w")) else [])
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
    flat = schema.replace("*", "")
    m["schema_utc_documented"] = ("open_time_ms` is the bar open time in epoch ms, UTC" in schema
                                  or "open_time_ms is the bar open time in epoch ms, UTC" in flat)
    m["schema_documents_missing_fields"] = ("quote_volume" in flat
                                            and "trade count and taker-buy splits are" in flat)
    m["schema_single_venue"] = "Binance USD-M perpetual futures, UTC" in schema
    m["schema_no_silent_gapfill"] = "No gaps are silently filled" in schema

    names = _all_entries(raw, 4)
    joined = "\n".join(names)
    m["fx_tokens_found"] = sorted({t for t in FX_TOKENS if t in joined})
    m["market_cap_tokens_found"] = sorted({t for t in MARKET_CAP_TOKENS if t in joined})
    m["price_source_tokens_found"] = sorted({t for t in PRICE_SOURCE_TOKENS if t in joined})
    m["universe_tokens_found"] = sorted({t for t in UNIVERSE_TOKENS if t in joined})
    m["listing_tokens_found"] = sorted({t for t in LISTING_TOKENS if t in joined})
    m["turnover_tokens_found"] = sorted({t for t in TURNOVER_TOKENS if t in joined})

    # the record's own universe vs the locally present base assets
    local_bases = sorted({s[:-4] for s in (m["klines_dataset_dirs"] or []) if s.endswith("USDT")})
    m["local_base_assets"] = local_bases
    m["record_universe"] = list(RECORD_UNIVERSE)
    m["record_universe_present"] = sorted(set(RECORD_UNIVERSE) & set(local_bases))
    m["record_universe_absent"] = sorted(set(RECORD_UNIVERSE) - set(local_bases))
    m["record_universe_absent_name_hits"] = sorted(
        {t for t in m["record_universe_absent"] if t.lower() in joined})

    m["meta_sha256"] = {}
    for rel in ("CONFIG.json", "SCHEMA.md", "INSTRUMENTS_EXPORT.json"):
        p = os.path.join(raw, "_meta", rel)
        if os.path.exists(p):
            m["meta_sha256"][rel] = _sha256_file(p)
    m["note"] = ("single venue (BINANCE USD-M perpetual), four fixed survivor contracts: klines "
                 "whose rows carry the six-field OHLCV shape only, plus that venue's funding. No "
                 "CoinMarketCap (or any aggregated/spot reference) price source, no market "
                 "capitalization, no FX source dataset, and no bar before 2022-01-01 - so the "
                 "record's fixed 10-crypto universe, its registered price source and its "
                 "2016-2021 sample window cannot be constructed.")
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

    # C3 - the decisive one for the universe: the record's fixed 10-crypto cross-section
    #      is not present. Only BTC and ETH exist locally; the other eight registered
    #      assets appear nowhere in the raw tree.
    add("C3", raw["record_universe_present"] == UNIVERSE_PRESENT_EXPECTED
        and raw["record_universe_absent"] == UNIVERSE_ABSENT_EXPECTED
        and raw["record_universe_absent_name_hits"] == []
        and raw["cross_section_size"] == len(EXPECTED_SYMBOLS),
        "record_universe present=%s absent=%s absent_name_hits=%s local_cross_section=%s "
        "(the record fixes 10 assets; the raw holds a different four-contract list)"
        % (raw["record_universe_present"], raw["record_universe_absent"],
           raw["record_universe_absent_name_hits"], raw["cross_section_size"]))

    # C4 - the FUSED/TRANSFER source dataset (BIS daily FX, 30 pairs, May-2000..Dec-2021)
    #      does not exist: no FX-named dataset under the raw tree and no non-USDT
    #      quoted instrument. Without it the registered source encoder cannot exist.
    add("C4", raw["fx_tokens_found"] == []
        and raw["instrument_quote_currencies"] == ["USDT"]
        and raw["instrument_settlement_currencies"] == ["USDT"],
        "fx_tokens=%s quote_currencies=%s settlement_currencies=%s"
        % (raw["fx_tokens_found"], raw["instrument_quote_currencies"],
           raw["instrument_settlement_currencies"]))

    # C5 - the record's registered target sample (2016-01-01..2021-12-31) has zero overlap
    #      with the canonical raw's measured daily window.
    add("C5", raw.get("sample_window_overlap_none") is True
        and raw.get("sample_window_overlap_days") == 0
        and (raw.get("raw_1d_window_utc") or [""])[0] >= "2022-01-01",
        "record_sample=%s raw_window=%s overlap_days=%s"
        % (raw.get("record_sample_window_utc"), raw.get("raw_1d_window_utc"),
           raw.get("sample_window_overlap_days")))

    # C6 - market capitalization is absent, and the record's universe is DEFINED by it
    #      (10 cryptocurrencies selected by market capitalization at end-Dec-2019).
    add("C6", raw["market_cap_tokens_found"] == []
        and not any("supply" in f.lower() or "cap" in f.lower()
                    for f in raw["instrument_field_names"]),
        "market_cap_tokens=%s instrument_supply_or_cap_fields=%s"
        % (raw["market_cap_tokens_found"],
           [f for f in raw["instrument_field_names"]
            if "supply" in f.lower() or "cap" in f.lower()]))

    # C7 - the registered price source is absent: no CoinMarketCap / aggregated / spot /
    #      index-price reference anywhere, and the stored weekly bars are Monday-anchored
    #      while the record samples Wednesday. Daily perpetual closes DO exist for the four
    #      local contracts, which is why this is recorded as an absent price SOURCE rather
    #      than an empty table.
    add("C7", raw["price_source_tokens_found"] == []
        and raw["paths_named_other_market"] == []
        and raw.get("klines_1w_first_open_weekday") == "Mon"
        and raw["schema_documents_missing_fields"] is True,
        "price_source_tokens=%s other_market_paths=%s 1w_first_open_weekday=%s "
        "schema_documents_missing_fields=%s"
        % (raw["price_source_tokens_found"], raw["paths_named_other_market"],
           raw.get("klines_1w_first_open_weekday"), raw["schema_documents_missing_fields"]))

    # ---- artifact side
    spec_path = os.path.join(round_dir, "round-spec.json")
    verdict_path = os.path.join(round_dir, "verdict.json")
    spec = _load_json(spec_path) if os.path.exists(spec_path) else {}
    verdict = _load_json(verdict_path) if os.path.exists(verdict_path) else {}

    # C8 - the registration still states the record's own 10-asset universe and price source
    #      (it was NOT shrunk to the locally available four contracts) and records the
    #      missing surfaces as unavailable.
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
        and matrix["target_universe_10_fixed"].get("status") == "ABSENT"
        and matrix["source_dataset_bis_fx_30_pairs"].get("status") == "ABSENT"
        and matrix["target_sample_2016_2021"].get("status") == "ABSENT"
        and matrix["market_cap_universe_selection"].get("status") == "ABSENT"
        and matrix["cross_section_breadth_for_ranking"].get("status") == "STRUCTURALLY_INSUFFICIENT"
        and univ.get("universe_shrunk_to_local_list") is False
        and univ.get("target_universe_required") == RECORD_UNIVERSE
        and univ.get("target_universe_required_size") == len(RECORD_UNIVERSE)
        and univ.get("target_universe_present") == UNIVERSE_PRESENT_EXPECTED
        and univ.get("target_universe_present_size") == len(UNIVERSE_PRESENT_EXPECTED)
        and univ.get("target_universe_absent") == UNIVERSE_ABSENT_EXPECTED
        and univ.get("target_universe_absent_size") == len(UNIVERSE_ABSENT_EXPECTED)
        and univ.get("cross_section_available") == len(EXPECTED_SYMBOLS)
        and univ.get("cross_section_required_minimum") == CROSS_SECTION_REQUIRED_MINIMUM
        and univ.get("cross_section_ranking_structurally_possible") is False
        and univ.get("source_dataset_available") is False
        and univ.get("source_encoder_available") is False
        and univ.get("market_cap_selection_data_available") is False
        and univ.get("target_price_source_available") is False
        and univ.get("sample_window_overlap_days") == 0
        and univ.get("weekly_sampling_required") == "Wednesday"
        and univ.get("delisted_assets_present") is False
        and sig.get("cross_section_ranking_structurally_possible") is False
        and sig.get("cross_section_available") == len(EXPECTED_SYMBOLS)
        and sig.get("cross_section_required_minimum") == CROSS_SECTION_REQUIRED_MINIMUM
        and sig.get("direction") == "long-short (top-2 long / bottom-2 short, equal weight)"
        and (spec.get("expected") or {}).get("status") == "not_computable"
        and (spec.get("strategy_domain") or {}).get("status") == "not_registered"
        and (spec.get("launch") or {}).get("attempts_launched") == 0
        and meas.get("cross_section_size") == len(EXPECTED_SYMBOLS)
        and all(v == [] for v in probes.values()) and bool(probes)
    )
    add("C8", bool(ok8),
        "round-spec ids/gate=%s required_available=%s required/present=%s/%s ranking_possible=%s "
        "shrunk=%s matrix_items=%d probes_empty=%s direction=%s"
        % (gate.get("outcome"), gate.get("required_data_available"),
           univ.get("cross_section_required_minimum"), univ.get("cross_section_available"),
           univ.get("cross_section_ranking_structurally_possible"),
           univ.get("universe_shrunk_to_local_list"), len(matrix),
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
                "cross_section_required_minimum": 4,
                "cross_section_ranking_structurally_possible": True})),
        "required_data_available_flipped_true": lambda d: _tamper_spec(d, lambda s: s[
            "prerequisite_gate"].update({
                "required_data_available": True, "outcome": "PREREQUISITE_PRESENT",
                "missing": []})),
        "fx_source_dataset_claimed_present": lambda d: _tamper_spec(d, lambda s: s[
            "universe_registration"].update({"source_dataset_available": True,
                                             "source_encoder_available": True})),
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
        tmp = tempfile.mkdtemp(prefix="xq-fusedenc-prereq-selftest-")
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
    market on the same venue, a broader symbol set that includes the record's eight missing
    assets (with a pre-2022 daily bar), an FX reference dataset for the source encoder, and
    market-cap / turnover / universe-membership / CoinMarketCap-named reference datasets.
    The raw-side checks must flip to FAIL.
    """
    tmp = tempfile.mkdtemp(prefix="xq-fusedenc-prereq-rawfix-")
    try:
        fixture = os.path.join(tmp, "raw")
        shutil.copytree(raw_root, fixture,
                        ignore=shutil.ignore_patterns(".DS_Store", "__pycache__"))
        # a second venue and a spot market on the same venue
        os.makedirs(os.path.join(fixture, "okx", "swap"))
        os.makedirs(os.path.join(fixture, "binance", "spot"))
        # the record's eight missing assets, one of them carrying a 2016 daily bar
        missing = ["DOGEUSDT", "DGBUSDT", "LTCUSDT", "XLMUSDT", "XRPUSDT", "XMRUSDT",
                   "XEMUSDT", "DASHUSDT"]
        for i, sym in enumerate(missing):
            start = 1451606400000 if i == 0 else 1640995200000  # 2016-01-01 for DASHUSDT
            d = os.path.join(fixture, "binance", "usdm", "klines", sym, "1d")
            os.makedirs(d)
            _kw_gz(os.path.join(d, "%s-1d-%s.jsonl.gz" % (sym, "2016-01" if i == 0 else "2022-01")),
                   {"open_time_ms": start, "close_time_ms": start + 86399999, "open": "1.0",
                    "high": "1.0", "low": "1.0", "close": "1.0", "volume": "1.0"})
            os.makedirs(os.path.join(fixture, "binance", "usdm", "funding", sym))
        cfg_path = os.path.join(fixture, "_meta", "CONFIG.json")
        cfg = _load_json(cfg_path)
        cfg["symbols"] = sorted(cfg["symbols"] + missing)
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=1)
        # the datasets this record's method would need, as reference trees
        ref = {
            "bis_fx": "bis_daily_fx_30_pairs.jsonl.gz",
            "market_cap": "market_cap_daily.jsonl.gz",
            "turnover": "quote_volume_daily.jsonl.gz",
            "universe_membership": "universe_membership_daily.jsonl.gz",
            "coinmarketcap": "coinmarketcap_daily_closes.jsonl.gz",
        }
        for d, f in ref.items():
            os.makedirs(os.path.join(fixture, "_ref", d))
            _kw_gz(os.path.join(fixture, "_ref", d, f),
                   {"symbol": "EURUSD", "source": "BIS", "value": "1.0"})
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
    """House-wide search for the record's universe and for FX data (read-only).

    The canonical raw is the only market-data source; this reports every other
    data-shaped hit so the evidence can state exactly what exists.
    """
    roots = [os.path.join(HOME, "workspace"), EXPANSION, "/Volumes/ResearchData",
             os.path.join(HOME, ".hermes"), os.path.join(HOME, "workspace", "qlib-apple-container")]
    exts = (".csv", ".parquet", ".jsonl", ".gz", ".bin", ".feather", ".h5", ".npy", ".json")
    market_dir = ("kline", "ohlc", "market", "price", "spot", "raw", "data", "perp", "swap")
    fx_tokens = ("eurusd", "usdjpy", "gbpusd", "audusd", "usdchf", "forex", "currency_pair",
                 "exchange_rate", "bis_", "_bis", "fx_data")
    per_asset, fx_hits, roots_scanned, skipped = {}, [], [], []
    for root in roots:
        if not os.path.isdir(root):
            skipped.append(root)
            continue
        roots_scanned.append(root)
    for asset in RECORD_UNIVERSE:
        hits = []
        for root in roots_scanned:
            for dp, dn, fn in os.walk(root):
                rel = os.path.relpath(dp, root)
                if rel.count(os.sep) >= 5:
                    dn[:] = []
                    continue
                dn[:] = [d for d in dn if not d.startswith(".") and d not in
                         ("node_modules", "__pycache__", ".git", "venvs", "site-packages")]
                low_dp = dp.lower()
                if not any(t in low_dp for t in market_dir):
                    continue
                for f in fn:
                    up = f.upper()
                    base = os.path.splitext(up)[0]
                    if not f.lower().endswith(exts):
                        continue
                    if asset in base or up.startswith(asset + "USDT") or ("_" + asset) in up:
                        hits.append(os.path.join(dp, f))
                    if any(t in f.lower() for t in fx_tokens) or "forex" in low_dp:
                        fx_hits.append(os.path.join(dp, f))
        per_asset[asset] = sorted(set(hits))[:25]
    return {"roots_scanned": roots_scanned, "skipped_roots": skipped,
            "per_asset_data_file_hits": per_asset,
            "fx_like_files": sorted(set(fx_hits))[:25],
            "universe_absent_house_wide": sorted(a for a, h in per_asset.items() if not h),
            "note": ("canonical raw is the only market-data source; hits outside it are "
                     "non-canonical research caches, listed for disclosure and not used")}


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
