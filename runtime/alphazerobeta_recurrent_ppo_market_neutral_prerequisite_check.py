#!/usr/bin/env python3
"""Prerequisite-gate read-back checker for the family

    alphazerobeta-recurrent-ppo-market-neutral-portfolio-2026-09-02

Card t_348d25aa terminalises this family as TECHNICAL_INCOMPLETE because the
record's required data is not in the canonical raw:

  * the record's object is a **market-neutral equity portfolio** produced by a
    recurrent-PPO agent. Its own Required-data list asks for **7 major equity
    index universes** (^GSPC, ^NDX, ^DJI, ^FTSE, ^GDAXI, ^HSI, 000001.SS) with
    their constituent cross-sections, **daily close prices adjusted for splits
    and dividends over calendar years 2004-2024 (20 years)**, the Bloomberg
    Terminal / Financial Modeling Prep fields they were built from (free-float
    shares, ADV60 dollar volume, quarterly balance sheets / income statements /
    cash flows, analyst consensus EPS, earnings-surprise revisions, insider
    transactions, options implied-volatility surfaces, structured news
    sentiment, and a macro block of rates / CPI / GDP / industrial production /
    unemployment / commodity prices / FX), **time-varying historical index
    membership** to avoid survivorship bias, exchange-specific local trading
    calendars, and a per-side cost schedule whose tiers are selected by trailing
    ADV60 dollar volume plus equity borrow fees for the short leg;
  * the canonical raw holds **one venue (BINANCE) of USD-M perpetuals and four
    fixed contracts** (BTCUSDT/ETHUSDT/BNBUSDT/SOLUSDT), OHLCV-only klines on
    seven intervals, funding and instrument metadata - all of it denominated in
    a 2022-01-01 -> 2026-09-16 window. There is no equity instrument, no index
    constituent list, no fundamental / sector / earnings / sentiment / macro
    series, no adjusted-price treatment, no borrow fee, no trading calendar and
    no second venue;
  * the record's own Crypto-portability paragraph (`adapted` / `unproven`) points
    at "top 50-100 altcoins on Binance or Bybit against BTC/ETH". Taken at face
    value the local raw still fails it: four contracts (two of which - BTCUSDT
    and ETHUSDT - are the paragraph's *benchmark* legs, leaving two altcoin
    legs), one venue, no survivorship-safe membership, and none of the feature
    groups the state representation names beyond price/volume.

This script exists so an independent reader can re-derive that determination
from the live filesystem instead of trusting prose:

  * C1-C4 re-measure the canonical raw: the store identity (venue / market type
    / symbols / intervals as the store itself documents them), the instrument
    surface (ids, types, field names, fee/tick metadata, absence of
    listing/market-cap/borrow fields), the kline surface (symbol dirs, interval
    set, dataset count, row shape, 1d window and UTC grid) and the funding
    surface;
  * C5-C6 probe the whole tree at full depth by entry name and by payload
    content for the tokens the record's requirements would have to carry
    (equity/index identity words, the seven index tickers, vendor names,
    corporate-action and adjustment words, fundamentals, GICS sector, earnings
    and analyst words, options/implied-volatility, news/sentiment, the macro
    block, trading-calendar words, membership/survivorship words, borrow words
    and the crypto-port venue words), and assert every hit is classified;
  * C7-C12 assert the record's decision surfaces item by item: no equity market
    or index universe, no 2004-2024 adjusted daily window (no 2004-2010 warm-up
    either), no point-in-time membership / delisting history, no
    fundamental / sector / earnings / sentiment / macro feature group, no ADV60
    dollar-volume / free-float / borrow / calendar inputs, and a crypto
    cross-section of four contracts against the portability paragraph's 50-100;
  * C13 asserts the published round did NOT shrink the registered universe to
    the locally available instruments;
  * C14-C18 re-read the round's immutable artifacts and assert they state
    exactly the contract-mandated terminal values (verdict, layer, class, yield
    decision, zero attempts, null run_id), that nothing was ever submitted (no
    run-spec, no attempt directory, no terminal sentinel), that the DCA
    registration still carries the contract 7.2 v1.3.1 provenance classes plus
    the complete 48-cell product, and that the coverage / survivor surface is
    empty by construction;
  * C19-C20 re-measure the host's non-canonical stores: the machine's own PIT
    evidence stores declare **zero** supported daily membership cells and zero
    point-in-time-supported assets (their own gates class them DATA_BLOCKED /
    BOUNDED), the widest host price panel is a 12-symbol *derived* daily panel
    whose own provenance document says the list is not a point-in-time universe,
    and no measured store carries an equity price, fundamental, sector, macro or
    borrow series;
  * C21-C23 assert the round is bound to this card and family, that the failure
    taxonomy was kept separated (infrastructure, not science), and that the
    falsification battery was neither lowered nor trimmed;
  * `--verify-verbatim` resolves every `*_verbatim` leaf of the persisted
    round-spec against its declared source (card / record / contract / footer)
    and re-checks the excerpt digest map, independent of the authoring run.

Read-only: it never writes inside the results tree or the raw tree. `--self-test`
builds tampered copies in fresh temp directories and asserts the checker refuses
them (non-vacuousness control). `--raw-fixture-control` builds a temp raw tree
carrying what this record would need (an equity index universe with constituent
membership and adjusted daily closes covering 2004-2024, fundamentals, GICS
sectors, earnings surprises, options IV, news sentiment, a macro block, a borrow
fee table, a trading calendar and a second venue) and asserts the raw-side checks
flip to FAIL. `--host-scan` re-runs the house-wide search for such surfaces.
Every temp tree is removed afterwards.

Usage:
    python3 runtime/alphazerobeta_recurrent_ppo_market_neutral_prerequisite_check.py [--json]
    python3 runtime/alphazerobeta_recurrent_ppo_market_neutral_prerequisite_check.py --measure-only
    python3 runtime/alphazerobeta_recurrent_ppo_market_neutral_prerequisite_check.py --verify-verbatim
    python3 runtime/alphazerobeta_recurrent_ppo_market_neutral_prerequisite_check.py --self-test
    python3 runtime/alphazerobeta_recurrent_ppo_market_neutral_prerequisite_check.py --raw-fixture-control
    python3 runtime/alphazerobeta_recurrent_ppo_market_neutral_prerequisite_check.py --host-scan

Exit codes: 0 = all checks PASS, 1 = at least one FAIL, 2 = usage error.
"""
import argparse
import csv
import gzip
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import date, datetime, timezone

FAMILY = "alphazerobeta-recurrent-ppo-market-neutral-portfolio-2026-09-02"
ROUND = FAMILY + "-r1"
TASK = "t_348d25aa"
BOARD = "quant-strategy-research"
DEFAULT_RESULTS = "/Volumes/ExpansionDrive/qlib-results"
DEFAULT_RAW = "/Volumes/ExpansionDrive/market-data-raw"
DEFAULT_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_RECORD = os.path.join(os.path.expanduser("~"), ".hermes/wiki/quant", FAMILY + ".md")
PROBE_MAX_DEPTH = 8

# ---- the record's own registered requirements, as constants so the measurement
# is a comparison against a registered value rather than a free-form claim.
REQUIRED_DAILY_WINDOW = ("2004-01-01", "2024-12-31")   # 20 years, split/dividend adjusted
REQUIRED_WARMUP_WINDOW = ("2004-01-01", "2010-12-31")  # winsorization pre-fit segment
REQUIRED_INDEX_TICKERS = ["^GSPC", "^NDX", "^DJI", "^FTSE", "^GDAXI", "^HSI", "000001.SS"]
REQUIRED_ALTCOIN_LEGS_MIN = 50    # record / Crypto portability: "top 50-100 altcoins"
REQUIRED_ALTCOIN_LEGS_MAX = 100
REQUIRED_VENUES = ["Binance", "Bybit"]   # portability paragraph: "on Binance or Bybit"
BTC_ETH_BENCHMARK_LEGS = {"BTCUSDT", "ETHUSDT"}

# Non-price state-feature groups the record's Signal section names. Each must be
# measured, not assumed.
REQUIRED_FEATURE_GROUPS = [
    ("multi_horizon_price_features", "5/20/60-day rolling return means and standard deviations"),
    ("volume_features", "5/20/60-day volume means and standard deviations"),
    ("technical_indicators", "EMA-12, EMA-26, MACD diff, RSI-14, Bollinger Bands-20"),
    ("weekly_monthly_streams", "weekly and monthly resampled streams aligned to the daily close"),
    ("gics_sector_vectors", "one-hot encoded GICS sector vectors"),
    ("lagged_fundamentals", "60-day lagged balance sheet / income statement ratios"),
    ("earnings_surprise_revisions", "point-in-time earnings surprise revisions"),
    ("macro_releases", "interest rates, CPI, GDP growth"),
    ("investor_sentiment", "investor sentiment indicators"),
]
PRICE_DERIVABLE_FEATURE_GROUPS = ("multi_horizon_price_features", "volume_features",
                                  "technical_indicators", "weekly_monthly_streams")
ABSENT_EXPECTED_FEATURE_GROUPS = ("gics_sector_vectors", "lagged_fundamentals",
                                  "earnings_surprise_revisions", "macro_releases",
                                  "investor_sentiment")

# Execution-side inputs the record's cost model needs (ADV60 tiers, borrow fees,
# exchange calendars) plus the adjusted-price treatment.
REQUIRED_EXECUTION_INPUTS = [
    ("adv60_dollar_volume", "trailing 60-day average dollar volume for cost-tier selection"),
    ("free_float_shares", "free-float shares (Price/Volume field list)"),
    ("borrow_fees", "annualized equity borrow fees for the short leg"),
    ("trading_calendars", "exchange-specific local trading calendars (no cross-market forward-filling)"),
    ("adjusted_prices", "prices adjusted for splits and dividend distributions"),
]

# Tokens a market-data store carrying *this* record's requirements would have to
# carry. Ticker and vocabulary tokens are matched with word boundaries
# everywhere. The list is deliberately broad and includes generic words (index,
# option, gold, news, sector, ...) that also occur in ordinary prose: a curated
# vocabulary that skipped the decisive words would prove nothing, so collisions
# are measured and classified instead.
PROBE_PATTERNS = {
    # ---- equity / index identity
    "equity": r"\bequit(y|ies)\b",
    "stock": r"\bstocks?\b",
    "ticker": r"\btickers?\b",
    "index": r"\bindex(es)?\b",
    "gspc": r"\bgspc\b",
    "ndx": r"\bndx\b",
    "dji": r"\bdji\b",
    "ftse": r"\bftse\b",
    "gdaxi": r"\bgdaxi\b",
    "dax": r"\bdax\b",
    "hsi": r"\bhsi\b",
    "hang_seng": r"\bhang[-_ ]seng\b",
    "sse": r"\bsse\b",
    "a_share": r"\ba[-_ ]shares?\b",
    "sp500": r"\b(s&p[-_ ]?500|sp500|spx)\b",
    "composite": r"\bcomposite\b",
    # ---- vendors
    "bloomberg": r"\bbloomberg\b",
    "fmp": r"\b(fmp|financial[-_ ]modeling[-_ ]prep)\b",
    "alt_vendor": r"\b(yfinance|yahoo[-_ ]finance|stooq|refinitiv|eodhd|polygon)\b",
    # ---- corporate actions / size fields
    "split": r"\bsplits?\b",
    "dividend": r"\bdividends?\b",
    "free_float": r"\bfree[-_ ]float\b",
    "shares_outstanding": r"\bshares?[-_ ](outstanding|issued)\b",
    "adjusted": r"\badjust(ed|ment|ments)\b",
    # ---- fundamentals / sector / earnings
    "fundamental": r"\bfundamentals?\b",
    "balance_sheet": r"\bbalance[-_ ]sheets?\b",
    "income_statement": r"\bincome[-_ ]statements?\b",
    "cash_flow": r"\bcash[-_ ]flows?\b",
    "eps": r"\beps\b",
    "earnings": r"\bearnings?\b",
    "analyst": r"\banalysts?\b",
    "consensus": r"\bconsensus\b",
    "insider": r"\binsiders?\b",
    "gics": r"\bgics\b",
    "sector": r"\bsectors?\b",
    "industry": r"\bindustr(y|ies)\b",
    # ---- options
    "implied_vol": r"\bimplied[-_ ]vol(atility)?\b",
    "option": r"\boptions?\b",
    # ---- news / sentiment
    "news": r"\bnews\b",
    "sentiment": r"\bsentiment\b",
    # ---- macro block
    "macro": r"\bmacro(economic)?\b",
    "cpi": r"\bcpi\b",
    "gdp": r"\bgdp\b",
    "fed_funds": r"\bfed(eral)?[-_ ]funds?\b",
    "unemployment": r"\bunemploy(ment|ed)\b",
    "industrial_production": r"\bindustrial[-_ ]production\b",
    "sovereign": r"\bsovereign\b",
    "bond_yield": r"\b(bond[-_ ]yields?|treasur(y|ies))\b",
    "wti": r"\b(wti|crude)\b",
    "gold": r"\bgold\b",
    "fx_rates": r"\b(fx[-_ ]?(rates?|pairs?)|foreign[-_ ]exchange)\b",
    "commodity": r"\bcommodit(y|ies)\b",
    "interest_rate": r"\binterest[-_ ]rates?\b",
    # ---- calendars
    "trading_calendar": r"\btrading[-_ ](calendar|day)s?\b",
    "holiday": r"\bholidays?\b",
    # ---- membership / survivorship
    "universe": r"\buniverse\b",
    "membership": r"\bmembership\b",
    "survivorship": r"\bsurvivorship\b",
    "point_in_time": r"\bpoint[-_ ]in[-_ ]time\b",
    "pit": r"\bpit\b",
    "listing": r"\blistings?\b",
    "delist": r"\bdelist(ing|ed|s)?\b",
    "constituent": r"\bconstituents?\b",
    # ---- borrow / short leg
    "borrow": r"\bborrow\b",
    "short_interest": r"\bshort[-_ ]interest\b",
    # ---- crypto-portability cross-section / venues
    "altcoin": r"\baltcoins?\b",
    "bybit": r"\bbybit\b",
    "okx": r"\bokx\b",
    "coinbase": r"\bcoinbase\b",
    "kraken": r"\bkraken\b",
    # ---- cost surface
    "spread": r"\bspread\b",
    "adv60": r"\badv[-_ ]?60\b",
    "dollar_volume": r"\bdollar[-_ ]volume\b",
}
COMPILED = {k: re.compile(v, re.I) for k, v in PROBE_PATTERNS.items()}

# Token groups used by the checks.
EQUITY_TOKEN_GROUPS = {
    "equity_identity": ("equity", "stock", "ticker", "index"),
    "index_tickers": ("gspc", "ndx", "dji", "ftse", "gdaxi", "dax", "hsi", "hang_seng", "sse",
                      "a_share", "sp500", "composite"),
    "vendors": ("bloomberg", "fmp", "alt_vendor"),
    "corporate_action_or_size": ("split", "dividend", "free_float", "shares_outstanding", "adjusted"),
    "fundamentals_sector_earnings": ("fundamental", "balance_sheet", "income_statement", "cash_flow",
                                    "eps", "earnings", "analyst", "consensus", "insider", "gics",
                                    "sector", "industry"),
    "options_or_sentiment": ("implied_vol", "option", "news", "sentiment"),
    "macro": ("macro", "cpi", "gdp", "fed_funds", "unemployment", "industrial_production",
              "sovereign", "bond_yield", "wti", "gold", "fx_rates", "commodity", "interest_rate"),
    "calendars": ("trading_calendar", "holiday"),
}
MEMBERSHIP_TOKENS = ("universe", "membership", "survivorship", "point_in_time", "pit",
                     "listing", "delist", "constituent")
BORROW_TOKENS = ("borrow", "short_interest")
PORT_VENUE_TOKENS = ("bybit", "okx", "coinbase", "kraken")
COST_TOKENS = ("spread", "adv60", "dollar_volume")
CORE_MISSING_TOKENS = (tuple(EQUITY_TOKEN_GROUPS["equity_identity"])
                       + tuple(EQUITY_TOKEN_GROUPS["index_tickers"])
                       + tuple(EQUITY_TOKEN_GROUPS["vendors"])
                       + tuple(EQUITY_TOKEN_GROUPS["corporate_action_or_size"])
                       + tuple(EQUITY_TOKEN_GROUPS["fundamentals_sector_earnings"])
                       + tuple(EQUITY_TOKEN_GROUPS["options_or_sentiment"])
                       + tuple(EQUITY_TOKEN_GROUPS["macro"])
                       + tuple(EQUITY_TOKEN_GROUPS["calendars"])
                       + MEMBERSHIP_TOKENS + BORROW_TOKENS + PORT_VENUE_TOKENS + ("altcoin",))

# Classified probe hits: token -> {relative path (or path suffix): reason}. A hit
# on any other path is unexplained and fails C5/C6.
RAW_NAME_HIT_CLASSES = {}

# Measured payload hits in the canonical raw tree: four prose/source files. Each
# reason states where the token actually occurs and why it is not a series.
RAW_PAYLOAD_HIT_CLASSES = {
    "equity": {
        "_meta/INVENTORY.md": "the store's own migration inventory: the 'not migrated' table names the "
                              "retired LEAN sample market datasets "
                              "`lean-system/workspace/data/{equity,future,option,forex,cfd,...}` (253 MB) and "
                              "itself classes them 'Non-canonical markets and third venues; out of scope for "
                              "this migration' - a record of what was left behind, not a series in the store",
    },
    "a_share": {
        "_meta/INVENTORY.md": "same 'not migrated' table: the `microsoft-quant-stack/data/qlib/cn_data` row "
                              "(Qlib CN A-share prepared features/calendars, *.day.bin, 505 MB), flagged "
                              "for a human decision and measured absent from the live filesystem",
    },
    "option": {
        "_meta/INVENTORY.md": "same 'not migrated' table row naming the LEAN sample markets "
                              "(`...option,indexoption,futureoption...`)",
        "_tools/README.md": "the updater's own CLI usage heading ('Options:')",
    },
    "membership": {
        "_meta/INVENTORY.md": "the same table's `a1-1-phase9-pit-membership/raw/**` row: 'S3 XML listings "
                              "(not data), old data.binance.vision probes'",
    },
    "pit": {
        "_meta/INVENTORY.md": "the same row's path token inside 'a1-1-phase9-pit-membership'",
    },
    "listing": {
        "_meta/INVENTORY.md": "the same row: 'S3 XML listings (not data)' - S3 bucket listings, not "
                              "instrument listing dates",
    },
    "borrow": {
        "_meta/INVENTORY.md": "the same table's `.../cryptofuture/binance/margin_interest/*.csv` row: "
                              "Binance margin/borrow interest for *crypto* pairs, 8-hourly "
                              "`timestamp,rate`, which the inventory records as having no home in the new "
                              "root - it is not an equity borrow-fee table",
    },
    "bybit": {
        "_meta/INVENTORY.md": "the same LEAN sample row naming third-party venues "
                              "('...plus bitfinex/coinbase/bybit/dydx crypto')",
    },
    "coinbase": {
        "_meta/INVENTORY.md": "same row, same venue list",
    },
    "split": {
        "_meta/SCHEMA.md": "the known-coverage-limit sentence: 'quote_volume, trade count and taker-buy "
                           "splits are **not** present' - a bar-field limitation, not a stock split",
        "_tools/market_data_sync.py": "Python string splits in the updater's own CLI parsing "
                                      "(`args.datasets.split(\",\")`, `item[\"stream\"].split(\"/\")`)",
    },
    "index": {
        "_tools/binance_public.py": "the vendored Binance client's dataset map entry "
                                    "`\"index\": \"/fapi/v1/indexPriceKlines\"` and its "
                                    "`params[\"pair\" if dataset == \"index\" else \"symbol\"]` branch - an "
                                    "endpoint name in the client source, not an index-constituent series "
                                    "(the store holds no index dataset)",
    },
}

# Host data-extension hits: every hit is an artifact of a *measured* research /
# PIT store, and each class states why it still does not satisfy the record's
# requirement. A hit matching no class fails C20.
HOST_DATA_HIT_CLASSES = [
    {"suffix": "a1-1-phase9-pit-membership-20260830/membership_cells.jsonl",
     "class": "pit_membership_cells_declared_unsupported",
     "why": "the machine's own PIT-membership attempt: 14,760 daily cells for 12 declared instruments, "
            "every one `membership_status=unknown`, `supported=false`, with the reason recorded per row "
            "('archived/current snapshots cannot be backfilled'); its gate_handoff.json classifies the "
            "phase DATA_BLOCKED with pit_supported_asset_count=0"},
    {"suffix": "a1-1-phase9-pit-membership-20260830/membership_observations.csv",
     "class": "pit_membership_observations_declared_unsupported",
     "why": "59 evidence rows behind the same attempt (present-state exchangeInfo snapshots + official "
            "boundary notices); every row carries `supported_for_daily_cell=False`, and the file's own "
            "provenance states present-state snapshots cannot backfill 2022-2025 membership"},
    {"suffix": "a1-usdm-pit-lifecycle-20260830/lifecycle_events.csv",
     "class": "binance_lifecycle_event_points_declared_bounded",
     "why": "772 official USD-M lifecycle notices (listing 736 / delisting 22 / contract_conversion 11 / "
            "contract_migration 2 / suspension 1). `listing`/`delist` are its event_type values; `index` "
            "and `constituent` occur only inside notice body text about Binance's own composite-index "
            "perpetuals, and `composite` only inside the CMS `source_url` path. Its gate classifies the "
            "evidence BOUNDED with pit_supported_asset_count=0, and no prices exist for those symbols"},
    {"suffix": "a1-2-prospective-pit-foundation-20260830/snapshot_observations.jsonl",
     "class": "present_state_exchangeinfo_snapshot",
     "why": "36 present-state /fapi/v1/exchangeInfo capture observations; the store's own contract marks "
            "current-state snapshots cross-check only, never historical backfill"},
    {"suffix": "a1-3-prospective-pit-cohort-20260830/snapshot_observations.jsonl",
     "class": "present_state_exchangeinfo_snapshot",
     "why": "72 cohort-level present-state snapshot observations; explicit no-backfill policy"},
    {"suffix": "phase10-pit-bitemporal/data/pit_events.sqlite",
     "class": "phase10_pit_contract_demonstration_spot_example",
     "why": "the Phase 10 bitemporal/PIT contract demonstration: 12 records, 19 lifecycle events, 6 "
            "snapshots, 16 raw captures for *spot* symbols (USDS, INS, MCO, POE, ...), with snapshot "
            "counts 'eligible 0-1 / unknown 12' - a capability example, not a USD-M point-in-time "
            "universe panel and not price data"},
    {"suffix": "phase10-pit-bitemporal/data/real_lifecycle_evidence.csv",
     "class": "binance_spot_delisting_sample_prose_scale",
     "why": "8 hand-built Binance *spot* delisting/listing notice records for the Phase 10 research "
            "contract; no price panel and no continuous membership interval"},
    {"suffix": "alpha-strategy-research/coverage_manifest.csv",
     "class": "research_corpus_index_equity_titles_only",
     "why": "a 5,808-row index of strategy-candidate markdown paths and dispositions; its equity / stock "
            "/ index / gold / commodity / option / sentiment / macro / spread / adjusted tokens all come "
            "from candidate *titles* (e.g. sp500-*, equity-*, china-ashare-*), not from any market series. "
            "It is the one measured artifact excluded by name from the 'carries a series' test - the host "
            "holds many equity *research records*, and no equity *data*"},
]

# The only classes a host data hit may be placed in. Every one of them names a
# measured store that is NOT an equity / fundamental / macro / borrow market-data
# surface for this record.
HOST_CLASS_ALLOWLIST = {
    "pit_membership_cells_declared_unsupported",
    "pit_membership_observations_declared_unsupported",
    "binance_lifecycle_event_points_declared_bounded",
    "present_state_exchangeinfo_snapshot",
    "phase10_pit_contract_demonstration_spot_example",
    "binance_spot_delisting_sample_prose_scale",
    "research_corpus_index_equity_titles_only",
}

# Text-corpus classes whose token hits come from candidate titles rather than
# from a series; excluded by name (never silently) from the "carries a series"
# flags, and named as an exception in the check detail.
CORPUS_ONLY_CLASSES = {"research_corpus_index_equity_titles_only"}

# Sentence named by the store's own migration inventory (INVENTORY.md section B):
# the non-crypto stores it lists as deliberately NOT migrated. Each path is
# measured on the live filesystem, because prose about a store that no longer
# exists is not evidence that equity data is available.
NAMED_NONCANONICAL_STORES = [
    {"path": "/Volumes/ExpansionDrive/lean-system",
     "kinds": ["LEAN sample/market datasets (equity, option, indexoption, ...)", "LEAN crypto bars"],
     "inventory_note": "section B: 'Non-canonical markets and third venues; out of scope for this migration'"},
    {"path": "/Users/hong/lean-system", "kinds": ["LEAN sample/market datasets"],
     "inventory_note": "section B, alternate home of the same retired stack"},
    {"path": "/Users/hong/workspace/lean-system", "kinds": ["LEAN sample/market datasets"],
     "inventory_note": "section B, alternate home of the same retired stack"},
    {"path": "/Users/hong/workspace/microsoft-quant-stack", "kinds": ["Qlib CN A-share prepared features (cn_data)"],
     "inventory_note": "section B: 'Non-crypto, engine-prepared form; Qlib is being retired, so this is "
                       "flagged for a human decision rather than silently dropped'"},
    {"path": "/Volumes/ExpansionDrive/microsoft-quant-stack", "kinds": ["Qlib CN A-share cn_data"],
     "inventory_note": "section B, alternate home of the same retired stack"},
    {"path": "/Users/hong/microsoft-quant-stack", "kinds": ["Qlib CN A-share cn_data"],
     "inventory_note": "section B, alternate home of the same retired stack"},
    {"path": "/Volumes/ExpansionDrive/nautilus-system", "kinds": ["retired canonical crypto bar/funding store"],
     "inventory_note": "section A/C: the migrated crypto source whose hashes remain the audit trail"},
]

TEXT_EXTS = (".csv", ".jsonl", ".gz", ".json", ".txt", ".md", ".tsv", ".raw",
             ".py", ".sh", ".yaml", ".yml")
DATA_EXTS = (".csv", ".jsonl", ".gz", ".parquet", ".bin", ".feather", ".h5",
             ".npy", ".arrow", ".db", ".sqlite", ".zst")

HOST_ROOTS = [
    "/Users/hong/workspace/a1-1-phase9-pit-membership-20260830",
    "/Users/hong/workspace/a1-usdm-pit-lifecycle-20260830",
    "/Users/hong/workspace/a1-2-prospective-pit-foundation-20260830",
    "/Users/hong/workspace/a1-3-prospective-pit-cohort-20260830",
    "/Users/hong/workspace/phase9-cross-sectional-factors",
    "/Users/hong/workspace/phase7-alpha-research",
    "/Users/hong/workspace/phase3-portfolio-risk",
    "/Users/hong/workspace/phase4-market-microstructure",
    "/Users/hong/workspace/phase5-crypto-derivatives",
    "/Users/hong/workspace/phase10-pit-bitemporal",
    "/Users/hong/workspace/phase12-l2-l3-execution-tca",
    "/Users/hong/workspace/alpha-strategy-research",
    "/Users/hong/workspace/btc-relative-entry-score",
    "/Users/hong/workspace/quant-runtime-pipeline",
    "/Users/hong/workspace/t_4b5afaa9-evidence",
    "/Volumes/ExpansionDrive/daily-crypto-brief",
    os.path.join(DEFAULT_RESULTS, "_handoff/bodies"),
    os.path.join(os.path.expanduser("~"), ".hermes/wiki/quant"),
]

# Host-side PIT evidence stores: their own gates, re-read rather than summarised.
PIT_GATE_SOURCES = {
    "membership_gate": "/Users/hong/workspace/a1-1-phase9-pit-membership-20260830/gate_handoff.json",
    "membership_cells": "/Users/hong/workspace/a1-1-phase9-pit-membership-20260830/membership_cells.jsonl",
    "lifecycle_gate": "/Users/hong/workspace/a1-usdm-pit-lifecycle-20260830/pit_handoff.json",
    "lifecycle_events": "/Users/hong/workspace/a1-usdm-pit-lifecycle-20260830/lifecycle_events.csv",
    "phase7_panel": "/Users/hong/workspace/phase7-alpha-research/data/derived/real_daily.csv",
    "phase7_provenance": "/Users/hong/workspace/phase7-alpha-research/data_provenance.md",
}
PHASE7_NON_PIT_STATEMENT = "不等於 point-in-time universe"


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


def _read_text(path, chunk=None):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", errors="replace") as fh:
            return fh.read() if chunk is None else fh.read(chunk)
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read() if chunk is None else fh.read(chunk)


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm(text):
    """Normalise separator style before probe matching: a snake_case or kebab-case
    column name is still the series it names (`free_float_shares` must match
    `free_float`). Applied to entry names, payload text and field names alike."""
    return re.sub(r"[_\-]", " ", text)


def _iter_files(root, max_depth=PROBE_MAX_DEPTH, cap=60000):
    """Walk a host root for the store-wide scan. Depth and per-root file cap are
    disclosed in the evidence."""
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


def _classify_hits(hit_map, classes, unexplained_out, classified_out, mode):
    """Every probe hit must resolve to a declared class; otherwise it is unexplained."""
    for tok, lst in hit_map.items():
        allowed = classes.get(tok, {})
        for rel in lst:
            is_series = mode == "data"
            hit = {"token": tok, "entry": rel, "series_shaped": is_series}
            reason = allowed.get(rel)
            if reason is None:
                for k, v in allowed.items():
                    if rel.endswith(k):
                        reason = v
                        break
            if reason is None:
                unexplained_out.append(hit)
            else:
                classified_out.append({**hit, "why": reason})
    return None


def _first_row_keys(path):
    """Column names / JSON keys of a data file's first record. Used to decide
    'carries a series' by shape rather than by prose."""
    try:
        if path.endswith(".csv"):
            txt = _read_text(path, 4096)
            line = txt.splitlines()[0] if txt.splitlines() else ""
            return [c.strip() for c in line.split(",") if c.strip()]
        if path.endswith(".jsonl") or path.endswith(".jsonl.gz"):
            txt = _read_text(path, 4096)
            line = txt.splitlines()[0] if txt.splitlines() else ""
            return sorted(json.loads(line).keys())
        if path.endswith(".json"):
            txt = _read_text(path, 4096)
            return sorted({m.group(1) for m in re.finditer(r"\"([A-Za-z0-9_\- ]+)\"\s*:", txt)})
    except Exception:  # noqa: BLE001
        return []
    return []


def _token_field_scan(field_names):
    """Tokens that appear as an actual column/field name (series-shaped)."""
    return sorted({k for k, rx in COMPILED.items()
                   if any(rx.search(_norm(f)) for f in field_names)})


def _group_present(token_hits, group):
    return sorted(t for t in group if token_hits.get(t))


def _days_between(a, b):
    return (date.fromisoformat(b) - date.fromisoformat(a)).days + 1


def measure_raw(raw, fresh=False):
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
    rep["venue_dirs"] = [d for d in rep["store_top_level"] if d not in ("_meta", "_tools")]
    rep["market_dirs"] = ["%s/%s" % (v, m) for v in rep["venue_dirs"]
                          for m in _ls(os.path.join(raw, v))]
    rep["venue_count"] = len(rep["venue_dirs"])
    rep["second_venue_present"] = rep["venue_count"] > 1
    rep["spot_market_present"] = any(m == "spot" for m in rep["binance_subdirs"])
    schema = _read_text(os.path.join(meta, "SCHEMA.md"))
    rep["schema_bytes"] = len(schema)
    rep["schema_dataset_sections"] = re.findall(r"^## Dataset: (.+)$", schema, re.M)
    rep["schema_documents_equity_or_index_dataset"] = bool(
        re.search(r"(equit|stock|index|constituent|fundamental|sector|earnings|macro|borrow)",
                  " ".join(rep["schema_dataset_sections"]), re.I))
    rep["schema_documents_universe_or_mcap_dataset"] = any(
        re.search(r"(universe|membership|market[-_ ]?cap|liquidity|turnover|listing|delist|survivorship)",
                  s, re.I) for s in rep["schema_dataset_sections"])
    rep["schema_quote_volume_documented_absent"] = bool(
        re.search(r"quote_volume[^.]{0,220}\bnot\b[^.]{0,40}present", schema, re.I)
        or re.search(r"\bnot\b[^.]{0,40}present[^.]{0,220}quote_volume", schema, re.I))
    rep["schema_mentions_share_or_adjustment_fields"] = bool(
        re.search(r"(free[-_ ]float|shares?[-_ ]outstanding|split|dividend|adjust)", schema, re.I))

    inst = _load_json(os.path.join(raw, "binance/usdm/instruments/usdm-perp-instruments.json"))
    inner = inst["instruments"]
    rows = inner if isinstance(inner, list) else list(inner.values())
    flds = [r["fields"] for r in rows]
    rep["instrument_count"] = len(rows)
    rep["instrument_ids"] = sorted(f["id"] for f in flds)
    rep["instrument_base_currencies"] = sorted({f["base_currency"] for f in flds})
    rep["instrument_types"] = sorted({f["type"] for f in flds})
    rep["instrument_field_names"] = sorted(flds[0].keys())
    rep["instrument_asset_class"] = sorted({f.get("asset_class", "crypto") for f in flds})
    rep["instrument_listing_fields"] = [f for f in sorted(flds[0].keys())
                                        if re.search(r"(listing|delist|onboard|expiry|delivery|"
                                                     r"valid_from|valid_to|constituent|index)", f, re.I)]
    rep["instrument_size_fields"] = [f for f in sorted(flds[0].keys())
                                     if re.search(r"(market[-_ ]?cap|mcap|supply|liquidity|"
                                                  r"turnover|float|shares|borrow)", f, re.I)]
    rep["instrument_fee_fields"] = [f for f in ("maker_fee", "taker_fee") if f in flds[0]]
    rep["instrument_tick_sizes"] = {f["id"]: f.get("price_increment") for f in flds}

    kl = os.path.join(raw, "binance/usdm/klines")
    rep["klines_symbol_dirs"] = _ls(kl)
    rep["cross_section_size"] = len(rep["klines_symbol_dirs"])
    rep["altcoin_legs_local"] = sorted(set(rep["klines_symbol_dirs"]) - BTC_ETH_BENCHMARK_LEGS)
    rep["altcoin_leg_count_local"] = len(rep["altcoin_legs_local"])
    rep["klines_intervals"] = _ls(os.path.join(kl, rep["klines_symbol_dirs"][0])) if rep["klines_symbol_dirs"] else []
    rep["klines_dataset_dirs"] = len(rep["klines_symbol_dirs"]) * len(rep["klines_intervals"])
    rep["klines_interval_set_full"] = sorted({i for s in rep["klines_symbol_dirs"]
                                              for i in _ls(os.path.join(kl, s))})
    if rep["klines_symbol_dirs"]:
        d0 = os.path.join(kl, "BTCUSDT", "1d")
        fs = _ls(d0)
        rep["klines_1d_file_count"] = len(fs)
        rows_all = [json.loads(x) for f in fs for x in _read_text(os.path.join(d0, f)).splitlines()]
        rep["klines_1d_rows"] = len(rows_all)
        rep["klines_1d_window_utc"] = [_iso(rows_all[0]["open_time_ms"]), _iso(rows_all[-1]["open_time_ms"])]
        rep["klines_1d_open_step_seconds"] = ((rows_all[-1]["open_time_ms"] - rows_all[0]["open_time_ms"])
                                              / 1000.0 / (len(rows_all) - 1))
        rep["klines_1d_row_keys"] = sorted(rows_all[0].keys())
        rep["klines_columns_missing_vs_venue_12"] = [c for c in ("quote_volume", "num_trades",
                                                                 "taker_buy_base_volume")
                                                     if c not in rows_all[0]]
        d5 = os.path.join(kl, "BTCUSDT", "5m")
        rows5 = [json.loads(x) for x in _read_text(os.path.join(d5, _ls(d5)[0])).splitlines()]
        rep["klines_finest_step_seconds"] = ((rows5[1]["open_time_ms"] - rows5[0]["open_time_ms"]) / 1000.0)
        rep["klines_finest_row_keys"] = sorted(rows5[0].keys())

    # ---- the registered window comparison (record: daily 2004-2024 + a 2004-2010 warm-up)
    avail_start = rep["klines_1d_window_utc"][0][:10]
    avail_end = rep["klines_1d_window_utc"][1][:10]
    rep["available_daily_window"] = [avail_start, avail_end]
    rep["available_daily_days"] = rep["klines_1d_rows"]
    rep["required_daily_window"] = list(REQUIRED_DAILY_WINDOW)
    rep["required_daily_days"] = _days_between(*REQUIRED_DAILY_WINDOW)
    rep["required_warmup_window"] = list(REQUIRED_WARMUP_WINDOW)
    rep["daily_window_covers_required"] = bool(avail_start <= REQUIRED_DAILY_WINDOW[0]
                                               and avail_end >= REQUIRED_DAILY_WINDOW[1])
    rep["warmup_segment_present"] = bool(avail_start <= REQUIRED_WARMUP_WINDOW[0])
    rep["daily_window_shortfall_years_at_open"] = int(REQUIRED_DAILY_WINDOW[0][:4]) - int(avail_start[:4])
    rep["daily_coverage_ratio_vs_required"] = round(rep["available_daily_days"]
                                                    / float(rep["required_daily_days"]), 4)
    rep["required_walkforward_folds"] = ("22 non-overlapping 6-month out-of-sample folds (2014-01..2024-12) "
                                         "from 36-month training + 6-month validation windows, 9 seeds")

    fd = os.path.join(raw, "binance/usdm/funding")
    rep["funding_symbol_dirs"] = _ls(fd)
    if rep["funding_symbol_dirs"]:
        p = os.path.join(fd, "BTCUSDT", "BTCUSDT-funding.jsonl.gz")
        lines = _read_text(p).splitlines()
        first, last = json.loads(lines[0]), json.loads(lines[-1])
        rep["funding_row_keys"] = sorted(first.keys())
        rep["funding_venues"] = sorted({json.loads(x).get("venue") for x in lines[:50]})
        rep["funding_window_utc"] = [_iso(first["funding_time_ms"]), _iso(last["funding_time_ms"])]
        rep["funding_rows"] = len(lines)

    # ---- whole-tree probes (entry names, then payload content)
    entries = _all_entries(raw)
    rep["raw_entry_count"] = len(entries)
    name_hits = {}
    for e in entries:
        low = _norm(e).lower()
        for t in COMPILED:
            if t in low:
                name_hits.setdefault(t, []).append(e)
    rep["probe_tokens_tested"] = len(COMPILED)
    rep["probe_hit_counts_entry_names"] = {k: len(v) for k, v in name_hits.items()}
    rep["probe_hits_in_entry_names"] = {k: v[:8] for k, v in name_hits.items()}
    name_unexplained, name_classified = [], []
    _classify_hits(name_hits, RAW_NAME_HIT_CLASSES, name_unexplained, name_classified, "name")
    rep["probe_classified_name_hits"] = name_classified
    rep["probe_hits_unexplained_name"] = sorted({h["token"] for h in name_unexplained})

    files = [e for e in entries if not e.endswith("/")]
    tot_bytes = 0
    token_files = {}
    for e in files:
        p = os.path.join(raw, e)
        try:
            tot_bytes += os.path.getsize(p)
        except OSError:
            pass
        if os.path.splitext(p)[1].lower() not in TEXT_EXTS:
            continue
        try:
            txt = _read_text(p, 400000)
        except Exception:  # noqa: BLE001
            continue
        probe_txt = _norm(txt)
        for k, rx in COMPILED.items():
            if rx.search(probe_txt):
                token_files.setdefault(k, []).append(e)
    rep["payload_scan_files"] = len(files)
    rep["payload_scan_bytes"] = tot_bytes
    rep["payload_hit_counts"] = {k: len(v) for k, v in token_files.items()}
    rep["payload_token_files"] = {k: v[:6] for k, v in token_files.items()}
    pay_unexplained, pay_classified = [], []
    _classify_hits(token_files, RAW_PAYLOAD_HIT_CLASSES, pay_unexplained, pay_classified, "payload")
    rep["probe_classified_payload_hits"] = pay_classified
    rep["probe_hits_unexplained_payload"] = sorted({h["token"] for h in pay_unexplained})
    rep["payload_data_ext_token_files"] = {
        k: [e for e in v if os.path.splitext(e)[1].lower() in DATA_EXTS] for k, v in token_files.items()}
    rep["probe_payload_data_ext_hits"] = sum(len(v) for v in rep["payload_data_ext_token_files"].values())

    # a *series* is only present if the token appears in a data-shaped place:
    # a non-prose payload file, or a column/field name.
    series_tokens = {k for k, v in rep["payload_data_ext_token_files"].items() if v}
    column_tokens = set(_token_field_scan(rep.get("klines_1d_row_keys", [])
                                          + rep.get("klines_finest_row_keys", [])
                                          + rep.get("funding_row_keys", [])
                                          + rep.get("instrument_field_names", [])))
    rep["series_shaped_tokens"] = sorted(series_tokens | column_tokens)
    token_hits = {k: (len(token_files.get(k, [])) + len(name_hits.get(k, []))) for k in COMPILED}
    rep["probe_token_hits_total"] = token_hits
    rep["equity_surface_token_groups_present"] = {
        g: _group_present(token_hits, toks) for g, toks in EQUITY_TOKEN_GROUPS.items()}
    rep["equity_series_present"] = bool((series_tokens | column_tokens)
                                        & set(CORE_MISSING_TOKENS) - set(BORROW_TOKENS))
    rep["equity_market_present"] = bool(set(series_tokens) &
                                        set(EQUITY_TOKEN_GROUPS["equity_identity"]))
    rep["index_universe_series_present"] = bool((series_tokens | column_tokens)
                                                & set(EQUITY_TOKEN_GROUPS["index_tickers"]))
    rep["equity_vendor_series_present"] = bool(series_tokens & set(EQUITY_TOKEN_GROUPS["vendors"]))
    rep["universe_membership_series_present"] = bool(
        (series_tokens | column_tokens) & {"universe", "membership", "survivorship",
                                           "point_in_time", "pit", "constituent"})
    rep["delisting_history_series_present"] = bool(
        (series_tokens | column_tokens) & {"listing", "delist"} or rep["instrument_listing_fields"])
    rep["adjustment_series_present"] = bool(
        (series_tokens | column_tokens) & {"adjusted", "split", "dividend"})
    rep["free_float_series_present"] = bool(
        (series_tokens | column_tokens) & {"free_float", "shares_outstanding"})
    rep["borrow_series_present"] = bool((series_tokens | column_tokens) & set(BORROW_TOKENS))
    rep["calendar_series_present"] = bool(
        (series_tokens | column_tokens) & set(EQUITY_TOKEN_GROUPS["calendars"]))
    rep["fundamental_series_present"] = bool(
        (series_tokens | column_tokens) & set(EQUITY_TOKEN_GROUPS["fundamentals_sector_earnings"]))
    rep["options_or_sentiment_series_present"] = bool(
        (series_tokens | column_tokens) & set(EQUITY_TOKEN_GROUPS["options_or_sentiment"]))
    rep["macro_series_present"] = bool(
        (series_tokens | column_tokens) & set(EQUITY_TOKEN_GROUPS["macro"]))
    rep["second_venue_series_present"] = bool(series_tokens & set(PORT_VENUE_TOKENS))
    rep["spread_series_present"] = bool((series_tokens | column_tokens) & {"spread"})
    rep["adv60_dollar_volume_series_present"] = bool(
        (series_tokens | column_tokens) & {"adv60", "dollar_volume", "quote_volume"})
    rep["altcoin_cross_section_series_present"] = bool(series_tokens & {"altcoin"})

    # a *market* daily panel - any data-shaped file whose first record carries a
    # date and a price column - would be the substrate of the record's daily
    # window requirement and of its adjusted-price treatment.
    data_ext_files = sorted({e for v in rep["payload_data_ext_token_files"].values() for e in v})
    panel_files, panel_keys = [], {}
    for e in data_ext_files:
        keys = _first_row_keys(os.path.join(raw, e))
        if not keys:
            continue
        low = [_norm(k).lower() for k in keys]
        if (any(re.search(r"\b(date|time|timestamp)\b", k) for k in low)
                and any(re.search(r"\b(close|adj close|price|open|high|low|value)\b", k) for k in low)):
            panel_files.append(e)
            panel_keys[e] = keys
    rep["market_daily_panel_files"] = panel_files
    rep["market_daily_panel_keys"] = panel_keys
    rep["market_daily_panel_present"] = bool(panel_files)

    # cost-model inputs the record's execution section needs
    rep["adv60_dollar_volume_computable"] = bool("quote_volume" in rep.get("klines_1d_row_keys", [])
                                                 and rep["klines_1d_rows"] > 60)
    rep["free_float_present"] = rep["free_float_series_present"]
    rep["borrow_fee_present"] = rep["borrow_series_present"]
    rep["trading_calendar_present"] = rep["calendar_series_present"]
    rep["adjusted_price_present"] = rep["adjustment_series_present"]
    rep["execution_input_status"] = []
    for key, note in REQUIRED_EXECUTION_INPUTS:
        flag = {"adv60_dollar_volume": rep["adv60_dollar_volume_computable"],
                "free_float_shares": rep["free_float_present"],
                "borrow_fees": rep["borrow_fee_present"],
                "trading_calendars": rep["trading_calendar_present"],
                "adjusted_prices": rep["adjusted_price_present"]}[key]
        rep["execution_input_status"].append({"item": key, "requirement": note,
                                              "present": bool(flag)})

    # state-feature groups named by the record's Signal section
    rep["state_feature_status"] = []
    for key, note in REQUIRED_FEATURE_GROUPS:
        if key in PRICE_DERIVABLE_FEATURE_GROUPS:
            status = "PRESENT_FOR_FOUR_CONTRACTS_ONLY"
        else:
            status = "ABSENT"
        rep["state_feature_status"].append({"group": key, "requirement": note, "status": status})
    rep["absent_state_feature_groups"] = [f["group"] for f in rep["state_feature_status"]
                                          if f["status"] == "ABSENT"]
    rep["price_derivable_feature_groups"] = [f["group"] for f in rep["state_feature_status"]
                                             if f["status"] == "PRESENT_FOR_FOUR_CONTRACTS_ONLY"]

    # crypto-portability cross-section (the reading the card canonicalises)
    rep["crypto_port_cross_section_ok"] = bool(rep["altcoin_leg_count_local"] >= REQUIRED_ALTCOIN_LEGS_MIN)
    rep["crypto_port_venues_required"] = REQUIRED_VENUES
    rep["crypto_port_venue_present"] = str(rep["documented_venue"]).lower() in [
        v.lower() for v in REQUIRED_VENUES]
    rep["crypto_port_venue_count"] = rep["venue_count"]
    rep["required_altcoin_legs_min"] = REQUIRED_ALTCOIN_LEGS_MIN
    rep["required_altcoin_legs_max"] = REQUIRED_ALTCOIN_LEGS_MAX
    rep["required_index_tickers"] = REQUIRED_INDEX_TICKERS

    # the one surface the raw does cover: OHLCV bars for the four local contracts
    rep["price_bars_computable_local"] = bool(
        rep["klines_1d_rows"] > 1500 and rep["klines_finest_step_seconds"] == 300.0)
    rep["price_panel_symbols"] = rep["klines_symbol_dirs"]

    # the non-crypto stores the store's own migration inventory names as *not*
    # migrated: measured on the live filesystem, because prose about a store that
    # no longer exists is not evidence that equity data is available.
    rep["named_noncanonical_stores"] = [
        {**s, "exists": os.path.exists(s["path"])} for s in NAMED_NONCANONICAL_STORES]
    rep["named_noncanonical_stores_present"] = [s["path"] for s in rep["named_noncanonical_stores"]
                                                if s["exists"]]
    rep["required_data_available"] = False
    return rep


def _read_pit_gates(sources=None):
    """Re-read the host's own PIT evidence gates. Nothing here is trusted prose."""
    sources = sources or PIT_GATE_SOURCES
    out = {"sources": sources}
    g = _load_json(sources["membership_gate"])
    cov = g.get("coverage", {})
    out["membership_gate_classification"] = g.get("classification")
    out["membership_gate_pit_supported_asset_count"] = g.get("pit_supported_asset_count")
    out["membership_gate_supported_cells"] = cov.get("supported_cells")
    out["membership_gate_total_cells"] = cov.get("total_cells")
    out["membership_gate_unknown_cells"] = cov.get("unknown_cells")
    out["membership_gate_next_gate"] = g.get("next_gate")
    supported = total = 0
    with open(sources["membership_cells"], encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            total += 1
            if json.loads(line).get("supported") is True:
                supported += 1
    out["membership_cells_rows"] = total
    out["membership_cells_supported"] = supported
    lc = _load_json(sources["lifecycle_gate"])
    out["lifecycle_classification"] = lc.get("classification")
    out["lifecycle_pit_supported_asset_count"] = lc.get("pit_supported_asset_count")
    out["lifecycle_daily_membership_pit_supported_asset_count"] = lc.get(
        "daily_membership_pit_supported_asset_count")
    out["lifecycle_bounded_event_point_asset_count"] = lc.get("bounded_event_point_asset_count")
    out["lifecycle_bounded_event_point_event_count"] = lc.get("bounded_event_point_event_count")
    out["lifecycle_current_exchange_info_policy"] = lc.get("current_exchange_info_policy")
    types = {}
    scopes = {}
    symbols = set()
    rows = 0
    with open(sources["lifecycle_events"], encoding="utf-8", errors="replace") as fh:
        for r in csv.DictReader(fh):
            rows += 1
            types[r.get("event_type")] = types.get(r.get("event_type"), 0) + 1
            scopes[r.get("support_scope")] = scopes.get(r.get("support_scope"), 0) + 1
            symbols.add(r.get("venue_symbol"))
    out["lifecycle_event_rows"] = rows
    out["lifecycle_event_type_counts"] = types
    out["lifecycle_event_support_scopes"] = scopes
    out["lifecycle_event_symbols"] = len(symbols)
    panel_rows = 0
    panel_symbols = set()
    panel_dates = set()
    panel_cols = []
    panel_markets = set()
    with open(sources["phase7_panel"], encoding="utf-8", errors="replace") as fh:
        for r in csv.DictReader(fh):
            panel_rows += 1
            panel_symbols.add(r.get("symbol"))
            panel_dates.add(r.get("date"))
            if r.get("market"):
                panel_markets.add(r.get("market"))
            if not panel_cols:
                panel_cols = sorted(r.keys())
    out["phase7_panel_rows"] = panel_rows
    out["phase7_panel_symbol_count"] = len(panel_symbols)
    out["phase7_panel_symbols"] = sorted(panel_symbols)
    out["phase7_panel_window"] = [min(panel_dates), max(panel_dates)] if panel_dates else []
    out["phase7_panel_columns"] = panel_cols
    out["phase7_panel_markets"] = sorted(panel_markets)
    prov = _read_text(sources["phase7_provenance"])
    out["phase7_provenance_declares_not_pit"] = PHASE7_NON_PIT_STATEMENT in prov
    out["phase7_provenance_survivorship_statement"] = bool(
        re.search(r"不能宣稱無 survivorship bias", prov))
    return out


def measure_host_stores(roots=None, cap_per_root=60000):
    """Re-measure the host's non-canonical stores for the required surfaces."""
    roots = roots or HOST_ROOTS
    rep = {"roots": {}, "totals": {}}
    tot_files = tot_hits = 0
    data_hits, prose_hits = [], []
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
            toks = {k for k, rx in COMPILED.items() if rx.search(_norm(rel))}
            if os.path.splitext(p)[1].lower() in TEXT_EXTS:
                try:
                    txt = _read_text(p, 200000)
                except Exception:  # noqa: BLE001
                    txt = ""
                probe_txt = _norm(txt)
                toks |= {k for k, rx in COMPILED.items() if rx.search(probe_txt)}
            if not toks:
                continue
            hits += 1
            for t in toks:
                per_tok[t] = per_tok.get(t, 0) + 1
            rec = {"root": root, "path": rel, "tokens": sorted(toks),
                   "ext": os.path.splitext(p)[1].lower()}
            if rec["ext"] in DATA_EXTS:
                data_hits.append(rec)
            else:
                prose_hits.append(rec)
        rep["roots"][root] = {"exists": True, "files_scanned": n, "token_files": hits,
                              "token_file_counts": per_tok,
                              "data_ext_hits": sum(1 for d in data_hits if d["root"] == root)}
        tot_files += n
        tot_hits += hits
    classified, unclassified = [], []
    for d in data_hits:
        full = os.path.join(d["root"], d["path"])
        cls = None
        for c in HOST_DATA_HIT_CLASSES:
            if full.endswith(c["suffix"]):
                cls = c
                break
        if cls is None:
            unclassified.append(d)
        else:
            classified.append({**d, "class": cls["class"], "why": cls["why"]})
    rep["totals"] = {"roots": len(roots), "files_scanned": tot_files, "token_files": tot_hits,
                     "data_extension_hits": len(data_hits),
                     "prose_extension_hits": len(prose_hits),
                     "classified_data_hits": len(classified),
                     "unclassified_data_hits": len(unclassified)}
    rep["data_candidates"] = data_hits[:20]
    rep["classified_data_hits"] = classified
    rep["unclassified_data_candidates"] = unclassified
    rep["prose_hits_sample"] = prose_hits[:10]
    rep["host_data_hit_classes_used"] = sorted({c["class"] for c in classified})
    rep["host_class_allowlist_violations"] = sorted(
        {c["class"] for c in classified if c["class"] not in HOST_CLASS_ALLOWLIST})
    rep["pid_gates"] = _read_pit_gates()
    rep["any_store_carries_pit_membership_panel"] = bool(
        (rep["pid_gates"].get("membership_cells_supported") or 0) > 0
        or (rep["pid_gates"].get("lifecycle_pit_supported_asset_count") or 0) > 0)

    # "carries a series" is decided by ROW SHAPE (column names / JSON keys of the
    # first record), not by prose: a token that only appears inside a notice body
    # or a file path is text, while a column named `adj_close`/`gics_sector` is a
    # series. Files whose shape cannot be probed (sqlite/bin) are named.
    cls_by_path = {os.path.join(c["root"], c["path"]): c["class"] for c in classified}

    series_candidates, shape_skipped, token_only = [], [], []
    geometry = {"equity_market": [], "membership": [], "macro": [], "borrow_or_calendar": [], "cost": []}
    MEMBERSHIP_GROUP_TOKENS = set(MEMBERSHIP_TOKENS)
    EQUITY_MARKET_TOKENS = (set(CORE_MISSING_TOKENS) - MEMBERSHIP_GROUP_TOKENS
                            - set(BORROW_TOKENS) - set(COST_TOKENS))
    for d in data_hits:
        full = os.path.join(d["root"], d["path"])
        cls = cls_by_path.get(full)
        keys = _first_row_keys(full)
        row_tokens = set(_token_field_scan(keys)) if keys else set()
        rec = {"root": d["root"], "path": d["path"], "case_class": cls, "tokens": d["tokens"],
               "row_keys": keys}
        if not keys:
            shape_skipped.append(rec)
            continue
        hit_groups = []
        if row_tokens & MEMBERSHIP_GROUP_TOKENS:
            hit_groups.append("membership")
        if row_tokens & EQUITY_MARKET_TOKENS:
            hit_groups.append("equity_market")
        if row_tokens & set(EQUITY_TOKEN_GROUPS["macro"]):
            hit_groups.append("macro")
        if row_tokens & set(BORROW_TOKENS + EQUITY_TOKEN_GROUPS["calendars"]):
            hit_groups.append("borrow_or_calendar")
        if row_tokens & set(COST_TOKENS):
            hit_groups.append("cost")
        if hit_groups:
            for g in hit_groups:
                geometry[g].append(rec)
            series_candidates.append({**rec, "groups": hit_groups, "row_tokens": sorted(row_tokens)})
        else:
            token_only.append(rec)

    rep["host_series_candidates"] = series_candidates
    rep["host_series_shape_skipped"] = shape_skipped
    rep["host_token_only_hits"] = token_only
    rep["host_row_shape_probe"] = {
        "files_probed": len(data_hits) - len(shape_skipped),
        "files_skipped": len(shape_skipped),
        "skipped_paths": [r["path"] for r in shape_skipped],
    }
    for g in geometry:
        key = {"equity_market": "host_equity_market_series_candidates",
               "membership": "host_membership_candidates",
               "macro": "host_macro_series_candidates",
               "borrow_or_calendar": "host_borrow_or_calendar_candidates",
               "cost": "host_spread_candidates"}[g]
        rep[key] = geometry[g]
    rep["corpus_only_classes"] = sorted(CORPUS_ONLY_CLASSES)
    rep["any_store_carries_equity_market_series"] = bool(geometry["equity_market"])
    rep["any_store_carries_macro_series"] = bool(geometry["macro"])
    rep["any_store_carries_borrow_or_calendar_series"] = bool(geometry["borrow_or_calendar"])
    rep["any_store_carries_spread_or_book_series"] = bool(geometry["cost"])
    rep["membership_candidates_all_classified"] = all(
        r["case_class"] in HOST_CLASS_ALLOWLIST for r in geometry["membership"] + shape_skipped)
    rep["widest_host_price_panel_symbol_count"] = rep["pid_gates"].get("phase7_panel_symbol_count")
    rep["widest_host_price_panel_is_declared_non_pit"] = bool(
        rep["pid_gates"].get("phase7_provenance_declares_not_pit"))
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


def run_checks(results_root, raw_root, repo_root=None, family=FAMILY, round_id=ROUND,
               task=TASK, raw=None, stores=None, record_path=None):
    repo_root = repo_root or DEFAULT_REPO
    raw = raw if raw is not None else measure_raw(raw_root)
    stores = stores if stores is not None else measure_host_stores()
    sf = _family_surfaces(results_root, family, round_id)
    spec = _load_json(sf["spec"])
    verdict = _load_json(sf["verdict"])
    fam = _load_json(sf["family_json"])
    pg = stores.get("pid_gates", {})
    checks = []
    add = checks.append

    # ---- C1 store identity
    add(_check("C1", "canonical raw identity is a single crypto-perp venue carrying the three dataset families, "
                     "as the store's own config documents them",
               raw["documented_venue"] == "BINANCE" and raw["documented_market_type"] == "usdm_perp"
               and set(raw["usdm_subdirs"]) >= {"funding", "instruments", "klines"}
               and set(raw["documented_symbols"]) == {"BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"}
               and sorted(raw["documented_intervals"]) == ["15m", "1d", "1h", "1w", "30m", "4h", "5m"],
               {"venue": raw["documented_venue"], "market_type": raw["documented_market_type"],
                "usdm_subdirs": raw["usdm_subdirs"], "venue_dirs": raw["venue_dirs"],
                "market_dirs": raw["market_dirs"], "symbols": raw["documented_symbols"],
                "intervals": raw["documented_intervals"]}))
    # ---- C2 instrument surface (fee/tick metadata present; listing/size fields absent)
    add(_check("C2", "instrument surface carries four crypto perpetuals, fee/tick metadata and no "
                     "listing, index, share or borrow field",
               raw["instrument_count"] == 4
               and raw["instrument_types"] == ["CryptoPerpetual"]
               and raw["instrument_listing_fields"] == []
               and raw["instrument_size_fields"] == []
               and raw["instrument_fee_fields"] == ["maker_fee", "taker_fee"]
               and set(raw["documented_symbols"]) == {"BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"},
               {"ids": raw["instrument_ids"], "types": raw["instrument_types"],
                "listing_fields": raw["instrument_listing_fields"],
                "size_fields": raw["instrument_size_fields"],
                "fee_fields": raw["instrument_fee_fields"],
                "field_names": raw["instrument_field_names"],
                "tick_sizes": raw["instrument_tick_sizes"]}))
    # ---- C3 kline surface: OHLCV-only, seven intervals, 28 datasets
    add(_check("C3", "kline surface is OHLCV-only on seven intervals with no equity/universe/market-cap column",
               raw["klines_symbol_dirs"] == ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
               and raw["klines_interval_set_full"] == ["15m", "1d", "1h", "1w", "30m", "4h", "5m"]
               and raw["klines_dataset_dirs"] == 28
               and raw["klines_1d_open_step_seconds"] == 86400.0
               and raw["klines_1d_rows"] > 1500
               and raw["klines_1d_row_keys"] == ["close", "close_time_ms", "high", "low", "open",
                                                 "open_time_ms", "volume"],
               {"symbols": raw["klines_symbol_dirs"], "intervals": raw["klines_interval_set_full"],
                "datasets": raw["klines_dataset_dirs"], "row_keys": raw["klines_1d_row_keys"],
                "1d_window": raw["klines_1d_window_utc"], "1d_rows": raw["klines_1d_rows"],
                "step_s": raw["klines_1d_open_step_seconds"],
                "finest_step_s": raw["klines_finest_step_seconds"]}))
    # ---- C4 funding surface
    add(_check("C4", "funding surface is perp funding only, with no universe/membership field",
               raw["funding_symbol_dirs"] == ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
               and raw["funding_venues"] == ["BINANCE"]
               and raw["funding_rows"] > 5000,
               {"symbols": raw["funding_symbol_dirs"], "keys": raw["funding_row_keys"],
                "venues": raw["funding_venues"], "window": raw["funding_window_utc"],
                "rows": raw["funding_rows"]}))
    # ---- C5 entry-name probe
    add(_check("C5", "entry-name probe over the whole raw tree finds no unexplained equity / fundamental / "
                     "macro / membership token",
               raw["probe_hits_unexplained_name"] == [] and raw["raw_entry_count"] > 1000,
               {"entries": raw["raw_entry_count"], "tokens": raw["probe_tokens_tested"],
                "hit_counts": raw["probe_hit_counts_entry_names"],
                "unexplained": raw["probe_hits_unexplained_name"]}))
    # ---- C6 payload probe
    add(_check("C6", "payload probe over every raw file finds only classified prose mentions and no "
                     "data-extension hit",
               raw["probe_hits_unexplained_payload"] == [] and raw["payload_scan_files"] > 1000
               and raw["probe_payload_data_ext_hits"] == 0,
               {"files": raw["payload_scan_files"], "bytes": raw["payload_scan_bytes"],
                "hit_counts": raw["payload_hit_counts"],
                "data_ext_token_files": raw["payload_data_ext_token_files"],
                "classified": raw["probe_classified_payload_hits"],
                "unexplained": raw["probe_hits_unexplained_payload"]}))
    # ---- C7 the equity / index universe itself (decisive requirement 1)
    add(_check("C7", "no equity market, no index universe and no vendor surface exists in the canonical raw, "
                     "and none of the non-crypto stores the store's own inventory names is on the machine",
               raw["equity_market_present"] is False
               and raw["index_universe_series_present"] is False
               and raw["equity_vendor_series_present"] is False
               and raw["schema_documents_equity_or_index_dataset"] is False
               and raw["market_dirs"] == ["binance/usdm"]
               and raw["venue_dirs"] == ["binance"]
               and raw["named_noncanonical_stores_present"] == [],
               {"equity_market_present": raw["equity_market_present"],
                "index_universe_series_present": raw["index_universe_series_present"],
                "equity_vendor_series_present": raw["equity_vendor_series_present"],
                "schema_dataset_sections": raw["schema_dataset_sections"],
                "required_index_tickers": raw["required_index_tickers"],
                "series_shaped_tokens": raw["series_shaped_tokens"],
                "market_dirs": raw["market_dirs"],
                "named_noncanonical_stores": raw["named_noncanonical_stores"],
                "named_noncanonical_stores_present": raw["named_noncanonical_stores_present"]}))
    # ---- C8 the registered daily sample window (decisive requirement 2)
    add(_check("C8", "the record's 2004-2024 split/dividend-adjusted daily window (and its 2004-2010 warm-up) "
                     "is absent; the raw's daily coverage starts 18 years late",
               raw["daily_window_covers_required"] is False
               and raw["warmup_segment_present"] is False
               and raw["adjusted_price_present"] is False
               and raw["market_daily_panel_present"] is False
               and raw["required_daily_days"] > raw["available_daily_days"]
               and raw["klines_1d_open_step_seconds"] == 86400.0,
               {"required_daily_window": raw["required_daily_window"],
                "required_daily_days": raw["required_daily_days"],
                "available_daily_window": raw["available_daily_window"],
                "available_daily_days": raw["available_daily_days"],
                "coverage_ratio_vs_required": raw["daily_coverage_ratio_vs_required"],
                "daily_window_shortfall_years_at_open": raw["daily_window_shortfall_years_at_open"],
                "required_warmup_window": raw["required_warmup_window"],
                "warmup_segment_present": raw["warmup_segment_present"],
                "adjusted_price_present": raw["adjusted_price_present"],
                "market_daily_panel_files": raw["market_daily_panel_files"],
                "required_walkforward_folds": raw["required_walkforward_folds"]}))
    # ---- C9 point-in-time membership / survivorship (decisive requirement 3)
    add(_check("C9", "time-varying index membership and delisting history are absent from the canonical raw",
               raw["universe_membership_series_present"] is False
               and raw["delisting_history_series_present"] is False
               and raw["instrument_listing_fields"] == []
               and raw["schema_documents_universe_or_mcap_dataset"] is False,
               {"universe_membership_series": raw["universe_membership_series_present"],
                "delisting_history_series": raw["delisting_history_series_present"],
                "instrument_listing_fields": raw["instrument_listing_fields"],
                "schema_dataset_sections": raw["schema_dataset_sections"],
                "listing_token_files": raw["payload_token_files"].get("listing", []),
                "constituent_token_files": raw["payload_token_files"].get("constituent", [])}))
    # ---- C10 the state-representation feature catalogue (decisive requirement 4)
    add(_check("C10", "every non-price state feature group the record names is absent; only the price/volume "
                      "derivable groups exist, and only for the four contracts",
               sorted(raw["absent_state_feature_groups"]) == sorted(ABSENT_EXPECTED_FEATURE_GROUPS)
               and sorted(raw["price_derivable_feature_groups"]) == sorted(PRICE_DERIVABLE_FEATURE_GROUPS)
               and raw["fundamental_series_present"] is False
               and raw["options_or_sentiment_series_present"] is False
               and raw["macro_series_present"] is False,
               {"state_feature_status": raw["state_feature_status"],
                "absent_groups": raw["absent_state_feature_groups"],
                "price_derivable_groups": raw["price_derivable_feature_groups"],
                "fundamental_series": raw["fundamental_series_present"],
                "options_or_sentiment_series": raw["options_or_sentiment_series_present"],
                "macro_series": raw["macro_series_present"]}))
    # ---- C11 execution-model inputs
    add(_check("C11", "the execution inputs the record's cost model needs (ADV60 dollar volume, free float, "
                      "borrow fees, exchange calendars, adjusted prices) are absent from the raw",
               raw["adv60_dollar_volume_computable"] is False
               and raw["free_float_present"] is False
               and raw["borrow_fee_present"] is False
               and raw["trading_calendar_present"] is False
               and raw["adjusted_price_present"] is False
               and all(not e["present"] for e in raw["execution_input_status"]),
               {"execution_input_status": raw["execution_input_status"],
                "klines_columns_missing_vs_venue_12": raw["klines_columns_missing_vs_venue_12"],
                "free_float_series": raw["free_float_series_present"],
                "borrow_series": raw["borrow_series_present"],
                "calendar_series": raw["calendar_series_present"],
                "adjustment_series": raw["adjustment_series_present"]}))
    # ---- C12 the crypto-portability reading the card canonicalises
    add(_check("C12", "even under the record's own adapted/unproven crypto port the cross-section fails: two "
                      "altcoin legs against a registered 50-100, one venue, no survivorship surface",
               raw["crypto_port_cross_section_ok"] is False
               and raw["altcoin_leg_count_local"] < REQUIRED_ALTCOIN_LEGS_MIN
               and raw["second_venue_present"] is False
               and raw["second_venue_series_present"] is False
               and raw["venue_count"] == 1
               and raw["crypto_port_venue_present"] is True,
               {"altcoin_legs_local": raw["altcoin_legs_local"],
                "altcoin_leg_count_local": raw["altcoin_leg_count_local"],
                "required_altcoin_legs": [REQUIRED_ALTCOIN_LEGS_MIN, REQUIRED_ALTCOIN_LEGS_MAX],
                "benchmark_legs": sorted(BTC_ETH_BENCHMARK_LEGS),
                "venues_required": raw["crypto_port_venues_required"],
                "venue_count": raw["venue_count"], "venue_dirs": raw["venue_dirs"],
                "second_venue_series_present": raw["second_venue_series_present"],
                "port_cross_section_ok": raw["crypto_port_cross_section_ok"]}))
    # ---- C13 universe kept whole + spec-side registration
    ur = spec.get("universe_registration", {})
    add(_check("C13", "the raw can offer only four fixed contracts; the published round did not shrink the "
                      "registered universe to them and recorded the prerequisite as absent",
               raw["cross_section_size"] == 4
               and raw["price_bars_computable_local"] is True
               and ur.get("universe_shrunk_to_local_list") is False
               and ur.get("required_data_available_local") is False,
               {"cross_section_size": raw["cross_section_size"],
                "price_bars_computable_local": raw["price_bars_computable_local"],
                "universe_shrunk_to_local_list": ur.get("universe_shrunk_to_local_list"),
                "required_data_available_local": ur.get("required_data_available_local"),
                "price_panel_symbols": raw["price_panel_symbols"]}))
    # ---- C14 round-spec terminal values
    gate = spec.get("prerequisite_gate", {})
    launch = spec.get("launch", {})
    add(_check("C14", "round-spec states the contract-mandated terminal values",
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
    # ---- C15 verdict.json terminal values
    add(_check("C15", "verdict.json states the same terminal values and an empty survivor/coverage surface",
               verdict.get("verdict") == "TECHNICAL_INCOMPLETE"
               and verdict.get("performance_claimable") is False
               and verdict.get("failure", {}).get("layer") == "card-local"
               and verdict.get("failure", {}).get("class") == "data_window_invalid"
               and verdict.get("failure", {}).get("last_run_id") is None
               and verdict.get("attempts", {}).get("launched") == 0
               and verdict.get("survivors") == [] and verdict.get("survivor_bundle") is None
               and verdict.get("coverage", {}).get("cells_computed") == 0
               and verdict.get("cohorts", {}).get("realized") == 0,
               {"verdict": verdict.get("verdict"),
                "performance_claimable": verdict.get("performance_claimable"),
                "attempts": verdict.get("attempts"), "survivors": verdict.get("survivors"),
                "coverage": verdict.get("coverage"), "cohorts": verdict.get("cohorts")}))
    # ---- C16 nothing submitted
    stray = []
    for dirpath, dirnames, filenames in os.walk(sf["family_dir"]):
        for name in filenames:
            if name in ("run-spec.json", "result.json", "state.json") or name.startswith("terminal"):
                stray.append(os.path.relpath(os.path.join(dirpath, name), sf["family_dir"]))
    add(_check("C16", "nothing was ever submitted: no run-spec, no attempt dir, no sentinel",
               not os.path.isdir(sf["attempts_dir"]) and stray == []
               and sorted(sf["round_dir_listing"]) == ["round-spec.json", "verdict.json"]
               and sorted(sf["family_dir_listing"]) == ["family.json", "rounds"],
               {"attempts_dir_exists": os.path.isdir(sf["attempts_dir"]),
                "round_dir_listing": sf["round_dir_listing"],
                "family_dir_listing": sf["family_dir_listing"], "stray": stray}))
    # ---- C17 DCA registration intact
    dd = spec.get("dca_domain", {})
    ufi = spec.get("user_fixed_invariants", {})
    axes = ["spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct"]
    dca_ok = (dd.get("configs_per_cohort_per_grid") == 48
              and dd.get("base_quote") == 1000
              and dd.get("base_quote_status") == "PROJECT_PRE_REGISTERED_CONSTANT"
              and dd.get("search_axes_status") == "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN"
              and sorted(dd.get("axes", {}).keys()) == sorted(axes)
              and not any(a in ufi for a in axes) and "base_quote" not in ufi)
    add(_check("C17", "DCA domain carries the 7.2 v1.3.1 provenance classes and the complete 48-cell product",
               dca_ok,
               {"dca_configs": dd.get("configs_per_cohort_per_grid"),
                "base_quote_status": dd.get("base_quote_status"),
                "search_axes_status": dd.get("search_axes_status"),
                "axes": sorted(dd.get("axes", {}).keys()), "dca_ok": dca_ok}))
    # ---- C18 coverage / survivor surface empty by construction
    cov = spec.get("robustness_plan", {})
    sel = spec.get("selector_and_disposition", {})
    add(_check("C18", "coverage is registered but empty; no survivor surface exists",
               cov.get("cells_registered_per_grid") == 48
               and cov.get("cells_computed") == 0
               and len(cov.get("registered_phase_grids", [])) == 10
               and all(v.get("computed") == 0 for v in cov.get("coverage_counts", {}).values())
               and sel.get("cohorts_realized") == 0 and sel.get("survivors") == []
               and sel.get("selector") == "cohort-selector-v1"
               and sel.get("disposition") == "cohort-disposition-v1",
               {"cells_registered_per_grid": cov.get("cells_registered_per_grid"),
                "cells_computed": cov.get("cells_computed"),
                "phase_grids": len(cov.get("registered_phase_grids", [])),
                "cohorts_realized": sel.get("cohorts_realized"),
                "selector": sel.get("selector"), "disposition": sel.get("disposition")}))
    # ---- C19 host-side PIT evidence gates declare zero supported membership
    add(_check("C19", "the machine's own PIT evidence stores declare zero supported daily membership",
               pg.get("membership_gate_classification") == "DATA_BLOCKED"
               and pg.get("membership_gate_pit_supported_asset_count") == 0
               and pg.get("membership_gate_supported_cells") == 0
               and (pg.get("membership_gate_unknown_cells") or 0) > 10000
               and pg.get("membership_cells_supported") == 0
               and pg.get("lifecycle_classification") == "BOUNDED"
               and pg.get("lifecycle_pit_supported_asset_count") == 0
               and pg.get("lifecycle_daily_membership_pit_supported_asset_count") == 0
               and stores.get("any_store_carries_pit_membership_panel") is False,
               {"membership_gate": {"classification": pg.get("membership_gate_classification"),
                                    "pit_supported_asset_count":
                                        pg.get("membership_gate_pit_supported_asset_count"),
                                    "supported_cells": pg.get("membership_gate_supported_cells"),
                                    "unknown_cells": pg.get("membership_gate_unknown_cells"),
                                    "total_cells": pg.get("membership_gate_total_cells")},
                "lifecycle_gate": {"classification": pg.get("lifecycle_classification"),
                                   "pit_supported_asset_count":
                                       pg.get("lifecycle_pit_supported_asset_count"),
                                   "daily_membership_pit_supported_asset_count":
                                       pg.get("lifecycle_daily_membership_pit_supported_asset_count"),
                                   "event_rows": pg.get("lifecycle_event_rows"),
                                   "event_types": pg.get("lifecycle_event_type_counts"),
                                   "support_scopes": pg.get("lifecycle_event_support_scopes")},
                "any_store_carries_pit_membership_panel":
                    stores.get("any_store_carries_pit_membership_panel")}))
    # ---- C20 host stores: all data hits classified; no equity-market series by row shape
    add(_check("C20", "every host data-extension hit is classified into a declared research-store class, no "
                      "store's row shape carries an equity-market / macro / borrow / cost series, and the "
                      "widest host price panel is declared non-point-in-time by its own provenance",
               stores.get("totals", {}).get("unclassified_data_hits") == 0
               and stores.get("totals", {}).get("files_scanned", 0) > 1000
               and stores.get("host_class_allowlist_violations") == []
               and pg.get("phase7_panel_symbol_count") == 12
               and pg.get("phase7_provenance_declares_not_pit") is True
               and stores.get("any_store_carries_equity_market_series") is False
               and stores.get("any_store_carries_macro_series") is False
               and stores.get("any_store_carries_borrow_or_calendar_series") is False
               and stores.get("any_store_carries_spread_or_book_series") is False
               and stores.get("membership_candidates_all_classified") is True,
               dict(stores.get("totals", {}),
                    classes=stores.get("host_data_hit_classes_used"),
                    allowlist_violations=stores.get("host_class_allowlist_violations"),
                    unclassified=stores.get("unclassified_data_candidates"),
                    row_shape_probe=stores.get("host_row_shape_probe"),
                    series_candidates=stores.get("host_series_candidates"),
                    membership_candidates=stores.get("host_membership_candidates"),
                    token_only_hits=stores.get("host_token_only_hits"),
                    phase7_panel={"symbols": pg.get("phase7_panel_symbol_count"),
                                  "rows": pg.get("phase7_panel_rows"),
                                  "window": pg.get("phase7_panel_window"),
                                  "declared_non_pit": pg.get("phase7_provenance_declares_not_pit")})))
    # ---- C21 family.json identity
    add(_check("C21", "family.json binds this round's family to this card and fingerprint",
               fam.get("family_id") == family and fam.get("kanban_task_id") == task
               and isinstance(fam.get("semantic_fingerprint"), str)
               and spec.get("provenance", {}).get("semantic_fingerprint", {}).get(
                   "semantic_fingerprint") == fam.get("semantic_fingerprint"),
               {"family_id": fam.get("family_id"), "kanban_task_id": fam.get("kanban_task_id"),
                "semantic_fingerprint": fam.get("semantic_fingerprint")}))
    # ---- C22 taxonomy separation
    costs = spec.get("costs", {})
    add(_check("C22", "failure taxonomy kept separated: infrastructure terminal, not a scientific failure",
               "infrastructure/technical failure" in costs.get("note", "")
               and spec.get("expected") == "PREREQUISITE_ABSENT"
               and verdict.get("yield", {}).get("yield_decision") == "STOP_TECHNICAL_INCOMPLETE",
               {"expected": spec.get("expected"),
                "yield_decision": verdict.get("yield", {}).get("yield_decision"),
                "note": costs.get("note")}))
    # ---- C23 falsification battery neither lowered nor trimmed
    fal = spec.get("falsification", {})
    add(_check("C23", "the falsification battery is registered unchanged and recorded as not executed",
               fal.get("no_threshold_lowering") is True
               and fal.get("no_item_removal") is True
               and fal.get("falsification_status", "").startswith("NOT_EXECUTED")
               and "1. **Transaction Cost Haircut Stress Test:**" in
               fal.get("record_falsification_verbatim", "")
               and "2. **Dynamic Beta Crash Test:**" in fal.get("record_falsification_verbatim", ""),
               {"status": fal.get("falsification_status"),
                "no_threshold_lowering": fal.get("no_threshold_lowering"),
                "no_item_removal": fal.get("no_item_removal")}))
    return {"checks": checks, "raw": raw, "stores": stores,
            "surfaces": {"family_dir": sf["family_dir"], "round_dir": sf["round_dir"]}}


def _result(checks, raw, stores):
    failed = [c for c in checks if not c["ok"]]
    return {"ok": not failed, "checks": checks, "failed": [c["id"] for c in failed],
            "raw_summary": {"entries": raw.get("raw_entry_count"),
                            "payload_files": raw.get("payload_scan_files"),
                            "payload_bytes": raw.get("payload_scan_bytes")},
            "stores_summary": stores.get("totals")}


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
    tmp = tempfile.mkdtemp(prefix="t348d25aa-selftest-")
    try:
        base = _copy_family(results_root, tmp, family, round_id)
        spec_p = os.path.join(base, "rounds", round_id, "round-spec.json")
        verd_p = os.path.join(base, "rounds", round_id, "verdict.json")
        fam_p = os.path.join(base, "family.json")
        results = []

        def v(name, expect, mutate):
            _copy_family(results_root, tmp, family, round_id)
            mutate(spec_p, verd_p, fam_p)
            res = run_checks(tmp, None, raw=raw, stores=stores, family=family,
                             round_id=round_id, task=task)
            failed = {c["id"] for c in res["checks"] if not c["ok"]}
            results.append({"variant": name, "expected": expect, "failed": sorted(failed),
                            "flipped": expect in failed, "ok": expect in failed})
            return res

        def m_spec(fn):
            return lambda s, vd, f: _mutate(s, fn)

        def m_verdict(fn):
            return lambda s, vd, f: _mutate(vd, fn)

        def m_family(fn):
            return lambda s, vd, f: _mutate(f, fn)

        v("verdict->PASS", "C15", m_verdict(lambda d: d.update({"verdict": "PASS"})))
        v("performance_claimable->true", "C15",
          m_verdict(lambda d: d.update({"performance_claimable": True})))
        v("failure.layer->shared-layer", "C15",
          m_verdict(lambda d: d["failure"].update({"layer": "shared-layer"})))
        v("failure.class->script_bug", "C15",
          m_verdict(lambda d: d["failure"].update({"class": "script_bug"})))
        v("failure.last_run_id->u1", "C15",
          m_verdict(lambda d: d["failure"].update({"last_run_id": round_id + "-u1"})))
        v("attempts.launched->1", "C15",
          m_verdict(lambda d: d["attempts"].update({"launched": 1})))
        v("survivors->[one]", "C15",
          m_verdict(lambda d: d.update({"survivors": [{"cohort": "BTCUSDT/1d"}]})))
        v("coverage.cells_computed->48", "C15",
          m_verdict(lambda d: d["coverage"].update({"cells_computed": 48})))
        v("yield_decision->CONTINUE", "C22",
          m_verdict(lambda d: d["yield"].update({"yield_decision": "CONTINUE"})))
        v("spec.launch.launched->true", "C14",
          m_spec(lambda d: d["launch"].update({"launched": True})))
        v("spec.gate.verdict->REJECT", "C14",
          m_spec(lambda d: d["prerequisite_gate"].update({"verdict": "REJECT"})))
        v("spec.gate.failure_layer->shared-layer", "C14",
          m_spec(lambda d: d["prerequisite_gate"].update({"failure_layer": "shared-layer"})))
        v("spec.gate.last_run_id->u1", "C14",
          m_spec(lambda d: d["prerequisite_gate"].update({"last_run_id": round_id + "-u1"})))
        v("spec.expected->RUN", "C22", m_spec(lambda d: d.update({"expected": "RUN"})))
        v("universe_shrunk_to_local_list->true", "C13",
          m_spec(lambda d: d["universe_registration"].update({"universe_shrunk_to_local_list": True})))
        v("required_data_available_local->true", "C13",
          m_spec(lambda d: d["universe_registration"].update({"required_data_available_local": True})))
        v("dca.configs->12", "C17",
          m_spec(lambda d: d["dca_domain"].update({"configs_per_cohort_per_grid": 12})))
        v("dca.base_quote_status->USER_FIXED", "C17",
          m_spec(lambda d: d["dca_domain"].update({"base_quote_status": "USER_FIXED"})))
        v("dca axes leak into user_fixed_invariants", "C17",
          m_spec(lambda d: d["user_fixed_invariants"].update({"spacing_pct": [0.01, 0.02]})))
        v("coverage.cells_computed->48 (spec)", "C18",
          m_spec(lambda d: d["robustness_plan"].update({"cells_computed": 48})))
        v("cohorts_realized->2", "C18",
          m_spec(lambda d: d["selector_and_disposition"].update({"cohorts_realized": 2})))
        v("selector version changed", "C18",
          m_spec(lambda d: d["selector_and_disposition"].update({"selector": "cohort-selector-v2"})))
        v("falsification item removed", "C23",
          m_spec(lambda d: d["falsification"].update({"no_item_removal": False})))
        v("falsification status -> EXECUTED", "C23",
          m_spec(lambda d: d["falsification"].update({"falsification_status": "EXECUTED"})))
        v("falsification plan key removed", "C23",
          m_spec(lambda d: d["falsification"].update({"record_falsification_verbatim": "trimmed"})))
        v("family.kanban_task_id->other", "C21",
          m_family(lambda d: d.update({"kanban_task_id": "t_00000000"})))
        v("family.semantic_fingerprint mismatch", "C21",
          m_family(lambda d: d.update({"semantic_fingerprint": "sha256:" + "0" * 64})))
        v("stray run-spec.json present", "C16",
          lambda s, vd, f: (os.makedirs(os.path.join(os.path.dirname(s), "attempts", round_id + "-u1"),
                                        exist_ok=True),
                            open(os.path.join(os.path.dirname(s), "attempts", round_id + "-u1",
                                              "run-spec.json"), "w").write("{}")))
        v("stray terminal sentinel present", "C16",
          lambda s, vd, f: open(os.path.join(os.path.dirname(s), "terminal-DONE"), "w").write("{}"))
        v("extra file in family dir", "C16",
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
    """Build a temp raw tree carrying what this record would need and assert the
    raw-side checks flip to FAIL (the raw checks are not vacuously green).

    The fixture keeps the live store's *shape* (one Binance USD-M venue, four
    symbols, seven intervals, 1,720 daily bars, 5,100 funding rows, four
    CryptoPerpetual instruments) so that only the checks about the missing
    equity / index / fundamental / macro / membership / cost surfaces move:
    C1-C4 stay green, C5-C12 flip.
    """
    tmp = tempfile.mkdtemp(prefix="t348d25aa-rawfix-")
    try:
        rawfix = os.path.join(tmp, "market-data-raw")
        symbols = ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
        intervals = ["15m", "1d", "1h", "1w", "30m", "4h", "5m"]
        for sub in (["_meta", "binance/usdm/instruments",
                     "binance/usdm/universe", "coingecko/market_cap", "costs/spread",
                     "token_migration/redenominations", "okx/spot/klines/BTCUSDT",
                     "bybit/usdm/klines/DOGEUSDT",
                     "equities/sp500/constituents", "equities/sp500/klines_1d_adjusted",
                     "equities/fundamentals", "equities/sectors", "equities/earnings",
                     "equities/options", "equities/news", "equities/calendars",
                     "macro/releases", "borrow/fees"]
                    + ["binance/usdm/klines/%s/%s" % (s, i)
                       for s in symbols for i in intervals]
                    + ["binance/usdm/funding/%s" % s for s in symbols]):
            os.makedirs(os.path.join(rawfix, sub), exist_ok=True)
        with open(os.path.join(rawfix, "_meta/CONFIG.json"), "w") as fh:
            json.dump({"schema": "market-data-raw/config/v1", "venue": "BINANCE",
                       "market_type": "usdm_perp", "symbols": symbols,
                       "intervals": intervals}, fh)
        with open(os.path.join(rawfix, "_meta/SCHEMA.md"), "w") as fh:
            fh.write("# fixture schema\n\n## Dataset: klines (Binance USD-M perpetual futures, UTC)\n\n"
                     "## Dataset: funding\n\n## Dataset: instruments\n\n"
                     "## Dataset: equity_index_constituents (point-in-time index membership)\n\n"
                     "## Dataset: equity_adjusted_daily_closes (split/dividend adjusted)\n\n"
                     "## Dataset: fundamentals (balance sheet / income statement / cash flow)\n\n"
                     "## Dataset: gics_sectors\n\n## Dataset: earnings_surprise\n\n"
                     "## Dataset: options_implied_volatility\n\n## Dataset: news_sentiment\n\n"
                     "## Dataset: macro_releases (CPI / GDP / fed funds)\n\n"
                     "## Dataset: borrow_fees\n\n## Dataset: trading_calendar\n")
        with gzip.open(os.path.join(rawfix, "equities/sp500/constituents/sp500-membership-2024.csv.gz"),
                       "wt") as fh:
            fh.write("date,symbol,index_ticker,membership_status,listing_date,delisting_date\n"
                     "2024-06-28,AAPL,^GSPC,member,1980-12-12,\n"
                     "2024-06-28,TSLA,^GSPC,member,2010-06-29,\n"
                     "2024-06-28,OLDCO,^GSPC,delisted,1999-01-04,2021-12-17\n")
        with gzip.open(os.path.join(rawfix, "equities/sp500/klines_1d_adjusted/sp500-1d-adjusted.csv.gz"),
                       "wt") as fh:
            fh.write("date,symbol,adjusted_close,split_factor,dividend,free_float,adv60_dollar_volume\n"
                     "2004-01-02,AAPL,1.0,1.0,0.0,700000000,300000000\n"
                     "2024-12-31,AAPL,250.0,4.0,0.96,15000000000,9000000000\n")
        with gzip.open(os.path.join(rawfix, "equities/fundamentals/balance_sheet.jsonl.gz"), "wt") as fh:
            fh.write(json.dumps({"date": "2004-03-31", "symbol": "AAPL", "income_statement": {},
                                 "balance_sheet": {}, "cash_flow": {},
                                 "earnings_surprise": 0.01}) + "\n")
        with gzip.open(os.path.join(rawfix, "equities/sectors/gics_sectors.csv.gz"), "wt") as fh:
            fh.write("date,symbol,gics_sector,industry\n2004-01-02,AAPL,Information Technology,Hardware\n")
        with gzip.open(os.path.join(rawfix, "equities/earnings/surprise_revisions.csv.gz"), "wt") as fh:
            fh.write("announcement_date,symbol,eps,analyst_consensus,insider_transactions\n"
                     "2004-01-15,AAPL,0.01,0.01,0\n")
        with gzip.open(os.path.join(rawfix, "equities/options/iv_surface.csv.gz"), "wt") as fh:
            fh.write("date,symbol,implied_volatility\n2004-01-02,AAPL,0.31\n")
        with gzip.open(os.path.join(rawfix, "equities/news/sentiment.jsonl.gz"), "wt") as fh:
            fh.write(json.dumps({"date": "2004-01-02", "symbol": "AAPL", "news_sentiment": 0.2}) + "\n")
        with gzip.open(os.path.join(rawfix, "equities/calendars/trading_calendar.csv.gz"), "wt") as fh:
            fh.write("date,exchange,is_trading_day,holiday\n2004-01-02,NYSE,true,\n")
        with gzip.open(os.path.join(rawfix, "macro/releases/macro.csv.gz"), "wt") as fh:
            fh.write("date,cpi,gdp,fed_funds,unemployment,industrial_production,gold,wti,fx_rates\n"
                     "2004-01-31,1.9,3.4,1.0,5.6,100.0,400.0,34.0,1.25\n")
        with gzip.open(os.path.join(rawfix, "borrow/fees/borrow_fees.csv.gz"), "wt") as fh:
            fh.write("date,symbol,borrow_fee_bps,short_interest\n2004-01-02,AAPL,30,100000\n")
        with gzip.open(os.path.join(rawfix, "bybit/usdm/klines/DOGEUSDT/DOGEUSDT-1d-2026-09.csv.gz"),
                       "wt") as fh:
            fh.write("open_time_ms,close\n1788220800000,1\n")
        for sym in symbols:
            # every dataset exists; only BTCUSDT/1d carries the measured 1,720-bar history
            # (the 5m fixture rows sit on the venue's real 300 s grid so the finest-step
            #  measurement is the same shape as the live store)
            for iv in intervals:
                path = os.path.join(rawfix, "binance/usdm/klines", sym, iv,
                                    "%s-%s-2022-01.jsonl.gz" % (sym, iv))
                n = (1720 if (sym == "BTCUSDT" and iv == "1d") else
                     288 if (sym == "BTCUSDT" and iv == "5m") else 2)
                step = 300000 if iv == "5m" else 86400000
                with gzip.open(path, "wt") as fh:
                    for i in range(n):
                        fh.write(json.dumps({"open_time_ms": 1640995200000 + i * step,
                                             "close_time_ms": 1640995200000 + i * step + step - 1,
                                             "open": "1", "high": "1", "low": "1", "close": "1",
                                             "volume": "1"}) + "\n")
            with gzip.open(os.path.join(rawfix, "binance/usdm/funding", sym,
                                        "%s-funding.jsonl.gz" % sym), "wt") as fh:
                n = 5100 if sym == "BTCUSDT" else 2
                for i in range(n):
                    fh.write(json.dumps({"symbol": sym, "venue": "BINANCE",
                                         "market_type": "usdm_perp",
                                         "funding_time_ms": 1640995200000 + i * 28800000,
                                         "funding_rate": "0", "mark_price": "1",
                                         "funding_price_source": "x", "rate_type": "y",
                                         "truth_status": "z"}) + "\n")
        with open(os.path.join(rawfix, "binance/usdm/instruments/usdm-perp-instruments.json"), "w") as fh:
            json.dump({"instruments": [{"fields": {"id": "%s-PERP.BINANCE" % s.replace("USDT", ""),
                                                   "base_currency": s.replace("USDT", ""),
                                                   "quote_currency": "USDT",
                                                   "settlement_currency": "USDT",
                                                   "type": "CryptoPerpetual",
                                                   "maker_fee": "0.0002", "taker_fee": "0.0005",
                                                   "price_increment": "0.10"},
                                        "python_type": "CryptoPerpetual"} for s in symbols]}, fh)
        fix_raw = measure_raw(rawfix)
        res = run_checks(results_root, rawfix, raw=fix_raw, stores=stores, family=family,
                         round_id=round_id, task=task)
        failed = {c["id"] for c in res["checks"] if not c["ok"]}
        expect_flip = ["C5", "C6", "C7", "C8", "C9", "C10", "C11", "C12"]
        must_not_flip = ["C1", "C2", "C3", "C4", "C13", "C14", "C15", "C16", "C17", "C18",
                         "C19", "C20", "C21", "C22", "C23"]
        return {"ok": all(c in failed for c in expect_flip)
                and not any(c in failed for c in must_not_flip),
                "expected_flip": expect_flip, "flipped": sorted(failed),
                "other_flips": sorted(failed - set(expect_flip)),
                "missing_flip": [c for c in expect_flip if c not in failed],
                "unexpected_flip": [c for c in must_not_flip if c in failed],
                "fixture_shape": {"symbols": symbols, "intervals": len(intervals),
                                  "btcusdt_1d_rows": 1720, "funding_rows_btcusdt": 5100,
                                  "instruments": len(symbols)},
                "fixture_flags": {"equity_market_present": fix_raw["equity_market_present"],
                                  "index_universe_series": fix_raw["index_universe_series_present"],
                                  "equity_vendor_series": fix_raw["equity_vendor_series_present"],
                                  "daily_window_covers_required":
                                      fix_raw["daily_window_covers_required"],
                                  "warmup_segment_present": fix_raw["warmup_segment_present"],
                                  "adjusted_price_present": fix_raw["adjusted_price_present"],
                                  "universe_membership_series":
                                      fix_raw["universe_membership_series_present"],
                                  "delisting_history_series":
                                      fix_raw["delisting_history_series_present"],
                                  "fundamental_series": fix_raw["fundamental_series_present"],
                                  "macro_series": fix_raw["macro_series_present"],
                                  "borrow_series": fix_raw["borrow_series_present"],
                                  "calendar_series": fix_raw["calendar_series_present"],
                                  "second_venue_present": fix_raw["second_venue_present"],
                                  "schema_sections": fix_raw["schema_dataset_sections"],
                                  "unexplained_name": fix_raw["probe_hits_unexplained_name"],
                                  "unexplained_payload": fix_raw["probe_hits_unexplained_payload"]}}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _print_checks(res):
    for c in res["checks"]:
        print("%-4s %-4s %s" % (c["id"], "PASS" if c["ok"] else "FAIL", c["name"]))
        if not c["ok"]:
            print("        detail: %s" % json.dumps(c["detail"], ensure_ascii=False)[:400])


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
    ap.add_argument("--other-stores", action="store_true")
    args = ap.parse_args(argv)

    if args.host_scan or args.other_stores:
        stores = measure_host_stores()
        print(json.dumps(stores["totals"], ensure_ascii=False, indent=1))
        print(json.dumps({"pid_gates": stores["pid_gates"],
                          "classes": stores["host_data_hit_classes_used"],
                          "classified": stores["classified_data_hits"],
                          "unclassified": stores["unclassified_data_candidates"]},
                         ensure_ascii=False, indent=1)[:20000])
        return 0

    if args.measure_only:
        raw = measure_raw(args.raw_root)
        print(json.dumps(raw, ensure_ascii=False, indent=1, default=str)[:20000])
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
        print(json.dumps(out, ensure_ascii=False, indent=1)[:20000])
        return 0 if out["ok"] else 1

    if args.raw_fixture_control:
        out = raw_fixture_control(args.results_root, raw, stores)
        print(json.dumps(out, ensure_ascii=False, indent=1)[:8000])
        return 0 if out["ok"] else 1

    res = run_checks(args.results_root, args.raw_root, repo_root=args.repo_root,
                     raw=raw, stores=stores)
    out = _result(res["checks"], raw, stores)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1)[:40000])
    else:
        _print_checks(res)
        print("\nok=%s failed=%s" % (out["ok"], out["failed"]))
        print("raw: %s" % json.dumps(out["raw_summary"], ensure_ascii=False))
        print("stores: %s" % json.dumps(out["stores_summary"], ensure_ascii=False))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
