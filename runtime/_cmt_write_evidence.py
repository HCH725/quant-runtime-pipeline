#!/usr/bin/env python3
"""Build the repo evidence snapshot for the prerequisite-gate of family

    continuous-macro-timing-growth-defensive-style-allocation-2026-09-02

Card t_20bec925. Reads the two immutable artifacts, re-runs every checker mode
against the live filesystem and writes ONE evidence file with O_EXCL:

    evidence/<family>-prerequisite-gate-20260918.json

Nothing here is trusted from the authoring run: every number is re-derived.
"""
import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys

REPO = "/Users/hong/workspace/quant-runtime-pipeline"
RESULTS = "/Volumes/ExpansionDrive/qlib-results"
RAW = "/Volumes/ExpansionDrive/market-data-raw"
FAMILY = "continuous-macro-timing-growth-defensive-style-allocation-2026-09-02"
ROUND = FAMILY + "-r1"
TASK = "t_20bec925"
BOARD = "quant-strategy-research"
BOARD_DB = os.path.join(os.path.expanduser("~"), ".hermes/kanban/boards", BOARD,
                        "kanban.db")
RECORD = "/Users/hong/.hermes/wiki/quant/%s.md" % FAMILY
CHECKER = os.path.join(REPO, "runtime",
                       "continuous_macro_timing_growth_defensive_style_allocation"
                       "_prerequisite_check.py")
AUTHOR = os.path.join(REPO, "runtime", "_author_cmt_gate.py")
OUT = os.path.join(REPO, "evidence",
                   "%s-prerequisite-gate-20260918.json" % FAMILY)
POOL = os.path.join(RESULTS, "_handoff", "candidates.json")


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def sha256_text(t):
    return "sha256:" + hashlib.sha256(t.encode("utf-8")).hexdigest()


def load_checker():
    spec = importlib.util.spec_from_file_location("cmt_checker", CHECKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(cmd, timeout=600):
    p = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=timeout)
    import re as _re
    ran = _re.findall(r"Ran \d+ tests? in [0-9.]+s", p.stdout)
    return {"cmd": " ".join(cmd), "rc": p.returncode,
            "ran_line": ran[-1] if ran else None,
            "has_ok_line": bool(_re.search(r"^OK$", p.stdout, _re.M)),
            "stdout_tail": p.stdout[-2000:], "stderr_tail": p.stderr[-800:]}


DRAFT_REMOVALS = []


def main():
    ck = load_checker()
    round_dir = os.path.join(RESULTS, FAMILY, "rounds", ROUND)
    spec_path = os.path.join(round_dir, "round-spec.json")
    verdict_path = os.path.join(round_dir, "verdict.json")
    spec = ck._load_json(spec_path)
    verdict = ck._load_json(verdict_path)

    raw = ck.measure_raw(RAW)
    host = ck.measure_host_stores()
    card = ck._card_body()
    checks = ck.run_checks(results_root=RESULTS, raw_root=RAW, repo_root=REPO,
                           record_path=RECORD, raw=raw, host=host)
    failed = [c["id"] for c in checks if not c["ok"]]
    print("checks: %d failed=%s" % (len(checks), failed), flush=True)
    for c in checks:
        if not c["ok"]:
            print("  FAIL %s %s -> %s" % (c["id"], c["name"],
                                          json.dumps(c["detail"], ensure_ascii=False)[:400]),
                  flush=True)
    vv_db = ck.verify_verbatim(repo_root=REPO, record_path=RECORD, results_root=RESULTS)
    vv_pool = None
    if os.path.isfile(POOL):
        pool = ck._load_json(POOL)
        entry = None
        for e in (pool.get("candidates") or pool.get("entries") or []):
            if e.get("family_id") == FAMILY:
                entry = e
        if entry:
            body = entry.get("card_body")
            if body is None and entry.get("card_body_file") and \
                    os.path.isfile(entry["card_body_file"]):
                body = open(entry["card_body_file"], encoding="utf-8").read()
            if body:
                tmp = os.path.join(REPO, ".kanban-scratch/cmt_parts/pool_card_body.md")
                with open(tmp, "w", encoding="utf-8") as fh:
                    fh.write(body)
                vv_pool = ck.verify_verbatim(repo_root=REPO, record_path=RECORD,
                                             results_root=RESULTS, card_body_path=tmp)
                vv_pool["pool_body_sha256"] = sha256_text(body)
                vv_pool["pool_body_matches_db_card_body"] = body == card

    host_check = run([sys.executable,
                      CHECKER, "--host-scan"], timeout=900)
    self_test = ck.self_test(results_root=RESULTS, repo_root=REPO, raw=raw, host=host)
    fixture = ck.raw_fixture_control(repo_root=REPO, results_root=RESULTS)
    excluded = ck.excluded_token_pass(RAW)
    preflight = run([sys.executable, os.path.join(REPO, "runtime", "preflight.py"), "--json"])
    suite = run([sys.executable, "-m", "unittest", "discover", "-s", "runtime/tests",
                 "-t", "runtime/tests"], timeout=900)
    author_rerun = run([sys.executable, AUTHOR])

    card = ck._card_body()
    fam = ck._load_json(os.path.join(RESULTS, FAMILY, "family.json"))
    inst = raw["instrument_surface"]
    ks = raw["kline_surface"]
    status_counts = spec["universe_registration"]["required_data_matrix_status_counts"]

    dr = os.path.join(REPO, ".kanban-scratch/cmt_parts/draft_removals.json")
    global DRAFT_REMOVALS
    if os.path.isfile(dr):
        DRAFT_REMOVALS = json.load(open(dr))
    ev = {
        "schema_version": 1,
        "kind": "prerequisite-gate-evidence",
        "family_id": FAMILY,
        "round_id": ROUND,
        "kanban_task_id": TASK,
        "kanban_board": BOARD,
        "created_at_utc": "2026-09-18T07:45:00Z",
        "outcome": "PREREQUISITE_ABSENT",
        "terminal": {
            "verdict": verdict["verdict"],
            "performance_claimable": verdict["performance_claimable"],
            "failure_layer": verdict["failure"]["layer"],
            "failure_class": verdict["failure"]["class"],
            "failure_class_note": verdict["failure"]["failure_class_note"],
            "last_run_id": verdict["run_id"],
            "attempts": verdict["attempts"],
            "cohorts": verdict["cohorts"],
            "survivors": verdict["survivors"],
            "survivor_bundle": verdict["survivor_bundle"],
            "yield_decision": verdict["yield"]["yield_decision"],
            "final_verdict_written_by": verdict["decided_by"],
        },
        "artifact_hashes": {
            "round_spec": {"path": spec_path, "sha256": sha256_file(spec_path),
                           "bytes": os.path.getsize(spec_path)},
            "verdict": {"path": verdict_path, "sha256": sha256_file(verdict_path),
                        "bytes": os.path.getsize(verdict_path)},
            "checker": {"path": CHECKER, "sha256": sha256_file(CHECKER)},
            "authoring_script": {"path": AUTHOR, "sha256": sha256_file(AUTHOR)},
            "sources": {
                "card_body": {"sha256": sha256_text(card), "chars": len(card),
                              "source": BOARD_DB},
                "canonical_record": {"sha256": sha256_file(RECORD),
                                     "path": RECORD},
                "family_json": {"sha256": sha256_file(os.path.join(RESULTS, FAMILY,
                                                                  "family.json")),
                                "path": os.path.join(RESULTS, FAMILY, "family.json"),
                                "semantic_fingerprint": fam["semantic_fingerprint"]},
                "contract": {"sha256": sha256_file(os.path.join(
                    REPO, "QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md"))},
            },
            "verdict_source_map_entries": len(spec["excerpt_source_map"]),
        },
        "measured_absence": verdict["failure"]["definitive_absences"],
        "measured_available": spec["prerequisite_gate"]["measured_available"],
        "raw_measurement": {
            "raw_root": raw["raw_root"],
            "files_walked": raw["files_walked"],
            "payload_files_probed": raw["payload_files_probed"],
            "payload_bytes_probed": raw["payload_bytes_probed"],
            "kline_files_probed": raw["kline_files_probed"],
            "probe_read_cap_bytes": raw["probe_read_cap_bytes"],
            "probe_max_depth": raw["probe_max_depth"],
            "token_count": raw["token_count"],
            "decisive_token_count": raw["decisive_token_count"],
            "dataset_families": raw["dataset_families"],
            "market_dirs": raw["market_dirs"],
            "instrument_ids": inst["instrument_ids"],
            "instrument_field_count": inst["field_count"],
            "instrument_has_maturity_field": inst["has_maturity_field"],
            "instrument_has_options_or_iv_field": inst["has_options_or_iv_field"],
            "kline_row_key_sets": raw["kline_row_key_sets"],
            "kline_intervals": ks["intervals"],
            "finest_resolved_interval": ks["finest_resolved_interval"],
            "kline_1d_windows": {s: {"bars": v["bars"],
                                     "first": v["first_bar_open_utc"],
                                     "last": v["last_bar_open_utc"],
                                     "contiguous": v["contiguous"]}
                                 for s, v in raw["kline_1d_windows"].items()},
            "funding_observations": {s: v["observations"]
                                     for s, v in raw["funding_surface"].items()},
            "funding_truth_status": {s: v["truth_status_counts"]
                                     for s, v in raw["funding_surface"].items()},
            "decisive_group_unexplained": raw["decisive_group_unexplained"],
            "decisive_group_declared_hits": raw["decisive_group_declared_hits"],
            "decisive_probe_hits_unexplained_total":
                raw["decisive_probe_hits_unexplained_total"],
            "decisive_carried_by_row_shape": raw["decisive_carried_by_row_shape"],
            "present_token_positive_control": raw["present_token_positive_control"],
            "schema_documents_coverage_limit": raw["schema_documents_coverage_limit"],
            "schema_dataset_headers": raw["schema_dataset_headers"],
            "state_last_run_utc": raw["state"]["last_run_utc"],
        },
        "required_data_matrix_status_counts": status_counts,
        "required_data_matrix_rows": len(spec["universe_registration"]["required_data_matrix"]),
        "decisive_matrix_item_count": spec["prerequisite_gate"]["decisive_matrix_item_count"],
        "decisive_status_map": spec["prerequisite_gate"]["decisive_status_map"],
        "declared_prose_allowances": {tok: raw["probe"][tok]["declared_allowed"]
                                      for tok in ("option", "open_interest")},
        "other_local_stores": {
            "scan_roots": host["roots"],
            "files_scanned": host["files_scanned"],
            "series_hit_classes": host["series_hit_classes"],
            "series_hits": host["series_hits"],
            "prose_hits_count": len(host["prose_hits"]),
            "unclassified": host["unclassified"],
            "measured_substitutes": host["measured_substitutes"],
            "note": host["note"],
        },
        "verification": {
            "checker_default_all_pass": {"checks": len(checks), "failed": failed,
                                         "ok": not failed},
            "checker_host_scan_all_pass": host_check,
            "self_test": self_test,
            "raw_fixture_control": fixture,
            "verify_verbatim_board_db": vv_db,
            "verify_verbatim_pool_body": vv_pool,
            "excluded_token_pass": {"tokens": {k: {"raw_entry_name_hit_count":
                                                   v["raw_entry_name_hit_count"],
                                                   "raw_payload_hits": v["raw_payload_hits"]}
                                               for k, v in excluded["tokens"].items()},
                                    "host_series_name_hits": excluded["host_series_name_hits"]},
            "preflight": preflight,
            "repo_suite": suite,
            "author_rerun_refused": author_rerun,
        },
        "no_fabrication": {
            "attempts_launched": verdict["attempts"]["launched"],
            "run_specs": verdict["attempts"]["run_specs"],
            "terminal_sentinels": verdict["attempts"]["terminal_sentinels"],
            "run_id": verdict["run_id"],
            "evidence_run_ids": verdict["evidence_run_ids"],
            "performance_fields": ck._no_performance_claims(verdict) +
                                  ck._no_performance_claims(spec),
            "falsification_battery_executed": False,
        },
        "readback": {
            "round_dir": round_dir,
            "round_dir_contents": sorted(os.listdir(round_dir)),
            "attempt_dir_exists": os.path.exists(os.path.join(round_dir, "attempts")),
            "survivor_bundle_exists": os.path.exists(os.path.join(round_dir,
                                                                  "survivor-bundle.json")),
            "evidence_file": OUT,
        },
        "disclosures": [
            "contract 13 has no dedicated prerequisite-missing class; `data_window_invalid` "
            "is used as the closest registered card-local class (its definition covers "
            "'instrument 缺失') with the contract 6.4 prerequisite-missing alignment as the "
            "terminal basis; whether a class should be added is the operator's call",
            "the decisive absence is a core-signal DATA FAMILY (an implied-volatility "
            "series: the record's VIX slot, whose crypto replacement the record itself "
            "names as Deribit DVOL), not the window and not the traded universe - so the "
            "local universe rule of the lifecycle footer cannot rescue it",
            "the card's falsification excerpt is truncated relative to the canonical record: "
            "the card quotes items 1-3 and the record carries 4; item 4 (Subperiod Breakdown) "
            "was restored verbatim from the record and the difference is disclosed in the "
            "round-spec instead of rewriting the frozen card bytes",
            "the card's required-data and portability excerpts carry truncation markers; the "
            "full record sections were registered verbatim alongside them",
            "the store is a live daily-updated store: every number in the artifacts is the "
            "value measured at pre-registration; the mean/present token counts and the "
            "window endpoints move as the updater runs, while the decisive structure "
            "(0 unexplained decisive hits, uniform 7-key k-line rows, 3 dataset families) "
            "does not",
            "the two declared prose allowances are audited single-line occurrences in the "
            "store's own documents (INVENTORY.md's LEAN dataset-directory name that was "
            "explicitly NOT migrated and whose tree no longer exists on this host; "
            "INVENTORY.md's Phase-1 mdfind query token list; the updater README's CLI "
            "'Options:' table header); any new occurrence is offending and flips the check",
            "networking was not used: the missing prerequisite IS the terminal condition",
        ],
        "draft_removals": DRAFT_REMOVALS,
    }

    with open(OUT, "x", encoding="utf-8") as fh:
        json.dump(ev, fh, indent=1, ensure_ascii=False)
    print("wrote %s (%s)" % (OUT, sha256_file(OUT)))
    print("checks: %d failed=%s" % (len(checks), failed))
    print("verify_verbatim: %s" % json.dumps({k: vv_db[k] for k in
                                              ("verbatim_leaves_checked",
                                               "source_map_entries", "ok")}))
    print("self_test ok=%s variants=%d" % (self_test["ok"], len(self_test["variants"])))
    print("fixture ok=%s failing=%s" % (fixture["ok"], fixture["failing_checks"]))
    print("preflight rc=%s" % preflight["rc"])
    print("suite rc=%s tail=%s" % (suite["rc"], suite["stdout_tail"][-200:]))
    print("author rerun rc=%s err=%s" % (author_rerun["rc"],
                                         author_rerun["stderr_tail"][-200:]))


if __name__ == "__main__":
    main()
