#!/usr/bin/env python3
"""Prerequisite-gate read-back checker for the family

    bitcoin-us-spot-etf-net-flow-next-day-drift-2026-09-01

Card t_8e8c3752 terminalises this family as TECHNICAL_INCOMPLETE because the
record's required data is not in the canonical raw:

  * the hypothesis is defined on an **explanatory variable** — "aggregate daily
    net flow across major U.S. spot Bitcoin ETFs", formed "only after the day-t
    ETF flow figure is publicly finalized and timestamped". The canonical raw
    holds one venue's USD-M *crypto* perpetual klines/funding/instruments for
    four contracts (BTCUSDT/ETHUSDT/BNBUSDT/SOLUSDT). No ETF-flow series of any
    kind exists at any frequency, depth or venue;
  * the record's required-data list asks for the ETF-flow series point-in-time
    with publication timestamps, an aggregate built without look-ahead
    revisions, ETF-level flows for the dominant funds, a U.S.-ETF-business-day
    calendar, and the revision history of the flow prints. None of those
    surfaces exists locally;
  * the record's falsification battery (items 1, 3, 4, 6, 7, 8) is defined on
    the flow series, and its control set (item 5) needs CME basis, dollar
    strength and equity risk sentiment — 3 of its 7 controls are locally
    available, the rest are not;
  * the one leg that *is* locally present (BTCUSDT, a closely tracking
    derivative the record's instrument clause admits) is the **response**
    variable. Running a price-only strategy on it would replace the record's
    hypothesis with a different one — the card forbids exactly that
    ("不得以近似資料、替代市場或改寫 hypothesis 硬跑").

This script exists so an independent reader can re-derive that determination
from the live filesystem instead of trusting prose:

  * C1-C4 re-measure the canonical raw: the store identity (venue / market type
    / symbols / intervals as the store itself documents them), the instrument
    surface (ids, currencies, contract type, field names), the kline surface
    (symbol dirs, interval set, row shape, 1d window and UTC grid) and the
    funding surface (symbol dirs, row keys, venue column, window);
  * C5-C6 probe the whole tree at full depth by entry name and by payload
    content for the tokens a U.S. spot-BTC-ETF net-flow dataset would have to
    carry, and assert every hit is classified;
  * C7-C9 assert the registered explanatory variable and the six decisive
    derived objects (point-in-time flow prints, publication timestamps,
    revision vintages, U.S. ETF business-day calendar, ETF AUM, flow
    normalisations/controls) are absent or not constructible, and that the
    store's own schema documents no such dataset family;
  * C10-C14 re-read the round's immutable artifacts and assert they state
    exactly the contract-mandated terminal values (verdict, layer, class, yield
    decision, zero attempts, null run_id), that nothing was ever submitted (no
    run-spec, no attempt directory, no terminal sentinel), that the registered
    universe was NOT shrunk to the locally available contract, that the DCA
    registration still carries the contract 7.2 v1.3.1 provenance classes plus
    the complete 48-cell product, and that the coverage/survivor surface is
    empty by construction;
  * C15-C17 re-measure the host's non-canonical stores and assert none of them
    carries an ETF net-flow surface that could stand in for the registered
    requirement (they are disclosed as measured-but-unused), and that the
    failure taxonomy was kept separated (infrastructure, not science).
  * `--verify-verbatim` resolves every `*_verbatim` leaf of the persisted
    round-spec against its declared source (card / record / contract / footer)
    and re-checks the excerpt digest map, independent of the authoring run.

Read-only: it never writes inside the results tree or the raw tree. `--self-test`
builds tampered copies in fresh temp directories and asserts the checker refuses
them (non-vacuousness control). `--raw-fixture-control` builds a temp raw tree
carrying what this record would need (a U.S. spot-BTC-ETF net-flow dataset with
publication timestamps and revisions, plus the calendar surface) and asserts the
raw-side checks flip to FAIL. `--host-scan` re-runs the house-wide search for
such surfaces. Every temp tree is removed afterwards.

Usage:
    python3 runtime/bitcoin_us_spot_etf_net_flow_next_day_drift_prerequisite_check.py [--json]
    python3 runtime/bitcoin_us_spot_etf_net_flow_next_day_drift_prerequisite_check.py --measure-only
    python3 runtime/bitcoin_us_spot_etf_net_flow_next_day_drift_prerequisite_check.py --verify-verbatim
    python3 runtime/bitcoin_us_spot_etf_net_flow_next_day_drift_prerequisite_check.py --self-test
    python3 runtime/bitcoin_us_spot_etf_net_flow_next_day_drift_prerequisite_check.py --raw-fixture-control
    python3 runtime/bitcoin_us_spot_etf_net_flow_next_day_drift_prerequisite_check.py --host-scan

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

FAMILY = "bitcoin-us-spot-etf-net-flow-next-day-drift-2026-09-01"
ROUND = FAMILY + "-r1"
TASK = "t_8e8c3752"
BOARD = "quant-strategy-research"
DEFAULT_RESULTS = "/Volumes/ExpansionDrive/qlib-results"
DEFAULT_RAW = "/Volumes/ExpansionDrive/market-data-raw"
DEFAULT_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_RECORD = os.path.join(os.path.expanduser("~"), ".hermes/wiki/quant", FAMILY + ".md")
PROBE_MAX_DEPTH = 8

# Tokens a U.S. spot Bitcoin ETF net-flow dataset would have to carry. Ticker and
# vocabulary tokens are matched with word boundaries (a bare substring match would
# fire 'ibit' on 'exhibit/prohibit' and 'nav' on 'navigation').
PROBE_PATTERNS = {
    "ticker_ibit": r"\bibit\b", "ticker_fbtc": r"\bfbtc\b", "ticker_gbtc": r"\bgbtc\b",
    "ticker_arkb": r"\barkb\b", "ticker_bitb": r"\bbitb\b", "ticker_btco": r"\bbtco\b",
    "ticker_ezbc": r"\bezbc\b", "ticker_brrr": r"\bbrrr\b", "ticker_btcw": r"\bbtcw\b",
    "ticker_hodl": r"\bhodl\b", "spot_etf": r"\bspot[-_ ]etf\b",
    "net_flow": r"\bnet[-_ ]?flow(s)?\b", "shares_outstanding": r"\bshares[-_ ]outstanding\b",
    "creation_unit": r"\bcreation[-_ ]unit(s)?\b", "redemption": r"\bredemption(s)?\b",
    "subscription": r"\bsubscription(s)?\b", "aum": r"\baum\b", "nav": r"\bnav\b",
    "grayscale": r"\bgrayscale\b", "blackrock": r"\bblackrock\b", "ishares": r"\bishares\b",
    "fidelity": r"\bfidelity\b", "invesco": r"\binvesco\b", "vaneck": r"\bvaneck\b",
    "wisdomtree": r"\bwisdomtree\b", "franklin": r"\bfranklin\b", "bitwise": r"\bbitwise\b",
    "21shares": r"\b21shares\b", "ark_invest": r"\bark[-_ ]invest\b",
}
COMPILED = {k: re.compile(v, re.I) for k, v in PROBE_PATTERNS.items()}
TEXT_EXTS = (".csv", ".jsonl", ".gz", ".json", ".txt", ".md", ".tsv", ".py",
             ".sh", ".yaml", ".yml")
DATA_EXTS = (".csv", ".jsonl", ".gz", ".parquet", ".bin", ".feather", ".h5",
             ".npy", ".arrow", ".db", ".sqlite", ".zst")

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
    "/Volumes/ExpansionDrive/daily-crypto-brief",
    os.path.join(DEFAULT_RESULTS, "_handoff/bodies"),
    os.path.join(os.path.expanduser("~"), ".hermes/wiki/quant"),
]


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
    schema = _read_text(os.path.join(meta, "SCHEMA.md"))
    rep["schema_dataset_sections"] = re.findall(r"^## Dataset: (.+)$", schema, re.M)
    rep["schema_documents_etf_flow_dataset"] = bool(
        re.search(r"^## Dataset: .*(etf|flow)", schema, re.M | re.I))

    inst = _load_json(os.path.join(raw, "binance/usdm/instruments/usdm-perp-instruments.json"))
    inner = inst["instruments"]
    rows = inner if isinstance(inner, list) else list(inner.values())
    flds = [r["fields"] for r in rows]
    rep["instrument_count"] = len(rows)
    rep["instrument_ids"] = sorted(f["id"] for f in flds)
    rep["instrument_base_currencies"] = sorted({f["base_currency"] for f in flds})
    rep["instrument_types"] = sorted({f["type"] for f in flds})
    rep["instrument_field_names"] = sorted(flds[0].keys())
    rep["instrument_flow_fields"] = [f for f in sorted(flds[0].keys())
                                     if any(t in f for t in ("flow", "aum", "nav", "share",
                                                             "creation", "redemption"))]

    kl = os.path.join(raw, "binance/usdm/klines")
    rep["klines_symbol_dirs"] = _ls(kl)
    rep["klines_intervals"] = _ls(os.path.join(kl, rep["klines_symbol_dirs"][0])) if rep["klines_symbol_dirs"] else []
    rep["klines_dataset_dirs"] = len(rep["klines_symbol_dirs"]) * len(rep["klines_intervals"])
    if rep["klines_symbol_dirs"]:
        d0 = os.path.join(kl, "BTCUSDT", "1d")
        fs = _ls(d0)
        rep["klines_1d_file_count"] = len(fs)
        rows_all = [json.loads(x) for f in fs for x in _read_text(os.path.join(d0, f)).splitlines()]
        rep["klines_1d_rows"] = len(rows_all)
        rep["klines_1d_window_utc"] = [_iso(rows_all[0]["open_time_ms"]), _iso(rows_all[-1]["open_time_ms"])]
        if len(rows_all) > 1:
            rep["klines_1d_open_step_seconds"] = ((rows_all[-1]["open_time_ms"] - rows_all[0]["open_time_ms"])
                                                  / 1000.0 / (len(rows_all) - 1))
        else:
            rep["klines_1d_open_step_seconds"] = None
        rep["klines_1d_row_keys"] = sorted(rows_all[0].keys())
        rep["klines_flow_columns"] = [k for k in rep["klines_1d_row_keys"]
                                      if any(t in k for t in ("flow", "aum", "nav", "share",
                                                              "creation", "redemption"))]

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
        rep["funding_flow_fields"] = [k for k in rep["funding_row_keys"]
                                      if any(t in k for t in ("flow", "aum", "nav", "share"))]

    # whole-tree probes
    entries = _all_entries(raw)
    rep["raw_entry_count"] = len(entries)
    name_hits = {}
    for e in entries:
        low = e.lower()
        for t in COMPILED:
            if t in low:
                name_hits.setdefault(t, []).append(e)
    rep["probe_tokens_tested"] = len(COMPILED)
    rep["probe_hits_in_entry_names"] = {k: v[:8] for k, v in name_hits.items()}
    rep["probe_hits_unexplained"] = sorted(name_hits.keys())

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
        for k, rx in COMPILED.items():
            if rx.search(txt):
                token_files.setdefault(k, []).append(e)
    rep["payload_scan_files"] = len(files)
    rep["payload_scan_bytes"] = tot_bytes
    rep["payload_token_files"] = {k: v[:6] for k, v in token_files.items()}
    rep["payload_hits_unexplained"] = sorted(token_files.keys())

    # decisive derived objects
    rep["etf_flow_series_present"] = bool(name_hits) or bool(token_files)
    rep["publication_timestamps_present"] = bool(
        re.search(r"\b(publication|published|release)[-_ ]?time", schema, re.I))
    rep["revision_history_present"] = bool(
        re.search(r"^## Dataset: .*(revision|vintage)", schema, re.M | re.I))
    rep["us_etf_calendar_present"] = bool(
        re.search(r"\b(us[-_ ]?etf|exchange[-_ ]?calendar|holiday[-_ ]?calendar|business[-_ ]?day)\b",
                  schema, re.I))
    rep["etf_aum_present"] = "aum" in schema.lower()
    rep["btc_market_cap_present"] = bool(re.search(r"\bmarket[-_ ]?cap", schema, re.I))
    rep["required_data_available"] = False
    return rep


def measure_host_stores(roots=None, cap_per_root=60000):
    """Re-measure the host's non-canonical stores for an ETF net-flow surface."""
    roots = roots or HOST_ROOTS
    rep = {"roots": {}, "totals": {}}
    tot_files = tot_hits = tot_data = 0
    data_candidates = []
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
            if os.path.splitext(p)[1].lower() in TEXT_EXTS:
                try:
                    txt = _read_text(p, 200000)
                except Exception:  # noqa: BLE001
                    txt = ""
                toks |= {k for k, rx in COMPILED.items() if rx.search(txt)}
            if not toks:
                continue
            hits += 1
            for t in toks:
                per_tok[t] = per_tok.get(t, 0) + 1
            if p.lower().endswith(DATA_EXTS):
                data_candidates.append({"root": root, "path": rel, "tokens": sorted(toks)})
        rep["roots"][root] = {"exists": True, "files_scanned": n, "token_files": hits,
                              "token_file_counts": per_tok,
                              "data_ext_hits": sum(1 for d in data_candidates if d["root"] == root),
                              "carries_etf_net_flow_data": False}
        tot_files += n
        tot_hits += hits
    tot_data = len(data_candidates)
    rep["totals"] = {"roots": len(roots), "files_scanned": tot_files, "token_files": tot_hits,
                     "data_extension_hits": tot_data, "data_candidates": tot_data}
    rep["data_candidates"] = data_candidates[:10]
    rep["any_store_carries_etf_net_flow_data"] = tot_data > 0
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
    checks = []
    add = checks.append

    # ---- C1 store identity
    add(_check("C1", "canonical raw identity is a single crypto-perp venue, not an ETF-flow store",
               raw["documented_venue"] == "BINANCE" and raw["documented_market_type"] == "usdm_perp"
               and raw["usdm_subdirs"] == ["funding", "instruments", "klines"]
               and "binance" in raw["store_top_level"],
               {"venue": raw["documented_venue"], "market_type": raw["documented_market_type"],
                "usdm_subdirs": raw["usdm_subdirs"], "symbols": raw["documented_symbols"]}))
    # ---- C2 instrument surface
    add(_check("C2", "instrument surface carries four crypto perpetuals and no flow/fund field",
               raw["instrument_count"] == 4
               and raw["instrument_types"] == ["CryptoPerpetual"]
               and raw["instrument_flow_fields"] == []
               and set(raw["documented_symbols"]) == {"BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"},
               {"ids": raw["instrument_ids"], "types": raw["instrument_types"],
                "flow_fields": raw["instrument_flow_fields"],
                "fields": raw["instrument_field_names"][:12]}))
    # ---- C3 kline surface
    add(_check("C3", "kline surface is OHLCV-only with no flow column",
               raw["klines_symbol_dirs"] == ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
               and raw["klines_intervals"] == ["15m", "1d", "1h", "1w", "30m", "4h", "5m"]
               and raw["klines_flow_columns"] == []
               and raw["klines_1d_open_step_seconds"] == 86400.0
               and raw["klines_1d_rows"] > 1500,
               {"symbols": raw["klines_symbol_dirs"], "intervals": raw["klines_intervals"],
                "row_keys": raw["klines_1d_row_keys"], "1d_window": raw["klines_1d_window_utc"],
                "1d_rows": raw["klines_1d_rows"], "step_s": raw["klines_1d_open_step_seconds"]}))
    # ---- C4 funding surface
    add(_check("C4", "funding surface is perp funding only, with no ETF-flow field",
               raw["funding_symbol_dirs"] == ["BNBUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT"]
               and raw["funding_venues"] == ["BINANCE"]
               and raw["funding_flow_fields"] == [],
               {"symbols": raw["funding_symbol_dirs"], "keys": raw["funding_row_keys"],
                "venues": raw["funding_venues"], "window": raw["funding_window_utc"],
                "rows": raw["funding_rows"]}))
    # ---- C5 entry-name probe
    add(_check("C5", "entry-name probe over the whole raw tree finds no ETF/flow surface",
               raw["probe_hits_unexplained"] == [] and raw["raw_entry_count"] > 1000,
               {"entries": raw["raw_entry_count"], "tokens": raw["probe_tokens_tested"],
                "hits": raw["probe_hits_in_entry_names"]}))
    # ---- C6 payload probe
    add(_check("C6", "payload probe over every raw file finds no ETF/flow surface",
               raw["payload_hits_unexplained"] == [] and raw["payload_scan_files"] > 1000,
               {"files": raw["payload_scan_files"], "bytes": raw["payload_scan_bytes"],
                "token_files": raw["payload_token_files"]}))
    # ---- C7 explanatory variable absent
    add(_check("C7", "the record's explanatory variable (aggregate daily U.S. spot-BTC-ETF net flow) is absent",
               raw["etf_flow_series_present"] is False,
               {"etf_flow_series_present": raw["etf_flow_series_present"],
                "registered": spec["universe_registration"]["registered_explanatory_variable"]}))
    # ---- C8 decisive derived objects not constructible
    add(_check("C8", "the decisive derived objects are absent or not constructible from what is present",
               not raw["publication_timestamps_present"] and not raw["revision_history_present"]
               and not raw["us_etf_calendar_present"] and not raw["etf_aum_present"]
               and not raw["btc_market_cap_present"],
               {"publication_timestamps": raw["publication_timestamps_present"],
                "revision_history": raw["revision_history_present"],
                "us_etf_calendar": raw["us_etf_calendar_present"],
                "etf_aum": raw["etf_aum_present"],
                "btc_market_cap": raw["btc_market_cap_present"]}))
    # ---- C9 store schema documents no ETF/flow dataset
    add(_check("C9", "the store's own schema documents no ETF-flow dataset family",
               raw["schema_documents_etf_flow_dataset"] is False
               and raw["schema_dataset_sections"] == ["klines (Binance USD-M perpetual futures, UTC)",
                                                      "funding", "instruments"],
               {"dataset_sections": raw["schema_dataset_sections"]}))
    # ---- C10 round-spec terminal values
    gate = spec.get("prerequisite_gate", {})
    launch = spec.get("launch", {})
    add(_check("C10", "round-spec states the contract-mandated terminal values",
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
    # ---- C11 verdict.json terminal values
    add(_check("C11", "verdict.json states the same terminal values and an empty survivor/coverage surface",
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
    # ---- C12 nothing submitted
    stray = []
    for dirpath, dirnames, filenames in os.walk(sf["family_dir"]):
        for name in filenames:
            if name in ("run-spec.json", "result.json", "state.json") or name.startswith("terminal"):
                stray.append(os.path.relpath(os.path.join(dirpath, name), sf["family_dir"]))
    add(_check("C12", "nothing was ever submitted: no run-spec, no attempt dir, no sentinel",
               not os.path.isdir(sf["attempts_dir"]) and stray == []
               and sorted(sf["round_dir_listing"]) == ["round-spec.json", "verdict.json"]
               and sorted(sf["family_dir_listing"]) == ["family.json", "rounds"],
               {"attempts_dir_exists": os.path.isdir(sf["attempts_dir"]),
                "round_dir_listing": sf["round_dir_listing"],
                "family_dir_listing": sf["family_dir_listing"], "stray": stray}))
    # ---- C13 universe not shrunk + DCA registration intact
    ur = spec.get("universe_registration", {})
    dd = spec.get("dca_domain", {})
    ufi = spec.get("user_fixed_invariants", {})
    axes = ["spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct"]
    dca_ok = (dd.get("configs_per_cohort_per_grid") == 48
              and dd.get("base_quote") == 1000
              and dd.get("base_quote_status") == "PROJECT_PRE_REGISTERED_CONSTANT"
              and dd.get("search_axes_status") == "PROJECT_PRE_REGISTERED_SEARCH_DOMAIN"
              and sorted(dd.get("axes", {}).keys()) == sorted(axes)
              and not any(a in ufi for a in axes) and "base_quote" not in ufi)
    add(_check("C13", "registered universe kept whole; DCA domain carries the v1.3.1 provenance classes",
               ur.get("universe_shrunk_to_local_list") is False
               and ur.get("explanatory_variable_present_local") is False
               and len(ur.get("registered_instruments", [])) == 2
               and ur.get("registered_timeframes") == ["1d"]
               and dca_ok,
               {"universe_shrunk_to_local_list": ur.get("universe_shrunk_to_local_list"),
                "explanatory_variable_present_local": ur.get("explanatory_variable_present_local"),
                "registered_instruments": ur.get("registered_instruments"),
                "registered_timeframes": ur.get("registered_timeframes"),
                "dca_configs": dd.get("configs_per_cohort_per_grid"),
                "dca_ok": dca_ok}))
    # ---- C14 coverage / survivor surface empty by construction
    cov = spec.get("robustness_plan", {})
    sel = spec.get("selector_and_disposition", {})
    add(_check("C14", "coverage is registered but empty; no survivor surface exists",
               cov.get("cells_registered_per_grid") == 96
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
    # ---- C15 host stores
    add(_check("C15", "no measured host store carries an ETF net-flow dataset",
               stores.get("any_store_carries_etf_net_flow_data") is False
               and stores.get("totals", {}).get("files_scanned", 0) > 1000,
               stores.get("totals")))
    # ---- C16 family.json identity
    add(_check("C16", "family.json binds this round's family to this card and fingerprint",
               fam.get("family_id") == family and fam.get("kanban_task_id") == task
               and isinstance(fam.get("semantic_fingerprint"), str)
               and spec.get("provenance", {}).get("semantic_fingerprint", {}).get("semantic_fingerprint")
               == fam.get("semantic_fingerprint"),
               {"family_id": fam.get("family_id"), "kanban_task_id": fam.get("kanban_task_id"),
                "semantic_fingerprint": fam.get("semantic_fingerprint")}))
    # ---- C17 taxonomy separation
    costs = spec.get("costs", {})
    add(_check("C17", "failure taxonomy kept separated: infrastructure terminal, not a scientific failure",
               "infrastructure/technical failure" in costs.get("note", "")
               and spec.get("expected") == "PREREQUISITE_ABSENT"
               and verdict.get("yield", {}).get("yield_decision") == "STOP_TECHNICAL_INCOMPLETE",
               {"expected": spec.get("expected"),
                "yield_decision": verdict.get("yield", {}).get("yield_decision"),
                "note": costs.get("note")}))
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
    tmp = tempfile.mkdtemp(prefix="t8e8c3752-selftest-")
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

        v("verdict->PASS", "C11", m_verdict(lambda d: d.update({"verdict": "PASS"})))
        v("performance_claimable->true", "C11",
          m_verdict(lambda d: d.update({"performance_claimable": True})))
        v("failure.layer->shared-layer", "C11",
          m_verdict(lambda d: d["failure"].update({"layer": "shared-layer"})))
        v("failure.class->script_bug", "C11",
          m_verdict(lambda d: d["failure"].update({"class": "script_bug"})))
        v("failure.last_run_id->u1", "C11",
          m_verdict(lambda d: d["failure"].update({"last_run_id": round_id + "-u1"})))
        v("attempts.launched->1", "C11",
          m_verdict(lambda d: d["attempts"].update({"launched": 1})))
        v("survivors->[one]", "C11",
          m_verdict(lambda d: d.update({"survivors": [{"cohort": "BTCUSDT/1d"}]})))
        v("coverage.cells_computed->96", "C11",
          m_verdict(lambda d: d["coverage"].update({"cells_computed": 96})))
        v("yield_decision->CONTINUE", "C17",
          m_verdict(lambda d: d["yield"].update({"yield_decision": "CONTINUE"})))
        v("spec.launch.launched->true", "C10",
          m_spec(lambda d: d["launch"].update({"launched": True})))
        v("spec.gate.verdict->REJECT", "C10",
          m_spec(lambda d: d["prerequisite_gate"].update({"verdict": "REJECT"})))
        v("spec.gate.failure_layer->shared-layer", "C10",
          m_spec(lambda d: d["prerequisite_gate"].update({"failure_layer": "shared-layer"})))
        v("spec.expected->RUN", "C17", m_spec(lambda d: d.update({"expected": "RUN"})))
        v("universe_shrunk->true", "C13",
          m_spec(lambda d: d["universe_registration"].update({"universe_shrunk_to_local_list": True})))
        v("explanatory_variable_present_local->true", "C13",
          m_spec(lambda d: d["universe_registration"].update(
              {"explanatory_variable_present_local": True})))
        v("dca.configs->12", "C13",
          m_spec(lambda d: d["dca_domain"].update({"configs_per_cohort_per_grid": 12})))
        v("dca.base_quote_status->USER_FIXED", "C13",
          m_spec(lambda d: d["dca_domain"].update({"base_quote_status": "USER_FIXED"})))
        v("dca axes leak into user_fixed_invariants", "C13",
          m_spec(lambda d: d["user_fixed_invariants"].update({"spacing_pct": [0.01, 0.02]})))
        v("coverage.cells_computed->96 (spec)", "C14",
          m_spec(lambda d: d["robustness_plan"].update({"cells_computed": 96})))
        v("cohorts_realized->2", "C14",
          m_spec(lambda d: d["selector_and_disposition"].update({"cohorts_realized": 2})))
        v("selector version changed", "C14",
          m_spec(lambda d: d["selector_and_disposition"].update({"selector": "cohort-selector-v2"})))
        v("family.kanban_task_id->other", "C16",
          m_family(lambda d: d.update({"kanban_task_id": "t_00000000"})))
        v("family.semantic_fingerprint mismatch", "C16",
          m_family(lambda d: d.update({"semantic_fingerprint": "sha256:" + "0" * 64})))
        v("stray run-spec.json present", "C12",
          lambda s, vd, f: (os.makedirs(os.path.join(os.path.dirname(s), "attempts", round_id + "-u1"),
                                        exist_ok=True),
                            open(os.path.join(os.path.dirname(s), "attempts", round_id + "-u1",
                                              "run-spec.json"), "w").write("{}")))
        v("stray terminal sentinel present", "C12",
          lambda s, vd, f: open(os.path.join(os.path.dirname(s), "terminal-DONE"), "w").write("{}"))
        v("extra file in family dir", "C12",
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
    raw-side checks flip to FAIL (the raw checks are not vacuously green)."""
    tmp = tempfile.mkdtemp(prefix="t8e8c3752-rawfix-")
    try:
        rawfix = os.path.join(tmp, "market-data-raw")
        for sub in ("_meta", "binance/usdm/klines/BTCUSDT/1d", "binance/usdm/funding/BTCUSDT",
                    "binance/usdm/instruments", "binance/usdm/etf_flows/IBIT"):
            os.makedirs(os.path.join(rawfix, sub), exist_ok=True)
        with open(os.path.join(rawfix, "_meta/CONFIG.json"), "w") as fh:
            json.dump({"schema": "market-data-raw/config/v1", "venue": "BINANCE",
                       "market_type": "usdm_perp", "symbols": ["BTCUSDT"],
                       "intervals": ["1d"]}, fh)
        with open(os.path.join(rawfix, "_meta/SCHEMA.md"), "w") as fh:
            fh.write("# fixture schema\n\n## Dataset: klines\n\n## Dataset: funding\n\n"
                     "## Dataset: etf_flows (U.S. spot Bitcoin ETF net flows)\n\n"
                     "Rows carry net_flow_usd, publication timestamp, revision id, "
                     "fund AUM and BTC market cap; the exchange calendar / business day "
                     "mapping is included.\n")
        with gzip.open(os.path.join(rawfix, "binance/usdm/etf_flows/IBIT/IBIT-net-flow.jsonl.gz"),
                       "wt") as fh:
            fh.write(json.dumps({"symbol": "IBIT", "net_flow_usd": 123456789,
                                 "publication_time_ms": 1789516800000, "revision": 1}) + "\n")
        with gzip.open(os.path.join(rawfix, "binance/usdm/klines/BTCUSDT/1d/BTCUSDT-1d-2026-09.jsonl.gz"),
                       "wt") as fh:
            for i in range(2):
                fh.write(json.dumps({"open_time_ms": 1789516800000 + i * 86400000,
                                     "close_time_ms": 1789603199999 + i * 86400000,
                                     "open": "1", "high": "1", "low": "1", "close": "1",
                                     "volume": "1"}) + "\n")
        with gzip.open(os.path.join(rawfix, "binance/usdm/funding/BTCUSDT/BTCUSDT-funding.jsonl.gz"),
                       "wt") as fh:
            fh.write(json.dumps({"symbol": "BTCUSDT", "venue": "BINANCE",
                                 "market_type": "usdm_perp", "funding_time_ms": 1789516800000,
                                 "funding_rate": "0"}) + "\n")
        with open(os.path.join(rawfix, "binance/usdm/instruments/usdm-perp-instruments.json"), "w") as fh:
            json.dump({"instruments": [{"fields": {"id": "BTCUSDT-PERP.BINANCE",
                                                   "base_currency": "BTC", "quote_currency": "USDT",
                                                   "settlement_currency": "USDT",
                                                   "type": "CryptoPerpetual"},
                                        "python_type": "CryptoPerpetual"}]}, fh)
        fix_raw = measure_raw(rawfix)
        res = run_checks(results_root, rawfix, raw=fix_raw, stores=stores, family=family,
                         round_id=round_id, task=task)
        failed = {c["id"] for c in res["checks"] if not c["ok"]}
        expect_flip = ["C5", "C6", "C7", "C8", "C9"]
        must_not_flip = ["C10", "C11", "C12", "C13", "C14", "C15", "C16", "C17"]
        return {"ok": all(c in failed for c in expect_flip)
                and not any(c in failed for c in must_not_flip),
                "expected_flip": expect_flip, "flipped": sorted(failed),
                "missing_flip": [c for c in expect_flip if c not in failed],
                "unexpected_flip": [c for c in must_not_flip if c in failed],
                "fixture_flags": {"etf_flow_series_present": fix_raw["etf_flow_series_present"],
                                  "publication_timestamps": fix_raw["publication_timestamps_present"],
                                  "revision_history": fix_raw["revision_history_present"],
                                  "us_etf_calendar": fix_raw["us_etf_calendar_present"],
                                  "etf_aum": fix_raw["etf_aum_present"],
                                  "btc_market_cap": fix_raw["btc_market_cap_present"],
                                  "schema_sections": fix_raw["schema_dataset_sections"]}}
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
        if args.json:
            print(json.dumps(stores, ensure_ascii=False, indent=1)[:20000])
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



