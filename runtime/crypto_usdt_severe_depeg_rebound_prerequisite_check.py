#!/usr/bin/env python3
"""Prerequisite-gate read-back checker for the family

    crypto-usdt-severe-depeg-next-day-rebound-100d-3sigma-2026-09-01

Card t_17cc1699 terminalised this family as TECHNICAL_INCOMPLETE because the
record's required data is not in the canonical raw:

  * the record's decision variable is the **USDT/USD (or a defensible USD-parity)
    daily price series**: the signal is `D_below(t) = 1` when
    `log(P_USDT,t) < mu_100(t) - 3*sigma_100(t)` over the most recent 100 daily
    observations. Nothing in the raw tree is a stablecoin price series: the store
    holds one venue's USD-M perpetual klines/funding/instruments for four
    contracts, every one of them *quoted in* USDT, and it carries no USDT/USD,
    USDC/USD, USD-parity index or composite series at all. The missing
    prerequisite is the signal's own independent variable, not an auxiliary
    filter;
  * the record's own required-data paragraph forbids the obvious substitution:
    "A USD-parity series must not be silently substituted with a crypto cross
    rate that embeds target-asset moves". A `BTCUSDT` mark price is a crypto
    cross rate whose moves are mostly the target asset's, so it cannot stand in
    for the peg series;
  * the registered universe is the source's ten assets (BTC, ETH, XRP, DOGE, TRX,
    BNB, ADA, LTC, XMR, XLM). Locally only BTC, ETH and BNB exist (as USDT-quoted
    USD-M perpetuals, which the record's own portability section calls *adapted*
    rather than direct); XRP, DOGE, TRX, ADA, LTC, XMR and XLM do not exist at any
    interval or depth;
  * the source's reported regression sample is 2017-11-11 -> 2024-11-02 (and the
    paper's subsample split extends to 2024-11-03) with 31 downside-depeg
    observations at k = 3. The raw's own daily history starts 2022-01-01, so even
    if a USDT series existed locally the source sample and the rolling 100-day
    state estimation over it are not coverable from this machine's data;
  * the perpetual-adaptation extras the record asks for are at best partial:
    funding exists (official rows for the four contracts), a `mark_price` column
    exists inside those funding rows, but there is no index-price series, no
    liquidation rule surface, and no point-in-time contract-availability history;
    point-in-time tradability / delisting / market-status data does not exist
    either.

Running the local four-contract perpetual panel instead would change the record's
decision variable, its price source, its universe and its sample. The card forbids
that ("不得以近似資料、替代市場或改寫 hypothesis 硬跑" / "不得縮減 universe 以硬造可
執行性"), so nothing was launched.

This script exists so an independent reader can re-derive that determination from
the live filesystem instead of trusting prose:

  * C1-C7 re-measure the canonical raw: the market-directory set, the instrument
    surface, the stored row shapes and the store's own documented dataset
    families, the decisive absence of any stablecoin/peg/USDT-USD dataset
    (entry-name probe over the whole tree at full depth), the absence of a
    USD-parity price source, the non-constructibility of the rolling 100-day state
    estimation and of the 3-sigma depeg event indicator, and the raw's own daily
    window versus the record's sample;
  * C8-C12 re-read the round's immutable artifacts and assert they state exactly
    the contract-mandated terminal values (verdict, layer, class, yield decision,
    zero attempts, null run_id), that nothing was ever submitted (no attempt
    directory, no terminal sentinel), that the registered universe was NOT shrunk
    to the locally available instruments, that the DCA registration still carries
    the contract 7.2 v1.3.1 provenance classes plus the complete 48-cell product,
    and that every raw-side value the artifact claims still equals a live
    re-measurement (C12);
  * C13 resolves every `*_verbatim` leaf of the persisted round-spec against its
    declared source (card / record / contract / footer) and re-checks the excerpt
    digest map - independent of the authoring run;
  * C14 re-asserts that the coverage/survivor surface is empty by construction and
    that the registered phase grid is intact;
  * C15 re-measures the host's non-canonical market-data stores and asserts that
    none of them carries a USD-parity or peg series that could stand in for the
    registered requirement (they are disclosed as measured-but-unused).

Read-only: it never writes inside the results tree or the raw tree. `--self-test`
builds tampered copies in fresh temp directories and asserts the checker refuses
them (non-vacuousness control). `--raw-fixture-control` builds a temp raw tree
carrying what this record would need (a documented daily USDT/USD series, a
stablecoin/peg reference tree, a spot market with a USDT/USD instrument, a second
venue, and a daily bar older than the card's registered raw start) and asserts the
raw-side checks flip to FAIL. `--host-scan` re-runs the house-wide search for such
a series. Every temp tree is removed afterwards.

Usage:
    python3 runtime/crypto_usdt_severe_depeg_rebound_prerequisite_check.py [--json]
    python3 runtime/crypto_usdt_severe_depeg_rebound_prerequisite_check.py --measure-only
    python3 runtime/crypto_usdt_severe_depeg_rebound_prerequisite_check.py --verify-verbatim
    python3 runtime/crypto_usdt_severe_depeg_rebound_prerequisite_check.py --self-test
    python3 runtime/crypto_usdt_severe_depeg_rebound_prerequisite_check.py --raw-fixture-control
    python3 runtime/crypto_usdt_severe_depeg_rebound_prerequisite_check.py --host-scan
    python3 runtime/crypto_usdt_severe_depeg_rebound_prerequisite_check.py --other-stores

Exit codes: 0 = all checks PASS, 1 = at least one FAIL, 2 = usage error.
"""
import argparse
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone

# Provenance classes are reused from the existing registered validator rather than
# re-implemented here (contract 7.2 v1.3.1).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import strategy_a_v2_counts as _sav2
except ImportError:  # pragma: no cover - only if the sibling module is missing
    _sav2 = None
try:
    from production_handoff import LIFECYCLE_FOOTER as _LIFECYCLE_FOOTER
except ImportError:  # pragma: no cover - only if the sibling module is missing
    _LIFECYCLE_FOOTER = None

FAMILY = "crypto-usdt-severe-depeg-next-day-rebound-100d-3sigma-2026-09-01"
ROUND = FAMILY + "-r1"
TASK = "t_17cc1699"
FAMILY_TITLE = "Crypto USDT Severe-Depeg Next-Day Rebound (100-Day Rolling 3-Sigma)"
DEFAULT_RESULTS = "/Volumes/ExpansionDrive/qlib-results"
DEFAULT_RAW = "/Volumes/ExpansionDrive/market-data-raw"
DEFAULT_BOARD_DB = os.path.join(os.path.expanduser("~"), ".hermes", "kanban", "boards",
                                "quant-strategy-research", "kanban.db")
DEFAULT_RECORD = os.path.join(os.path.expanduser("~"), ".hermes", "wiki", "quant",
                              FAMILY + ".md")
DEFAULT_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPANSION = "/Volumes/ExpansionDrive"
HOME = os.path.expanduser("~")
EXPECTED_SYMBOLS = ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
# The record's direct source universe (ten assets), and the local perpetual bases.
SOURCE_UNIVERSE = ["ADA", "BNB", "BTC", "DOGE", "ETH", "LTC", "TRX", "XLM", "XMR", "XRP"]
LOCAL_BASES = ["BNB", "BTC", "ETH", "SOL"]
KLINE_ROW_FIELDS = ["close", "close_time_ms", "high", "low", "open", "open_time_ms",
                    "volume"]
# The store nests to six levels (binance/usdm/klines/<SYMBOL>/<interval>/<file>). A planted
# dataset is probed to a depth the store cannot reach, so it cannot escape the probe.
PROBE_MAX_DEPTH = 8
# Name tokens that would have to exist for this record's required dataset (a documented
# daily USDT/USD or USD-parity series, the derived rolling state estimation and the
# 3-sigma depeg event indicator, and the stablecoin-supply/peg surfaces a composite would
# need). One unified list is used by the raw entry-name probe, the house-wide scan and
# the checker's own re-measurement; the two documents can therefore never disagree about
# what the probe returns. Deliberately includes tokens whose hits are substring
# collisions of local USDT pair names (tusd/busd/usdd) so "0 unclassified" is a
# measurement instead of an artefact of a curated vocabulary.
STABLECOIN_PEG_TOKENS = (
    "usdt_usd", "usdtusd", "usdt_usd_price", "usd_parity", "usdparity", "usdt_parity",
    "parity_price", "usdt_peg", "usdt_depeg", "depeg", "depeg_event", "peg_deviation",
    "pegdeviation", "tether", "tether_price", "usdt_price", "usdtprice", "stablecoin",
    "stablecoin_price", "stablecoin_supply", "stablecoin_supply_ratio", "usdc",
    "usdcusd", "usdc_usd", "usdd", "tusd", "busd", "dai_usd", "chainlink_usdt",
    "usdt_usd_oracle", "coinmarketcap_usdt", "coinmetrics", "kaiko", "cryptocompare",
    "coingecko", "glassnode")
# Tokens deliberately NOT probed, with the measured reason. Every exclusion is a
# probe-design decision demonstrated by a measurement (see `probe_exclusions` in the
# measurement output and the disclosed second pass in --host-scan); no exclusion removes
# a specific surface, because a bare `usdt`/`usd` hit could never distinguish a USDT/USD
# price series from this store's own quote metadata, and every compound token stays in
# the probe list.
PROBE_EXCLUSIONS = {
    "usdt": "substring of the quote currency in every local contract name and file name "
            "('BTCUSDT', 'BTCUSDT-1d-2022-01.jsonl.gz') and of the store's own funding/"
            "instrument metadata, so a hit could never distinguish a stablecoin price "
            "series from the quote currency; the compound tokens 'usdt_usd', "
            "'usdt_parity', 'usdt_price', 'usdt_peg', 'usdt_depeg' stay in the probe list",
    "usd": "substring of 'USDT', of the market directory 'usdm', of the settlement "
           "currency field and of ordinary English words, so a hit could never "
           "distinguish a USD-parity series; 'usd_parity'/'usdparity'/'usdc_usd'/"
           "'dai_usd' stay in the probe list",
    "price": "matches the store's own funding row keys ('mark_price', "
             "'funding_price_source') and every instrument price increment/min/max "
             "field, so a hit could never distinguish a price series from price "
             "metadata; 'usdt_price', 'stablecoin_price', 'tether_price', "
             "'parity_price' stay in the probe list",
    "peg": "substring of ordinary tokens the host already uses (ffmpeg library names, "
           "'.mcp_schema_cache__67dpegf.tmp' cache files); 'usdt_peg', 'peg_deviation', "
           "'pegdeviation' stay in the probe list",
    "stable": "substring of 'stablecoin' (itself a probe token) and of generic "
              "software artefacts ('stable-array.ts', 'stable-text.tsx', "
              "'stable-diffusion'); the compound stablecoin tokens stay in the probe "
              "list",
}
# Rows whose status carries the determination. C14 asserts the full map, not just its
# length, so flipping one row to PRESENT is refused.
DECISIVE_REQUIRED_STATUS = {
    "reference_asset_usdt_usd_daily_price_series": "ABSENT",
    "usdt_usd_series_documented_methodology_venue_or_composite": "ABSENT",
    "at_least_100_prior_usdt_daily_observations": "ABSENT",
    "usd_parity_series_not_substitutable_by_crypto_cross_rate": "NOT_CONSTRUCTIBLE",
    "rolling_100d_mean_sigma_state_estimation": "NOT_CONSTRUCTIBLE",
    "negative_depeg_event_indicator_3sigma": "NOT_CONSTRUCTIBLE",
    "source_sample_2017_11_11_to_2024_11_02": "ABSENT",
    "daily_close_to_close_returns_target_cryptocurrencies": "PARTIAL_3_OF_10",
}
MISSING_DATA_MATRIX_ITEMS = (
    "reference_asset_usdt_usd_daily_price_series",
    "usdt_usd_series_documented_methodology_venue_or_composite",
    "consistent_daily_timestamp_timezone_boundary_usdt_and_targets",
    "daily_close_to_close_returns_target_cryptocurrencies",
    "at_least_100_prior_usdt_daily_observations",
    "point_in_time_tradability_delisting_market_status",
    "perpetual_funding",
    "perpetual_mark_index_price",
    "perpetual_liquidation_rules",
    "perpetual_contract_availability_point_in_time",
    "usd_parity_series_not_substitutable_by_crypto_cross_rate",
    "rolling_100d_mean_sigma_state_estimation",
    "negative_depeg_event_indicator_3sigma",
    "source_universe_ten_assets",
    "source_sample_2017_11_11_to_2024_11_02",
    "falsification_item_1_reconstruct_event_indicator_without_future_information",
    "falsification_item_2_source_universe_sample_then_post2024_oos",
    "falsification_item_3_threshold_grid_k_1_1p5_2_3",
    "falsification_item_4_matched_vol_matched_beta_adjusted_controls",
    "falsification_item_5_clustered_se_block_bootstrap",
    "falsification_item_6_stressed_execution_costs",
    "falsification_item_7_leave_one_crisis_out",
    "falsification_item_8_alternative_usdt_sources_composites",
    "baseline_buy_and_hold_long_basket",
    "market_type_venue_execution_timing",
    "transaction_cost_convention",
)
DECISIVE_MATRIX_ITEMS = tuple(DECISIVE_REQUIRED_STATUS)
# The record's own reported regression sample and subsample split.
RECORD_SAMPLE_START = "2017-11-11"
RECORD_SAMPLE_END = "2024-11-02"
RECORD_SUBSAMPLE_END = "2024-11-03"
RECORD_SAMPLE_DAYS = 2548
# The frozen measured overlap of the record's sample with the raw's own daily window
# (raw starts 2022-01-01 -> 2022-01-01..2024-11-02). Literals, not re-derived, so C7 is
# not tautological.
EXPECTED_RECORD_DAYS_INSIDE_RAW_WINDOW = 1036
EXPECTED_RECORD_COVERAGE_FRACTION = 0.4066
RECORD_SEVERE_EVENT_COUNT_AT_3SIGMA = 31
# The raw's own daily window as registered on the card.
CARD_REGISTERED_RAW_WINDOW_START = "2022-01-01"
CARD_REGISTERED_RAW_WINDOW = "klines 2022-01-01\u21922026-09-11"
CARD_REGISTERED_FUNDING_WINDOW = "funding 2022-01-01T00:00Z\u21922026-09-12T08:00Z"
REGISTERED_PHASE_GRIDS = ["historical", "oos", "full", "fee_2x", "funding_2x",
                          "entry_delay_1_bar", "slippage_2ticks", "no_funding",
                          "no_funding_full", "cost_attrition_40bps"]
DCA_FOUR_AXES = ("spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct")
SEARCH_DOMAIN = "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN"
PROJECT_CONSTANT = "PROJECT_PRE_REGISTERED_CONSTANT"
USER_FIXED = "USER_FIXED"
OTHER_MARKET_NAMES = ("spot", "margin", "options", "inverse", "coinm", "delivery",
                      "futures", "quarter", "index", "stablecoin", "peg", "usdt")
INSTRUMENT_PEG_FIELDS = ("inflow", "outflow", "price_series", "peg", "stablecoin",
                         "tether", "parity", "usdt_usd", "usdc", "depeg", "supply")
# Liquidation-rule field probe: 'margin' alone is NOT probed because the store's own
# instrument metadata legitimately carries `margin_init` / `margin_maint` (initial and
# maintenance margin RATIOS), which are not liquidation rules.
LIQUIDATION_FIELD_TOKENS = ("liquidation", "liq_price", "liq_", "bankruptcy",
                            "auto_deleverage", "adl")
LISTING_FIELD_TOKENS = ("listing", "list_", "onboard", "delist", "tradability",
                        "market_status", "contract_availability", "status_ts")
DOCUMENTED_DATASET_FAMILIES = ["funding", "instruments",
                               "klines (Binance USD-M perpetual futures, UTC)"]
# The store's own schema documents that its kline rows carry the OHLCV shape only; this is
# the sentence that owns the "no quote volume / trade count / taker splits" claim.
SCHEMA_MISSING_FIELDS_SENTENCE = "trade count and taker-buy splits are"
# A USDT/USD or peg series would need one of these words in the normalised entry names.
PEG_DATASET_SURFACE_TOKENS = ("usdt_usd", "usdtusd", "usd_parity", "usdparity",
                              "usdt_parity", "parity_price", "usdt_peg", "usdt_depeg",
                              "tether", "stablecoin", "depeg", "usdt_price",
                              "stablecoin_supply", "stablecoin_price", "usdc", "usdcusd",
                              "usdc_usd", "coinmetrics", "kaiko", "cryptocompare",
                              "coingecko", "glassnode", "chainlink_usdt",
                              "coinmarketcap_usdt")
TMP_ROOTS = [os.path.join(HOME, "workspace"), EXPANSION, "/Volumes/ResearchData",
             os.path.join(HOME, ".hermes"),
             os.path.join(HOME, "workspace", "qlib-apple-container")]
SCAN_SKIP_DIRS = ("node_modules", "__pycache__", ".git", "venvs", "site-packages", ".venv",
                  "Photos Library.photoslibrary")
# The host's known non-canonical market-data stores - measured, disclosed, never used.
OTHER_STORE_ROOTS = (
    os.path.join(HOME, "workspace", "phase7-alpha-research"),
    os.path.join(HOME, "workspace", "phase3-portfolio-risk"),
    os.path.join(HOME, "workspace", "phase4-market-microstructure"),
    os.path.join(HOME, "workspace", "phase5-crypto-derivatives"),
    os.path.join(HOME, "workspace", "phase9-cross-sectional-factors"),
    os.path.join(HOME, "workspace", "phase12-l2-l3-execution-tca"),
    os.path.join(HOME, "workspace", "phase10-pit-bitemporal"),
    os.path.join(HOME, "workspace", "phase11-options-volatility"),
    os.path.join(HOME, "workspace", "ml4t-real-evidence-remediation"),
    os.path.join(HOME, "workspace", "a1-usdm-pit-lifecycle-20260830"),
    os.path.join(HOME, "workspace", "a1-1-phase9-pit-membership-20260830"),
    os.path.join(HOME, "workspace", "a1-2-prospective-pit-foundation-20260830"),
    os.path.join(HOME, "workspace", "a1-3-prospective-pit-cohort-20260830"),
    os.path.join(HOME, "workspace", "alpha-strategy-research"),
    os.path.join(EXPANSION, "daily-crypto-brief"),
)
# Classification of every house-wide hit. A hit is acceptable only if it is a document,
# code, session/log/cache artefact, our own provenance artefact, an unrelated source
# identifier (a substring collision of a local USDT pair name), or a directory with no
# data file beneath it - never a data store that the raw tree lacks.
HOUSE_HIT_CLASSES = ("canonical_record_document", "related_wiki_document",
                     "own_evidence_snapshot_false_positive",
                     "own_round_artifact_false_positive",
                     "own_family_directory_false_positive",
                     "own_checker_source_false_positive",
                     "own_handoff_body_false_positive",
                     "repo_documentation_false_positive", "source_code_false_positive",
                     "research_document_false_positive",
                     "unrelated_source_identifier_false_positive",
                     "agent_session_or_log_false_positive", "hermes_cache_false_positive",
                     "hermes_state_artifact_false_positive",
                     "hermes_skill_or_catalog_false_positive",
                     "integrity_manifest_false_positive",
                     "non_canonical_staging_copy_false_positive",
                     "dataset_stub_directory_false_positive",
                     "unclassified_data_candidate")
UNCLASSIFIED_CLASS = "unclassified_data_candidate"
INTEGRITY_MANIFEST_MARKERS = ("hash-manifest", "hashes", "rehash", "hash_compare",
                              "pre_hashes", "post_hash", "hash_three_states",
                              "results_hash")
# this card's own evidence snapshot carries the family name in its filename
OWN_ARTIFACT_BASENAMES = (FAMILY + "-prerequisite-gate-20260917.json",)
WIKI_DIR_MARKER = os.path.join(".hermes", "wiki")
ROUND_DIR_PARTS = (FAMILY, "rounds", ROUND)
HANDOFF_BODY_DIR = os.path.join("qlib-results", "_handoff", "bodies")
# the raw-side values the persisted artifact claims and that C12 re-derives live
CLAIMED_MEASUREMENT_KEYS = (
    "raw_1d_window_utc", "raw_1d_history_days", "klines_1d_open_step_seconds",
    "klines_1d_open_grid_aligned_utc", "instrument_count", "instrument_symbols",
    "instrument_ids", "instrument_base_currencies", "instrument_quote_currencies",
    "instrument_settlement_currencies", "instrument_types", "instrument_is_inverse",
    "instrument_field_names", "instrument_peg_field_hits",
    "instrument_definition_files_outside_usdm", "klines_dataset_dirs",
    "klines_interval_set", "klines_row_field_set", "funding_symbol_dirs",
    "funding_row_keys", "funding_venues", "funding_row_has_mark_price",
    "raw_entry_name_count", "probe_tokens_tested", "probe_exclusions",
    "stablecoin_peg_token_hits_in_entry_names", "peg_token_name_collisions",
    "stablecoin_peg_token_hits_unexplained", "ref_or_peg_subtrees",
    "stablecoin_peg_dataset_present", "usdt_usd_reference_series_present",
    "usdt_usd_series_documented_methodology_present",
    "usdt_log_price_rolling_inputs_constructible", "depeg_event_indicator_constructible",
    "stablecoin_supply_panel_present", "liquidation_rule_fields_present",
    "listing_or_tradability_history_present", "source_universe_overlap",
    "source_universe_missing", "local_contracts_not_in_record_universe",
    "record_reported_sample", "record_sample_days_inside_raw_window",
    "record_sample_coverage_fraction", "schema_dataset_sections",
    "schema_documents_stablecoin_dataset", "schema_sha256", "inventory_sha256",
    "meta_sha256", "venue", "market_type", "binance_market_dirs",
    "paths_named_other_market", "non_binance_paths",
)


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


def _rel_dirs(root, max_depth=3):
    out = []
    for dp, dn, fn in os.walk(root):
        rel = os.path.relpath(dp, root)
        if rel.count(os.sep) >= max_depth:
            dn[:] = []
            continue
        out.append(rel)
    return sorted(p for p in out if p != ".")


def _all_entries(root, max_depth=PROBE_MAX_DEPTH):
    """Every file/directory basename token under the raw root (for name probes)."""
    names = []
    for dp, dn, fn in os.walk(root):
        rel = os.path.relpath(dp, root)
        if rel.count(os.sep) >= max_depth:
            dn[:] = []
            continue
        dn[:] = [d for d in dn if not d.startswith(".")]
        names.extend(dn)
        names.extend(f for f in fn if not f.startswith("."))
    return sorted({n.lower() for n in names})


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def _sha256_text(text):
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rows(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _day_span_days(a_iso, b_iso):
    a = datetime.fromisoformat(a_iso)
    b = datetime.fromisoformat(b_iso)
    return (b - a).days


LOCAL_SYMBOL_LC = [s.lower() for s in EXPECTED_SYMBOLS]
_RAW_MEASURE_CACHE = {}
_OTHER_STORES_CACHE = {}


def _token_only_matches_local_contract_names(path, token):
    """True when every occurrence of `token` in `path` sits inside a local contract name.

    `busd` is a substring of `BNBUSDT`, so the store's own BNBUSDT directories match a
    Binance-USD probe; removing the local contract names from the path and re-testing the
    token is the measured form of "this hit names another instrument, not a stablecoin
    dataset". A hit outside the contract names (`busd-daily.jsonl.gz`) is NOT a collision.
    """
    low = path.lower()
    if token not in low:
        return False
    stripped = low
    for sym in LOCAL_SYMBOL_LC:
        stripped = stripped.replace(sym, "")
    return token not in stripped


def _is_local_contract_name(name):
    """True when a raw-tree name IS a local contract name or one of its files.

    `bnbusdt` contains the token `busd`, so a probe hit on `busd` is a substring collision
    with the local BNBUSDT contract - not evidence of a Binance-USD dataset. Every hit is
    classified this way instead of dropping the token from the probe list, so the collision
    is measured rather than hidden.
    """
    low = name.lower()
    return any(low == s or low.startswith(s + "-") or low.startswith(s + ".")
               for s in LOCAL_SYMBOL_LC)


def measure_raw(raw, fresh=False):
    """Independent re-measurement of the canonical raw. No artifact is trusted.

    Memoised per raw root inside the process: `--self-test` re-runs the whole check once per
    tampered copy, and the raw tree is read-only, so re-walking it 30 times buys nothing. The
    fixture control passes its own temp root, which is measured fresh.
    """
    if not fresh and raw in _RAW_MEASURE_CACHE:
        return _RAW_MEASURE_CACHE[raw]
    m = {}
    m["raw_root"] = raw
    m["top_level_dirs"] = _dirs(raw)
    m["binance_market_dirs"] = _dirs(os.path.join(raw, "binance"))
    m["binance_usdm_subdirs"] = _dirs(os.path.join(raw, "binance", "usdm"))
    m["raw_paths"] = _rel_dirs(raw, 3)
    m["paths_named_other_market"] = [
        p for p in m["raw_paths"] if os.path.basename(p).lower() in OTHER_MARKET_NAMES]
    m["non_binance_paths"] = [p for p in m["raw_paths"]
                              if not p.startswith("binance") and not p.startswith("_")]
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
    btc1h = os.path.join(kl, "BTCUSDT", "1h")
    files1h = sorted(f for f in os.listdir(btc1h) if f.endswith(".jsonl.gz")) \
        if os.path.isdir(btc1h) else []
    if files1h:
        r_first = _rows(os.path.join(btc1h, files1h[0]))
        r_last = _rows(os.path.join(btc1h, files1h[-1]))
        m["klines_row_field_set"] = sorted(r_last[-1].keys())
        m["klines_1h_first_open_utc"] = _iso(r_first[0]["open_time_ms"])
        m["klines_1h_last_open_utc"] = _iso(r_last[-1]["open_time_ms"])
        steps = set()
        for f in files1h:
            rr = _rows(os.path.join(btc1h, f))
            for i in range(len(rr) - 1):
                steps.add((rr[i + 1]["open_time_ms"] - rr[i]["open_time_ms"]) // 1000)
        m["klines_1h_open_step_seconds"] = sorted(steps)

    # ---- the daily grid the signal's t / t+1 semantics would live on ---------------
    first, last, steps_1d, aligned = {}, {}, set(), True
    for sym in (m["klines_dataset_dirs"] or []):
        d = os.path.join(kl, sym, "1d")
        if not os.path.isdir(d):
            continue
        fs = sorted(f for f in os.listdir(d) if f.endswith(".jsonl.gz"))
        if not fs:
            continue
        f_rows = _rows(os.path.join(d, fs[0]))
        l_rows = _rows(os.path.join(d, fs[-1]))
        first[sym] = f_rows[0]["open_time_ms"]
        last[sym] = l_rows[-1]["open_time_ms"]
        for rr in (f_rows, l_rows):
            for r in rr:
                if r["open_time_ms"] % 86400000 != 0:
                    aligned = False
        for fs_name in fs:
            rr = _rows(os.path.join(d, fs_name))
            for i in range(len(rr) - 1):
                steps_1d.add((rr[i + 1]["open_time_ms"] - rr[i]["open_time_ms"]) // 1000)
    m["klines_1d_first_by_symbol"] = {k: _iso(v) for k, v in first.items()}
    m["klines_1d_last_by_symbol"] = {k: _iso(v) for k, v in last.items()}
    m["klines_1d_open_step_seconds"] = sorted(steps_1d)
    m["klines_1d_open_grid_aligned_utc"] = bool(aligned and first)
    window = [_iso(min(first.values())), _iso(max(last.values()))] if first else None
    m["raw_1d_window_utc"] = window
    m["card_registered_raw_window"] = CARD_REGISTERED_RAW_WINDOW
    m["card_registered_funding_window"] = CARD_REGISTERED_FUNDING_WINDOW
    m["raw_1d_window_starts_at_or_after_card_registration"] = bool(
        window and window[0][:10] >= CARD_REGISTERED_RAW_WINDOW_START)
    m["raw_1d_window_covers_record_sample_end"] = bool(
        window and window[1][:10] >= RECORD_SAMPLE_END)
    if window:
        m["raw_1d_history_days"] = _day_span_days(window[0], window[1])
    # the record's own reported sample, and how much of it the raw's window can cover
    m["record_reported_sample"] = {"start": RECORD_SAMPLE_START, "end": RECORD_SAMPLE_END,
                                   "subsample_end": RECORD_SUBSAMPLE_END,
                                   "days": RECORD_SAMPLE_DAYS,
                                   "severe_event_count_at_3sigma":
                                       RECORD_SEVERE_EVENT_COUNT_AT_3SIGMA}
    if window:
        a = max(RECORD_SAMPLE_START, window[0][:10])
        b = min(RECORD_SAMPLE_END, window[1][:10])
        overlap = max(0, _day_span_days(a + "T00:00:00+00:00", b + "T00:00:00+00:00"))
        m["record_sample_days_inside_raw_window"] = overlap
        m["record_sample_coverage_fraction"] = round(overlap / RECORD_SAMPLE_DAYS, 4)
        m["raw_window_covers_record_sample_start"] = window[0][:10] <= RECORD_SAMPLE_START
        m["raw_window_excludes_record_sample_years"] = sorted(
            {str(y) for y in range(int(RECORD_SAMPLE_START[:4]),
                                   int(window[0][:4]))})
    # the record's universe vs what exists locally
    m["source_universe"] = list(SOURCE_UNIVERSE)
    m["source_universe_present_local"] = sorted(set(SOURCE_UNIVERSE) & set(LOCAL_BASES))
    m["source_universe_overlap"] = len(m["source_universe_present_local"])
    m["source_universe_missing"] = sorted(set(SOURCE_UNIVERSE) - set(LOCAL_BASES))
    m["local_contracts_not_in_record_universe"] = [
        s for s in (m["klines_dataset_dirs"] or [])
        if s[:-4].upper() not in SOURCE_UNIVERSE] if m["klines_dataset_dirs"] else []

    fu = os.path.join(ud, "funding")
    m["funding_symbol_dirs"] = _dirs(fu)
    p = os.path.join(fu, "BTCUSDT", "BTCUSDT-funding.jsonl.gz")
    if os.path.exists(p):
        rs = _rows(p)
        m["funding_row_keys"] = sorted(rs[-1].keys())
        m["funding_venues"] = sorted({str(r.get("venue")) for r in rs})
        m["funding_truth_status_values"] = sorted({str(r.get("truth_status")) for r in rs})
        m["funding_first_ms"] = _iso(rs[0]["funding_time_ms"])
        m["funding_last_ms"] = _iso(rs[-1]["funding_time_ms"])
        m["funding_row_has_mark_price"] = "mark_price" in rs[-1]
        m["funding_row_has_index_price_field"] = any(
            "index" in k.lower() for k in rs[-1])
    m["funding_row_has_peg_field"] = any(
        t in k.lower() for k in (m.get("funding_row_keys") or [])
        for t in STABLECOIN_PEG_TOKENS)

    inst_path = os.path.join(ud, "instruments", "usdm-perp-instruments.json")
    inst = _load_json(inst_path).get("instruments", []) if os.path.exists(inst_path) else []
    m["instrument_count"] = len(inst)
    m["instrument_symbols"] = sorted(i["fields"].get("raw_symbol") for i in inst
                                     if i["fields"].get("raw_symbol"))
    m["instrument_ids"] = sorted(i["fields"].get("id") for i in inst
                                 if i["fields"].get("id"))
    m["instrument_base_currencies"] = sorted(i["fields"].get("base_currency") for i in inst
                                             if i["fields"].get("base_currency"))
    m["instrument_is_inverse"] = sorted({bool(i["fields"].get("is_inverse")) for i in inst})
    m["instrument_types"] = sorted({i["fields"].get("type") for i in inst})
    m["instrument_quote_currencies"] = sorted({i["fields"].get("quote_currency")
                                               for i in inst
                                               if i["fields"].get("quote_currency")})
    m["instrument_settlement_currencies"] = sorted({i["fields"].get("settlement_currency")
                                                    for i in inst
                                                    if i["fields"].get("settlement_currency")})
    m["instrument_field_names"] = sorted({k for i in inst for k in i["fields"]})
    m["instrument_peg_field_hits"] = sorted(
        f for f in m["instrument_field_names"]
        if any(t in f.lower() for t in INSTRUMENT_PEG_FIELDS))
    m["liquidation_rule_fields_present"] = any(
        t in f.lower() for f in m["instrument_field_names"]
        for t in LIQUIDATION_FIELD_TOKENS)
    m["listing_or_tradability_history_present"] = any(
        t in f.lower() for f in m["instrument_field_names"]
        for t in LISTING_FIELD_TOKENS)
    m["kline_row_has_peg_field"] = any(
        t in f.lower() for f in (m.get("klines_row_field_set") or [])
        for t in STABLECOIN_PEG_TOKENS)

    schema_path = os.path.join(raw, "_meta", "SCHEMA.md")
    schema = open(schema_path, encoding="utf-8").read() if os.path.exists(schema_path) else ""
    flat = schema.replace("*", "")
    m["schema_sha256"] = _sha256_text(schema) if schema else None
    m["schema_dataset_sections"] = sorted(ln.split("## Dataset:", 1)[1].strip()
                                          for ln in schema.splitlines()
                                          if ln.startswith("## Dataset:"))
    m["schema_documents_stablecoin_dataset"] = any(
        t in flat.lower() for t in PEG_DATASET_SURFACE_TOKENS)
    m["schema_single_venue"] = "Binance USD-M perpetual futures, UTC" in schema
    m["schema_documents_missing_fields"] = ("quote_volume" in flat
                                            and SCHEMA_MISSING_FIELDS_SENTENCE in flat)
    m["schema_no_silent_gapfill"] = "No gaps are silently filled" in schema
    m["schema_peg_word_lines"] = [ln.strip()[:90] for ln in schema.splitlines()
                                  if any(t in ln.lower()
                                         for t in ("usdt", "peg", "stablecoin", "tether",
                                                   "usd "))]
    inv_path = os.path.join(raw, "_meta", "INVENTORY.md")
    inv = open(inv_path, encoding="utf-8").read() if os.path.exists(inv_path) else ""
    m["inventory_sha256"] = _sha256_text(inv) if inv else None
    m["inventory_declares_no_other_store_found"] = (
        "No other Binance/klines/tardis/aggTrade store was found on" in inv)

    names = _all_entries(raw, PROBE_MAX_DEPTH)
    joined = "\n".join(names)
    # A dataset name may use hyphens where a spec uses underscores ('usdt-usd' vs
    # 'usdt_usd'), so every token is matched against the raw names AND against a
    # separator-normalised copy. Normalising can only add hits, never hide one.
    joined_norm = joined.replace("-", "_").replace(" ", "_").replace(".", "_")
    m["raw_entry_name_count"] = len(names)
    m["probe_tokens_tested"] = list(STABLECOIN_PEG_TOKENS)
    m["stablecoin_peg_token_hits_in_entry_names"] = sorted(
        {t for t in STABLECOIN_PEG_TOKENS if t in joined or t.replace("-", "_") in joined_norm})
    m["peg_token_name_examples"] = {
        t: sorted({n for n in names if t in n
                   or t.replace("-", "_") in n.lower().replace("-", "_").replace(" ", "_")
                   .replace(".", "_")})[:3]
        for t in STABLECOIN_PEG_TOKENS if any(
            t in n or t.replace("-", "_") in n.lower().replace("-", "_")
            .replace(" ", "_").replace(".", "_") for n in names)}
    # a token hit on a name that IS a local contract name (or one of its data files) is a
    # substring collision, not a dataset: `busd` sits inside `bnbusdt`
    m["peg_token_name_collisions"] = {
        t: sorted({n for n in names if t in n and _is_local_contract_name(n)})[:3]
        for t in STABLECOIN_PEG_TOKENS
        if any(t in n and _is_local_contract_name(n) for n in names)}
    m["stablecoin_peg_token_hits_unexplained"] = sorted(
        t for t in STABLECOIN_PEG_TOKENS
        if any((t in n or t.replace("-", "_") in n.lower().replace("-", "_")
                .replace(" ", "_").replace(".", "_")) and not _is_local_contract_name(n)
               for n in names))
    m["excluded_token_raw_tree_collisions"] = {
        t: sorted({n for n in names if t in n})[:3] for t in PROBE_EXCLUSIONS
        if any(t in n for n in names)}
    m["raw_entry_names_sample"] = names[:40]
    m["probe_exclusions"] = dict(PROBE_EXCLUSIONS)
    # reference subtrees: a planted peg/composite store normally lands in one of these
    m["ref_or_peg_subtrees"] = sorted(
        p for p in m["raw_paths"]
        if p.split(os.sep)[0].lower() in ("_ref", "ref", "reference", "stablecoin",
                                          "stablecoins", "peg", "tether", "usdt",
                                          "usd_parity", "index"))
    # any second instrument-definition surface (a spot/other-venue definition file);
    # `_meta/**` holds the store's own provenance manifests for the same usdm instruments,
    # so it is not a second surface.
    m["instrument_definition_files_outside_usdm"] = sorted(
        os.path.join(dp, f) for dp, dn, fn in os.walk(raw)
        for f in fn if "instrument" in f.lower()
        and os.path.relpath(os.path.join(dp, f), raw).split(os.sep)[:2] != ["binance", "usdm"]
        and os.path.relpath(os.path.join(dp, f), raw).split(os.sep)[0] != "_meta")
    m["meta_sha256"] = {rel: _sha256_file(os.path.join(raw, "_meta", rel))
                        for rel in ("CONFIG.json", "SCHEMA.md", "INVENTORY.md",
                                    "INSTRUMENTS_EXPORT.json", "TRANSCODE_MANIFEST.json",
                                    "FUNDING_EXPORT.json")
                        if os.path.exists(os.path.join(raw, "_meta", rel))}

    # ---- the record's decision-variable surface, measured item by item -------------
    hay = joined + "\n" + joined_norm
    m["stablecoin_peg_dataset_present"] = bool(m["stablecoin_peg_token_hits_unexplained"])
    m["usdt_usd_reference_series_present"] = any(
        t in hay for t in ("usdt_usd", "usdtusd", "usdt_price", "usdtprice", "usd_parity",
                           "usdparity", "usdt_parity", "parity_price", "usdt_peg",
                           "usdt_depeg", "tether", "tether_price"))
    m["usdt_usd_series_documented_methodology_present"] = bool(
        m["usdt_usd_reference_series_present"] and
        any(t in hay for t in ("methodology", "composite", "index_source")))
    m["stablecoin_supply_panel_present"] = any(
        t in hay for t in ("stablecoin_supply", "stablecoin_price", "supply_ratio",
                           "stablecoin"))
    # the rolling 100-day state estimation needs the peg series itself; the 3-sigma event
    # indicator needs that state estimation. Without the series neither exists.
    m["usdt_log_price_rolling_inputs_constructible"] = bool(
        m["usdt_usd_reference_series_present"])
    m["depeg_event_indicator_constructible"] = bool(
        m["usdt_log_price_rolling_inputs_constructible"])
    m["note"] = ("single venue (BINANCE USD-M perpetual), four fixed contracts: klines "
                 "whose rows carry the seven-field OHLCV shape only, that venue's "
                 "funding (official rows, with a `mark_price` column), and its "
                 "instrument definitions. No stablecoin price series of any kind: no "
                 "USDT/USD, no USD-parity index, no composite, no supply panel - every "
                 "instrument is quoted and settled in USDT, and a USDT-quoted crypto "
                 "cross rate is exactly what the record's required-data paragraph "
                 "forbids substituting for the peg series. The raw's own daily history "
                 "begins 2022-01-01, so only %d of the record's %d sample days (%.2f%%) "
                 "fall inside it, and the record's pre-2022 crisis episodes are absent."
                 % (m.get("record_sample_days_inside_raw_window") or 0, RECORD_SAMPLE_DAYS,
                    100.0 * (m.get("record_sample_coverage_fraction") or 0.0)))
    _RAW_MEASURE_CACHE[raw] = m
    return m


def measure_other_stores(fresh=False):
    """The host's non-canonical market-data stores, measured and disclosed as unused.

    A substitute is only relevant if it could carry a USD-parity / peg series. Each store
    is probed with the registered token list at a depth the store cannot escape, and its
    data-file surface is counted so a hit directory cannot hide behind a stub. Memoised:
    the stores are read-only and the self-test re-runs the check once per tampered copy.
    """
    if not fresh and _OTHER_STORES_CACHE:
        return _OTHER_STORES_CACHE["v1"]
    out = {}
    for root in OTHER_STORE_ROOTS:
        rec = {}
        rec["exists"] = os.path.isdir(root)
        rec["used_as_input"] = False
        if rec["exists"]:
            pairs = _probe_hit_pairs([root], STABLECOIN_PEG_TOKENS)
            rec["peg_token_hits"] = len(pairs)
            rec["peg_token_hit_examples"] = [p for p, _ in pairs[:5]]
            unexplained = [p for p, toks in pairs
                           if not all(_token_only_matches_local_contract_names(p, t)
                                      for t in toks)]
            rec["unexplained_peg_hits"] = len(unexplained)
            rec["unexplained_peg_hit_examples"] = unexplained[:5]
            rec["data_files"] = _dir_data_probe(root, max_depth=4, cap=2000)[
                "data_file_count"]
            rec["has_usd_parity_series"] = any(
                not p.lower().endswith(DOC_EXTS + CODE_EXTS) for p in unexplained)
        out[root] = rec
    result = {"stores": out,
              "any_store_carries_usd_parity_series": any(
                  v.get("has_usd_parity_series") for v in out.values()),
              "note": ("every host-local substitute was probed with the same %d-token list "
                       "used for the raw tree; none is a USD-parity/peg price series and "
                       "none was used as an input. The only USDT-referencing objects found "
                       "are documents (the canonical record, wiki raw articles, the "
                       "frozen handoff body) and research prose."
                       % len(STABLECOIN_PEG_TOKENS))}
    _OTHER_STORES_CACHE["v1"] = result
    return result


def run_checks(results_root, raw_root, repo_root=None, card_body_path=None,
               record_path=None, board_db=None):
    checks = []
    add = lambda cid, ok, detail: checks.append({
        "id": cid, "status": "PASS" if ok else "FAIL", "detail": detail})
    raw = measure_raw(raw_root)
    round_dir = os.path.join(results_root, FAMILY, "rounds", ROUND)
    spec_path = os.path.join(round_dir, "round-spec.json")
    verdict_path = os.path.join(round_dir, "verdict.json")
    spec = _load_json(spec_path) if os.path.exists(spec_path) else {}
    verdict = _load_json(verdict_path) if os.path.exists(verdict_path) else {}
    pg = spec.get("prerequisite_gate") or {}
    ur = spec.get("universe_registration") or {}

    # C1 - one market type for one venue, and no other market directory at all
    #      (no spot market, no dated futures, no second venue, no reference subtree).
    md = raw["binance_market_dirs"] or []
    add("C1", md == ["usdm"] and raw["venue"] == "BINANCE" and raw["market_type"] == "usdm_perp"
        and raw["paths_named_other_market"] == [] and raw["non_binance_paths"] == []
        and raw["ref_or_peg_subtrees"] == [],
        "binance market dirs=%s venue=%s market_type=%s other_market_paths=%s "
        "non_binance_paths=%s ref/peg subtrees=%s"
        % (md, raw["venue"], raw["market_type"], raw["paths_named_other_market"],
           raw["non_binance_paths"], raw["ref_or_peg_subtrees"]))

    # C2 - the four USD-M perpetual contracts are the only local instruments, all
    #      USDT-quoted and USDT-settled, carrying no peg/series field, no liquidation-rule
    #      field and no listing/tradability/availability field.
    add("C2", raw["instrument_count"] == 4
        and raw["instrument_symbols"] == EXPECTED_SYMBOLS
        and raw["instrument_types"] == ["CryptoPerpetual"]
        and raw["instrument_quote_currencies"] == ["USDT"]
        and raw["instrument_settlement_currencies"] == ["USDT"]
        and raw["instrument_is_inverse"] == [False]
        and raw["instrument_peg_field_hits"] == []
        and raw["liquidation_rule_fields_present"] is False
        and raw["listing_or_tradability_history_present"] is False
        and raw["instrument_definition_files_outside_usdm"] == [],
        "instruments=%d symbols=%s types=%s quote=%s settlement=%s is_inverse=%s "
        "peg_fields=%s liquidation_fields=%s listing_fields=%s instrument files outside "
        "binance/usdm=%s"
        % (raw["instrument_count"], raw["instrument_symbols"], raw["instrument_types"],
           raw["instrument_quote_currencies"], raw["instrument_settlement_currencies"],
           raw["instrument_is_inverse"], raw["instrument_peg_field_hits"],
           raw["liquidation_rule_fields_present"],
           raw["listing_or_tradability_history_present"],
           raw["instrument_definition_files_outside_usdm"]))

    # C3 - the stored row shapes carry no peg surface and the store documents its own
    #      dataset families (klines/funding/instruments) plus the fields it does NOT hold.
    add("C3", raw["klines_row_field_set"] == KLINE_ROW_FIELDS
        and raw["kline_row_has_peg_field"] is False
        and raw["funding_row_has_peg_field"] is False
        and raw["funding_row_has_mark_price"] is True
        and raw["schema_dataset_sections"] == DOCUMENTED_DATASET_FAMILIES
        and raw["schema_documents_stablecoin_dataset"] is False
        and raw["schema_documents_missing_fields"] is True
        and raw["inventory_declares_no_other_store_found"] is True,
        "kline row fields=%s kline peg field=%s funding peg field=%s funding mark_price "
        "column=%s documented dataset families=%s schema documents a stablecoin dataset=%s "
        "schema documents missing fields=%s inventory declares no other store=%s"
        % (raw["klines_row_field_set"], raw["kline_row_has_peg_field"],
           raw["funding_row_has_peg_field"], raw["funding_row_has_mark_price"],
           raw["schema_dataset_sections"], raw["schema_documents_stablecoin_dataset"],
           raw["schema_documents_missing_fields"],
           raw["inventory_declares_no_other_store_found"]))

    # C4 - the decisive absence: the whole-tree entry-name probe returns no stablecoin /
    #      peg / USDT-USD dataset, so the record's own independent variable is absent.
    add("C4", raw["stablecoin_peg_token_hits_unexplained"] == []
        and raw["stablecoin_peg_dataset_present"] is False
        and raw["usdt_usd_reference_series_present"] is False
        and raw["usdt_usd_series_documented_methodology_present"] is False
        and raw["stablecoin_supply_panel_present"] is False,
        "token hits in entry names=%s (all explained as local contract-name collisions: "
        "%s), unexplained hits=%s dataset present=%s USDT/USD series present=%s "
        "documented-methodology series present=%s stablecoin supply panel present=%s "
        "(probe: %d tokens, %d entry names, depth %d)"
        % (raw["stablecoin_peg_token_hits_in_entry_names"], raw["peg_token_name_collisions"],
           raw["stablecoin_peg_token_hits_unexplained"],
           raw["stablecoin_peg_dataset_present"], raw["usdt_usd_reference_series_present"],
           raw["usdt_usd_series_documented_methodology_present"],
           raw["stablecoin_supply_panel_present"], len(STABLECOIN_PEG_TOKENS),
           raw["raw_entry_name_count"], PROBE_MAX_DEPTH))

    # C5 - the registered price source (a USD-parity series) does not exist: there is no
    #      spot/index market path anywhere in the tree and the only local price series are
    #      USDT-quoted USD-M perpetuals, which the record's own univariate-independent
    #      paragraph forbids substituting for the peg series.
    add("C5", raw["klines_dataset_dirs"] == EXPECTED_SYMBOLS
        and raw["instrument_settlement_currencies"] == ["USDT"]
        and raw["paths_named_other_market"] == []
        and raw["instrument_definition_files_outside_usdm"] == []
        and raw["usdt_usd_reference_series_present"] is False,
        "klines datasets=%s quote/settlement=%s/%s no other-market path=%s no instrument "
        "surface outside binance/usdm=%s USDT/USD series present=%s - a USDT-quoted USD-M "
        "perpetual is not the registered 'USDT/USD or defensible USD-parity' source"
        % (raw["klines_dataset_dirs"], raw["instrument_quote_currencies"],
           raw["instrument_settlement_currencies"], raw["paths_named_other_market"] == [],
           raw["instrument_definition_files_outside_usdm"] == [],
           raw["usdt_usd_reference_series_present"]))

    # C6 - the record's own decision variable cannot be constructed from anything the
    #      store holds: no peg series -> no rolling 100-day mean/sigma -> no 3-sigma event.
    add("C6", raw["usdt_log_price_rolling_inputs_constructible"] is False
        and raw["depeg_event_indicator_constructible"] is False
        and raw["usdt_usd_reference_series_present"] is False
        and raw["stablecoin_supply_panel_present"] is False,
        "rolling 100-day state estimation constructible=%s 3-sigma depeg event indicator "
        "constructible=%s USDT/USD series present=%s stablecoin supply panel present=%s"
        % (raw["usdt_log_price_rolling_inputs_constructible"],
           raw["depeg_event_indicator_constructible"],
           raw["usdt_usd_reference_series_present"], raw["stablecoin_supply_panel_present"]))

    # C7 - the raw's own daily window vs the record's reported sample: the window starts at
    #      2022-01-01 on a UTC-aligned daily grid, so the record's pre-2022 sample years
    #      (the 2020-03 and 2022-11 style peg episodes among them) are absent. The spec's own
    #      data block is compared against the live measurement, so a spec that claims the
    #      sample is covered is refused rather than merely unreported.
    dat = spec.get("data") or {}
    add("C7", raw["raw_1d_window_starts_at_or_after_card_registration"] is True
        and raw["raw_1d_window_utc"] is not None
        and raw["raw_1d_window_utc"][0][:10] == CARD_REGISTERED_RAW_WINDOW_START
        and raw["klines_1d_open_step_seconds"] == [86400]
        and raw["klines_1d_open_grid_aligned_utc"] is True
        and raw["record_sample_days_inside_raw_window"] == EXPECTED_RECORD_DAYS_INSIDE_RAW_WINDOW
        and raw["record_sample_coverage_fraction"] == EXPECTED_RECORD_COVERAGE_FRACTION
        and raw["raw_window_covers_record_sample_start"] is False
        and dat.get("measured_raw_1d_window_utc") == raw["raw_1d_window_utc"]
        and dat.get("record_sample_days_inside_raw_window") ==
            raw["record_sample_days_inside_raw_window"]
        and dat.get("record_sample_coverage_fraction") == raw["record_sample_coverage_fraction"]
        and dat.get("raw_window_covers_record_sample_start") is False,
        "raw 1d window=%s step=%s UTC-day aligned=%s record sample=%s..%s (%d days) days "
        "inside window=%s coverage=%s window covers record sample start=%s (spec claims %s, "
        "spec days=%s) missing years=%s"
        % (raw["raw_1d_window_utc"], raw["klines_1d_open_step_seconds"],
           raw["klines_1d_open_grid_aligned_utc"], RECORD_SAMPLE_START, RECORD_SAMPLE_END,
           RECORD_SAMPLE_DAYS, raw["record_sample_days_inside_raw_window"],
           raw["record_sample_coverage_fraction"],
           raw["raw_window_covers_record_sample_start"],
           dat.get("raw_window_covers_record_sample_start"),
           dat.get("record_sample_days_inside_raw_window"),
           raw["raw_window_excludes_record_sample_years"]))

    # C8 - the persisted verdict states exactly the contract-mandated terminal values.
    att = verdict.get("attempts") or {}
    add("C8", verdict.get("verdict") == "TECHNICAL_INCOMPLETE"
        and verdict.get("performance_claimable") is False
        and (verdict.get("failure") or {}).get("layer") == "card-local"
        and (verdict.get("failure") or {}).get("class") == "data_window_invalid"
        and (verdict.get("yield") or {}).get("yield_decision") == "STOP_TECHNICAL_INCOMPLETE"
        and verdict.get("run_id") is None
        and att.get("launched") == 0 and att.get("run_specs") == 0
        and att.get("terminal_sentinels") == 0
        and verdict.get("evidence_run_ids") == []
        and verdict.get("family_id") == FAMILY and verdict.get("round_id") == ROUND
        and verdict.get("kanban_task_id") == TASK,
        "verdict=%s performance_claimable=%s layer=%s class=%s yield=%s run_id=%s "
        "attempts=%s evidence_run_ids=%s"
        % (verdict.get("verdict"), verdict.get("performance_claimable"),
           (verdict.get("failure") or {}).get("layer"),
           (verdict.get("failure") or {}).get("class"),
           (verdict.get("yield") or {}).get("yield_decision"), verdict.get("run_id"),
           att, verdict.get("evidence_run_ids")))

    # C9 - nothing was ever submitted: the round directory holds exactly the two immutable
    #      artifacts, no attempt directory, no terminal sentinel.
    present = sorted(os.listdir(round_dir)) if os.path.isdir(round_dir) else []
    attempts_dir = os.path.join(round_dir, "attempts")
    add("C9", present == ["round-spec.json", "verdict.json"]
        and not os.path.exists(attempts_dir)
        and pg.get("attempts_launched") == 0
        and (verdict.get("terminal_evidence") or {}).get("attempt_dir") is None
        and (verdict.get("terminal_evidence") or {}).get("terminal_sentinel") is None
        and (verdict.get("terminal_evidence") or {}).get("run_spec") is None,
        "round dir entries=%s attempts dir exists=%s attempts_launched=%s terminal_evidence="
        "attempt_dir/run_spec/sentinel=%s"
        % (present, os.path.exists(attempts_dir), pg.get("attempts_launched"),
           [(verdict.get("terminal_evidence") or {}).get(k)
            for k in ("attempt_dir", "run_spec", "terminal_sentinel")]))

    # C10 - the registered requirement was NOT shrunk to the locally available instruments,
    #       and the record's own USD-parity rule is registered as unsatisfiable.
    add("C10", ur.get("universe_shrunk_to_local_list") is False
        and ur.get("target_universe_required") == list(SOURCE_UNIVERSE)
        and ur.get("target_universe_present_local") == ["BNB", "BTC", "ETH"]
        and ur.get("target_universe_overlap_count") == 3
        and sorted(ur.get("target_universe_missing") or []) ==
        ["ADA", "DOGE", "LTC", "TRX", "XLM", "XMR", "XRP"]
        and sorted(ur.get("local_contracts_not_in_record_universe") or []) == ["SOLUSDT"]
        and (ur.get("usdt_usd_series_available") is False)
        and (ur.get("usd_parity_source_available_as_registered") is False)
        and (ur.get("crypto_cross_rate_substitution_permitted_by_record") is False),
        "universe_shrunk_to_local_list=%s required=%s present_local=%s overlap=%s missing=%s "
        "local contracts outside the record universe=%s USDT/USD series available=%s "
        "USD-parity source available as registered=%s record permits a cross-rate "
        "substitute=%s"
        % (ur.get("universe_shrunk_to_local_list"), ur.get("target_universe_required"),
           ur.get("target_universe_present_local"), ur.get("target_universe_overlap_count"),
           ur.get("target_universe_missing"),
           ur.get("local_contracts_not_in_record_universe"),
           ur.get("usdt_usd_series_available"),
           ur.get("usd_parity_source_available_as_registered"),
           ur.get("crypto_cross_rate_substitution_permitted_by_record")))

    # C11 - the DCA registration still carries the contract 7.2 v1.3.1 provenance classes
    #       and the complete 48-cell product.
    dd = spec.get("dca_domain") or {}
    statuses = [dd.get(a + "_status") for a in DCA_FOUR_AXES]
    grid = dd.get("grid") or []
    add("C11", dd.get("base_quote_status") == PROJECT_CONSTANT
        and statuses == [SEARCH_DOMAIN] * 4
        and dd.get("config_count") == 48 and len(grid) == 48
        and dd.get("base_quote") == 1000
        and _sav2 is not None
        and set(DCA_FOUR_AXES) <= set(getattr(_sav2, "USER_FIXED_FORBIDDEN_KEYS", ()))
        and dd.get("spacing_pct") == [0.01, 0.02, 0.03, 0.04]
        and dd.get("size_multiplier") == [1.0, 1.1]
        and dd.get("breakeven_tp_pct") == [0.01, 0.02, 0.03]
        and dd.get("invalidation_pct") == [0.05, 0.10],
        "searched axes statuses=%s base_quote=%s base_quote_status=%s config_count=%s grid "
        "len=%s axes=%s" % (statuses, dd.get("base_quote"), dd.get("base_quote_status"),
                            dd.get("config_count"), len(grid),
                            {a: dd.get(a) for a in DCA_FOUR_AXES}))

    # C12 - artifact-to-live-raw agreement: every raw-side value the persisted artifact
    #       claims still equals a live re-measurement (so a planted dataset cannot pass).
    claims = pg.get("measured_available") or {}
    disagreements = [k for k in CLAIMED_MEASUREMENT_KEYS
                     if k in claims and claims[k] != raw.get(k)]
    add("C12", claims and not disagreements
        and set(CLAIMED_MEASUREMENT_KEYS) <= set(claims),
        "claimed keys=%d covered=%d disagreements vs live raw=%s missing keys=%s"
        % (len(claims), len(set(CLAIMED_MEASUREMENT_KEYS) & set(claims)), disagreements,
           sorted(set(CLAIMED_MEASUREMENT_KEYS) - set(claims))))

    # C13 - every persisted `*_verbatim` leaf resolves against its declared source.
    vv = verify_verbatim(spec_path, repo_root=repo_root, card_body_path=card_body_path,
                         record_path=record_path, board_db=board_db)
    add("C13", vv["misses"] == [] and vv["problems"] == []
        and vv["unclassified_verbatim_paths"] == [] and vv["missing_from_source_map"] == []
        and vv["orphan_map_entries"] == [] and vv["duplicate_map_keys"] == []
        and vv["digests_disagreeing_with_content"] == []
        and vv["source_sha256_matches_declared"] is True,
        "%d verbatim leaves resolved (%s), misses=%d problems=%s unclassified=%d "
        "missing_from_map=%d orphan_map=%d"
        % (vv["verbatim_strings_on_disk"], vv["declared_source_counts"],
           len(vv["misses"]), vv["problems"], len(vv["unclassified_verbatim_paths"]),
           len(vv["missing_from_source_map"]), len(vv["orphan_map_entries"])))

    # C14 - coverage and survivor surface is empty by construction, the registered phase
    #       grid is intact, and every decisive requirement row still carries its registered
    #       status (no decisive item may be flipped to PRESENT).
    cov = verdict.get("coverage") or {}
    sur = verdict.get("survivors") or {}
    matrix = pg.get("required_data_matrix") or []
    row_status = {row.get("item"): row.get("status") for row in matrix}
    bad_status = {k: row_status.get(k) for k, v in DECISIVE_REQUIRED_STATUS.items()
                  if row_status.get(k) != v}
    decisive_status = {k: row_status.get(k) for k in DECISIVE_REQUIRED_STATUS}
    add("C14", cov.get("cohorts_realized") == 0 and cov.get("case_evaluations") == 0
        and cov.get("registered_phase_grids") == REGISTERED_PHASE_GRIDS
        and cov.get("coverage_complete") is False
        and sur.get("count") == 0 and sur.get("bundle") is None and sur.get("ranking") is None
        and pg.get("required_data_available") is False
        and pg.get("outcome") == "PREREQUISITE_ABSENT"
        and sorted(pg.get("decisive_items") or []) == sorted(DECISIVE_MATRIX_ITEMS)
        and decisive_status == DECISIVE_REQUIRED_STATUS
        and [row.get("item") for row in matrix] == list(MISSING_DATA_MATRIX_ITEMS),
        "cohorts=%s cases=%s phase grids=%d complete=%s survivors=%s required_data_available="
        "%s outcome=%s decisive_items=%d matrix rows=%d wrong_statuses=%s"
        % (cov.get("cohorts_realized"), cov.get("case_evaluations"),
           len(cov.get("registered_phase_grids") or []), cov.get("coverage_complete"),
           sur.get("count"), pg.get("required_data_available"), pg.get("outcome"),
           len(pg.get("decisive_items") or []), len(matrix), bad_status))

    # C15 - the host's other market-data stores are measured and disclosed as unused, and
    #       none of them carries the missing USD-parity series; the round-spec's disclosure
    #       block must name exactly the stores this re-measurement sees.
    stores = measure_other_stores()
    disclosed = (spec.get("other_local_stores") or {})
    live_existing = sorted(k for k, v in stores["stores"].items() if v["exists"])
    disclosed_existing = sorted(k for k, v in (disclosed.get("stores") or {}).items()
                                if v.get("exists"))
    add("C15", stores["any_store_carries_usd_parity_series"] is False
        and all(v.get("used_as_input") is False for v in stores["stores"].values())
        and disclosed.get("any_store_carries_usd_parity_series") is False
        and live_existing == disclosed_existing
        and all((disclosed.get("stores") or {}).get(k, {}).get("used_as_input") is False
                for k in live_existing),
        "stores scanned=%d existing=%d any carries a USD-parity series=%s all declared "
        "unused=%s round-spec disclosure names the same existing stores=%s"
        % (len(stores["stores"]), len(live_existing),
           stores["any_store_carries_usd_parity_series"],
           all(v.get("used_as_input") is False for v in stores["stores"].values()),
           live_existing == disclosed_existing))

    overall = "PASS" if all(c["status"] == "PASS" for c in checks) else "FAIL"
    return {"family_id": FAMILY, "round_id": ROUND, "kanban_task_id": TASK,
            "checks": checks, "overall": overall,
            "raw_measurement_note": raw["note"]}


def _resolve_source_texts(repo_root=None, card_body_path=None, record_path=None,
                          board_db=None, expected_card_sha=None):
    """The four declared verbatim sources, read from their live locations."""
    repo_root = repo_root or DEFAULT_REPO
    record_path = record_path or DEFAULT_RECORD
    board_db = board_db or DEFAULT_BOARD_DB
    contract_path = os.path.join(repo_root, "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md")
    texts = {}
    texts["contract"] = open(contract_path, encoding="utf-8").read()
    texts["record"] = open(record_path, encoding="utf-8").read()
    if _LIFECYCLE_FOOTER is None:
        raise RuntimeError("runtime/production_handoff.py LIFECYCLE_FOOTER not importable")
    texts["footer"] = _LIFECYCLE_FOOTER
    if card_body_path:
        texts["card"] = open(card_body_path, encoding="utf-8").read()
    else:
        if not os.path.exists(board_db):
            raise RuntimeError("board db not readable: %s (pass --card-body-file)" % board_db)
        con = sqlite3.connect("file:%s?mode=ro" % board_db, uri=True)
        try:
            row = con.execute("select body from tasks where id=?", (TASK,)).fetchone()
        finally:
            con.close()
        if not row or not row[0]:
            raise RuntimeError("card %s body not found in %s" % (TASK, board_db))
        body = row[0]
        if not body.endswith(_LIFECYCLE_FOOTER):
            raise RuntimeError("card body does not end with the system-owned lifecycle footer")
        texts["card"] = body[:-len(_LIFECYCLE_FOOTER)]
    texts["__sha256__"] = {k: _sha256_text(v) for k, v in texts.items()}
    if expected_card_sha and texts["__sha256__"]["card"] != expected_card_sha:
        raise RuntimeError("card body sha256 %s != declared %s"
                           % (texts["__sha256__"]["card"], expected_card_sha))
    return texts


def _walk_verbatim(node, path=(), flagged=False):
    """Every scalar leaf under a key (or under any ancestor key) containing 'verbatim'.

    A `*_verbatim` key may hold a single string, a list of strings (one excerpt split into
    lines), or a nested block; all of them are resolved, so the convention cannot be dodged
    by wrapping an excerpt in a list or an object. `excerpt_source_map` is the map itself,
    not content, and is never walked.
    """
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            if str(k) == "excerpt_source_map":
                continue
            out.extend(_walk_verbatim(v, path + (str(k),),
                                      flagged or "verbatim" in str(k).lower()))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(_walk_verbatim(v, path + (str(i),), flagged))
    elif flagged:
        key = next((seg for seg in reversed(path) if "verbatim" in seg.lower()),
                   path[-1] if path else "")
        out.append((".".join(path), key, node))
    return out


def verify_verbatim(spec_path, repo_root=None, card_body_path=None, record_path=None,
                    board_db=None):
    """Resolve every persisted `*_verbatim` leaf against its declared source."""
    spec = _load_json(spec_path)
    src_map = spec.get("excerpt_source_map") or {}
    expected_card_sha = ((spec.get("provenance") or {}).get("card") or {}).get(
        "frozen_candidate_body_sha256")
    texts = _resolve_source_texts(repo_root=repo_root, card_body_path=card_body_path,
                                 record_path=record_path, board_db=board_db,
                                 expected_card_sha=expected_card_sha)
    leaves = _walk_verbatim(spec)
    report = {"verbatim_strings_on_disk": len(leaves), "strings_with_declared_source": 0,
              "misses": [], "unclassified_verbatim_paths": [], "declared_source_counts": {},
              "missing_from_source_map": [], "orphan_map_entries": [], "problems": [],
              "duplicate_map_keys": [], "digests_disagreeing_with_content": [],
              "source_sha256_matches_declared": True,
              "source_sha256": {k: v for k, v in texts["__sha256__"].items()
                                if not k.startswith("__")}}
    keys = [k for _, k, _ in leaves]
    dupes = [k for k, n in Counter(keys).items() if n > 1]
    if dupes:
        report["duplicate_map_keys"] = dupes
        report["problems"].append("duplicate verbatim key names: %s" % dupes)
    for path, key, value in leaves:
        entry = src_map.get(key)
        if not entry:
            report["missing_from_source_map"].append({"path": path, "key": key})
            continue
        src = entry.get("source")
        if src not in ("card", "record", "contract", "footer"):
            report["unclassified_verbatim_paths"].append({"path": path, "key": key,
                                                          "source": src})
            continue
        report["strings_with_declared_source"] += 1
        report["declared_source_counts"][src] = report["declared_source_counts"].get(src, 0) + 1
        if value not in texts[src]:
            report["misses"].append({"path": path, "key": key, "source": src,
                                     "chars": len(value), "head": value[:70]})
        if _sha256_text(value) != entry.get("sha256"):
            report["digests_disagreeing_with_content"].append(
                {"path": path, "key": key, "declared": entry.get("sha256"),
                 "computed": _sha256_text(value)})
        if entry.get("chars") != len(value):
            report["digests_disagreeing_with_content"].append(
                {"path": path, "key": key, "declared_chars": entry.get("chars"),
                 "computed_chars": len(value)})
    used = {k for _, k, _ in leaves}
    report["orphan_map_entries"] = sorted(set(src_map) - used)
    return report


def _copy_tree(results_root, tmp):
    dst = os.path.join(tmp, FAMILY)
    os.makedirs(os.path.join(dst, "rounds"))
    shutil.copy(os.path.join(results_root, FAMILY, "family.json"),
                os.path.join(dst, "family.json"))
    shutil.copytree(os.path.join(results_root, FAMILY, "rounds", ROUND),
                    os.path.join(dst, "rounds", ROUND))
    return os.path.join(dst, "rounds", ROUND)


def self_test(results_root, raw_root, **kw):
    """Non-vacuousness control: the checker must refuse every tampered copy."""
    variants = {
        "verdict_tampered_to_PASS": lambda d: _tamper(d, lambda v: v.update(
            {"verdict": "PASS", "performance_claimable": True})),
        "layer_flipped_to_shared": lambda d: _tamper(d, lambda v: v["failure"].update(
            {"layer": "shared-layer"})),
        "failure_class_flipped_to_a_research_result": lambda d: _tamper(
            d, lambda v: v["failure"].update({"class": "research_rejected"})),
        "yield_decision_flipped_to_continue": lambda d: _tamper(
            d, lambda v: v["yield"].update({"yield_decision": "CONTINUE"})),
        "run_id_fabricated": lambda d: _tamper(d, lambda v: v.update(
            {"run_id": ROUND + "-u1"})),
        "attempt_fabricated": lambda d: _fabricate_attempt(d),
        "incomplete_sentinel_fabricated": lambda d: _fabricate_attempt(d, name="INCOMPLETE"),
        "required_data_available_flipped_true": lambda d: _tamper_spec(d, lambda s: s[
            "prerequisite_gate"].update({
                "required_data_available": True, "outcome": "PREREQUISITE_PRESENT",
                "missing": []})),
        "usdt_usd_series_matrix_item_claimed_present": lambda d: _tamper_spec(
            d, lambda s: _set_matrix_status(s, "reference_asset_usdt_usd_daily_price_series",
                                            "PRESENT")),
        "depeg_event_indicator_claimed_constructible": lambda d: _tamper_spec(
            d, lambda s: _set_matrix_status(s, "negative_depeg_event_indicator_3sigma",
                                            "PRESENT")),
        "usdt_series_claimed_available": lambda d: _tamper_spec(d, lambda s: s[
            "universe_registration"].update({
                "usdt_usd_series_available": True,
                "usd_parity_source_available_as_registered": True})),
        "universe_shrunk_to_the_local_four_contract_list": lambda d: _tamper_spec(d, lambda s: s[
            "universe_registration"].update({
                "universe_shrunk_to_local_list": True,
                "target_universe_required": ["BNB", "BTC", "ETH", "SOL"]})),
        "missing_seven_assets_claimed_present": lambda d: _tamper_spec(d, lambda s: s[
            "universe_registration"].update({
                "target_universe_missing": [],
                "target_universe_present_local": list(SOURCE_UNIVERSE)})),
        "record_sample_overlap_claimed_fully_covered": lambda d: _tamper_spec(d, lambda s: s[
            "data"].update({
                "raw_window_covers_record_sample_start": True,
                "record_sample_days_inside_raw_window": RECORD_SAMPLE_DAYS,
                "record_sample_coverage_fraction": 1.0})),
        "claimed_record_sample_overlap_falsified": lambda d: _tamper_spec(d, lambda s: s[
            "prerequisite_gate"]["measured_available"].update(
                {"record_sample_days_inside_raw_window": RECORD_SAMPLE_DAYS,
                 "record_sample_coverage_fraction": 1.0})),
        "probe_token_list_narrowed": lambda d: _tamper_spec(d, lambda s: s[
            "prerequisite_gate"]["measured_available"].update(
                {"probe_tokens_tested": ["usdt_usd", "tether"]})),
        "claimed_raw_window_edited": lambda d: _tamper_spec(d, lambda s: s[
            "prerequisite_gate"]["measured_available"].update(
                {"raw_1d_window_utc": ["2017-11-09T00:00:00+00:00",
                                       "2026-09-15T00:00:00+00:00"]})),
        "claimed_peg_token_hits_faked": lambda d: _tamper_spec(d, lambda s: s[
            "prerequisite_gate"]["measured_available"].update(
                {"stablecoin_peg_token_hits_in_entry_names": []})),
        "other_store_disclosure_stripped": lambda d: _tamper_spec(d, lambda s: s.update(
            {"other_local_stores": {"stores": {}, "any_store_carries_usd_parity_series":
                                    False}})),
        "searched_axis_mislabelled_user_fixed": lambda d: _tamper_spec(
            d, lambda s: s["dca_domain"].update({"spacing_pct_status": USER_FIXED})),
        "project_constant_relabelled_user_fixed": lambda d: _tamper_spec(
            d, lambda s: s["dca_domain"].update({"base_quote_status": USER_FIXED})),
        "dca_grid_truncated_to_47": lambda d: _tamper_spec(
            d, lambda s: s["dca_domain"]["grid"].pop()),
        "coverage_claimed_complete_with_1_cohort": lambda d: _tamper(d, lambda v: v[
            "coverage"].update({"cohorts_realized": 1, "case_evaluations": 48,
                                "coverage_complete": True})),
        "survivor_bundle_fabricated": lambda d: _tamper(d, lambda v: v["survivors"].update(
            {"count": 1, "bundle": ROUND + "-survivors"})),
        "zero_attempt_claim_replaced_by_an_attempt_dir": lambda d: _fabricate_attempt(
            d, name="DONE"),
        "verbatim_leaf_replaced_by_a_paraphrase": lambda d: _tamper_spec(
            d, lambda s: s["provenance"]["record_excerpts"].update(
                {"identity_verbatim": "# USDT depeg rebound (paraphrased)"})),
        "verbatim_digest_removed_from_the_map": lambda d: _tamper_spec(
            d, lambda s: s["excerpt_source_map"].pop("identity_verbatim", None)),
        "verbatim_source_repointed": lambda d: _tamper_spec(
            d, lambda s: s["excerpt_source_map"]["identity_verbatim"].update(
                {"source": "card"})),
        "verbatim_chars_field_falsified": lambda d: _tamper_spec(
            d, lambda s: s["excerpt_source_map"]["identity_verbatim"].update({"chars": 1})),
        "verbatim_leaf_relabelled_as_authored_prose": lambda d: _tamper_spec(
            d, lambda s: s["provenance"]["record_excerpts"].update(
                {"identity_note": s["provenance"]["record_excerpts"].pop(
                    "identity_verbatim")})),
        "verbatim_leaf_nested_in_a_list_is_still_resolved": lambda d: _tamper_spec(
            d, lambda s: s["provenance"]["record_excerpts"].update(
                {"identity_verbatim": [s["provenance"]["record_excerpts"]["identity_verbatim"],
                                       "an extra appended line that is not in the record"]})),
    }
    results = []
    ok = True
    for name, mutate in variants.items():
        tmp = tempfile.mkdtemp(prefix="xq-usdtdepeg-prereq-selftest-")
        try:
            rdir = _copy_tree(results_root, tmp)
            mutate(rdir)
            res = run_checks(tmp, raw_root, **kw)
            refused = [c["id"] for c in res["checks"] if c["status"] == "FAIL"]
            results.append({"variant": name, "refused": bool(refused),
                            "failed_checks": refused})
            ok = ok and bool(refused)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return {"self_test": results, "variants": len(variants),
            "refused": sum(1 for r in results if r["refused"]),
            "overall": "PASS" if ok else "FAIL"}


def _kw_gz(path, row):
    with gzip.GzipFile(path, "wb", mtime=0) as gz:
        gz.write(json.dumps(row).encode() + b"\n")


def raw_fixture_control(results_root, raw_root):
    """Measurement-side non-vacuousness control.

    Build a temp raw tree that carries what this record would need: a documented daily
    USDT/USD price series, a stablecoin/peg reference tree, a spot market holding a
    USDT/USD instrument (so the price source is no longer only a USDT-quoted perpetual),
    a second venue, and a daily bar older than the card's registered raw start. The
    raw-side checks must flip to FAIL.
    """
    tmp = tempfile.mkdtemp(prefix="xq-usdtdepeg-prereq-rawfix-")
    try:
        fixture = os.path.join(tmp, "raw")
        shutil.copytree(raw_root, fixture,
                        ignore=shutil.ignore_patterns(".DS_Store", "__pycache__"))
        # the record's required dataset: a documented daily USDT/USD (USD-parity) series
        d = os.path.join(fixture, "_ref", "stablecoin")
        os.makedirs(d)
        _kw_gz(os.path.join(d, "usdt-usd-daily-parity-index.jsonl.gz"),
               {"date_utc": "2017-11-11", "usdt_usd_close": "0.9987",
                "open": "0.999", "high": "1.002", "low": "0.985", "close": "0.9987",
                "volume": "15420000", "methodology": "documented composite USD-parity "
                                                       "index", "source": "composite"})
        _kw_gz(os.path.join(d, "stablecoin-supply-daily.jsonl.gz"),
               {"date_utc": "2017-11-11", "stablecoin_supply": "1234567",
                "stablecoin_price": "0.9987"})
        # a spot market holding the USD-parity instrument, and a second venue
        sp = os.path.join(fixture, "binance", "spot", "USDTUSD", "1d")
        os.makedirs(sp)
        _kw_gz(os.path.join(sp, "USDTUSD-1d-2017-11.jsonl.gz"),
               {"open_time_ms": 1510358400000, "close_time_ms": 1510444799999,
                "open": "0.999", "high": "1.002", "low": "0.985", "close": "0.9987",
                "volume": "15420000"})
        os.makedirs(os.path.join(fixture, "okx", "spot"))
        inst = os.path.join(fixture, "binance", "spot", "instruments")
        os.makedirs(inst)
        with open(os.path.join(inst, "spot-instruments.json"), "w", encoding="utf-8") as f:
            json.dump({"instruments": [{"fields": {"symbol": "USDTUSD",
                                                   "type": "CryptoSpot",
                                                   "quote_currency": "USD",
                                                   "settlement_currency": "USDT"}}]}, f)
        # a daily bar older than the card's registered raw window start
        old = os.path.join(fixture, "binance", "usdm", "klines", "BTCUSDT", "1d",
                           "BTCUSDT-1d-2017-11.jsonl.gz")
        _kw_gz(old, {"open_time_ms": 1510358400000, "close_time_ms": 1510444799999,
                     "open": "6500.0", "high": "6700.0", "low": "6400.0",
                     "close": "6610.0", "volume": "15200.0"})
        res = run_checks(results_root, fixture)
        failed = [c["id"] for c in res["checks"] if c["status"] == "FAIL"]
        # C1/C2/C4/C5/C6/C7 are pure raw-measurement checks; C12 additionally re-asserts
        # that the persisted artifact agrees with the LIVE raw measurement (name probe,
        # window, probe token list), so a fixture that plants a USD-parity series must flip
        # it too. C3 (documented row shapes) and C8-C11/C13/C14/C15 (artifact-side and
        # host-store-side) are not expected to flip: the fixture adds datasets, it does not
        # edit the store's SCHEMA.md, the stored row shapes or the immutable round
        # artifacts, and it never touches the host stores.
        expected = {"C1", "C2", "C4", "C5", "C6", "C7", "C12"}
        return {"raw_fixture_control": {
            "failed_checks": sorted(failed), "expected": sorted(expected),
            "why": "the fixture plants what this record needs (a documented daily USDT/USD "
                   "parity series with a methodology and composite source, a "
                   "stablecoin-supply/price reference tree, a spot USDT/USD market with a "
                   "spot instrument definition, an OKX subtree, and a 2017-11 daily bar); "
                   "C1/C2/C4/C5/C6/C7 re-measure the raw and C12 re-asserts "
                   "artifact-to-live-raw agreement, so all seven must flip. C3 (documented "
                   "dataset families / stored row shapes) is not expected to flip: the "
                   "fixture does not edit the store's own SCHEMA.md.",
            "detail": {c["id"]: c["detail"] for c in res["checks"]
                       if c["id"] in expected | {"C3"}},
            "overall": res["overall"]},
            "overall": "PASS" if expected <= set(failed) else "FAIL"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


DATA_EXTS = (".csv", ".parquet", ".jsonl", ".gz", ".bin", ".feather", ".h5", ".npy", ".arrow",
             ".db", ".sqlite", ".raw", ".hdf5", ".zst", ".zip", ".tar")
DOC_EXTS = (".md", ".txt", ".rst", ".mdx", ".ipynb")
CODE_EXTS = (".py", ".ts", ".tsx", ".js", ".jsx", ".sh", ".yaml", ".yml", ".json", ".toml",
             ".cfg", ".ini", ".html", ".css", ".sql", ".rs", ".go", ".c", ".h", ".cpp")
BACKUP_LIKE_DIRS = ("_archived", "archive", "backup", "backups", "old")


def _dir_data_probe(path, max_depth=3, cap=4000):
    """What a hit directory actually holds. A directory with no data file beneath it is a
    stub (a downloader/README folder), not a dataset."""
    data, other, seen, truncated = [], [], 0, False
    for dp, dn, fn in os.walk(path):
        rel = os.path.relpath(dp, path)
        if rel.count(os.sep) >= max_depth:
            dn[:] = []
            continue
        dn[:] = [d for d in dn if not d.startswith(".") and d not in SCAN_SKIP_DIRS]
        for f in fn:
            seen += 1
            if seen > cap:
                truncated = True
                break
            fl = f.lower()
            (data if fl.endswith(DATA_EXTS) else other).append(os.path.join(dp, f))
        if truncated:
            break
    return {"files_seen": seen, "data_files": data[:20], "data_file_count": len(data),
            "other_file_count": len(other), "truncated": truncated}


def _classify_house_hit(path, token=None):
    """A hit is acceptable only if it is a document, code, session/log/cache artefact, our
    own provenance artefact, an unrelated source identifier, or a data-less stub.

    Never acceptable as-is: anything that could be a data store carrying the missing peg
    series. Those land in UNCLASSIFIED_CLASS and must be disclosed.
    """
    low = path.lower()
    base = os.path.basename(low)
    if any(k in base for k in INTEGRITY_MANIFEST_MARKERS):
        return "integrity_manifest_false_positive"
    if base in OWN_ARTIFACT_BASENAMES:
        return "own_evidence_snapshot_false_positive"
    if "/rounds/" in low and FAMILY in low:
        return "own_round_artifact_false_positive"
    if HANDOFF_BODY_DIR in low:
        return "own_handoff_body_false_positive"
    if FAMILY in low and "qlib-results" in low:
        return "own_family_directory_false_positive"
    if base.endswith(CODE_EXTS) and os.path.basename(os.path.dirname(low)) == "runtime":
        return "own_checker_source_false_positive"
    if WIKI_DIR_MARKER in low and base.endswith(".md"):
        if FAMILY in low:
            return "canonical_record_document"
        return "related_wiki_document"
    if low.startswith(os.path.join(HOME, "workspace", "quant-runtime-pipeline").lower()):
        if base.endswith(DOC_EXTS):
            return "repo_documentation_false_positive"
        return "source_code_false_positive"
    if low.startswith(os.path.join(HOME, ".hermes", "sessions").lower()) \
            or low.startswith(os.path.join(HOME, ".hermes", "logs").lower()) \
            or "/transcripts/" in low or base.endswith(".log"):
        return "agent_session_or_log_false_positive"
    if low.startswith(os.path.join(HOME, ".hermes").lower()) and (
            "/cache/" in low or "/.cache/" in low or "/tmp/" in low):
        return "hermes_cache_false_positive"
    if low.startswith(os.path.join(HOME, ".hermes", "state").lower()) \
            or low.startswith(os.path.join(HOME, ".hermes", "kanban").lower()):
        return "hermes_state_artifact_false_positive"
    if low.startswith(os.path.join(HOME, ".hermes", "skills").lower()) \
            or low.startswith(os.path.join(HOME, ".hermes", "wiki", "_meta").lower()):
        return "hermes_skill_or_catalog_false_positive"
    if "contributors" in low or "author" in base:
        return "contributor_metadata_false_positive"
    if any(part in low for part in BACKUP_LIKE_DIRS):
        return "non_canonical_staging_copy_false_positive"
    # a token that only appears inside a local contract name is an unrelated source
    # identifier: it names another instrument, not a stablecoin price series
    if token and _token_only_matches_local_contract_names(path, token):
        return "unrelated_source_identifier_false_positive"
    if os.path.isdir(path):
        probe = _dir_data_probe(path)
        if probe["data_file_count"] == 0:
            return "dataset_stub_directory_false_positive"
    # A hit that is a document or a source file cannot be a market-data store, so it is
    # classified as a false positive with its own class rather than left unclassified; the
    # unclassified bucket therefore holds only hits that are neither documents, code, own
    # artefacts, caches, staging copies, nor data-less stubs.
    if base.endswith(DOC_EXTS) or base.endswith(".ipynb"):
        return "research_document_false_positive"
    if base.endswith(CODE_EXTS):
        return "source_code_false_positive"
    return UNCLASSIFIED_CLASS


_HOUSE_SCAN_CACHE = {}


def _scan_hit_map(roots, tokens):
    """path -> sorted matched tokens, for every path whose path/basename matches."""
    hits = {}
    for root in roots:
        for dp, dn, fn in os.walk(root):
            rel = os.path.relpath(dp, root)
            if rel.count(os.sep) >= 6:
                dn[:] = []
                continue
            dn[:] = [d for d in dn if not d.startswith(".") and d not in SCAN_SKIP_DIRS]
            low_dp = dp.lower()
            for t in tokens:
                if t in low_dp:
                    hits.setdefault(dp, set()).add(t)
            for f in fn:
                low_f = f.lower()
                for t in tokens:
                    if t in low_f:
                        hits.setdefault(os.path.join(dp, f), set()).add(t)
    return {k: sorted(v) for k, v in hits.items()}


def _probe_hit_pairs(roots, tokens):
    """(path, matched_tokens) pairs, so the classifier can name a substring collision
    (`tusd` inside `MMTUSDT`) instead of guessing."""
    return sorted(_scan_hit_map(roots, tokens).items())


def _probe_hits(roots, tokens):
    """Bare matched paths."""
    return sorted(_scan_hit_map(roots, tokens))


def host_scan(hits_cap=400, fresh=False):
    """House-wide search for a stablecoin/USDT-USD price series (read-only).

    Two probes are run: the registered token list, and - separately and disclosed - the
    excluded tokens, so an exclusion can never hide a hit that the registered probe would
    have found. Memoised inside the process (a `--self-test` runs the whole check once per
    tampered copy); `fresh=True` from the CLI forces a re-measurement.
    """
    if not fresh and "scan" in _HOUSE_SCAN_CACHE:
        return _HOUSE_SCAN_CACHE["scan"]
    scanned, skipped = [], []
    for r in TMP_ROOTS:
        (scanned if os.path.isdir(r) else skipped).append(r)

    records, counts = [], {}
    cls_by_token = {}
    for p, toks in _probe_hit_pairs(scanned, STABLECOIN_PEG_TOKENS):
        tok = sorted(toks)[0]
        cls = _classify_house_hit(p, token=tok)
        counts[cls] = counts.get(cls, 0) + 1
        for t in toks:
            cls_by_token.setdefault(t, {}).setdefault(cls, 0)
            cls_by_token[t][cls] += 1
        records.append({"path": p, "classification": cls, "matched_tokens": sorted(toks)})
    unclassified = [r for r in records if r["classification"] == UNCLASSIFIED_CLASS]
    listed = records[:hits_cap]

    excluded_scan = {}
    for tok in PROBE_EXCLUSIONS:
        tk = _probe_hits(scanned, [tok])
        ex_counts = {}
        for p in tk:
            cls = _classify_house_hit(p, token=tok)
            ex_counts[cls] = ex_counts.get(cls, 0) + 1
        excluded_scan[tok] = {
            "hit_count": len(tk),
            "classification_counts": ex_counts,
            "unclassified_data_candidates": [p for p in tk
                                             if _classify_house_hit(p, token=tok)
                                             == UNCLASSIFIED_CLASS],
            "contract_named_hits": sum(1 for p in tk
                                       if any(s.lower() in os.path.basename(p).lower()
                                              for s in EXPECTED_SYMBOLS)),
            "examples": tk[:5],
        }
    out = {"roots_scanned": scanned, "skipped_roots": skipped,
           "probe_tokens": list(STABLECOIN_PEG_TOKENS),
           "probe_token_count": len(STABLECOIN_PEG_TOKENS),
           "probe_exclusions": dict(PROBE_EXCLUSIONS),
           "probe_exclusion_demonstration": {
               tok: {"house_collision_example": (excluded_scan[tok]["examples"] or [None])[0],
                     "house_hit_count": excluded_scan[tok]["hit_count"],
                     "reason": PROBE_EXCLUSIONS[tok]} for tok in PROBE_EXCLUSIONS},
           "excluded_token_scan": excluded_scan,
           "hit_count": len(records),
           "classification_counts": counts,
           "classification_counts_by_token": cls_by_token,
           "hits_listed": len(listed),
           "hits_capped_by": max(0, len(records) - len(listed)),
           "hits": listed,
           "unclassified_hits": unclassified,
           "note": ("a house-wide name probe with the same %d-token list used for the raw "
                    "tree was run over %d roots; it returned %d hit(s) in %d "
                    "classification(s), %d of them unclassified, and no hit class is a "
                    "USD-parity price series. A separate disclosed pass re-ran the %d "
                    "excluded token(s) - excluded because each is a substring of a name "
                    "the host already uses (the local quote currency, the market "
                    "directory, or the store's own price metadata) - and returned %s "
                    "hit(s) in %d classification(s), %s of them unclassified, of which "
                    "%s are files named after a local contract symbol."
                    % (len(STABLECOIN_PEG_TOKENS), len(scanned), len(records), len(counts),
                       len(unclassified), len(PROBE_EXCLUSIONS),
                       sum(v["hit_count"] for v in excluded_scan.values()),
                       len({c for v in excluded_scan.values()
                            for c in v["classification_counts"]}),
                       sum(len(v["unclassified_data_candidates"])
                           for v in excluded_scan.values()),
                       sum(v["contract_named_hits"] for v in excluded_scan.values())))}
    if "scan" not in _HOUSE_SCAN_CACHE:
        _HOUSE_SCAN_CACHE["scan"] = out
    return out


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


def _set_matrix_status(spec, item, status):
    for row in spec["prerequisite_gate"]["required_data_matrix"]:
        if row.get("item") == item:
            row["status"] = status


def _fabricate_attempt(round_dir, name="DONE"):
    adir = os.path.join(round_dir, "attempts", ROUND + "-u1")
    os.makedirs(adir, exist_ok=True)
    with open(os.path.join(adir, name), "w", encoding="utf-8") as f:
        f.write("{}\n")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="prerequisite-gate read-back checker (%s)" % FAMILY)
    ap.add_argument("--results-root", default=DEFAULT_RESULTS)
    ap.add_argument("--raw-root", default=DEFAULT_RAW)
    ap.add_argument("--repo-root", default=DEFAULT_REPO)
    ap.add_argument("--card-body-file", default=None)
    ap.add_argument("--record-path", default=DEFAULT_RECORD)
    ap.add_argument("--board-db", default=DEFAULT_BOARD_DB)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--measure-only", action="store_true")
    ap.add_argument("--other-stores", action="store_true")
    ap.add_argument("--verify-verbatim", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--raw-fixture-control", action="store_true")
    ap.add_argument("--host-scan", action="store_true")
    args = ap.parse_args(argv)

    kw = dict(repo_root=args.repo_root, card_body_path=args.card_body_file,
              record_path=args.record_path, board_db=args.board_db)

    if args.host_scan:
        print(json.dumps(host_scan(fresh=True), indent=2, ensure_ascii=False))
        return 0

    if args.other_stores:
        print(json.dumps(measure_other_stores(), indent=2, ensure_ascii=False))
        return 0

    if not os.path.isdir(args.results_root) or not os.path.isdir(args.raw_root):
        print("usage error: results/raw root not mounted", file=sys.stderr)
        return 2

    if args.measure_only:
        print(json.dumps(measure_raw(args.raw_root), indent=2, ensure_ascii=False))
        return 0

    if args.verify_verbatim:
        spec_path = os.path.join(args.results_root, FAMILY, "rounds", ROUND, "round-spec.json")
        out = verify_verbatim(spec_path, **kw)
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0 if (out["misses"] == [] and out["problems"] == []
                     and out["unclassified_verbatim_paths"] == []
                     and out["missing_from_source_map"] == []
                     and out["orphan_map_entries"] == []) else 1

    if args.self_test:
        out = self_test(args.results_root, args.raw_root, **kw)
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0 if out["overall"] == "PASS" else 1

    if args.raw_fixture_control:
        out = raw_fixture_control(args.results_root, args.raw_root)
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0 if out["overall"] == "PASS" else 1

    out = run_checks(args.results_root, args.raw_root, **kw)
    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        for c in out["checks"]:
            print("%-4s %-4s %s" % (c["id"], c["status"], c["detail"]))
        print("overall:", out["overall"])
    return 0 if out["overall"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
