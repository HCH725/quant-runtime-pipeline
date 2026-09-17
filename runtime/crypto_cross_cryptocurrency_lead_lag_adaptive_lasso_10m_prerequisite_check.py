#!/usr/bin/env python3
"""Prerequisite-gate read-back checker for the family

    crypto-cross-cryptocurrency-lead-lag-adaptive-lasso-10m-2026-09-01

Card t_7ee25fe3 terminalises this family as TECHNICAL_INCOMPLETE: the record's
required data is not in the canonical raw at the resolution and breadth the
hypothesis is defined on.

  * the hypothesis is a **minute-frequency cross-coin lead-lag** specification
    (predictors = lagged *minute* returns of other cryptocurrencies; horizons
    "up to ten minutes"; falsification item 4 = "Test horizons from 1 through
    10 minutes"). The canonical raw's finest dataset is a 5-minute bar: 0 of
    its 28 datasets is a 1-minute (or finer) series, and the measured bar step
    of the finest dataset is 300 s. On a 300 s grid only 2 of the 10 registered
    horizons (5 and 10 minutes) can be formed at all;
  * the hypothesis is **cross-sectional** ("liquid cryptocurrencies traded on
    Binance"; portfolio use = "rank or otherwise map out-of-sample return
    forecasts into long and short positions"), and its required data asks for a
    point-in-time Binance instrument universe plus listing/delisting history.
    The canonical raw holds exactly four contracts of one venue and no
    membership, listing or delisting surface at all;
  * two further required surfaces are absent: a bid-ask spread series, and (if
    derivatives are tested) a mark/index price series - the funding rows carry
    a `mark_price` column but it is null throughout the modelled prefix, and
    there is no index-price surface anywhere in the store;
  * the falsification battery (items 1-8) is defined on the absent minute panel
    and the absent point-in-time universe; items 2, 3, 5 and 8 cannot be
    executed, item 4 cannot be formed, item 6 is 2-of-7 locally available.

This script exists so an independent reader can re-derive that determination
from the live filesystem instead of trusting prose:

  * C1-C4 re-measure the canonical raw: store identity, the on-disk kline
    dataset inventory (is any dataset at or below one minute?), the measured
    bar step of the finest dataset and what that does to the registered 1-10
    minute horizon grid, and the kline row shape;
  * C5-C6 re-measure the instrument surface (count, contract type, fee and tick
    fields, and whether any listing/delisting/status field exists) and the
    funding surface (symbols, venue, truth classes, mark-price nullness);
  * C7-C8 probe the whole raw tree by entry name and by payload content for the
    resolution / cross-section / spread tokens this record needs, and assert
    every single hit is classified (a closed table, no leftovers);
  * C9 re-measures the store's own documentation (its schema's dataset families,
    its config's interval alphabet, its inventory's own statement that no other
    Binance/klines/aggTrade store was found and that no Binance Vision copy was
    ever retained);
  * C10 asserts the measured cross-section breadth (four contracts, one venue)
    and the total absence of a point-in-time membership surface in the raw;
  * C11 re-measures the host's non-canonical stores and asserts none of them
    carries a minute-resolution crypto panel, a priced point-in-time membership
    panel or a spread surface; the lifecycle/membership files that do exist are
    classified and disclosed as measured-but-unused;
  * C12-C18 re-read the round's immutable artifacts and assert they state
    exactly the contract-mandated terminal values, that nothing was ever
    submitted, that the registered universe was NOT shrunk to the four local
    contracts, that the DCA registration still carries the contract 7.2 v1.3.1
    provenance classes plus the complete 48-cell product, and that the failure
    taxonomy was kept separated (infrastructure, not science).
  * `--verify-verbatim` resolves every `*_verbatim` leaf of the persisted
    round-spec against its declared source (card / record / contract / footer)
    and re-checks the excerpt digest map, independent of the authoring run.

Read-only: it never writes inside the results tree or the raw tree. `--self-test`
builds tampered copies in fresh temp directories and asserts the checker refuses
them (non-vacuousness control). `--raw-fixture-control` builds a temp raw tree
carrying what this record would need (1-minute bars for a wide symbol set, a
point-in-time universe surface, a listing/delisting history and a bid-ask
spread surface) and asserts the raw-side checks flip to FAIL. `--host-scan`
re-runs the house-wide search. Every temp tree is removed afterwards.

Usage:
    python3 runtime/crypto_cross_cryptocurrency_lead_lag_adaptive_lasso_10m_prerequisite_check.py [--json]
    python3 runtime/crypto_cross_cryptocurrency_lead_lag_adaptive_lasso_10m_prerequisite_check.py --measure-only
    python3 runtime/crypto_cross_cryptocurrency_lead_lag_adaptive_lasso_10m_prerequisite_check.py --verify-verbatim
    python3 runtime/crypto_cross_cryptocurrency_lead_lag_adaptive_lasso_10m_prerequisite_check.py --self-test
    python3 runtime/crypto_cross_cryptocurrency_lead_lag_adaptive_lasso_10m_prerequisite_check.py --raw-fixture-control
    python3 runtime/crypto_cross_cryptocurrency_lead_lag_adaptive_lasso_10m_prerequisite_check.py --host-scan

Exit codes: 0 = all checks PASS, 1 = at least one FAIL, 2 = usage error.
"""
import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone

FAMILY = "crypto-cross-cryptocurrency-lead-lag-adaptive-lasso-10m-2026-09-01"
ROUND = FAMILY + "-r1"
TASK = "t_7ee25fe3"
BOARD = "quant-strategy-research"
DEFAULT_RESULTS = "/Volumes/ExpansionDrive/qlib-results"
DEFAULT_RAW = "/Volumes/ExpansionDrive/market-data-raw"
DEFAULT_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_RECORD = os.path.join(os.path.expanduser("~"), ".hermes/wiki/quant", FAMILY + ".md")
PROBE_MAX_DEPTH = 8
MINUTE_OR_FINER = ("1m", "1min", "min", "1s", "10s", "30s")

# Tokens a minute-resolution cross-sectional crypto panel (or the universe /
# spread surfaces this record asks for) would have to carry. Word boundaries
# everywhere: a bare substring match would fire 'ask' on 'asks the venue' and
# 'pit' on 'capital'/'hospital'. The bare '1m' form is NOT used as a token
# ('$1m' = one million); resolved by `structured_1m` and reported separately as
# `ambiguous_bare_1m`.
RESOLUTION_PATTERNS = {
    "minute_bars": r"\bminute[-_ ]?(bars?|klines?|candles?|ohlcv)\b",
    "one_minute": r"\bone[-_ ]minute\b",
    "structured_1m": r"[\"']1m[\"']|\binterval[-_ ]?1m\b|\b1m[-_ ](bars?|klines?|candles?)\b",
    "tick_data": r"\b(ticks?|aggtrades?|trade[-_ ]?prints?)\b",
    "order_book": r"\b(order[-_ ]?book|bookticker|level[-_ ]?2|level2|depth[-_ ]?snapshot)\b",
    "bid_ask": r"\b(bid|ask|bids|asks|bid[-_ ]ask|spread|quotes?)\b",
    "latency": r"\b(latency|co-?location|matching[-_ ]engine)\b",
}
UNIVERSE_PATTERNS = {
    "membership": r"\b(membership|member[-_ ]list|point[-_ ]in[-_ ]time|pit)\b",
    "universe": r"\buniverse\b",
    "listing_delisting": r"\b(listings?|listed|delist(ed|ing)?|survivorship)\b",
    "breakpoints": r"\b(breakpoints?|quintiles?|deciles?|terciles?)\b",
}
PROBE_PATTERNS = dict(RESOLUTION_PATTERNS)
PROBE_PATTERNS.update(UNIVERSE_PATTERNS)
COMPILED = {k: re.compile(v, re.I) for k, v in PROBE_PATTERNS.items()}
RESOLUTION_TOKENS = tuple(RESOLUTION_PATTERNS)
UNIVERSE_TOKENS = tuple(UNIVERSE_PATTERNS)
AMBIGUOUS_1M = re.compile(r"(?<![\d.$])1m(?![a-z0-9])", re.I)

TEXT_EXTS = (".csv", ".jsonl", ".gz", ".json", ".txt", ".md", ".tsv", ".py",
             ".sh", ".yaml", ".yml")
DATA_EXTS = (".csv", ".jsonl", ".gz", ".parquet", ".bin", ".feather", ".h5",
             ".npy", ".arrow", ".db", ".sqlite", ".zst")
# Minute-resolution naming, for the wide filename probe over the whole workspace
# and the data volume. Matches a standalone `1m` / `1min` / `1s` / `10s` / `30s`
# / `minute` token in a file name - never `$1m` (one million).
MINUTE_NAME_RX = re.compile(
    r"(?<![0-9a-z$])(1m|1min|1s|10s|30s|minute)(?![0-9a-z])", re.I)

# Every raw file that carries a probe token, with the class that explains it.
# A file outside this table (or a token class outside it) is `unclassified` and
# fails C8: the probe is not allowed to quietly absorb a hit.
RAW_HIT_CLASSES = {
    "_meta/SCHEMA.md": "store_schema_interval_vocabulary",
    "_meta/INVENTORY.md": "store_inventory_search_scope",
    "_tools/README.md": "token_collision_prose",
    "_tools/binance_public.py": "upstream_klines_reconstruction_code",
    "_tools/market_data_sync.py": "updater_interval_map",
}
RAW_HIT_CLASS_NOTES = {
    "store_schema_interval_vocabulary":
        "SCHEMA.md documents the store's interval alphabet as `1m 5m 15m 30m 1h 4h 1d 1w`; it is "
        "the documented vocabulary, not a dataset - the store's own CONFIG.json (what the updater "
        "maintains) lists seven intervals and none of them is 1m, and the on-disk kline inventory "
        "contains no 1m dataset",
    "store_inventory_search_scope":
        "INVENTORY.md records the host-wide Spotlight sweep for `binance / klines / aggTrade / "
        "bookTicker / openInterest / tardis / market-data` and its own result - 'No other "
        "Binance/klines/tardis/aggTrade store was found on the host or on /Volumes/ExpansionDrive' "
        "and 'no local raw copy of Binance Vision was ever retained'; the same table records that "
        "the retired LEAN `minute/` directory actually held 30-minute bars",
    "token_collision_prose":
        "_tools/README.md line 37 'asks the venue for everything after it' - the verb, not a quote",
    "upstream_klines_reconstruction_code":
        "the vendored client can reconstruct coarser bars from 1-minute rows *upstream* "
        "(`_aggregate_klines(..., \"1m\", ...)`); the published store retains only the derived 5m+ "
        "bars, and INVENTORY.md states no Binance Vision copy was retained",
    "updater_interval_map":
        "market_data_sync.py carries the 1-minute millisecond constant in its interval table; the "
        "updater's own CONFIG.json does not maintain a 1m stream",
}

HOST_ROOTS = [
    "/Users/hong/workspace/phase7-alpha-research",
    "/Users/hong/workspace/phase3-portfolio-risk",
    "/Users/hong/workspace/phase4-market-microstructure",
    "/Users/hong/workspace/phase5-crypto-derivatives",
    "/Users/hong/workspace/phase9-cross-sectional-factors",
    "/Users/hong/workspace/phase12-l2-l3-execution-tca",
    "/Users/hong/workspace/phase10-pit-bitemporal",
    "/Users/hong/workspace/phase11-options-volatility",
    "/Users/hong/workspace/ml4t-real-evidence-remediation",
    "/Users/hong/workspace/a1-usdm-pit-lifecycle-20260830",
    "/Users/hong/workspace/a1-1-phase9-pit-membership-20260830",
    "/Users/hong/workspace/a1-2-prospective-pit-foundation-20260830",
    "/Users/hong/workspace/a1-3-prospective-pit-cohort-20260830",
    "/Users/hong/workspace/alpha-strategy-research",
    "/Users/hong/workspace/btc-relative-entry-score",
    "/Volumes/ExpansionDrive/daily-crypto-brief",
    os.path.join(DEFAULT_RESULTS, "_handoff/bodies"),
    os.path.join(os.path.expanduser("~"), ".hermes/wiki/quant"),
]

# Market-data-extension files on the host that carry a probe token. Each is
# classified, measured and disclosed; none is used as an input by this round.
HOST_DATA_HIT_CLASSES = {
    "phase10-pit-bitemporal/data/real_lifecycle_evidence.csv":
        "lifecycle_boundary_events_official_venue_notices",
    "a1-usdm-pit-lifecycle-20260830/lifecycle_events.csv":
        "lifecycle_boundary_events_official_venue_notices",
    "a1-1-phase9-pit-membership-20260830/membership_cells.jsonl":
        "daily_pit_membership_cells_all_unsupported",
    "a1-1-phase9-pit-membership-20260830/membership_observations.csv":
        "membership_observations_unsupported_for_daily_cell",
    "alpha-strategy-research/coverage_manifest.csv":
        "prose_lexical_collision_manifest",
}
HOST_DATA_HIT_NOTES = {
    "lifecycle_boundary_events_official_venue_notices":
        "listing/delisting boundary events parsed from official Binance notices, with explicit "
        "effective times - a boundary archive, not a continuous daily membership panel, and it "
        "carries no prices of any frequency",
    "daily_pit_membership_cells_all_unsupported":
        "the sibling PIT project's own daily cell ledger; every one of its cells is recorded "
        "`supported: false` with `membership_status: unknown`, i.e. the local PIT work itself "
        "declares daily membership unsupported",
    "membership_observations_unsupported_for_daily_cell":
        "membership observations whose own column `supported_for_daily_cell` is False in every "
        "sampled row (current-state snapshots cannot be backfilled; notice-derived points are not "
        "continuous intervals)",
    "prose_lexical_collision_manifest":
        "a candidate-coverage manifest whose rows say `Lexical collision risk with 'spread'`: the "
        "token is the manifest's own collision label, not market data",
}

# Wide filename probe: the two roots that between them hold every candidate market store on
# this machine (the workspace, and the data volume that carries the canonical raw + results).
WIDE_ROOTS = [
    "/Users/hong/workspace",
    "/Volumes/ExpansionDrive",
]

# .json files carrying a resolution token that are *not* report prose. Each is
# measured (row count, symbol coverage) and disclosed as measured-but-unused.
JSON_SNAPSHOT_CLASSES = {
    "phase12-l2-l3-execution-tca/raw/binance_futures_aggtrades_btcusdt_limit20.json":
        "single_symbol_tick_sample_20_rows",
    "phase12-l2-l3-execution-tca/raw/binance_futures_depth_btcusdt_limit20.json":
        "single_symbol_depth_snapshot_20_levels",
}
JSON_SNAPSHOT_NOTES = {
    "single_symbol_tick_sample_20_rows":
        "a 20-row aggTrades probe for BTCUSDT (one capture) - a request/sample artifact, not a "
        "panel: no other symbol, no history, no depth of book",
    "single_symbol_depth_snapshot_20_levels":
        "one depth snapshot (top 20 levels, BTCUSDT). It is the only bid/ask surface on the "
        "machine that is not prose; it is a single instant for a single symbol and cannot supply "
        "the record's spread series",
}


def _load_json(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return json.load(fh)


def _ls(path):
    if not os.path.isdir(path):
        return []
    return sorted(n for n in os.listdir(path) if not n.startswith("."))


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _sha256_text(text):
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_text(path, chunk=200000):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", errors="replace") as fh:
            return fh.read(chunk)
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read(chunk)


def _read_full(path):
    """Whole file, no truncation (a cut tail would break the last-row parse)."""
    return _read_text(path, chunk=None)


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iter_files(root, max_depth=6, cap=60000):
    base = os.path.abspath(root).rstrip(os.sep).count(os.sep)
    n = 0
    for dirpath, dirnames, filenames in os.walk(root):
        if dirpath.count(os.sep) - base >= max_depth:
            dirnames[:] = []
        dirnames.sort()
        for name in sorted(filenames):
            if name == ".DS_Store":
                continue
            yield os.path.join(dirpath, name)
            n += 1
            if n >= cap:
                return


def _all_entries(root, max_depth=PROBE_MAX_DEPTH):
    base = os.path.abspath(root).rstrip(os.sep).count(os.sep)
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        if dirpath.count(os.sep) - base >= max_depth:
            dirnames[:] = []
        dirnames.sort()
        for n in sorted(dirnames):
            out.append(os.path.relpath(os.path.join(dirpath, n), root) + "/")
        for n in sorted(filenames):
            if n != ".DS_Store":
                out.append(os.path.relpath(os.path.join(dirpath, n), root))
    return out


def _horizons_for_step(step_seconds):
    """Which of the record's 1..10 minute horizons can be formed on a grid whose
    finest bar step is `step_seconds`."""
    if not step_seconds:
        return [], list(range(1, 11))
    reach = [h for h in range(1, 11) if (h * 60) % int(round(step_seconds)) == 0]
    return reach, [h for h in range(1, 11) if h not in reach]


TIME_KEY_RX = re.compile(r"time|timestamp|^ts$|^t$|^date$|^open_time|^T$|^E$", re.I)


def _json_panel_probe(path, max_bytes=5_000_000, min_records=60):
    """Content probe: does this .json actually hold a time-series panel of at
    least `min_records` timestamped records? Token matches alone are not proof
    that a store carries a usable series."""
    try:
        if os.path.getsize(path) > max_bytes:
            return False
        with open(path, encoding="utf-8", errors="replace") as fh:
            doc = json.load(fh)
    except Exception:  # noqa: BLE001
        return False
    lists = []

    def walk(node, depth=0):
        if depth > 4:
            return
        if isinstance(node, list) and node:
            lists.append(node)
            walk(node[0], depth + 1)
        elif isinstance(node, dict):
            for key in ("data", "result", "rows", "records", "items"):
                if key in node:
                    walk(node[key], depth + 1)
            for value in node.values():
                if isinstance(value, (list, dict)):
                    walk(value, depth + 1)

    walk(doc)
    for seq in lists:
        if len(seq) < min_records or not all(isinstance(x, dict) for x in seq[:5]):
            continue
        keyed = [x for x in seq if any(TIME_KEY_RX.search(k) for k in x)]
        if len(keyed) >= min_records:
            return True
    return False


JSON_SNAPSHOT_CLASSES.update({
    "phase11-options-volatility/data/real_valid_quotes.json":
        "single_instant_single_underlying_options_chain",
})
JSON_SNAPSHOT_NOTES["single_instant_single_underlying_options_chain"] = (
    "936 BTC *options* quotes (13 expiries) all stamped at ONE instant "
    "(2026-08-28T12:19:42Z), single underlying (BTC), quote currency BTC, every row carrying "
    "bid/ask - a Phase-11 option-pricing validation input. It is a single-instant chain "
    "snapshot: not a time series, not the family's traded instruments (USD-M perpetuals), not a "
    "cross-section of coins, and therefore not the record's spread series. Measured-but-unused.")
# Panel-shaped files whose class is already explained as *not* the record's panel.
ALLOWED_PANEL_HIT_CLASSES = {"single_instant_single_underlying_options_chain"}

# Data-extension files whose *name* carries a minute token, found by the wide
# filename probe. Both are US-equity tutorial artifacts whose label means
# 1-month, not 1-minute.
WIDE_NAME_HIT_CLASSES = {
    "tmp/ml4t-p1/ml4t-src/case_studies/us_firm_characteristics/benchmark/fwd_ret_1m.json":
        "us_equity_monthly_label_benchmark_result",
    "tmp/ml4t-p1/ml4t-src/case_studies/us_firm_characteristics/benchmark/fwd_ret_1m.parquet":
        "us_equity_monthly_label_benchmark_result",
    "tmp/ml4t-p1/ml4t-src/case_studies/us_firm_characteristics/benchmark/fwd_ret_1m_win.json":
        "us_equity_monthly_label_benchmark_result",
    "tmp/ml4t-p1/ml4t-src/case_studies/us_firm_characteristics/benchmark/fwd_ret_1m_win.parquet":
        "us_equity_monthly_label_benchmark_result",
}
WIDE_NAME_HIT_NOTES = {
    "us_equity_monthly_label_benchmark_result":
        "the label is `fwd_ret_1m` = one-MONTH forward return (`periods_per_year: 12`, "
        "2006-01..2016-12, 120 periods, 795 B) from the ml4t US-firm-characteristics tutorial "
        "benchmark; US equities, monthly, and not a minute dataset - the filename token is a "
        "month, not a minute",
}


def measure_raw(raw):
    """Independent re-measurement of the canonical raw. No artifact is trusted."""
    rep = {"raw_root": raw}
    meta = os.path.join(raw, "_meta")
    rep["store_top_level"] = _ls(raw)
    rep["binance_subdirs"] = _ls(os.path.join(raw, "binance"))
    rep["usdm_subdirs"] = _ls(os.path.join(raw, "binance/usdm"))
    cfg = _load_json(os.path.join(meta, "CONFIG.json"))
    rep["documented_venue"] = cfg.get("venue")
    rep["documented_market_type"] = cfg.get("market_type")
    rep["documented_symbols"] = sorted(cfg.get("symbols", []))
    rep["documented_intervals"] = sorted(cfg.get("intervals", []))
    schema = _read_text(os.path.join(meta, "SCHEMA.md"))
    rep["schema_sha256"] = _sha256_file(os.path.join(meta, "SCHEMA.md"))
    rep["schema_dataset_sections"] = re.findall(r"^## Dataset: (.+)$", schema, re.M)
    rep["schema_documents_minute_dataset"] = bool(
        re.search(r"^## Dataset: .*(minute|1m|tick|order[-_ ]?book|spread|universe|membership)",
                  schema, re.M | re.I))
    rep["schema_interval_alphabet"] = sorted(set(
        re.findall(r"\b(1m|3m|5m|15m|30m|1h|4h|1d|1w)\b", schema)))
    inventory = _read_text(os.path.join(meta, "INVENTORY.md"))
    rep["inventory_states_no_other_store"] = bool(re.search(
        r"No other Binance/klines/tardis/aggTrade store was found", inventory))
    rep["inventory_states_no_vision_copy"] = bool(re.search(
        r"no local raw copy of Binance Vision was ever retained", inventory))
    rep["inventory_notes_lean_minute_is_30m"] = bool(re.search(
        r"`minute/` files actually hold 30-minute trade bars", inventory))

    kl = os.path.join(raw, "binance/usdm/klines")
    rep["klines_symbol_dirs"] = _ls(kl)
    per_sym = {s: _ls(os.path.join(kl, s)) for s in rep["klines_symbol_dirs"]}
    rep["intervals_per_symbol"] = per_sym
    rep["intervals_union"] = sorted({i for v in per_sym.values() for i in v})
    rep["intervals_identical_across_symbols"] = len({tuple(v) for v in per_sym.values()}) <= 1
    rep["klines_datasets"] = sum(len(v) for v in per_sym.values())
    rep["minute_datasets"] = sorted(s + "/" + i for s, v in per_sym.items() for i in v
                                    if i in MINUTE_OR_FINER)
    rep["sub_minute_datasets"] = sorted(s + "/" + i for s, v in per_sym.items() for i in v
                                        if i.endswith("s") and i[:-1].isdigit())

    # finest bar step, measured from the bar open times themselves
    steps = {}
    for s in rep["klines_symbol_dirs"]:
        for iv in per_sym.get(s, []):
            d = os.path.join(kl, s, iv)
            fs = _ls(d)
            if not fs:
                continue
            rows = [json.loads(x) for x in _read_full(os.path.join(d, fs[-1])).splitlines()[:3]]
            if len(rows) < 2:
                continue
            steps[s + "/" + iv] = (rows[1]["open_time_ms"] - rows[0]["open_time_ms"]) / 1000.0
    rep["measured_steps_seconds"] = steps
    rep["distinct_measured_steps"] = sorted(set(steps.values()))
    finest = min(steps.items(), key=lambda kv: kv[1]) if steps else (None, None)
    rep["finest_dataset"] = finest[0]
    rep["finest_step_seconds"] = finest[1]
    reach, unreach = _horizons_for_step(finest[1])
    rep["registered_horizon_grid_minutes"] = list(range(1, 11))
    rep["reachable_horizons_minutes"] = reach
    rep["unreachable_horizons_minutes"] = unreach
    rep["unreachable_horizon_count"] = len(unreach)
    rep["falsification_item_4_executable"] = unreach == []

    d5 = os.path.join(kl, "BTCUSDT", "5m")
    fs5 = sorted(_ls(d5))
    if fs5:
        first = json.loads(_read_full(os.path.join(d5, fs5[0])).splitlines()[0])
        last_files = _read_full(os.path.join(d5, fs5[-1])).splitlines()
        last = json.loads(last_files[-1])
        rep["five_m_row_keys"] = sorted(first.keys())
        rep["five_m_window_utc"] = [_iso(first["open_time_ms"]), _iso(last["open_time_ms"])]
        rep["five_m_first_open_ms"] = first["open_time_ms"]
        rep["five_m_files"] = len(fs5)
        rep["kline_row_keys"] = sorted(first.keys())
        rep["kline_extra_columns_absent"] = sorted(
            k for k in ("quote_volume", "count", "taker_buy_base", "taker_buy_quote")
            if k not in first)
        # synchronised grid at 5m across the whole local cross-section
        grid = {}
        for s in rep["klines_symbol_dirs"]:
            d = os.path.join(kl, s, "5m")
            fs = sorted(_ls(d))
            if not fs:
                continue
            f0 = json.loads(_read_full(os.path.join(d, fs[0])).splitlines()[0])
            l0 = json.loads(_read_full(os.path.join(d, fs[-1])).splitlines()[-1])
            grid[s] = (f0["open_time_ms"], l0["open_time_ms"], len(fs))
        rep["grid_5m_per_symbol"] = {k: list(v) for k, v in grid.items()}
        rep["grid_5m_identical_across_symbols"] = (
            len({(v[0], v[1], v[2]) for v in grid.values()}) == 1 and len(grid) > 1 if grid else False)

    inst_path = os.path.join(raw, "binance/usdm/instruments/usdm-perp-instruments.json")
    inst = _load_json(inst_path)
    inner = inst["instruments"]
    rows = inner if isinstance(inner, list) else list(inner.values())
    flds = [r["fields"] for r in rows]
    rep["instrument_count"] = len(rows)
    rep["instrument_ids"] = sorted(f["id"] for f in flds)
    rep["instrument_base_currencies"] = sorted({f["base_currency"] for f in flds})
    rep["instrument_types"] = sorted({f["type"] for f in flds})
    rep["instrument_quote_currencies"] = sorted({f["quote_currency"] for f in flds})
    rep["instrument_settlement_currencies"] = sorted({f["settlement_currency"] for f in flds})
    rep["instrument_field_names"] = sorted(flds[0].keys())
    rep["instrument_fee_fields"] = sorted(k for k in flds[0] if "fee" in k.lower())
    rep["instrument_tick_fields"] = sorted(k for k in flds[0] if "increment" in k.lower())
    rep["instrument_lifecycle_fields"] = sorted(
        k for k in flds[0] if re.search(r"list|delist|expir|launch|first|last|status", k, re.I))
    rep["instrument_fees"] = sorted({(f.get("maker_fee"), f.get("taker_fee")) for f in flds})
    rep["instrument_price_increments"] = sorted({str(f.get("price_increment")) for f in flds})
    rep["instrument_mark_index_fields"] = sorted(
        k for k in flds[0] if re.search(r"mark|index|basis", k, re.I))

    fd = os.path.join(raw, "binance/usdm/funding")
    rep["funding_symbol_dirs"] = _ls(fd)
    if rep["funding_symbol_dirs"]:
        p = os.path.join(fd, "BTCUSDT", "BTCUSDT-funding.jsonl.gz")
        lines = _read_full(p).splitlines()
        first, last = json.loads(lines[0]), json.loads(lines[-1])
        rep["funding_row_keys"] = sorted(first.keys())
        rep["funding_rows"] = len(lines)
        rep["funding_venues"] = sorted({json.loads(x).get("venue") for x in lines[:200]})
        rep["funding_truth_status"] = sorted({json.loads(x).get("truth_status") for x in lines})
        rep["funding_window_utc"] = [_iso(first["funding_time_ms"]), _iso(last["funding_time_ms"])]
        rep["funding_mark_price_present"] = "mark_price" in first
        rep["funding_mark_price_null_first_2000"] = sum(
            1 for x in lines[:2000] if json.loads(x).get("mark_price") is None)
        rep["funding_mark_price_all_null"] = all(
            json.loads(x).get("mark_price") is None for x in lines)
    rep["required_data_available"] = False
    return rep


def probe_raw(raw):
    """Entry-name and payload probes over the whole raw tree, with a closed
    classification table: every hit must be explained by RAW_HIT_CLASSES."""
    rep = {}
    entries = _all_entries(raw)
    rep["raw_entry_count"] = len(entries)
    name_hits = {}
    for e in entries:
        low = e.lower()
        for t in COMPILED:
            if COMPILED[t].search(low):
                name_hits.setdefault(t, []).append(e)
    rep["probe_tokens_tested"] = len(COMPILED)
    rep["entry_name_hits"] = {k: v[:8] for k, v in name_hits.items()}
    rep["entry_name_hit_classes"] = sorted(name_hits)
    rep["entry_name_hits_unexplained"] = sorted(
        k for k in name_hits if k in RESOLUTION_TOKENS or k in UNIVERSE_TOKENS)

    files = [e for e in entries if not e.endswith("/")]
    tot_bytes = 0
    token_files = {}
    token_files_data_ext = {}
    ambiguous = []
    for e in files:
        p = os.path.join(raw, e)
        try:
            tot_bytes += os.path.getsize(p)
        except OSError:
            pass
        ext = os.path.splitext(p)[1].lower()
        if ext not in TEXT_EXTS:
            continue
        try:
            txt = _read_text(p, 400000)
        except Exception:  # noqa: BLE001
            continue
        for k, rx in COMPILED.items():
            if rx.search(txt) or rx.search(e):
                token_files.setdefault(k, []).append(e)
                if ext in DATA_EXTS:
                    token_files_data_ext.setdefault(k, []).append(e)
        if AMBIGUOUS_1M.search(txt) or AMBIGUOUS_1M.search(e):
            ambiguous.append(e)
    rep["payload_scan_files"] = len(files)
    rep["payload_scan_bytes"] = tot_bytes
    rep["payload_token_files"] = {k: v[:6] for k, v in token_files.items()}
    rep["payload_token_files_data_ext"] = {k: v[:6] for k, v in token_files_data_ext.items()}
    rep["payload_data_ext_hit_count"] = sum(len(v) for v in token_files_data_ext.values())
    rep["ambiguous_bare_1m_files"] = ambiguous
    hit_files = sorted({f for v in token_files.values() for f in v} | set(ambiguous))
    rep["hit_files"] = hit_files
    rep["hit_file_classes"] = {f: RAW_HIT_CLASSES.get(f, "UNCLASSIFIED") for f in hit_files}
    rep["unclassified_hit_files"] = [f for f in hit_files if f not in RAW_HIT_CLASSES]
    rep["unclassified_ambiguous_files"] = [f for f in ambiguous if f not in RAW_HIT_CLASSES]
    rep["minute_dataset_present"] = False
    rep["pit_universe_surface_present"] = False
    rep["spread_surface_present"] = False
    rep["mark_index_series_present"] = False
    rep["listing_history_surface_present"] = False
    return rep


def measure_host_stores(roots=None, wide_roots=None, cap_per_root=60000, cap_wide=200000):
    """Re-measure the host's non-canonical stores: does any of them carry a
    minute-resolution crypto panel, a priced point-in-time membership panel or a
    spread surface that could stand in for the registered requirement?

    Two independent instruments:
      * a token probe over the named stores (entry names + payload text), whose
        data-extension hits must all be classified;
      * a filename probe over the whole workspace + data volume for
        minute-resolution naming (`1m`/`minute`/... in a data extension), plus a
        content probe that asks whether any JSON hit actually holds a
        time-series panel of >= 60 timestamped records.
    """
    roots = roots if roots is not None else HOST_ROOTS
    wide_roots = wide_roots if wide_roots is not None else WIDE_ROOTS
    rep = {"roots": {}, "totals": {}}
    tot_files = tot_hits = 0
    data_candidates = []
    resolution_json_hits = []
    for root in roots:
        if not os.path.isdir(root):
            rep["roots"][root] = {"exists": False}
            continue
        n = 0
        hits = 0
        per_tok = {}
        for p in _iter_files(root, cap=cap_per_root):
            n += 1
            rel = os.path.relpath(p, root)
            toks = {k for k, rx in COMPILED.items() if rx.search(rel)}
            ext = os.path.splitext(p)[1].lower()
            if ext in TEXT_EXTS:
                try:
                    txt = _read_text(p)
                except Exception:  # noqa: BLE001
                    txt = ""
                toks |= {k for k, rx in COMPILED.items() if rx.search(txt)}
            if not toks:
                continue
            hits += 1
            for t in toks:
                per_tok[t] = per_tok.get(t, 0) + 1
            if ext in DATA_EXTS:
                data_candidates.append({"root": root, "path": rel, "ext": ext,
                                        "tokens": sorted(toks)})
            elif ext == ".json" and (toks & set(RESOLUTION_TOKENS)):
                resolution_json_hits.append({"root": root, "path": rel, "ext": ext,
                                             "tokens": sorted(toks)})
        rep["roots"][root] = {"exists": True, "files_scanned": n, "token_files": hits,
                              "token_file_counts": per_tok,
                              "data_ext_hits": sum(1 for d in data_candidates if d["root"] == root),
                              "minute_resolution_data_hits": 0,
                              "carries_minute_panel": False,
                              "carries_priced_pit_membership": False,
                              "carries_spread_surface": False,
                              "used_as_input": False}
        tot_files += n
        tot_hits += hits

    def _rel(d):
        return os.path.join(os.path.basename(d["root"]), d["path"])

    for d in data_candidates:
        d["class"] = HOST_DATA_HIT_CLASSES.get(_rel(d), "UNCLASSIFIED")
    for d in resolution_json_hits:
        d["class"] = JSON_SNAPSHOT_CLASSES.get(_rel(d), "report_or_document_json")
        d["is_panel"] = _json_panel_probe(os.path.join(d["root"], d["path"]))
    minute_token_hits = [d for d in data_candidates
                         if set(d["tokens"]) & {"minute_bars", "one_minute", "structured_1m"}]
    panel_hits = [d for d in resolution_json_hits if d.get("is_panel")]
    unexplained_panel_hits = [d for d in panel_hits
                              if d.get("class") not in ALLOWED_PANEL_HIT_CLASSES]
    unclassified_data = [d for d in data_candidates if d.get("class") == "UNCLASSIFIED"]

    wide = {}
    w_files = 0
    wide_data_hits = []
    for root in wide_roots:
        if not os.path.isdir(root):
            wide[root] = {"exists": False}
            continue
        name_hits, data_hits = [], []
        n = 0
        for p in _iter_files(root, max_depth=6, cap=cap_wide):
            n += 1
            if not MINUTE_NAME_RX.search(os.path.basename(p)):
                continue
            rel = os.path.relpath(p, root)
            name_hits.append(rel)
            if os.path.splitext(p)[1].lower() in DATA_EXTS:
                data_hits.append(rel)
                wide_data_hits.append(rel)
        wide[root] = {"exists": True, "files_scanned": n, "name_hits": len(name_hits),
                      "name_hit_samples": name_hits[:12],
                      "data_extension_name_hits": len(data_hits),
                      "data_extension_samples": data_hits[:12]}
        w_files += n
    wide_classified = {rel: WIDE_NAME_HIT_CLASSES.get(rel, "UNCLASSIFIED")
                       for rel in wide_data_hits}

    rep["totals"] = {"roots": len(roots), "files_scanned": tot_files, "token_files": tot_hits,
                     "data_extension_hits": len(data_candidates),
                     "minute_token_data_hits": len(minute_token_hits),
                     "resolution_json_hits": len(resolution_json_hits),
                     "panel_shaped_hits": len(panel_hits),
                     "unexplained_panel_hits": len(unexplained_panel_hits),
                     "unclassified_data_candidates": len(unclassified_data),
                     "wide_roots": len(wide_roots), "wide_files_scanned": w_files,
                     "wide_name_hits": sum(v.get("name_hits", 0) for v in wide.values()),
                     "wide_data_extension_name_hits": len(wide_data_hits)}
    rep["data_candidates"] = [{"rel": _rel(d), "ext": d["ext"], "tokens": d["tokens"],
                               "class": d["class"], "note": HOST_DATA_HIT_NOTES.get(d["class"])}
                              for d in data_candidates]
    rep["unclassified_data_candidates"] = [c["rel"] for c in rep["data_candidates"]
                                           if c["class"] == "UNCLASSIFIED"]
    rep["membership_or_lifecycle_candidates"] = [c for c in rep["data_candidates"]
                                                 if "membership" in c["class"]
                                                 or "lifecycle" in c["class"]]
    rep["json_snapshot_hits"] = [{"rel": _rel(d), "tokens": d["tokens"], "class": d["class"],
                                  "note": JSON_SNAPSHOT_NOTES.get(d["class"]),
                                  "is_panel": d["is_panel"]}
                                 for d in resolution_json_hits
                                 if d["class"] != "report_or_document_json"]
    rep["report_json_hits"] = sum(1 for d in resolution_json_hits
                                  if d["class"] == "report_or_document_json")
    rep["report_json_sample"] = [_rel(d) for d in resolution_json_hits
                                 if d["class"] == "report_or_document_json"][:10]
    rep["wide_name_probe"] = wide
    rep["wide_data_extension_hits"] = [{"rel": rel, "class": wide_classified[rel],
                                        "note": WIDE_NAME_HIT_NOTES.get(wide_classified[rel])}
                                       for rel in wide_data_hits]
    rep["unclassified_wide_hits"] = [rel for rel, cls in wide_classified.items()
                                     if cls == "UNCLASSIFIED"]
    rep["panel_shaped_hits"] = [_rel(d) for d in panel_hits]
    rep["unexplained_panel_hits"] = [_rel(d) for d in unexplained_panel_hits]
    rep["any_store_carries_minute_panel"] = bool(unexplained_panel_hits)
    rep["any_store_carries_priced_pit_membership"] = False
    rep["any_store_carries_spread_surface"] = False
    return rep


def _check(cid, name, ok, detail):
    return {"id": cid, "name": name, "ok": bool(ok), "detail": detail}


def _family_surfaces(results_root, family, round_id):
    fam_dir = os.path.join(results_root, family)
    round_dir = os.path.join(fam_dir, "rounds", round_id)
    return {
        "family_dir": fam_dir,
        "round_dir": round_dir,
        "family_json": os.path.join(fam_dir, "family.json"),
        "spec": os.path.join(round_dir, "round-spec.json"),
        "verdict": os.path.join(round_dir, "verdict.json"),
        "attempts_dir": os.path.join(round_dir, "attempts"),
        "round_dir_listing": _ls(round_dir),
        "family_dir_listing": _ls(fam_dir),
    }


EXPECTED_SYMBOLS = ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
EXPECTED_INTERVALS = ["15m", "1d", "1h", "1w", "30m", "4h", "5m"]
EXPECTED_STEPS = [300.0, 900.0, 1800.0, 3600.0, 14400.0, 86400.0]
EXPECTED_KLINE_KEYS = ["close", "close_time_ms", "high", "low", "open", "open_time_ms", "volume"]
EXPECTED_ABSENT_COLUMNS = ["count", "quote_volume", "taker_buy_base", "taker_buy_quote"]
EXPECTED_SCHEMA_SECTIONS = ["klines (Binance USD-M perpetual futures, UTC)", "funding", "instruments"]
EXPECTED_RAW_HIT_CLASSES = sorted(set(RAW_HIT_CLASSES.values()))
EXPECTED_HOST_DATA_HITS = 5
EXPECTED_HOST_DATA_CLASSES = sorted(set(HOST_DATA_HIT_CLASSES.values()))
EXPECTED_WIDE_DATA_HITS = 2
PHASE_GRIDS = ["historical", "oos", "full", "fee_2x", "funding_2x",
               "entry_delay_1_bar", "slippage_2ticks", "no_funding",
               "no_funding_full", "cost_attrition_40bps"]
MINUTE_RESOLUTION_HORIZONS = list(range(1, 11))


def run_checks(results_root, raw_root, repo_root=None, family=FAMILY, round_id=ROUND,
               task=TASK, raw=None, stores=None, probe=None, record_path=None):
    repo_root = repo_root or DEFAULT_REPO
    raw = raw if raw is not None else measure_raw(raw_root)
    stores = stores if stores is not None else measure_host_stores()
    probe = probe if probe is not None else probe_raw(raw.get("raw_root") or raw_root)
    sf = _family_surfaces(results_root, family, round_id)
    spec = _load_json(sf["spec"])
    verdict = _load_json(sf["verdict"])
    fam = _load_json(sf["family_json"])
    checks = []
    add = checks.append

    # ---- C1 store identity
    add(_check("C1", "canonical raw identity is one crypto-perp venue with three dataset families",
               raw["documented_venue"] == "BINANCE" and raw["documented_market_type"] == "usdm_perp"
               and "binance" in raw["store_top_level"]
               and set(raw["usdm_subdirs"]) >= {"funding", "instruments", "klines"}
               and raw["documented_symbols"] == EXPECTED_SYMBOLS,
               {"venue": raw["documented_venue"], "market_type": raw["documented_market_type"],
                "usdm_subdirs": raw["usdm_subdirs"], "symbols": raw["documented_symbols"]}))
    # ---- C2 on-disk kline dataset inventory: is anything at or below one minute?
    add(_check("C2", "on-disk kline inventory holds 28 datasets and none of them is 1-minute",
               raw["klines_symbol_dirs"] == EXPECTED_SYMBOLS
               and raw["intervals_identical_across_symbols"] is True
               and raw["intervals_union"] == EXPECTED_INTERVALS
               and raw["klines_datasets"] == 28
               and raw["minute_datasets"] == [] and raw["sub_minute_datasets"] == [],
               {"symbols": raw["klines_symbol_dirs"], "intervals": raw["intervals_union"],
                "datasets": raw["klines_datasets"], "minute_datasets": raw["minute_datasets"],
                "sub_minute_datasets": raw["sub_minute_datasets"]}))
    # ---- C3 measured resolution vs the registered 1-10 minute horizon grid
    add(_check("C3", "finest measured bar step is 300 s, so 8 of the 10 registered horizons are unreachable",
               raw["distinct_measured_steps"] == EXPECTED_STEPS
               and raw["finest_step_seconds"] == 300.0
               and str(raw["finest_dataset"]).endswith("/5m")
               and raw["reachable_horizons_minutes"] == [5, 10]
               and raw["unreachable_horizons_minutes"] == [1, 2, 3, 4, 6, 7, 8, 9]
               and raw["unreachable_horizon_count"] == 8
               and raw["falsification_item_4_executable"] is False,
               {"steps_s": raw["distinct_measured_steps"], "finest": raw["finest_dataset"],
                "finest_step_s": raw["finest_step_seconds"],
                "registered_horizons_min": raw["registered_horizon_grid_minutes"],
                "reachable": raw["reachable_horizons_minutes"],
                "unreachable": raw["unreachable_horizons_minutes"]}))
    # ---- C4 kline row shape
    add(_check("C4", "kline rows are OHLCV only: no quote volume, trade count or taker split",
               raw["kline_row_keys"] == EXPECTED_KLINE_KEYS
               and raw["kline_extra_columns_absent"] == EXPECTED_ABSENT_COLUMNS,
               {"row_keys": raw["kline_row_keys"],
                "extra_columns_absent": raw["kline_extra_columns_absent"]}))
    # ---- C5 instrument surface
    add(_check("C5", "instrument surface carries four perpetuals, a fee/tick schedule and no lifecycle field",
               raw["instrument_count"] == 4
               and raw["instrument_ids"] == ["BNBUSDT-PERP.BINANCE", "BTCUSDT-PERP.BINANCE",
                                             "ETHUSDT-PERP.BINANCE", "SOLUSDT-PERP.BINANCE"]
               and raw["instrument_base_currencies"] == ["BNB", "BTC", "ETH", "SOL"]
               and raw["instrument_types"] == ["CryptoPerpetual"]
               and raw["instrument_quote_currencies"] == ["USDT"]
               and raw["instrument_settlement_currencies"] == ["USDT"]
               and raw["instrument_fee_fields"] == ["maker_fee", "taker_fee"]
               and raw["instrument_tick_fields"] == ["price_increment", "size_increment"]
               and raw["instrument_fees"] == [("0.0002", "0.0005")]
               and raw["instrument_lifecycle_fields"] == []
               and raw["instrument_mark_index_fields"] == [],
               {"ids": raw["instrument_ids"], "types": raw["instrument_types"],
                "fees": [(str(a), str(b)) for a, b in raw["instrument_fees"]],
                "lifecycle_fields": raw["instrument_lifecycle_fields"],
                "mark_index_fields": raw["instrument_mark_index_fields"]}))
    # ---- C6 funding surface
    add(_check("C6", "funding surface is perp funding only, with a mark_price column that is null where modelled",
               raw["funding_symbol_dirs"] == EXPECTED_SYMBOLS
               and raw["funding_venues"] == ["BINANCE"]
               and raw["funding_truth_status"] == ["modeled_funding", "official"]
               and raw["funding_mark_price_present"] is True
               and raw["funding_mark_price_null_first_2000"] == 2000
               and raw["funding_mark_price_all_null"] is False
               and not any("index" in k for k in raw["funding_row_keys"])
               and raw["funding_window_utc"][0] == "2022-01-01T00:00:00Z",
               {"symbols": raw["funding_symbol_dirs"], "venues": raw["funding_venues"],
                "truth_status": raw["funding_truth_status"], "rows": raw["funding_rows"],
                "window": raw["funding_window_utc"],
                "mark_price_null_first_2000": raw["funding_mark_price_null_first_2000"],
                "row_keys": raw["funding_row_keys"]}))
    # ---- C7 entry-name probe
    add(_check("C7", "entry-name probe over the whole raw tree finds no minute/universe/spread surface",
               probe["entry_name_hits"] == {} and probe["entry_name_hits_unexplained"] == []
               and probe["raw_entry_count"] >= 1600,
               {"entries": probe["raw_entry_count"], "tokens": probe["probe_tokens_tested"],
                "hits": probe["entry_name_hits"]}))
    # ---- C8 payload probe with a closed classification table
    add(_check("C8", "payload probe finds no data file with a minute/universe/spread token and no unclassified hit",
               probe["payload_data_ext_hit_count"] == 0
               and probe["unclassified_hit_files"] == []
               and probe["unclassified_ambiguous_files"] == []
               and sorted(set(probe["hit_file_classes"].values())) == EXPECTED_RAW_HIT_CLASSES
               and probe["payload_scan_files"] >= 1600,
               {"files": probe["payload_scan_files"], "bytes": probe["payload_scan_bytes"],
                "data_ext_hits": probe["payload_token_files_data_ext"],
                "hit_files": probe["hit_file_classes"],
                "ambiguous_bare_1m": probe["ambiguous_bare_1m_files"]}))
    # ---- C9 the store's own documentation
    add(_check("C9", "the store documents three dataset families, seven intervals and no minute dataset",
               raw["schema_dataset_sections"] == EXPECTED_SCHEMA_SECTIONS
               and raw["schema_documents_minute_dataset"] is False
               and "1m" in raw["schema_interval_alphabet"]
               and raw["documented_intervals"] == EXPECTED_INTERVALS
               and "1m" not in raw["documented_intervals"]
               and raw["inventory_states_no_other_store"] is True
               and raw["inventory_states_no_vision_copy"] is True
               and raw["inventory_notes_lean_minute_is_30m"] is True,
               {"dataset_sections": raw["schema_dataset_sections"],
                "documented_intervals": raw["documented_intervals"],
                "schema_interval_alphabet": raw["schema_interval_alphabet"],
                "no_other_store_stated": raw["inventory_states_no_other_store"],
                "no_vision_copy_stated": raw["inventory_states_no_vision_copy"],
                "lean_minute_was_30m": raw["inventory_notes_lean_minute_is_30m"]}))
    # ---- C10 cross-section breadth
    add(_check("C10", "the local cross-section is four synchronised contracts over the registered window, not the record's liquid universe",
               raw["instrument_count"] == 4
               and len(raw["documented_symbols"]) == 4
               and raw["grid_5m_identical_across_symbols"] is True
               and raw["five_m_files"] >= 50
               and raw["five_m_window_utc"][0] == "2022-01-01T00:00:00Z"
               and probe["entry_name_hits"] == {},
               {"contracts": raw["documented_symbols"],
                "synchronised_5m_grid": raw["grid_5m_identical_across_symbols"],
                "five_m_files": raw["five_m_files"], "five_m_window": raw["five_m_window_utc"],
                "grid": raw.get("grid_5m_per_symbol"),
                "universe_token_entry_hits": probe["entry_name_hits"]}))
    # ---- C11 host stores
    add(_check("C11", "no measured host store carries a minute panel, a priced PIT membership panel or a spread series",
               stores["any_store_carries_minute_panel"] is False
               and stores["totals"]["files_scanned"] >= 4000
               and stores["totals"]["minute_token_data_hits"] == 0
               and stores["totals"]["data_extension_hits"] == EXPECTED_HOST_DATA_HITS
               and stores["unclassified_data_candidates"] == []
               and sorted({c["class"] for c in stores["data_candidates"]}) == EXPECTED_HOST_DATA_CLASSES
               and stores["unclassified_wide_hits"] == []
               and stores["totals"]["wide_data_extension_name_hits"] == EXPECTED_WIDE_DATA_HITS,
               {"totals": stores["totals"],
                "data_candidate_classes": sorted({c["class"] for c in stores["data_candidates"]}),
                "minute_panel_hits": stores["panel_shaped_hits"],
                "unexplained_panels": stores["unexplained_panel_hits"],
                "wide_data_hits": stores["wide_data_extension_hits"]}))
    # ---- C12 round-spec terminal values
    gate = spec.get("prerequisite_gate", {})
    launch = spec.get("launch", {})
    add(_check("C12", "round-spec states the contract-mandated terminal values",
               spec.get("family_id") == family and spec.get("round_id") == round_id
               and spec.get("kanban_task_id") == task
               and gate.get("verdict") == "TECHNICAL_INCOMPLETE"
               and gate.get("failure_layer") == "card-local"
               and gate.get("failure_class_used") == "data_window_invalid"
               and gate.get("last_run_id") is None
               and launch.get("launched") is False and launch.get("attempts") == 0,
               {"verdict": gate.get("verdict"), "layer": gate.get("failure_layer"),
                "class": gate.get("failure_class_used"), "last_run_id": gate.get("last_run_id"),
                "attempts": launch.get("attempts")}))
    # ---- C13 verdict.json terminal values
    add(_check("C13", "verdict.json states the same terminal values with an empty survivor/coverage surface",
               verdict.get("verdict") == "TECHNICAL_INCOMPLETE"
               and verdict.get("performance_claimable") is False
               and verdict.get("failure", {}).get("layer") == "card-local"
               and verdict.get("failure", {}).get("class") == "data_window_invalid"
               and verdict.get("failure", {}).get("last_run_id") is None
               and verdict.get("attempts", {}).get("launched") == 0
               and verdict.get("attempts", {}).get("run_specs") == 0
               and verdict.get("attempts", {}).get("terminal_sentinels") == 0
               and verdict.get("survivors") == [] and verdict.get("survivor_bundle") is None
               and verdict.get("coverage", {}).get("cells_computed") == 0
               and verdict.get("cohorts", {}).get("realized") == 0
               and verdict.get("evidence_run_ids") == [],
               {"verdict": verdict.get("verdict"),
                "performance_claimable": verdict.get("performance_claimable"),
                "attempts": verdict.get("attempts"), "survivors": verdict.get("survivors"),
                "coverage": verdict.get("coverage"), "cohorts": verdict.get("cohorts")}))
    # ---- C14 nothing submitted
    stray = []
    for dirpath, dirnames, filenames in os.walk(sf["family_dir"]):
        for name in filenames:
            if name in ("run-spec.json", "result.json", "state.json") or name.startswith("terminal"):
                stray.append(os.path.relpath(os.path.join(dirpath, name), sf["family_dir"]))
    add(_check("C14", "nothing was ever submitted: no run-spec, no attempt dir, no sentinel",
               not os.path.isdir(sf["attempts_dir"]) and stray == []
               and sorted(sf["round_dir_listing"]) == ["round-spec.json", "verdict.json"]
               and sorted(sf["family_dir_listing"]) == ["family.json", "rounds"],
               {"attempts_dir_exists": os.path.isdir(sf["attempts_dir"]),
                "round_dir_listing": sf["round_dir_listing"],
                "family_dir_listing": sf["family_dir_listing"], "stray": stray}))
    # ---- C15 universe kept whole + DCA registration intact
    ur = spec.get("universe_registration", {})
    dd = spec.get("dca_domain", {})
    ufi = spec.get("user_fixed_invariants", {})
    axes = ["spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct"]
    matrix = ur.get("required_data_matrix", [])
    decided = {row["item"]: row["status"] for row in matrix if row.get("decisive")}
    dca_ok = (dd.get("configs_per_cohort_per_grid") == 48
              and dd.get("base_quote") == 1000
              and dd.get("base_quote_status") == "PROJECT_PRE_REGISTERED_CONSTANT"
              and dd.get("search_axes_status") == "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN"
              and sorted(dd.get("axes", {}).keys()) == sorted(axes)
              and not any(a in ufi for a in axes) and "base_quote" not in ufi)
    add(_check("C15", "registered universe kept whole; DCA domain carries the v1.3.1 provenance classes",
               ur.get("universe_shrunk_to_local_list") is False
               and ur.get("local_instruments_present") == ["BTCUSDT/USD-M-perp",
                                                           "ETHUSDT/USD-M-perp",
                                                           "BNBUSDT/USD-M-perp",
                                                           "SOLUSDT/USD-M-perp"]
               and ur.get("registered_timeframes") == ["1m"]
               and len(ur.get("registered_instruments", [])) == 1
               and len(matrix) >= 20
               and len(decided) == 10
               and decided.get("minute_level_tradeable_prices_or_ohlcv") == "ABSENT"
               and decided.get("point_in_time_binance_instrument_universe") == "ABSENT_FROM_CANONICAL_RAW"
               and decided.get("bid_ask_spread_surface") == "ABSENT_FOR_TRADED_INSTRUMENTS"
               and dca_ok,
               {"universe_shrunk_to_local_list": ur.get("universe_shrunk_to_local_list"),
                "local_instruments_present": ur.get("local_instruments_present"),
                "registered_timeframes": ur.get("registered_timeframes"),
                "matrix_items": len(matrix), "decisive": decided, "dca_ok": dca_ok}))
    # ---- C16 coverage / survivor surface empty by construction
    cov = spec.get("robustness_plan", {})
    sel = spec.get("selector_and_disposition", {})
    add(_check("C16", "coverage is registered but empty; no survivor surface exists",
               cov.get("cells_registered_per_grid") == 48
               and cov.get("cells_computed") == 0
               and cov.get("registered_phase_grids") == PHASE_GRIDS
               and all(v.get("computed") == 0 for v in cov.get("coverage_counts", {}).values())
               and sel.get("cohorts_realized") == 0 and sel.get("survivors") == []
               and sel.get("selector") == "cohort-selector-v1"
               and sel.get("disposition") == "cohort-disposition-v1"
               and sel.get("cross_cohort_median") == "non_gating",
               {"cells_registered_per_grid": cov.get("cells_registered_per_grid"),
                "cells_computed": cov.get("cells_computed"),
                "phase_grids": len(cov.get("registered_phase_grids", [])),
                "cohorts_realized": sel.get("cohorts_realized"),
                "selector": sel.get("selector"), "disposition": sel.get("disposition")}))
    # ---- C17 family.json identity
    add(_check("C17", "family.json binds this round's family to this card and fingerprint",
               fam.get("family_id") == family and fam.get("kanban_task_id") == task
               and isinstance(fam.get("semantic_fingerprint"), str)
               and spec.get("provenance", {}).get("semantic_fingerprint", {}).get("semantic_fingerprint")
               == fam.get("semantic_fingerprint"),
               {"family_id": fam.get("family_id"), "kanban_task_id": fam.get("kanban_task_id"),
                "semantic_fingerprint": fam.get("semantic_fingerprint")}))
    # ---- C18 taxonomy separation
    costs = spec.get("costs", {})
    add(_check("C18", "failure taxonomy kept separated: infrastructure terminal, not a scientific failure",
               "infrastructure/technical failure" in costs.get("note", "")
               and spec.get("expected") == "PREREQUISITE_ABSENT"
               and "never evaluated" in spec.get("hypothesis", {}).get("hypothesis_status", "")
               and verdict.get("yield", {}).get("yield_decision") == "STOP_TECHNICAL_INCOMPLETE"
               and verdict.get("verdict") != "REJECT",
               {"expected": spec.get("expected"),
                "hypothesis_status": spec.get("hypothesis", {}).get("hypothesis_status"),
                "yield_decision": verdict.get("yield", {}).get("yield_decision"),
                "note": costs.get("note")}))
    return {"checks": checks, "raw": raw, "probe": probe, "stores": stores,
            "surfaces": {"family_dir": sf["family_dir"], "round_dir": sf["round_dir"]}}


def _result(checks, raw, stores, probe=None):
    failed = [c for c in checks if not c["ok"]]
    out = {"ok": not failed, "checks": checks, "failed": [c["id"] for c in failed],
           "raw_summary": {"entries": (probe or {}).get("raw_entry_count"),
                           "klines_datasets": raw.get("klines_datasets"),
                           "minute_datasets": raw.get("minute_datasets"),
                           "finest_step_s": raw.get("finest_step_seconds"),
                           "unreachable_horizons": raw.get("unreachable_horizons_minutes"),
                           "contracts": raw.get("documented_symbols")},
           "stores_summary": stores.get("totals")}
    if probe is not None:
        out["probe_summary"] = {"entries": probe.get("raw_entry_count"),
                                "payload_files": probe.get("payload_scan_files"),
                                "payload_bytes": probe.get("payload_scan_bytes"),
                                "data_ext_hits": probe.get("payload_data_ext_hit_count"),
                                "hit_file_classes": probe.get("hit_file_classes")}
    return out


def _resolve_source_texts(repo_root=None, card_body_path=None, record_path=None,
                          board_db=None, family=FAMILY, task=TASK):
    """Card / record / contract / footer texts a verbatim leaf may be checked against."""
    import sqlite3
    repo_root = repo_root or DEFAULT_REPO
    record_path = record_path or DEFAULT_RECORD
    with open(record_path, encoding="utf-8", errors="replace") as fh:
        record = fh.read()
    with open(os.path.join(repo_root, "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md"),
              encoding="utf-8", errors="replace") as fh:
        contract = fh.read()
    try:
        sys.path.insert(0, os.path.join(repo_root, "runtime"))
        from production_handoff import LIFECYCLE_FOOTER as footer
    except ImportError:  # pragma: no cover
        footer = ""
    card = None
    if card_body_path and os.path.exists(card_body_path):
        with open(card_body_path, encoding="utf-8", errors="replace") as fh:
            card = fh.read()
    if card is None:
        db = board_db or os.path.join(os.path.expanduser("~"), ".hermes/kanban/boards",
                                      BOARD, "kanban.db")
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        try:
            row = con.execute("select body from tasks where id=?", (task,)).fetchone()
        finally:
            con.close()
        body = row[0] if row else ""
        if footer and body.endswith(footer):
            body = body[:-len(footer)]
        card = body
    return {"card": card, "record": record, "contract": contract, "footer": footer,
            "record_path": record_path}


def _walk_verbatim(node, path=()):
    if path and path[0] == "excerpt_source_map":
        return []
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            out.extend(_walk_verbatim(v, path + (str(k),)))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(_walk_verbatim(v, path + ("[%d]" % i,)))
    elif isinstance(node, str) and path and "verbatim" in path[-1].lower():
        out.append((".".join(path), node))
    return out


def verify_verbatim(spec_path, repo_root=None, card_body_path=None, record_path=None,
                    board_db=None, family=FAMILY, task=TASK):
    src = _resolve_source_texts(repo_root=repo_root, card_body_path=card_body_path,
                                record_path=record_path, board_db=board_db,
                                family=family, task=task)
    texts = {"card": src["card"], "record": src["record"], "contract": src["contract"],
             "footer": src["footer"]}
    spec = _load_json(spec_path)
    leaves = _walk_verbatim(spec)
    cmap = spec.get("excerpt_source_map", {})
    problems, misses, checked = [], [], 0
    seen = set()
    for key, value in leaves:
        entry = cmap.get(key)
        if entry is None:
            problems.append("%s: no excerpt_source_map entry" % key)
            continue
        seen.add(key)
        source = entry.get("source")
        if source not in texts:
            problems.append("%s: unknown source %r" % (key, source))
            continue
        if entry.get("chars") != len(value):
            problems.append("%s: chars %s != %d" % (key, entry.get("chars"), len(value)))
        if entry.get("sha256") != _sha256_text(value):
            problems.append("%s: sha256 mismatch in map" % key)
        if value not in texts[source]:
            misses.append("%s: not a verbatim substring of %s" % (key, source))
        checked += 1
    orphans = sorted(set(cmap) - seen)
    ok = not problems and not misses and not orphans and checked > 0
    return {"ok": ok, "checked": checked, "leaves": len(leaves), "problems": problems,
            "misses": misses, "orphans": orphans,
            "sources": {k: len(v) for k, v in texts.items()},
            "source_counts": {"card": len(texts["card"]), "record": len(texts["record"]),
                              "contract": len(texts["contract"]), "footer": len(texts["footer"])},
            "card_source": "pool_body_file" if card_body_path else "board_db"}


def _copy_family(src_results, dst_results, family=FAMILY, round_id=ROUND):
    """Fresh copy of the frozen family surface; any previous copy is dropped first
    so tamper variants cannot leak into one another."""
    dst = os.path.join(dst_results, family)
    if os.path.exists(dst):
        shutil.rmtree(dst)
    os.makedirs(os.path.join(dst, "rounds"), exist_ok=True)
    shutil.copy2(os.path.join(src_results, family, "family.json"),
                 os.path.join(dst, "family.json"))
    shutil.copytree(os.path.join(src_results, family, "rounds", round_id),
                    os.path.join(dst, "rounds", round_id))
    return dst


def _mutate(path, fn):
    doc = _load_json(path)
    fn(doc)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
        fh.write("\n")


def self_test(results_root, raw, stores, family=FAMILY, round_id=ROUND, task=TASK):
    """Non-vacuousness control: tampered copies must be refused by the named check."""
    tmp = tempfile.mkdtemp(prefix="t7ee25fe3-selftest-")
    try:
        probe = probe_raw(raw.get("raw_root") or DEFAULT_RAW)
        base = _copy_family(results_root, tmp, family, round_id)
        spec_p = os.path.join(base, "rounds", round_id, "round-spec.json")
        verd_p = os.path.join(base, "rounds", round_id, "verdict.json")
        fam_p = os.path.join(base, "family.json")
        results = []

        def v(name, expect, mutate):
            _copy_family(results_root, tmp, family, round_id)
            mutate(spec_p, verd_p, fam_p)
            res = run_checks(tmp, None, raw=raw, stores=stores, probe=probe, family=family,
                             round_id=round_id, task=task)
            failed = {c["id"] for c in res["checks"] if not c["ok"]}
            results.append({"variant": name, "expected": expect, "failed": sorted(failed),
                            "flipped": expect in failed, "ok": expect in failed})

        def m_spec(fn):
            return lambda s, vd, f: _mutate(s, fn)

        def m_verdict(fn):
            return lambda s, vd, f: _mutate(vd, fn)

        def m_family(fn):
            return lambda s, vd, f: _mutate(f, fn)

        v("verdict->PASS", "C13", m_verdict(lambda d: d.update({"verdict": "PASS"})))
        v("verdict->REJECT (science verdict on an unmeasured hypothesis)", "C13",
          m_verdict(lambda d: d.update({"verdict": "REJECT"})))
        v("performance_claimable->true", "C13",
          m_verdict(lambda d: d.update({"performance_claimable": True})))
        v("failure.layer->shared-layer", "C13",
          m_verdict(lambda d: d["failure"].update({"layer": "shared-layer"})))
        v("failure.class->script_bug", "C13",
          m_verdict(lambda d: d["failure"].update({"class": "script_bug"})))
        v("failure.last_run_id->forged", "C13",
          m_verdict(lambda d: d["failure"].update({"last_run_id": round_id + "-u1"})))
        v("attempts.launched->1", "C13",
          m_verdict(lambda d: d["attempts"].update({"launched": 1})))
        v("attempts.terminal_sentinels->1", "C13",
          m_verdict(lambda d: d["attempts"].update({"terminal_sentinels": 1})))
        v("evidence_run_ids->[u1]", "C13",
          m_verdict(lambda d: d.update({"evidence_run_ids": [round_id + "-u1"]})))
        v("survivors->[one]", "C13",
          m_verdict(lambda d: d.update({"survivors": [{"cohort": "cross-sectional"}]})))
        v("coverage.cells_computed->48", "C13",
          m_verdict(lambda d: d["coverage"].update({"cells_computed": 48})))
        v("cohorts.realized->1", "C13",
          m_verdict(lambda d: d["cohorts"].update({"realized": 1})))
        v("yield_decision->CONTINUE", "C18",
          m_verdict(lambda d: d["yield"].update({"yield_decision": "CONTINUE"})))
        v("spec.launch.launched->true", "C12",
          m_spec(lambda d: d["launch"].update({"launched": True})))
        v("spec.launch.attempts->1", "C12",
          m_spec(lambda d: d["launch"].update({"attempts": 1})))
        v("spec.gate.verdict->REJECT", "C12",
          m_spec(lambda d: d["prerequisite_gate"].update({"verdict": "REJECT"})))
        v("spec.gate.failure_layer->shared-layer", "C12",
          m_spec(lambda d: d["prerequisite_gate"].update({"failure_layer": "shared-layer"})))
        v("spec.gate.failure_class->script_bug", "C12",
          m_spec(lambda d: d["prerequisite_gate"].update({"failure_class_used": "script_bug"})))
        v("spec.gate.last_run_id->forged", "C12",
          m_spec(lambda d: d["prerequisite_gate"].update({"last_run_id": round_id + "-u1"})))
        v("spec.expected->RUN", "C18", m_spec(lambda d: d.update({"expected": "RUN"})))
        v("hypothesis_status->evaluated", "C18",
          m_spec(lambda d: d["hypothesis"].update({"hypothesis_status": "registered and evaluated"})))
        v("costs.note->scientific failure", "C18",
          m_spec(lambda d: d["costs"].update({"note": "this round's terminal is a scientific failure"})))
        v("universe_shrunk->true", "C15",
          m_spec(lambda d: d["universe_registration"].update({"universe_shrunk_to_local_list": True})))
        v("registered_timeframes->5m (resolution rewritten)", "C15",
          m_spec(lambda d: d["universe_registration"].update({"registered_timeframes": ["5m"]})))
        v("registered_instruments->local four", "C15",
          m_spec(lambda d: d["universe_registration"].update(
              {"registered_instruments": ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]})))
        v("minute_level matrix item -> PRESENT", "C15",
          m_spec(lambda d: [r.update({"status": "PRESENT"})
                            for r in d["universe_registration"]["required_data_matrix"]
                            if r["item"] == "minute_level_tradeable_prices_or_ohlcv"]))
        v("pit universe matrix item -> PRESENT", "C15",
          m_spec(lambda d: [r.update({"status": "PRESENT"})
                            for r in d["universe_registration"]["required_data_matrix"]
                            if r["item"] == "point_in_time_binance_instrument_universe"]))
        v("spread matrix item -> PRESENT", "C15",
          m_spec(lambda d: [r.update({"status": "PRESENT"})
                            for r in d["universe_registration"]["required_data_matrix"]
                            if r["item"] == "bid_ask_spread_surface"]))
        v("decisive flags dropped", "C15",
          m_spec(lambda d: [r.pop("decisive", None)
                            for r in d["universe_registration"]["required_data_matrix"]]))
        v("matrix truncated to 3 rows", "C15",
          m_spec(lambda d: d["universe_registration"].update(
              {"required_data_matrix": d["universe_registration"]["required_data_matrix"][:3]})))
        v("dca.configs->12", "C15",
          m_spec(lambda d: d["dca_domain"].update({"configs_per_cohort_per_grid": 12})))
        v("dca.base_quote_status->USER_FIXED", "C15",
          m_spec(lambda d: d["dca_domain"].update({"base_quote_status": "USER_FIXED"})))
        v("dca axes leak into user_fixed_invariants", "C15",
          m_spec(lambda d: d["user_fixed_invariants"].update({"spacing_pct": [0.01, 0.02]})))
        v("coverage.cells_registered_per_grid->96", "C16",
          m_spec(lambda d: d["robustness_plan"].update({"cells_registered_per_grid": 96})))
        v("coverage.cells_computed->48 (spec)", "C16",
          m_spec(lambda d: d["robustness_plan"].update({"cells_computed": 48})))
        v("phase grid dropped", "C16",
          m_spec(lambda d: d["robustness_plan"].update(
              {"registered_phase_grids": d["robustness_plan"]["registered_phase_grids"][:-1]})))
        v("cohorts_realized->1 (spec)", "C16",
          m_spec(lambda d: d["selector_and_disposition"].update({"cohorts_realized": 1})))
        v("selector version changed", "C16",
          m_spec(lambda d: d["selector_and_disposition"].update({"selector": "cohort-selector-v2"})))
        v("cross_cohort_median->gating", "C16",
          m_spec(lambda d: d["selector_and_disposition"].update({"cross_cohort_median": "gating"})))
        v("family.kanban_task_id->other", "C17",
          m_family(lambda d: d.update({"kanban_task_id": "t_00000000"})))
        v("family.semantic_fingerprint mismatch", "C17",
          m_family(lambda d: d.update({"semantic_fingerprint": "sha256:" + "0" * 64})))
        v("stray run-spec.json present", "C14",
          lambda s, vd, f: (os.makedirs(os.path.join(os.path.dirname(s), "attempts", round_id + "-u1"),
                                        exist_ok=True),
                            open(os.path.join(os.path.dirname(s), "attempts", round_id + "-u1",
                                              "run-spec.json"), "w").write("{}")))
        v("stray terminal sentinel present", "C14",
          lambda s, vd, f: open(os.path.join(os.path.dirname(s), "terminal-DONE"), "w").write("{}"))
        v("extra file in family dir", "C14",
          lambda s, vd, f, fam_dir=base: open(os.path.join(fam_dir, "extra.json"), "w").write("{}"))
        # must-not-flip: a mutation that touches nothing the checks read
        v("benign round-spec note", "NO_FLIP",
          m_spec(lambda d: d.update({"worker_note": "benign"})))
        results[-1]["ok"] = results[-1]["flipped"] is False
        results[-1]["flipped"] = results[-1]["failed"]
        return {"ok": all(r["ok"] for r in results), "variants": results}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def raw_fixture_control(results_root, raw, stores, family=FAMILY, round_id=ROUND, task=TASK):
    """Build a temp raw tree carrying what this record would need (1-minute bars,
    a point-in-time universe surface, a listing/delisting field and a bid-ask
    spread surface) and assert the raw-side checks flip to FAIL - the raw checks
    are not vacuously green."""
    tmp = tempfile.mkdtemp(prefix="t7ee25fe3-rawfix-")
    try:
        rawfix = os.path.join(tmp, "market-data-raw")
        syms = EXPECTED_SYMBOLS + ["ADAUSDT", "XRPUSDT", "DOGEUSDT"]
        for sub in ["_meta", "binance/usdm/instruments", "binance/usdm/universe",
                    "binance/usdm/spreads"]:
            os.makedirs(os.path.join(rawfix, sub), exist_ok=True)
        for s in syms:
            for iv in ("1m", "5m"):
                os.makedirs(os.path.join(rawfix, "binance/usdm/klines", s, iv), exist_ok=True)
        for s in EXPECTED_SYMBOLS:
            os.makedirs(os.path.join(rawfix, "binance/usdm/funding", s), exist_ok=True)
        with open(os.path.join(rawfix, "_meta/CONFIG.json"), "w") as fh:
            json.dump({"schema": "market-data-raw/config/v1", "venue": "BINANCE",
                       "market_type": "usdm_perp", "symbols": list(EXPECTED_SYMBOLS),
                       "intervals": list(EXPECTED_INTERVALS) + ["1m"]}, fh)
        with open(os.path.join(rawfix, "_meta/SCHEMA.md"), "w") as fh:
            fh.write("# fixture schema\n\n## Dataset: klines (Binance USD-M perpetual futures, UTC)\n\n"
                     "Bar interval = `interval` in the path (`1m 5m 15m 30m 1h 4h 1d 1w`).\n\n"
                     "## Dataset: funding\n\n## Dataset: instruments\n\n"
                     "## Dataset: universe_membership (point-in-time eligible universe)\n\n"
                     "## Dataset: spreads (bid/ask quotes per symbol)\n\n")
        with open(os.path.join(rawfix, "_meta/INVENTORY.md"), "w") as fh:
            fh.write("# fixture inventory (no search-scope statement)\n")
        for s in syms:
            for iv, step in (("1m", 60000), ("5m", 300000)):
                p = os.path.join(rawfix, "binance/usdm/klines", s, iv,
                                 "%s-%s-2026-09.jsonl.gz" % (s, iv))
                with gzip.open(p, "wt") as fh:
                    for i in range(3):
                        fh.write(json.dumps({"open_time_ms": 1789516800000 + i * step,
                                             "close_time_ms": 1789516800000 + (i + 1) * step - 1,
                                             "open": "1", "high": "1", "low": "1", "close": "1",
                                             "volume": "1"}) + "\n")
        with gzip.open(os.path.join(rawfix, "binance/usdm/funding/BTCUSDT/BTCUSDT-funding.jsonl.gz"),
                       "wt") as fh:
            for i in range(2100):
                fh.write(json.dumps({"symbol": "BTCUSDT", "venue": "BINANCE",
                                     "market_type": "usdm_perp",
                                     "funding_time_ms": 1640995200000 + i * 28800000,
                                     "funding_rate": "0.0001",
                                     "mark_price": None if i < 2000 else "1.0",
                                     "rate_type": "Regular",
                                     "truth_status": "official" if i >= 2000 else "modeled_funding"}) + "\n")
        for s in [x for x in EXPECTED_SYMBOLS if x != "BTCUSDT"]:
            with gzip.open(os.path.join(rawfix, "binance/usdm/funding", s,
                                        "%s-funding.jsonl.gz" % s), "wt") as fh:
                fh.write(json.dumps({"symbol": s, "venue": "BINANCE", "market_type": "usdm_perp",
                                     "funding_time_ms": 1640995200000, "funding_rate": "0.0001",
                                     "mark_price": None, "rate_type": "Regular",
                                     "truth_status": "modeled_funding"}) + "\n")
        with open(os.path.join(rawfix, "binance/usdm/instruments/usdm-perp-instruments.json"), "w") as fh:
            json.dump({"instruments": [
                {"fields": {"id": "%s-PERP.BINANCE" % s, "base_currency": s.replace("USDT", ""),
                            "quote_currency": "USDT", "settlement_currency": "USDT",
                            "type": "CryptoPerpetual", "maker_fee": "0.0002", "taker_fee": "0.0005",
                            "price_increment": "0.1", "size_increment": "0.001",
                            "listing_time": "2022-01-01T00:00:00Z"}} for s in syms]}, fh)
        with gzip.open(os.path.join(rawfix, "binance/usdm/universe/membership.jsonl.gz"), "wt") as fh:
            fh.write(json.dumps({"date": "2022-01-01", "symbols": list(syms), "eligible": True}) + "\n")
        with gzip.open(os.path.join(rawfix, "binance/usdm/spreads/spreads.jsonl.gz"), "wt") as fh:
            fh.write(json.dumps({"symbol": "BTCUSDT", "ts": 1789516800000, "bid": "1.0",
                                 "ask": "1.1"}) + "\n")
        fix_raw = measure_raw(rawfix)
        res = run_checks(results_root, rawfix, raw=fix_raw, stores=stores, family=family,
                         round_id=round_id, task=task)
        failed = {c["id"] for c in res["checks"] if not c["ok"]}
        expect_flip = ["C2", "C3", "C5", "C7", "C8", "C9", "C10"]
        must_not_flip = ["C1", "C4", "C6", "C11", "C12", "C13", "C14", "C15", "C16", "C17", "C18"]
        return {"ok": all(c in failed for c in expect_flip)
                and not any(c in failed for c in must_not_flip),
                "expected_flip": expect_flip, "flipped": sorted(failed),
                "missing_flip": [c for c in expect_flip if c not in failed],
                "unexpected_flip": [c for c in must_not_flip if c in failed],
                "fixture_flags": {"minute_datasets": fix_raw["minute_datasets"],
                                  "finest_step_s": fix_raw["finest_step_seconds"],
                                  "reachable_horizons": fix_raw["reachable_horizons_minutes"],
                                  "usdm_subdirs": fix_raw["usdm_subdirs"],
                                  "schema_sections": fix_raw["schema_dataset_sections"],
                                  "lifecycle_fields": fix_raw["instrument_lifecycle_fields"],
                                  "documented_intervals": fix_raw["documented_intervals"],
                                  "probe_hit_files": sorted(res["probe"]["hit_file_classes"]),
                                  "probe_data_ext_hits": res["probe"]["payload_data_ext_hit_count"]}}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _print_checks(res):
    for c in res["checks"]:
        print("%-4s %-4s %s" % (c["id"], "PASS" if c["ok"] else "FAIL", c["name"]))
        if not c["ok"]:
            print("        detail: %s" % json.dumps(c["detail"], ensure_ascii=False)[:600])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results-root", default=DEFAULT_RESULTS)
    ap.add_argument("--raw-root", default=DEFAULT_RAW)
    ap.add_argument("--repo-root", default=DEFAULT_REPO)
    ap.add_argument("--card-body", default=os.path.join(DEFAULT_RESULTS, "_handoff/bodies",
                                                        FAMILY + ".md"))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--measure-only", action="store_true")
    ap.add_argument("--verify-verbatim", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--raw-fixture-control", action="store_true")
    ap.add_argument("--host-scan", action="store_true")
    args = ap.parse_args(argv)

    if args.measure_only:
        out = {"raw": measure_raw(args.raw_root), "probe": probe_raw(args.raw_root),
               "stores": measure_host_stores()}
        print(json.dumps(out, ensure_ascii=False, indent=1, default=str)[:60000])
        return 0

    if args.host_scan:
        stores = measure_host_stores()
        print(json.dumps(stores, ensure_ascii=False, indent=1, default=str)[:30000])
        return 0

    if args.verify_verbatim:
        sf = _family_surfaces(args.results_root, FAMILY, ROUND)
        out = verify_verbatim(sf["spec"], repo_root=args.repo_root,
                              card_body_path=args.card_body if os.path.exists(args.card_body) else None)
        print(json.dumps(out, ensure_ascii=False, indent=1)[:8000])
        return 0 if out["ok"] else 1

    raw = measure_raw(args.raw_root)
    stores = measure_host_stores()

    if args.self_test:
        out = self_test(args.results_root, raw, stores)
        print(json.dumps(out, ensure_ascii=False, indent=1)[:40000])
        return 0 if out["ok"] else 1

    if args.raw_fixture_control:
        out = raw_fixture_control(args.results_root, raw, stores)
        print(json.dumps(out, ensure_ascii=False, indent=1)[:8000])
        return 0 if out["ok"] else 1

    res = run_checks(args.results_root, args.raw_root, repo_root=args.repo_root,
                     raw=raw, stores=stores)
    out = _result(res["checks"], raw, stores, probe=res.get("probe"))
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1)[:40000])
    else:
        _print_checks(res)
        print("\nok=%s failed=%s" % (out["ok"], out["failed"]))
        print("raw: %s" % json.dumps(out["raw_summary"], ensure_ascii=False))
        print("probe: %s" % json.dumps(out["probe_summary"], ensure_ascii=False)[:600])
        print("stores: %s" % json.dumps(out["stores_summary"], ensure_ascii=False))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
