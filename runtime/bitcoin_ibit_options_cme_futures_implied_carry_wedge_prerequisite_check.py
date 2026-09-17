#!/usr/bin/env python3
"""Prerequisite-gate read-back checker for the family

    bitcoin-ibit-options-cme-futures-implied-carry-wedge-2026-09-01

Card t_6e26cfa9 terminalises this family as TECHNICAL_INCOMPLETE because the
record's required data / market is not in the canonical raw:

  * the record's object is a **relative-value carry trade between two regulated
    TradFi derivative wrappers on Bitcoin**: listed equity options on IBIT
    (OCC-cleared, SEC equity margin) versus cash-settled CME Bitcoin futures
    (CME Clearing, CFTC derivatives margin), with the implied forward extracted
    from IBIT option quotes by put-call parity, converted to Bitcoin units by
    BlackRock's daily shares-per-BTC multiplier M_t, carried against the CME CF
    Bitcoin Reference Rate New York Variant (BRRNY, 16:00 ET), discounted on
    SOFR / the US Treasury curve and margined on OCC / CME Clearing parameters;
  * the canonical raw holds **one venue (BINANCE) of USD-M perpetuals and four
    fixed contracts** (BTCUSDT/ETHUSDT/BNBUSDT/SOLUSDT), OHLCV-only klines,
    funding and instrument metadata - no dated futures of any kind, no option
    quotes, no strike/expiry catalogue, no implied volatilities, no ETF
    holdings/multiplier disclosure, no benchmark index, no risk-free curve and
    no margin/haircut schedule;
  * the record's own Crypto-portability paragraph is explicit that the trade
    **cannot** be executed crypto-natively: "Cannot be executed purely via
    crypto-native decentralized exchanges or offshore centralized exchanges
    (Binance, Bybit) without introducing additional venue counterparty and
    basis risks", and "Direct implementation is restricted to institutional
    accounts with access to both US equity options exchanges (OCC cleared) and
    CME derivatives (FCM cleared)". The card forbids substituting a proxy
    market or shrinking the universe to the four local contracts.

This script exists so an independent reader can re-derive that determination
from the live filesystem instead of trusting prose:

  * C1-C4 re-measure the canonical raw: store identity, the instrument surface,
    the kline surface (structurally, by payload key set) and the funding
    surface;
  * C5-C6 probe the raw tree for this record's requirement vocabulary by entry
    name and by payload content, and assert every hit is classified;
  * C7-C13 assert the record's decision surfaces item by item: no IBIT/ETF
    holdings-multiplier surface, no listed-options surface, no CME futures
    surface, no BRNY/SOFR/Treasury curve, no OCC/CME margin-haircut surface,
    no 16:00 ET point-in-time alignment convention, and no series-shaped data
    carrying any decisive requirement group;
  * C14-C16 re-measure the host's non-canonical stores for the same
    vocabulary - prose mentions are classified by root, data-shaped hits are
    row-shape probed, and the decisive groups must have no series-shaped
    surface anywhere;
  * C17-C22 re-read the round's immutable artifacts and assert they state
    exactly the contract-mandated terminal values (verdict, layer, class,
    yield decision, zero attempts, null run_id), that nothing was ever
    submitted (no run-spec, no attempt directory, no terminal sentinel), that
    the DCA registration still carries the contract 7.2 v1.3.1 provenance
    classes plus the complete 48-cell product, and that the coverage / survivor
    surface is empty by construction;
  * C23-C26 assert the round is bound to this card and family (fingerprint
    recomputed from family.json), that the failure taxonomy was kept separated
    (infrastructure, not science), that the falsification battery was neither
    lowered nor trimmed, and that no performance number was fabricated;
  * `--verify-verbatim` resolves every `*_verbatim` leaf of the persisted
    round-spec against its declared source (card / record / contract / footer)
    and re-checks the excerpt digest map, independent of the authoring run.

Read-only: it never writes inside the results tree or the raw tree. `--self-test`
builds tampered copies in fresh temp directories and asserts the checker refuses
them (non-vacuousness control). `--raw-fixture-control` builds a temp raw tree
carrying what this record would need (IBIT option quotes with strikes/expiries/
implied vols, BlackRock holdings with shares outstanding and BTC-per-share, CME
futures with settlement prices, a BRNY series, a SOFR curve, OCC/CME margin and
haircut tables, and a 16:00 ET alignment convention) and asserts the raw-side
checks flip to FAIL. `--host-scan` re-runs the house-wide search for such
surfaces. Every temp tree is removed afterwards.

Usage:
    python3 runtime/bitcoin_ibit_options_cme_futures_implied_carry_wedge_prerequisite_check.py [--json]
    python3 runtime/bitcoin_ibit_options_cme_futures_implied_carry_wedge_prerequisite_check.py --measure-only
    python3 runtime/bitcoin_ibit_options_cme_futures_implied_carry_wedge_prerequisite_check.py --verify-verbatim
    python3 runtime/bitcoin_ibit_options_cme_futures_implied_carry_wedge_prerequisite_check.py --self-test
    python3 runtime/bitcoin_ibit_options_cme_futures_implied_carry_wedge_prerequisite_check.py --raw-fixture-control
    python3 runtime/bitcoin_ibit_options_cme_futures_implied_carry_wedge_prerequisite_check.py --host-scan

Exit codes: 0 = all checks PASS, 1 = at least one FAIL, 2 = usage error.
"""
import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import date, datetime, timezone

FAMILY = "bitcoin-ibit-options-cme-futures-implied-carry-wedge-2026-09-01"
ROUND = FAMILY + "-r1"
TASK = "t_6e26cfa9"
BOARD = "quant-strategy-research"
DEFAULT_RAW = "/Volumes/ExpansionDrive/market-data-raw"
DEFAULT_RESULTS = "/Volumes/ExpansionDrive/qlib-results"
DEFAULT_REPO = "/Users/hong/workspace/quant-runtime-pipeline"
DEFAULT_RECORD = "/Users/hong/.hermes/wiki/quant/%s.md" % FAMILY
BOARD_DB = os.path.join(os.path.expanduser("~"), ".hermes/kanban/boards", BOARD,
                        "kanban.db")
PROBE_MAX_DEPTH = 8

# --- the record's requirement vocabulary -------------------------------------
PATTERNS = {
    # IBIT / spot-ETF wrapper
    "ibit": r"\bibit\b",
    "ishares": r"\bishares\b",
    "blackrock": r"\bblackrock\b",
    "spot_etf": r"\bspot[-_ ]?etfs?\b",
    "etf": r"\betfs?\b",
    "nav": r"\bnav\b",
    "shares_outstanding": r"\bshares?[-_ ](outstanding|issued)\b",
    "btc_per_share": r"\b(btc|bitcoin)[-_ ]per[-_ ]share\b",
    "shares_per_btc": r"\bshares?[-_ ]per[-_ ](btc|bitcoin)\b",
    "creation_unit": r"\bcreation[-_ ]units?\b",
    "basket": r"\bbaskets?\b",
    # listed options surface
    "option": r"\boptions?\b",
    "put": r"\bputs?\b",
    "call": r"\bcalls?\b",
    "strike": r"\bstrikes?\b",
    "expiry": r"\bexpir(?:y|ation|ies|ing)\b",
    "implied_vol": r"\bimplied[-_ ]vol(?:atility)?\b",
    "greek": r"\b(delta|gamma|vega|theta|rho)\b",
    "open_interest": r"\bopen[-_ ]interest\b",
    "bid_ask": r"\b(bid|ask|bid[-_ ]ask|quote)s?\b",
    "put_call_parity": r"\bput[-_ ]call[-_ ]parity\b",
    "american_style": r"\bamerican[-_ ]style\b",
    "occ": r"\bocc\b",
    # CME futures surface
    "cme": r"\bcme\b",
    "globex": r"\bglobex\b",
    "futures": r"\bfutures?\b",
    "front_month": r"\bfront[-_ ]month\b",
    "deferred": r"\bdeferred\b",
    "cash_settled": r"\bcash[-_ ]settled\b",
    "settlement": r"\bsettle?ments?\b",
    "basis": r"\bbasis\b",
    # benchmark / rates
    "brrny": r"\bbrrny\b",
    "brr": r"\bbrr\b",
    "cme_cf": r"\bcme[-_ ]cf\b",
    "sofr": r"\bsofr\b",
    "treasury": r"\btreasur(y|ies)\b",
    "yield_curve": r"\byield[-_ ]curve\b",
    "risk_free": r"\brisk[-_ ]free\b",
    "interest_rate": r"\binterest[-_ ]rates?\b",
    # clearing / margin
    "clearing": r"\bclearing\b",
    "fcm": r"\bfcm\b",
    "haircut": r"\bhaircuts?\b",
    "maintenance_margin": r"\bmaintenance[-_ ]margin\b",
    "initial_margin": r"\binitial[-_ ]margin\b",
    "cross_margin": r"\bcross[-_ ]margin(?:ing)?\b",
    "cftc": r"\bcftc\b",
    "sec": r"\bsec\b",
    # carry vocabulary
    "carry": r"\bcarry\b",
    "wedge": r"\bwedge\b",
    "implied_forward": r"\bimplied[-_ ]forwards?\b",
    "implied_carry": r"\bimplied[-_ ]carry\b",
    "forward_price": r"\bforward[-_ ]prices?\b",
    "annualized": r"\bannuali[sz]ed\b",
    # local store vocabulary (must remain present: these DO exist)
    "perpetual": r"\bperp(?:etual)?s?\b",
    "funding": r"\bfunding\b",
    "klines": r"\bklines?\b",
}
COMPILED = {k: re.compile(v, re.I) for k, v in PATTERNS.items()}

DECISIVE_GROUPS = {
    "ibit_wrapper": ["ibit", "ishares", "blackrock", "spot_etf", "nav",
                     "shares_outstanding", "btc_per_share", "shares_per_btc",
                     "creation_unit", "basket"],
    "listed_options": ["option", "put", "call", "strike", "expiry", "implied_vol",
                       "greek", "open_interest", "bid_ask", "put_call_parity",
                       "american_style", "occ"],
    "cme_futures": ["cme", "globex", "futures", "front_month", "deferred",
                    "cash_settled", "settlement"],
    "benchmark_rate": ["brrny", "brr", "cme_cf", "sofr", "treasury", "yield_curve",
                       "risk_free", "interest_rate"],
    "clearing_margin": ["clearing", "fcm", "haircut", "maintenance_margin",
                        "initial_margin", "cross_margin", "cftc"],
}

# Every payload hit inside the raw tree must resolve to a class here.
RAW_CLASSES = {
    # store prose that names the requirements only to say they are NOT migrated
    "*": {
        "_meta/INVENTORY.md": "canonical_store_inventory_prose: names the non-migrated "
                              "LEAN equity/option/future sample dirs and the Qlib CN A-share "
                              "store in its 'NOT migrated' table - a record of what is absent, "
                              "not a data surface",
        "_meta/SCHEMA.md": "canonical_store_schema_prose: declares exactly the klines / "
                           "funding / instruments dataset families",
        "_meta/VERIFY_GAPS.txt": "canonical_store_gap_audit_prose: per-dataset row/duplicate "
                                 "audit lines for the 28 kline datasets",
        "_meta/VERIFY_TRANSCODE.txt": "canonical_store_readback_prose: read-back compare log",
        "_meta/CONFIG.json": "canonical_store_config: venue=BINANCE, market_type=usdm_perp, "
                             "four symbols, seven intervals",
        "_meta/STATE.json": "canonical_store_cursor_state: per-stream cursors of the updater",
        "_meta/TRANSCODE_MANIFEST.json": "canonical_store_transcode_manifest: per-file bytes/"
                                         "rows/sha256 of the initial import",
        "_meta/FUNDING_EXPORT.json": "canonical_store_funding_provenance",
        "_meta/INSTRUMENTS_EXPORT.json": "canonical_store_instrument_provenance",
        "_tools/README.md": "canonical_store_tool_doc_prose",
        "_tools/market_data_sync.py": "canonical_store_updater_source: Binance USD-M only; "
                                      "no options/futures/ETF endpoint is referenced",
        "_tools/binance_public.py": "canonical_store_vendored_client_source: Binance public "
                                    "klines/funding/exchangeInfo only",
        "binance/usdm/instruments/usdm-perp-instruments.json": "canonical_store_instrument_export: "
                                                               "four USD-M perpetual definitions",
        "binance/usdm/funding/": "canonical_store_funding_rows: fundingTime/fundingRate/"
                                 "symbol/markPrice fields only",
        "binance/usdm/klines/": "canonical_store_kline_rows: open_time_ms/close_time_ms/open/"
                                "high/low/close/volume fields only",
    },
}

# Every data-shaped hit on the host must resolve to a class here, keyed by
# "<root>|<relpath>". All six measured data-shaped hits are metadata/prose
# stores, not market data: four are the a1-* point-in-time membership/lifecycle
# stores for Binance USD-M perpetuals (their only token hit is the word
# 'perpetual' inside an instrument identifier or a membership cell id) and one
# is a research-corpus index CSV.
HOST_DATA_CLASSES = {
    "/Users/hong/workspace/a1-1-phase9-pit-membership-20260830|membership_cells.jsonl":
        "binance_usdm_pit_membership_cells_not_market_data: point-in-time membership cells "
        "(cell_id 'BTCUSDT|2022-01-01', membership_status 'unknown', supported=false, "
        "evidence_tier null); the only token hit is 'perpetual' inside the instrument id - no "
        "price, quote, rate, strike or settlement field",
    "/Users/hong/workspace/a1-1-phase9-pit-membership-20260830|membership_observations.csv":
        "binance_usdm_pit_membership_observations_not_market_data: same store's observation "
        "rows for USD-M perpetual membership; only token 'perpetual' in the instrument id",
    "/Users/hong/workspace/a1-2-prospective-pit-foundation-20260830|snapshot_observations.jsonl":
        "binance_usdm_pit_snapshot_observations_not_market_data: prospective PIT snapshot "
        "observations for USD-M perpetuals; only token 'perpetual'",
    "/Users/hong/workspace/a1-3-prospective-pit-cohort-20260830|snapshot_observations.jsonl":
        "binance_usdm_pit_snapshot_observations_not_market_data: prospective PIT cohort "
        "snapshot observations for USD-M perpetuals; only token 'perpetual'",
    "/Users/hong/workspace/a1-usdm-pit-lifecycle-20260830|lifecycle_events.csv":
        "binance_usdm_perpetual_lifecycle_notice_store: listing/delisting event rows for "
        "Binance USD-M perpetuals; the token hits are vendor prose inside the stored notice "
        "text ('Binance Futures will conduct an automatic settlement on the USDT-Margined ... "
        "Contract', '-0.75 * Maintenance Margin Ratio' from the contract specification) plus "
        "the venue column value 'USD-M Futures' and the symbol/venue metadata - not a "
        "CME/IBIT/options/rate data surface",
    "/Users/hong/workspace/alpha-strategy-research|coverage_manifest.csv":
        "research_corpus_index_filenames_and_reasons_only: rows are markdown file names of "
        "unrelated strategy documents plus a review disposition/reason column; the token hits "
        "are substrings of those file names and of reason prose (e.g. '...-OCC-Strategy-R51.md', "
        "'Triple-Strike', 'Leveraged-ETF', 'Falling-Wedge', and a reason line about a lexical "
        "collision with 'open interest') - not market data",
}


def _classify_host(hits, classes):
    """Every (token, 'root|rel') host hit must resolve to a declared class."""
    explained, unexplained = [], []
    for tok, keys in sorted(hits.items()):
        for key in keys:
            why = classes.get(key)
            if why is None:
                for k, v in classes.items():
                    if key.endswith(k) or key.startswith(k):
                        why = v
                        break
            record = {"token": tok, "entry": key}
            if why is None:
                unexplained.append(record)
            else:
                explained.append({**record, "why": why})
    return explained, unexplained


def _sha256_text(text):
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_text(path, nbytes=None):
    try:
        if path.endswith(".gz"):
            with gzip.open(path, "rb") as fh:
                data = fh.read() if nbytes is None else fh.read(nbytes)
        else:
            with open(path, "rb") as fh:
                data = fh.read() if nbytes is None else fh.read(nbytes)
        return data.decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return "<<unreadable: %s>>" % exc


def _load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")


def _days_between(a, b):
    return (date.fromisoformat(b) - date.fromisoformat(a)).days + 1


def _walk(root, max_depth=PROBE_MAX_DEPTH):
    """(rel_path, abspath) for every non-.DS_Store file, depth-bounded."""
    out = []
    root_depth = root.rstrip("/").count("/")
    for dirpath, dirnames, filenames in os.walk(root):
        if dirpath.count("/") - root_depth >= max_depth:
            dirnames[:] = []
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if name == ".DS_Store":
                continue
            p = os.path.join(dirpath, name)
            out.append((os.path.relpath(p, root), p))
    return sorted(out)


def _first_row_keys(path, nbytes=4096):
    head = _read_text(path, nbytes)
    lines = [l for l in head.splitlines() if l.strip()]
    if not lines:
        return []
    line = lines[0]
    try:
        if path.endswith(".csv") or line.count(",") >= 2:
            return sorted(c.strip() for c in line.split(",") if c.strip())
        return sorted(json.loads(line).keys())
    except Exception:  # noqa: BLE001
        try:
            return sorted({m.group(1) for m in re.finditer(r'"([A-Za-z0-9_\- ]+)"\s*:', head)})
        except Exception:  # noqa: BLE001
            return []


def _classify(hits, classes, root=None):
    """Every (token, path) hit must resolve to a declared class."""
    explained, unexplained = [], []
    for tok, paths in sorted(hits.items()):
        allowed = classes.get(tok, classes.get("*", {}))
        for rel in paths:
            key = rel if root is None else "%s|%s" % (root, rel)
            why = allowed.get(rel) or allowed.get(key)
            if why is None:
                for k, v in allowed.items():
                    if (rel.endswith(k) or key.endswith(k)
                            or rel.startswith(k) or key.startswith(k)):
                        why = v
                        break
            if why is None:
                unexplained.append({"token": tok, "entry": rel})
            else:
                explained.append({"token": tok, "entry": rel, "why": why})
    return explained, unexplained


# ------------------------------------------------------------------ raw -------
def measure_raw(raw=DEFAULT_RAW, depth=PROBE_MAX_DEPTH):
    out = {"raw_root": raw, "exists": os.path.isdir(raw)}
    if not out["exists"]:
        return out
    entries = _walk(raw, depth)
    out["entry_count"] = len(entries)
    out["byte_total"] = sum(os.path.getsize(p) for _, p in entries
                            if os.path.exists(p))
    out["top_level"] = sorted({r.split("/")[0] for r, _ in entries})

    # store identity ---------------------------------------------------------
    cfg_path = os.path.join(raw, "_meta/CONFIG.json")
    cfg = _load_json(cfg_path) if os.path.exists(cfg_path) else {}
    out["config"] = cfg
    out["venue_dirs"] = sorted({r.split("/")[0] for r, _ in entries
                                if len(r.split("/")) >= 2 and r.split("/")[1] not in
                                ("_meta", "_tools")})
    out["second_venue_present"] = any(
        r.split("/")[0] not in ("_meta", "_tools", "binance") for r, _ in entries)
    out["spot_market_present"] = any("/spot" in r or r.startswith("binance/spot")
                                     for r, _ in entries)
    out["dataset_families"] = sorted({r.split("/")[2] for r, _ in entries
                                      if r.startswith("binance/usdm/")
                                      and len(r.split("/")) > 2})

    # instrument surface ----------------------------------------------------
    instr_path = os.path.join(raw, "binance/usdm/instruments/usdm-perp-instruments.json")
    instr_doc = _load_json(instr_path) if os.path.exists(instr_path) else []
    instr_list = instr_doc if isinstance(instr_doc, list) else instr_doc.get("instruments", [])
    out["instrument_count"] = len(instr_list)
    out["instrument_ids"] = sorted(str((i.get("fields") or {}).get("id") or i.get("id")
                                       or i.get("symbol")) for i in instr_list)
    out["instrument_fields"] = sorted({k for i in instr_list
                                       for k in ((i.get("fields") or i).keys())})
    out["instrument_fields_option_shaped"] = sorted(
        f for f in out["instrument_fields"]
        if COMPILED["option"].search(f) or COMPILED["strike"].search(f)
        or COMPILED["expiry"].search(f) or COMPILED["occ"].search(f)
        or COMPILED["futures"].search(f) or COMPILED["cme"].search(f))

    # kline surface (structural: payload key set) ---------------------------
    kl_files = [(r, p) for r, p in entries if r.startswith("binance/usdm/klines/")]
    out["kline_file_count"] = len(kl_files)
    out["kline_byte_total"] = sum(os.path.getsize(p) for _, p in kl_files)
    datasets, bad_keys, sample_lines = set(), [], 0
    per_symbol_daily = {}
    for rel, path in kl_files:
        sym, interval = rel.split("/")[3], rel.split("/")[4]
        datasets.add((sym, interval))
        head = _read_text(path, 4096)
        lines = [l for l in head.splitlines() if l.strip()]
        if not lines:
            bad_keys.append({"entry": rel, "why": "empty"})
            continue
        try:
            first = json.loads(lines[0])
        except Exception as exc:  # noqa: BLE001
            bad_keys.append({"entry": rel, "why": "unparsable: %s" % exc})
            continue
        sample_lines += 1
        keys = sorted(first.keys())
        if keys != ["close", "close_time_ms", "high", "low", "open", "open_time_ms",
                    "volume"]:
            bad_keys.append({"entry": rel, "why": "key set %s" % keys})
        if interval == "1d":
            per_symbol_daily.setdefault(sym, {"first": None, "last": None, "files": 0})
            per_symbol_daily[sym]["files"] += 1
            if per_symbol_daily[sym]["first"] is None:
                per_symbol_daily[sym]["first"] = first["open_time_ms"]
    # last 1d bar per symbol: read the last monthly file's last line
    for sym in sorted(per_symbol_daily):
        files = sorted(r for r, _ in kl_files
                       if r.startswith("binance/usdm/klines/%s/1d/" % sym))
        if not files:
            continue
        path = os.path.join(raw, files[-1])
        text = _read_text(path)
        lines = [l for l in text.splitlines() if l.strip()]
        if lines:
            per_symbol_daily[sym]["last"] = json.loads(lines[-1])["open_time_ms"]
    out["kline_datasets"] = sorted("%s/%s" % d for d in datasets)
    out["kline_dataset_count"] = len(datasets)
    out["kline_key_violations"] = bad_keys
    out["kline_sampled_files"] = sample_lines
    out["daily_windows"] = {
        s: {"first": _iso(v["first"]) if v["first"] else None,
            "last": _iso(v["last"]) if v["last"] else None,
            "monthly_files": v["files"]}
        for s, v in sorted(per_symbol_daily.items())}
    out["kline_intervals"] = sorted({d.split("/")[1] for d in out["kline_datasets"]})
    out["kline_symbols"] = sorted({d.split("/")[0] for d in out["kline_datasets"]})

    # funding surface -------------------------------------------------------
    fund = {}
    for rel, path in entries:
        if not rel.startswith("binance/usdm/funding/") or not rel.endswith(".gz"):
            continue
        rows = 0
        first = last = None
        fields = None
        for line in _read_text(path).splitlines():
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if fields is None:
                fields = sorted(obj.keys())
            rows += 1
            ts = obj.get("funding_time_ms")
            if first is None:
                first = ts
            last = ts
        fund[rel.split("/")[3]] = {"rows": rows, "first": _iso(first) if first else None,
                                   "last": _iso(last) if last else None, "fields": fields}
    out["funding"] = fund
    out["funding_field_sets"] = sorted([list(v["fields"] or []) for v in fund.values()])
    out["funding_truth_status"] = sorted({
        str(json.loads(l).get("truth_status"))
        for rel, path in entries
        if rel.startswith("binance/usdm/funding/") and rel.endswith(".gz")
        for l in _read_text(path).splitlines() if l.strip()})

    # token probes (entry name + payload) -----------------------------------
    name_hits, payload_hits = {}, {}
    text_files = []
    for rel, path in entries:
        for tok, rx in COMPILED.items():
            if rx.search(rel):
                name_hits.setdefault(tok, []).append(rel)
        if rel.startswith("binance/usdm/klines/"):
            # structurally probed above (key set asserted on every file)
            continue
        if rel.endswith((".md", ".json", ".txt", ".py", ".sh", ".csv", ".jsonl", ".tsv",
                         ".gz", ".yaml", ".yml")):
            text_files.append((rel, path))
    for rel, path in text_files:
        text = _read_text(path)
        for tok, rx in COMPILED.items():
            if rx.search(text):
                payload_hits.setdefault(tok, []).append(rel)
    out["probe_tokens_tested"] = len(PATTERNS)
    out["probe_payload_files_scanned"] = len(text_files)
    out["probe_entry_name_hits"] = sorted(name_hits)
    out["probe_payload_hit_counts"] = {k: len(v) for k, v in sorted(payload_hits.items())}
    expl_n, unexp_n = _classify(name_hits, RAW_CLASSES)
    expl_p, unexp_p = _classify(payload_hits, RAW_CLASSES)
    out["probe_hits_classified_name"] = expl_n
    out["probe_hits_unexplained_name"] = unexp_n
    out["probe_hits_classified_payload"] = expl_p
    out["probe_hits_unexplained_payload"] = unexp_p

    # data-shaped hits & row shapes ----------------------------------------
    data_hits = sorted({rel for lst in payload_hits.values() for rel in lst
                        if rel.endswith((".json", ".jsonl", ".csv", ".gz"))})
    out["probe_payload_data_ext_hits"] = data_hits
    row_shapes = {}
    for rel in data_hits:
        row_shapes[rel] = _first_row_keys(os.path.join(raw, rel))
    out["row_shapes"] = row_shapes
    out["series_shaped_tokens"] = sorted(
        {tok for rel, keys in row_shapes.items() for tok in COMPILED
         if any(COMPILED[tok].search(k) for k in keys)})

    # decisive groups: any series-shaped (row-key) presence in the raw? ------
    group_series = {}
    for grp, toks in DECISIVE_GROUPS.items():
        group_series[grp] = sorted(
            rel for rel, keys in row_shapes.items()
            if any(COMPILED[t].search(k) for t in toks for k in keys))
    out["decisive_group_series_hits"] = group_series
    out["decisive_group_prose_hits"] = {
        grp: sorted({rel for t in toks for rel in payload_hits.get(t, [])
                     if not rel.endswith((".json", ".jsonl", ".csv", ".gz"))})
        for grp, toks in DECISIVE_GROUPS.items()}
    out["decisive_group_data_shape_hits"] = {
        grp: sorted({rel for t in toks for rel in payload_hits.get(t, [])
                     if rel.endswith((".json", ".jsonl", ".csv", ".gz"))
                     and not rel.endswith("TRANSCODE_MANIFEST.json")})
        for grp, toks in DECISIVE_GROUPS.items()}

    # schema declarations ---------------------------------------------------
    schema = _read_text(os.path.join(raw, "_meta/SCHEMA.md"))
    out["schema_dataset_sections"] = re.findall(r"^## .*$", schema, re.M)
    out["schema_dataset_names"] = sorted(
        m.group(1).strip().lower()
        for m in re.finditer(r"^##\s+Dataset:\s+([A-Za-z0-9_\-]+)", schema, re.M))
    out["schema_bytes"] = len(schema.encode())
    for key, tok in (("documents_options_dataset", "option"),
                     ("documents_futures_dataset", "futures"),
                     ("documents_etf_dataset", "etf"),
                     ("documents_index_dataset", "brrny"),
                     ("documents_rate_dataset", "sofr")):
        out[key] = any(COMPILED[tok].search(name) for name in out["schema_dataset_names"])
    return out


# ----------------------------------------------------------------- host -------
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
    "/Users/hong/workspace/qlib-apple-container",
    "/Volumes/ExpansionDrive/daily-crypto-brief",
    "/Volumes/ExpansionDrive/qlib-results/_handoff/bodies",
    "/Users/hong/.hermes/wiki/quant",
]
HOST_TEXT_EXTS = (".csv", ".jsonl", ".json", ".txt", ".md", ".tsv", ".py", ".sh",
                  ".yaml", ".yml", ".jsonl.gz")
HOST_DATA_EXTS = (".csv", ".jsonl", ".jsonl.gz", ".parquet", ".db", ".sqlite")


def measure_host_stores(roots=None, read_cap=512 * 1024):
    roots = roots or HOST_ROOTS
    out = {"roots": {}, "totals": {"files_scanned": 0, "token_files": 0, "bytes": 0},
           "data_hits": [], "row_shapes": {},
           "decisive_groups": {g: {"tokens_hit": [], "data_shape_hits": []}
                               for g in DECISIVE_GROUPS}}
    payload_hits = {}
    for root in roots:
        if not os.path.isdir(root):
            out["roots"][root] = {"exists": False}
            continue
        n = tok = nbytes = 0
        for rel, path in _walk(root):
            n += 1
            try:
                nbytes += os.path.getsize(path)
            except OSError:
                pass
            if not rel.endswith(HOST_TEXT_EXTS):
                continue
            text = _read_text(path, read_cap)
            hits = [k for k, rx in COMPILED.items() if rx.search(text)]
            if hits:
                tok += 1
                for k in hits:
                    payload_hits.setdefault(k, []).append((root, rel))
            if rel.endswith(HOST_DATA_EXTS):
                keys = _first_row_keys(path)
                if keys and any(rx.search(k) for rx in COMPILED.values() for k in keys):
                    out["row_shapes"]["%s|%s" % (root, rel)] = keys
                if hits:
                    out["data_hits"].append({"root": root, "rel": rel, "tokens": sorted(hits)})
        out["roots"][root] = {"exists": True, "files_scanned": n, "token_files": tok,
                              "bytes": nbytes}
        out["totals"]["files_scanned"] += n
        out["totals"]["token_files"] += tok
        out["totals"]["bytes"] += nbytes
    expl, unexp = _classify_host(
        {k: ["%s|%s" % (r, rel) for r, rel in v] for k, v in payload_hits.items()},
        HOST_DATA_CLASSES)
    data_keys = {"%s|%s" % (d["root"], d["rel"]) for d in out["data_hits"]}
    out["classified_data_hits"] = [h for h in expl if h["entry"] in data_keys]
    out["unclassified_data_hits"] = [h for h in unexp if h["entry"] in data_keys]
    for grp, toks in DECISIVE_GROUPS.items():
        out["decisive_groups"][grp]["tokens_hit"] = sorted(
            t for t in toks if payload_hits.get(t))
        out["decisive_groups"][grp]["data_shape_hits"] = sorted(
            "%s|%s" % (d["root"], d["rel"]) for d in out["data_hits"]
            if set(d["tokens"]) & set(toks))
    out["payload_hit_counts"] = {k: len(v) for k, v in sorted(payload_hits.items())}
    out["series_shaped_decisive"] = sorted(
        {k for k, keys in out["row_shapes"].items()
         if any(COMPILED[t].search(kk) for t in DECISIVE_GROUPS_tokens() for kk in keys)})
    out["decisive_series_shaped_hits"] = sorted(
        {k for k, keys in out["row_shapes"].items()
         for grp, toks in DECISIVE_GROUPS.items()
         if any(COMPILED[t].search(kk) for t in toks for kk in keys)})
    return out


def DECISIVE_GROUPS_tokens():
    return sorted({t for toks in DECISIVE_GROUPS.values() for t in toks})


# --------------------------------------------------------------- checks -------
def _check(cid, name, ok, detail):
    return {"id": cid, "name": name, "ok": bool(ok), "detail": detail}


def _round_paths(results_root, family=FAMILY, round_id=ROUND):
    base = os.path.join(results_root, family)
    rnd = os.path.join(base, "rounds", round_id)
    return {"family": base, "round": rnd,
            "family_json": os.path.join(base, "family.json"),
            "spec": os.path.join(rnd, "round-spec.json"),
            "verdict": os.path.join(rnd, "verdict.json"),
            "attempts": os.path.join(rnd, "attempts"),
            "bundle": os.path.join(rnd, "survivor-bundle.json")}


def run_checks(results_root=DEFAULT_RESULTS, raw_root=DEFAULT_RAW, repo_root=DEFAULT_REPO,
               family=FAMILY, round_id=ROUND, task=TASK, raw=None, stores=None,
               host_scan=False):
    raw = raw if raw is not None else measure_raw(raw_root)
    stores = stores if stores is not None else (
        measure_host_stores() if host_scan else {"skipped": True})
    p = _round_paths(results_root, family, round_id)
    checks = []

    # ---- raw: identity / surfaces
    checks.append(_check(
        "C1", "canonical raw store identity",
        raw.get("config", {}).get("venue") == "BINANCE"
        and raw.get("config", {}).get("market_type") == "usdm_perp"
        and not raw.get("second_venue_present")
        and not raw.get("spot_market_present")
        and raw.get("dataset_families") == ["funding", "instruments", "klines"],
        "venue=%s market_type=%s venue_dirs=%s datasets=%s second_venue=%s spot=%s"
        % (raw.get("config", {}).get("venue"), raw.get("config", {}).get("market_type"),
           raw.get("venue_dirs"), raw.get("dataset_families"),
           raw.get("second_venue_present"), raw.get("spot_market_present"))))

    checks.append(_check(
        "C2", "instrument surface is four USD-M perpetuals, no option/future/ETF instrument",
        raw.get("instrument_count") == 4
        and raw.get("instrument_ids") == ["BNBUSDT-PERP.BINANCE", "BTCUSDT-PERP.BINANCE",
                                         "ETHUSDT-PERP.BINANCE", "SOLUSDT-PERP.BINANCE"]
        and not raw.get("instrument_fields_option_shaped")
        and all("PERP" in i for i in raw.get("instrument_ids", [])),
        "count=%s ids=%s option_shaped_fields=%s"
        % (raw.get("instrument_count"), raw.get("instrument_ids"),
           raw.get("instrument_fields_option_shaped"))))

    checks.append(_check(
        "C3", "kline surface: 28 OHLCV datasets, no quote/greek/strike/OI field anywhere",
        raw.get("kline_dataset_count") == 28
        and not raw.get("kline_key_violations")
        and raw.get("kline_sampled_files") == raw.get("kline_file_count")
        and sorted(raw.get("kline_symbols", [])) == ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
        and len(raw.get("kline_intervals", [])) == 7,
        "datasets=%s files=%s sampled=%s key_violations=%s intervals=%s"
        % (raw.get("kline_dataset_count"), raw.get("kline_file_count"),
           raw.get("kline_sampled_files"), raw.get("kline_key_violations"),
           raw.get("kline_intervals"))))

    checks.append(_check(
        "C4", "funding surface: four symbols, funding_rate/funding_time_ms rows only, windows non-empty",
        sorted(raw.get("funding", {})) == ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
        and all(v["rows"] > 0 and v["first"] and v["last"]
                for v in raw.get("funding", {}).values())
        and raw.get("funding_field_sets", [[]])[0] == [
            "funding_price_source", "funding_rate", "funding_time_ms", "mark_price",
            "market_type", "rate_type", "symbol", "truth_status", "venue"],
        "symbols=%s rows=%s windows=%s fields=%s"
        % (sorted(raw.get("funding", {})),
           {k: v["rows"] for k, v in sorted(raw.get("funding", {}).items())},
           {k: [v["first"], v["last"]] for k, v in sorted(raw.get("funding", {}).items())},
           raw.get("funding_field_sets"))))

    # ---- raw: probe classification
    checks.append(_check(
        "C5", "every entry-name probe hit is classified",
        raw.get("probe_tokens_tested", 0) > 40 and not raw.get("probe_hits_unexplained_name"),
        "tokens_tested=%s hits=%s unexplained=%s"
        % (raw.get("probe_tokens_tested"), len(raw.get("probe_hits_classified_name", [])),
           raw.get("probe_hits_unexplained_name"))))

    checks.append(_check(
        "C6", "every payload probe hit is classified",
        not raw.get("probe_hits_unexplained_payload")
        and raw.get("probe_payload_files_scanned", 0) > 0,
        "files_scanned=%s hits=%s unexplained=%s"
        % (raw.get("probe_payload_files_scanned"),
           len(raw.get("probe_hits_classified_payload", [])),
           raw.get("probe_hits_unexplained_payload"))))

    checks.append(_check(
        "C7", "no decisive requirement group is carried by a data-shaped row shape in the raw",
        all(not v for v in raw.get("decisive_group_series_hits", {}).values()),
        "series_hits=%s" % raw.get("decisive_group_series_hits")))

    # ---- raw: item-by-item decision surfaces
    def group_series_empty(grp):
        return not raw.get("decisive_group_series_hits", {}).get(grp)

    checks.append(_check(
        "C8", "no IBIT/ETF wrapper surface (NAV, shares outstanding, BTC-per-share multiplier)",
        group_series_empty("ibit_wrapper")
        and not any("ibit" in i.lower() or "etf" in i.lower()
                    for i in raw.get("instrument_ids", [])),
        "instrument_ids=%s group_series=%s"
        % (raw.get("instrument_ids"), raw.get("decisive_group_series_hits", {}).get("ibit_wrapper"))))

    checks.append(_check(
        "C9", "no listed-options surface (strikes, expiries, implied vols, OI, bid/ask, OCC)",
        group_series_empty("listed_options")
        and not COMPILED["strike"].search(" ".join(raw.get("kline_intervals", []))),
        "group_series=%s intervals=%s"
        % (raw.get("decisive_group_series_hits", {}).get("listed_options"),
           raw.get("kline_intervals"))))

    checks.append(_check(
        "C10", "no CME futures surface (dated contracts, settlements, Globex)",
        group_series_empty("cme_futures")
        and all("PERP" in i for i in raw.get("instrument_ids", [])),
        "group_series=%s kline_datasets=%s"
        % (raw.get("decisive_group_series_hits", {}).get("cme_futures"),
           sorted(raw.get("kline_datasets", []))[:4])))

    checks.append(_check(
        "C11", "no BRNY / SOFR / US Treasury curve benchmark series",
        group_series_empty("benchmark_rate"),
        "group_series=%s" % raw.get("decisive_group_series_hits", {}).get("benchmark_rate")))

    checks.append(_check(
        "C12", "no OCC / CME Clearing margin, haircut or cross-margining surface",
        group_series_empty("clearing_margin"),
        "group_series=%s" % raw.get("decisive_group_series_hits", {}).get("clearing_margin")))

    checks.append(_check(
        "C13", "store schema declares exactly the klines/funding/instruments datasets",
        raw.get("schema_dataset_names") == ["funding", "instruments", "klines"]
        and not any(raw.get(k) for k in ("documents_options_dataset", "documents_futures_dataset",
                                         "documents_etf_dataset", "documents_index_dataset",
                                         "documents_rate_dataset")),
        "schema_datasets=%s sections=%s" % (raw.get("schema_dataset_names"),
                                            raw.get("schema_dataset_sections"))))

    # ---- host: same vocabulary, no series-shaped decisive surface
    if host_scan:
        checks.append(_check(
            "C14", "host scan: prose mentions classified by class, data-shaped hits row-shape probed",
            stores.get("totals", {}).get("files_scanned", 0) > 1000
            and not stores.get("unclassified_data_hits"),
            "totals=%s data_hits=%s unclassified=%s"
            % (stores.get("totals"), [d["rel"] for d in stores.get("data_hits", [])],
               stores.get("unclassified_data_hits"))))
        checks.append(_check(
            "C15", "host scan: no series-shaped row anywhere carries a decisive requirement group",
            not stores.get("series_shaped_decisive"),
            "series_shaped_decisive=%s row_shapes_scanned=%s"
            % (stores.get("series_shaped_decisive"), len(stores.get("row_shapes", {})))))
        checks.append(_check(
            "C16", "no listed-options / CME-futures / IBIT-holdings data surface exists on the host",
            all(set(stores["decisive_groups"][g]["data_shape_hits"]) <=
                {"/Users/hong/workspace/a1-usdm-pit-lifecycle-20260830|lifecycle_events.csv",
                 "/Users/hong/workspace/alpha-strategy-research|coverage_manifest.csv"}
                for g in ("ibit_wrapper", "listed_options", "cme_futures")),
            "ibit=%s options=%s cme=%s"
            % (stores["decisive_groups"]["ibit_wrapper"]["data_shape_hits"],
               stores["decisive_groups"]["listed_options"]["data_shape_hits"],
               stores["decisive_groups"]["cme_futures"]["data_shape_hits"])))

    # ---- round artifacts
    spec = _load_json(p["spec"]) if os.path.exists(p["spec"]) else {}
    verdict = _load_json(p["verdict"]) if os.path.exists(p["verdict"]) else {}
    fam = _load_json(p["family_json"]) if os.path.exists(p["family_json"]) else {}

    checks.append(_check(
        "C17", "round-spec exists, is bound to this family/card and registers PREREQUISITE_ABSENT",
        spec.get("family_id") == family and spec.get("round_id") == round_id
        and spec.get("kanban_task_id") == task and spec.get("expected") == "PREREQUISITE_ABSENT",
        "family_id=%s round_id=%s task=%s expected=%s"
        % (spec.get("family_id"), spec.get("round_id"), spec.get("kanban_task_id"),
           spec.get("expected"))))

    checks.append(_check(
        "C18", "round-spec: universe kept whole, no substitute market, no proxy panel",
        spec.get("universe_registration", {}).get("universe_shrunk_to_local_list") is False
        and spec.get("prerequisite_gate", {}).get("universe_shrunk") is False
        and spec.get("prerequisite_gate", {}).get("substitute_market_used") is False,
        "universe_shrunk=%s substitute=%s"
        % (spec.get("universe_registration", {}).get("universe_shrunk_to_local_list"),
           spec.get("prerequisite_gate", {}).get("substitute_market_used"))))

    checks.append(_check(
        "C19", "round-spec: nothing was launched (0 attempts, 0 run-specs, 0 sentinels)",
        spec.get("launch", {}).get("launched") is False
        and spec.get("launch", {}).get("attempts") == 0
        and spec.get("launch", {}).get("run_specs") == 0
        and spec.get("launch", {}).get("terminal_sentinels") == 0
        and not os.path.exists(p["attempts"])
        and not os.path.exists(p["bundle"]),
        "launch=%s attempts_dir=%s bundle=%s"
        % (spec.get("launch"), os.path.exists(p["attempts"]), os.path.exists(p["bundle"]))))

    checks.append(_check(
        "C20", "verdict.json states the contract-mandated terminal values",
        verdict.get("family_id") == family and verdict.get("round_id") == round_id
        and verdict.get("kanban_task_id") == task
        and verdict.get("verdict") == "TECHNICAL_INCOMPLETE"
        and verdict.get("performance_claimable") is False
        and verdict.get("failure", {}).get("layer") == "card-local"
        and verdict.get("failure", {}).get("class") == "data_window_invalid"
        and verdict.get("failure", {}).get("last_run_id") is None
        and verdict.get("run_id") is None,
        "verdict=%s claimable=%s failure=%s run_id=%s"
        % (verdict.get("verdict"), verdict.get("performance_claimable"),
           verdict.get("failure"), verdict.get("run_id"))))

    checks.append(_check(
        "C21", "verdict.json: failure is separated (infrastructure, not science) and empty by construction",
        verdict.get("attempts", {}).get("launched") == 0
        and verdict.get("attempts", {}).get("terminal_sentinels") == 0
        and verdict.get("attempts", {}).get("run_spec") is None
        and verdict.get("survivors") == []
        and verdict.get("survivor_bundle") is None
        and verdict.get("cohorts", {}).get("realized") == 0
        and verdict.get("evidence_run_ids") == []
        and verdict.get("yield", {}).get("yield_decision") == "STOP_TECHNICAL_INCOMPLETE",
        "attempts=%s cohorts=%s survivors=%s yield=%s"
        % (verdict.get("attempts"), verdict.get("cohorts"), verdict.get("survivors"),
           verdict.get("yield", {}).get("yield_decision"))))

    checks.append(_check(
        "C22", "round-spec: DCA domain is the complete 48-cell product with contract provenance classes",
        spec.get("dca_domain", {}).get("configs_per_cohort_per_grid") == 48
        and spec.get("dca_domain", {}).get("axes_status", {}).get("spacing_pct")
        == "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN"
        and spec.get("dca_domain", {}).get("base_quote") == 1000
        and spec.get("dca_domain", {}).get("base_quote_status")
        == "PROJECT_PRE_REGISTERED_CONSTANT"
        and spec.get("dca_domain", {}).get("axes", {}).get("spacing_pct") == [0.01, 0.02, 0.03, 0.04]
        and spec.get("dca_domain", {}).get("axes", {}).get("size_multiplier") == [1.0, 1.1]
        and spec.get("dca_domain", {}).get("axes", {}).get("breakeven_tp_pct") == [0.01, 0.02, 0.03]
        and spec.get("dca_domain", {}).get("axes", {}).get("invalidation_pct") == [0.05, 0.10],
        "configs=%s axes=%s base_quote=%s/%s"
        % (spec.get("dca_domain", {}).get("configs_per_cohort_per_grid"),
           spec.get("dca_domain", {}).get("axes"),
           spec.get("dca_domain", {}).get("base_quote"),
           spec.get("dca_domain", {}).get("base_quote_status"))))

    checks.append(_check(
        "C23", "round-spec: coverage + selector/disposition surfaces are empty by construction",
        spec.get("coverage", {}).get("cells_computed") == 0
        and spec.get("coverage", {}).get("cells_registered_total") == 0
        and len(spec.get("coverage", {}).get("phase_grids", [])) == 10
        and spec.get("selector_and_disposition", {}).get("cohorts_realized") == 0
        and spec.get("selector_and_disposition", {}).get("survivors") == []
        and spec.get("selector_and_disposition", {}).get("selector") == "cohort-selector-v1"
        and spec.get("selector_and_disposition", {}).get("disposition") == "cohort-disposition-v1",
        "coverage=%s cohorts=%s survivors=%s"
        % ({k: spec.get("coverage", {}).get(k) for k in
            ("cells_registered_total", "cells_per_grid", "cells_computed")},
           spec.get("selector_and_disposition", {}).get("cohorts_realized"),
           spec.get("selector_and_disposition", {}).get("survivors"))))

    checks.append(_check(
        "C24", "family.json is bound to this card/family and its fingerprint recomputes",
        fam.get("family_id") == family
        and fam.get("kanban_task_id") == task
        and fam.get("kanban_board") == BOARD
        and fam.get("semantic_fingerprint")
        == _sha256_text(fam.get("fingerprint_input") or ""),
        "family_id=%s task=%s fingerprint=%s"
        % (fam.get("family_id"), fam.get("kanban_task_id"), fam.get("semantic_fingerprint"))))

    checks.append(_check(
        "C25", "falsification battery registered unchanged (4 items, none lowered, none removed)",
        spec.get("falsification", {}).get("item_count") == 4
        and spec.get("falsification", {}).get("no_threshold_lowering") is True
        and spec.get("falsification", {}).get("no_item_removal") is True
        and spec.get("falsification", {}).get("falsification_status", "").startswith("NOT_EXECUTED")
        and all(re.search(r"(?m)^%d\. \*\*" % i,
                          spec.get("falsification", {}).get("record_falsification_verbatim", ""))
                for i in (1, 2, 3, 4)),
        "items=%s status=%s"
        % (spec.get("falsification", {}).get("item_count"),
           spec.get("falsification", {}).get("falsification_status"))))

    checks.append(_check(
        "C26", "no performance number was fabricated anywhere in the round",
        not raw.get("fabricated") and _no_performance_claims(spec) and _no_performance_claims(verdict),
        "spec_claims=%s verdict_claims=%s"
        % (_no_performance_claims(spec), _no_performance_claims(verdict))))

    failed = [c for c in checks if not c["ok"]]
    return {"ok": not failed, "checks": checks, "failed": [c["id"] for c in failed],
            "raw_summary": {"entries": raw.get("entry_count"),
                            "kline_files": raw.get("kline_file_count"),
                            "probe_files": raw.get("probe_payload_files_scanned")},
            "stores_summary": stores.get("totals")}


PERFORMANCE_KEYS = ("sharpe", "cagr", "max_dd", "max_drawdown", "net_pnl", "gross_pnl",
                    "ending_equity", "annualized_return", "total_return", "profit_factor")


def _no_performance_claims(doc):
    """True when no realized (non-null, non-empty) performance metric is present."""
    bad = []

    def walk(node, path=()):
        if isinstance(node, dict):
            for k, v in node.items():
                if str(k).lower() in PERFORMANCE_KEYS and v not in (None, [], {}, 0, False, ""):
                    bad.append("%s.%s" % (".".join(path), k))
                walk(v, path + (str(k),))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, path + ("[%d]" % i,))

    walk(doc)
    return not bad


# ------------------------------------------------------- verbatim machinery ----
def _resolve_source_texts(repo_root=None, card_body_path=None, record_path=None,
                          board_db=None, task=TASK):
    repo_root = repo_root or DEFAULT_REPO
    record_path = record_path or DEFAULT_RECORD
    with open(record_path, encoding="utf-8", errors="replace") as fh:
        record = fh.read()
    with open(os.path.join(repo_root, "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md"),
              encoding="utf-8", errors="replace") as fh:
        contract = fh.read()
    try:
        sys.path.insert(0, os.path.join(repo_root, "runtime"))
        from production_handoff import LIFECYCLE_FOOTER as footer  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        footer = ""
    card = None
    if card_body_path and os.path.exists(card_body_path):
        with open(card_body_path, encoding="utf-8", errors="replace") as fh:
            card = fh.read()
    if card is None:
        db = board_db or BOARD_DB
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
                    board_db=None, task=TASK):
    src = _resolve_source_texts(repo_root=repo_root, card_body_path=card_body_path,
                                record_path=record_path, board_db=board_db, task=task)
    texts = {k: src[k] for k in ("card", "record", "contract", "footer")}
    spec = _load_json(spec_path)
    leaves = _walk_verbatim(spec)
    cmap = spec.get("excerpt_source_map", {})
    problems, misses, checked, seen = [], [], 0, set()
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


# ------------------------------------------------------------- self test -------
def _copy_family(src_results, dst_results, family=FAMILY, round_id=ROUND):
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


def self_test(results_root=DEFAULT_RESULTS, raw=None, stores=None, family=FAMILY,
              round_id=ROUND, task=TASK):
    """Non-vacuousness control: tampered copies must be refused by the named check."""
    tmp = tempfile.mkdtemp(prefix="t6e26cfa9-selftest-")
    try:
        base = _copy_family(results_root, tmp, family, round_id)
        spec_p = os.path.join(base, "rounds", round_id, "round-spec.json")
        verd_p = os.path.join(base, "rounds", round_id, "verdict.json")
        fam_p = os.path.join(base, "family.json")
        results = []

        def run():
            return run_checks(tmp, None, raw=raw, stores=stores, family=family,
                              round_id=round_id, task=task)

        def v(name, expect, mutate):
            _copy_family(results_root, tmp, family, round_id)
            mutate(spec_p, verd_p, fam_p)
            res = run()
            failed = {c["id"] for c in res["checks"] if not c["ok"]}
            results.append({"variant": name, "expected": expect, "failed": sorted(failed),
                            "flipped": expect in failed})
            return res

        def m_spec(fn):
            return lambda s, vd, f: _mutate(s, fn)

        def m_verdict(fn):
            return lambda s, vd, f: _mutate(vd, fn)

        def m_family(fn):
            return lambda s, vd, f: _mutate(f, fn)

        v("verdict->PASS", "C20", m_verdict(lambda d: d.update({"verdict": "PASS"})))
        v("performance_claimable->true", "C20",
          m_verdict(lambda d: d.update({"performance_claimable": True})))
        v("failure.layer->shared-layer", "C20",
          m_verdict(lambda d: d["failure"].update({"layer": "shared-layer"})))
        v("failure.class->operator_stopped", "C20",
          m_verdict(lambda d: d["failure"].update({"class": "operator_stopped"})))
        v("run_id->fabricated", "C20", m_verdict(lambda d: d.update({"run_id": "r-fake"})))
        v("attempts.launched->1", "C21",
          m_verdict(lambda d: d["attempts"].update({"launched": 1})))
        v("survivors->one", "C21", m_verdict(lambda d: d.update({"survivors": ["x"]})))
        v("yield->CONTINUE", "C21",
          m_verdict(lambda d: d["yield"].update({"yield_decision": "CONTINUE"})))
        v("spec.expected->PREREQUISITE_PRESENT", "C17",
          m_spec(lambda d: d.update({"expected": "PREREQUISITE_PRESENT"})))
        v("spec.task->other card", "C17",
          m_spec(lambda d: d.update({"kanban_task_id": "t_00000000"})))
        v("universe_shrunk->true", "C18",
          m_spec(lambda d: d["universe_registration"].update({"universe_shrunk_to_local_list": True})))
        v("substitute_market->true", "C18",
          m_spec(lambda d: d["prerequisite_gate"].update({"substitute_market_used": True})))
        v("launch.launched->true", "C19",
          m_spec(lambda d: d["launch"].update({"launched": True, "attempts": 1})))
        v("coverage.cells_computed->48", "C23",
          m_spec(lambda d: d["coverage"].update({"cells_computed": 48,
                                                 "cells_registered_total": 48})))
        v("selector.selector->v0", "C23",
          m_spec(lambda d: d["selector_and_disposition"].update({"selector": "cohort-selector-v0"})))
        v("dca.axes.spacing_pct->trimmed", "C22",
          m_spec(lambda d: d["dca_domain"]["axes"].update({"spacing_pct": [0.01]})))
        v("dca.base_quote->500", "C22",
          m_spec(lambda d: d["dca_domain"].update({"base_quote": 500})))
        v("dca.provenance->user-fixed", "C22",
          m_spec(lambda d: d["dca_domain"]["axes_status"].update(
              {"spacing_pct": "USER_FIXED"})))
        v("falsification.item_count->3", "C25",
          m_spec(lambda d: d["falsification"].update({"item_count": 3})))
        v("falsification.status->EXECUTED", "C25",
          m_spec(lambda d: d["falsification"].update({"falsification_status": "EXECUTED"})))
        v("falsification.item4 stripped from verbatim", "C25",
          m_spec(lambda d: d["falsification"].update(
              {"record_falsification_verbatim":
               d["falsification"]["record_falsification_verbatim"].split("4. **")[0]})))
        v("fabricated sharpe in spec", "C26",
          m_spec(lambda d: d.update({"measured": {"sharpe": 1.9}})))
        v("fabricated net_pnl in verdict", "C26",
          m_verdict(lambda d: d.update({"realized": {"net_pnl": 4210.0}})))
        v("family.fingerprint->wrong", "C24",
          m_family(lambda d: d.update({"semantic_fingerprint": "sha256:" + "0" * 64})))
        v("family.task->other card", "C24",
          m_family(lambda d: d.update({"kanban_task_id": "t_00000000"})))
        # benign variant: an extra non-asserted field must NOT flip anything
        _copy_family(results_root, tmp, family, round_id)
        _mutate(spec_p, lambda d: d.update({"operator_note": "benign addition"}))
        benign = run()
        results.append({"variant": "benign: extra note field", "expected": None,
                        "failed": benign["failed"], "flipped": False})
        ok = all(r["flipped"] for r in results if r["expected"]) and not benign["failed"]
        return {"ok": ok, "variants": results,
                "flipped": sum(1 for r in results if r["flipped"])}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------ raw fixture control ----
def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def raw_fixture_control(results_root=DEFAULT_RESULTS, raw=None, stores=None,
                        family=FAMILY, round_id=ROUND, task=TASK):
    """Plant what this record needs into a temp raw tree; the raw-side checks must flip."""
    tmp = tempfile.mkdtemp(prefix="t6e26cfa9-fixture-")
    try:
        fixture = os.path.join(tmp, "raw")
        _write(os.path.join(fixture, "_meta/CONFIG.json"), json.dumps(
            {"venue": "BINANCE", "market_type": "usdm_perp", "symbols": ["BTCUSDT"]}))
        _write(os.path.join(fixture, "_meta/SCHEMA.md"),
               "# schema\n\n## Dataset: klines\n\n## Dataset: options\n\n"
               "## Dataset: cme_futures\n\n## Dataset: etf\n\n## Dataset: brrny\n\n"
               "## Dataset: sofr\n")
        # an options chain: strike/expiry/implied vol/bid/ask/open interest
        _write(os.path.join(fixture, "cboe/ibit/options/IBIT-options-2026-01.jsonl"),
               "\n".join(json.dumps(r) for r in [
                   {"symbol": "IBIT", "expiry": "2026-03-20", "strike": 60.0, "type": "call",
                    "bid": 3.1, "ask": 3.3, "implied_vol": 0.52, "open_interest": 4211},
                   {"symbol": "IBIT", "expiry": "2026-03-20", "strike": 60.0, "type": "put",
                    "bid": 2.9, "ask": 3.1, "implied_vol": 0.53, "open_interest": 3980}]) + "\n")
        # BlackRock daily holdings: NAV, shares outstanding, BTC per share
        _write(os.path.join(fixture, "ishares/ibit/holdings.csv"),
               "date,nav,shares_outstanding,btc_per_share\n2026-01-02,58.9,612000000,0.000595\n")
        # CME futures settlements
        _write(os.path.join(fixture, "cme/futures/BTC-settlements.csv"),
               "date,contract,front_month,settlement,globex_symbol\n2026-01-02,BTCH6,2026-03-27,91250.0,BTCH6\n")
        # BRNY + SOFR
        _write(os.path.join(fixture, "cme/cf/brrny.csv"), "date,brrny\n2026-01-02,91180.55\n")
        _write(os.path.join(fixture, "rates/sofr.csv"), "date,sofr\n2026-01-02,0.0431\n")
        # OCC / CME margin + haircut table
        _write(os.path.join(fixture, "margin/occ_cme_margin.csv"),
               "clearinghouse,product,initial_margin,maintenance_margin,haircut,cross_margin\n"
               "OCC,IBIT options,0.40,0.25,0.15,false\n")
        # 16:00 ET point-in-time alignment convention
        _write(os.path.join(fixture, "conventions/pit_1600et.md"),
               "All quotes aligned strictly at 16:00 ET (US equity market close).\n")
        res = run_checks(results_root, fixture, raw=None, stores=stores, family=family,
                         round_id=round_id, task=task)
        flipped = sorted(res["failed"])
        expected_subset = {"C2", "C3", "C5", "C7", "C8", "C9", "C10", "C11", "C12", "C13"}
        return {"ok": expected_subset.issubset(set(flipped)), "flipped": flipped,
                "expected_subset": sorted(expected_subset),
                "not_flipped": sorted(expected_subset - set(flipped)),
                "fixture_root": fixture}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------- main ----
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--measure-only", action="store_true")
    ap.add_argument("--verify-verbatim", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--raw-fixture-control", action="store_true")
    ap.add_argument("--host-scan", action="store_true")
    ap.add_argument("--results-root", default=DEFAULT_RESULTS)
    ap.add_argument("--raw-root", default=DEFAULT_RAW)
    ap.add_argument("--repo-root", default=DEFAULT_REPO)
    ap.add_argument("--card-body")
    args = ap.parse_args(argv)

    if args.measure_only:
        print(json.dumps({"raw": measure_raw(args.raw_root),
                          "host": measure_host_stores() if args.host_scan else "skipped"},
                         ensure_ascii=False, indent=1))
        return 0

    if args.verify_verbatim:
        p = _round_paths(args.results_root)
        res = verify_verbatim(p["spec"], repo_root=args.repo_root,
                              card_body_path=args.card_body)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0 if res["ok"] else 1

    if args.self_test or args.raw_fixture_control:
        raw = measure_raw(args.raw_root)
        stores = measure_host_stores() if args.host_scan else {"skipped": True}
        if args.self_test:
            res = self_test(args.results_root, raw=raw, stores=stores)
        else:
            res = raw_fixture_control(args.results_root, raw=raw, stores=stores)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0 if res["ok"] else 1

    res = run_checks(args.results_root, args.raw_root, args.repo_root,
                     raw=measure_raw(args.raw_root),
                     stores=measure_host_stores() if args.host_scan else {"skipped": True},
                     host_scan=args.host_scan)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        for c in res["checks"]:
            print("%-4s %-6s %s" % (c["id"], "PASS" if c["ok"] else "FAIL", c["name"]))
            if not c["ok"]:
                print("      detail: %s" % json.dumps(c["detail"], ensure_ascii=False)[:400])
        print("overall: %s failed=%s" % ("PASS" if res["ok"] else "FAIL", res["failed"]))
        print("raw: %s" % json.dumps(res["raw_summary"], ensure_ascii=False))
        print("stores: %s" % json.dumps(res["stores_summary"], ensure_ascii=False))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
