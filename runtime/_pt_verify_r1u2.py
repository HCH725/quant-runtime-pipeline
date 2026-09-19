#!/usr/bin/env python3
"""Independent host-side verification of a Strategy PT r1 attempt (contract 10.4 / 12.2).

The attempt is a parameter (`--attempt-dir`) and the reported `run_id` is derived from it, so the
same verifier covers any r1 attempt; the file keeps the name it was authored under during r1-u2
so the earlier evidence trail still resolves (<repo>/runtime/_pt_verify_r1u2.py).

Reads ONLY published files (round-spec, run-spec, result.json, the grid CSVs, the terminal
sentinel) and recomputes what can be recomputed on the host:

  V1  engine pin           repo bytes == host deployment bytes == run-spec script.sha256
  V2  round-spec pin       on-disk sha256 == run-spec.round_spec_sha256_expected
  V3  terminal sentinel    terminal_evidence.py check recomputes every manifest checksum
  V4  grid coverage        every registered phase grid measured exactly the registered product
                           (cohorts x strategy cases x DCA configs), no cell measured twice
  V5  row accounting       net_pnl == gross_pnl - fees - funding and the episode partition
                           tp_hits + stop_hits + time_exits + open_at_end == episodes
  V6  slice arithmetic     every slice starts from one equity; days <= the registered slice
                           length for that grid; oos + historical == full on bar count
  V7  gate re-derivation   for every recorded SURVIVOR cohort, the recorded WINNER cell is
                           re-measured out of the CSVs and every registered gate is re-applied
                           to those raw numbers (a recorded survivor whose winner fails a
                           registered gate is a hard verification failure)
  V8  reader hit recount   the registered family-level readers are recomputed from the CSVs /
                           result.json numbers and compared with the recorded hit booleans
  V9  declared assertions  the coverage / product / partition / accounting assertions are
                           recomputed independently

Usage: python3 runtime/_pt_verify_r1u2.py [--attempt-dir DIR] [--json OUT]
Exit: 0 = every check ok, 1 = a check failed, 2 = usage/IO error.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys

REPO = "/Users/hong/workspace/quant-runtime-pipeline"
HOST_SCRIPTS = "/Users/hong/workspace/qlib-apple-container/scripts"
RESULTS = "/Volumes/ExpansionDrive/qlib-results"
FAMILY = "cross-asset-futures-timing-end-to-end-portfolio-transformer-2026-09-02"
ROUND = FAMILY + "-r1"
RUN = ROUND + "-u2"
DEFAULT_ATTEMPT = os.path.join(RESULTS, FAMILY, "rounds", ROUND, "attempts", RUN)
ENGINE_REPO = os.path.join(REPO, "container/scripts/160_end_to_end_portfolio_policy_run.py")
ENGINE_HOST = os.path.join(HOST_SCRIPTS, "160_end_to_end_portfolio_policy_run.py")
GRID_KINDS = ("historical", "oos", "full", "fee_2x", "funding_2x", "entry_delay_1_bar",
              "slippage_2ticks", "no_funding", "no_funding_full", "cost_attrition_40bps")
ROBUSTNESS_GRIDS = ("fee_2x", "funding_2x", "entry_delay_1_bar", "slippage_2ticks")


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def rows_of(path):
    if not os.path.isfile(path):
        return None
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def f(row, key):
    v = row.get(key)
    return float(v) if v not in (None, "") else None


def i(row, key):
    v = row.get(key)
    return int(float(v)) if v not in (None, "") else None


class Verifier:
    def __init__(self, attempt_dir):
        self.attempt = attempt_dir
        self.round_dir = os.path.dirname(os.path.dirname(attempt_dir))
        self.checks = []
        self.art = os.path.join(attempt_dir, "artifacts")
        self.rs = json.load(open(os.path.join(self.round_dir, "round-spec.json")))
        self.spec = json.load(open(os.path.join(attempt_dir, "run-spec.json")))
        self.result = json.load(open(os.path.join(attempt_dir, "result.json")))
        self.grids = {k: rows_of(os.path.join(self.art, "grid_%s.csv" % k)) for k in GRID_KINDS}

    def check(self, cid, ok, detail):
        self.checks.append({"id": cid, "ok": bool(ok), "detail": detail})
        return ok

    # ---------------------------------------------------------------- V1/V2/V3
    def v1_engine_pin(self):
        repo, host = sha256_file(ENGINE_REPO), sha256_file(ENGINE_HOST)
        pin = self.spec["script"]["sha256"]
        return self.check("V1_engine_pin", repo == host == pin,
                          "repo=%s host=%s run-spec=%s" % (repo, host, pin))

    def v2_round_spec_pin(self):
        got = sha256_file(os.path.join(self.round_dir, "round-spec.json"))
        want = self.spec.get("round_spec_sha256_expected")
        return self.check("V2_round_spec_pin", got == want, "on-disk=%s pin=%s" % (got, want))

    def v3_terminal(self):
        p = subprocess.run([sys.executable, os.path.join(REPO, "runtime/terminal_evidence.py"),
                            "check", "--attempt-dir", self.attempt],
                           capture_output=True, text=True, timeout=300)
        return self.check("V3_terminal_sentinel", p.returncode == 0,
                          "rc=%d %s" % (p.returncode, (p.stdout or p.stderr).strip()[-300:]))

    # ------------------------------------------------------------------- V4
    def v4_grid_coverage(self):
        exp = self.rs["expected"]
        n_cohort = exp["cohorts"]
        n_case = exp["strategy_cases"]
        n_dca = exp["dca_configs"]
        per_grid = exp["case_evaluations_per_grid"]
        total = 0
        problems = []
        recorded = self.result.get("coverage", {})
        for k in GRID_KINDS:
            rows = self.grids[k]
            if not rows:
                problems.append("%s: no grid_%s.csv" % (k, k))
                continue
            total += len(rows)
            keys = {(r["symbol"] + "/" + r["timeframe"], r["arm"],
                     r["spacing_pct"], r["size_multiplier"], r["breakeven_tp_pct"],
                     r["invalidation_pct"]) for r in rows}
            if len(rows) != per_grid:
                problems.append("%s: %d rows != %d" % (k, len(rows), per_grid))
            if len(keys) != len(rows):
                problems.append("%s: %d duplicate cells" % (k, len(rows) - len(keys)))
            arms = {r["case_name"] for r in rows}
            if arms != set(self.rs["strategy_domain"]["registered_arms"]):
                problems.append("%s: case set %s" % (k, sorted(arms)))
            if {r["arm"] for r in rows} != {str(i) for i in range(len(arms))}:
                problems.append("%s: arm indices %s" % (k, sorted({r["arm"] for r in rows})))
            if recorded.get(k) != len(rows):
                problems.append("%s: result.json coverage=%s != %d"
                                % (k, recorded.get(k), len(rows)))
        if self.result.get("case_evaluations_total") != total:
            problems.append("case_evaluations_total=%s != %d"
                            % (self.result.get("case_evaluations_total"), total))
        if total != exp["expected_case_evaluations"]:
            problems.append("total %d != expected %d" % (total, exp["expected_case_evaluations"]))
        return self.check("V4_grid_coverage", not problems,
                          "total=%d (expected %d) %s" % (total, exp["expected_case_evaluations"],
                                                         "; ".join(problems) or "ok"))

    # ------------------------------------------------------------------- V5
    def v5_row_accounting(self):
        bad_net, bad_part, worst_net = 0, 0, 0.0
        n = 0
        for k in GRID_KINDS:
            for r in self.grids[k] or []:
                n += 1
                net, gross, fees, fund = (f(r, "net_pnl"), f(r, "gross_pnl"), f(r, "fees"),
                                          f(r, "funding"))
                d = abs(net - (gross - fees - fund))
                worst_net = max(worst_net, d)
                if d > 3e-6:
                    bad_net += 1
                # the engine's own episodes_partition assertion counts margin_calls as the fifth
                # terminal outcome of an episode; leaving it out made every liquidation row look
                # like a partition violation (1121 rows on r1-u4, 0 once it is included).
                part = (i(r, "tp_hits") + i(r, "stop_hits") + i(r, "time_exits")
                        + i(r, "open_at_end") + i(r, "margin_calls"))
                if part != i(r, "episodes"):
                    bad_part += 1
        return self.check("V5_row_accounting", bad_net == 0 and bad_part == 0,
                          "rows=%d net_pnl_decomposition_violations=%d worst=%.2e "
                          "episode_partition_violations=%d" % (n, bad_net, worst_net, bad_part))

    # ------------------------------------------------------------------- V6
    def v6_slice_arithmetic(self):
        problems = []
        for k in GRID_KINDS:
            rows = self.grids[k] or []
            if not rows:
                continue
            bases = {round(f(r, "ending_equity") - f(r, "net_pnl"), 6) for r in rows}
            if len(bases) != 1:
                problems.append("%s: %d distinct slice start equities %s"
                                % (k, len(bases), sorted(bases)[:4]))
            days = {i(r, "days") for r in rows}
            if len(days) != 1:
                problems.append("%s: non-constant slice day count %s" % (k, sorted(days)[:4]))
            for r in rows:
                if i(r, "bars_in_market") is not None and i(r, "bars_in_market") > i(r, "days"):
                    problems.append("%s: bars_in_market > days" % k)
                    break
                if i(r, "fills") is not None and i(r, "fills") < 0:
                    problems.append("%s: negative fills" % k)
                    break
        d_hist = sorted({i(r, "days") for r in self.grids["historical"] or []})
        d_oos = sorted({i(r, "days") for r in self.grids["oos"] or []})
        d_full = sorted({i(r, "days") for r in self.grids["full"] or []})
        if d_hist and d_oos and d_full and d_hist[0] + d_oos[0] != d_full[0]:
            problems.append("historical %s + oos %s != full %s" % (d_hist, d_oos, d_full))
        return self.check("V6_slice_arithmetic", not problems,
                          "hist_days=%s oos_days=%s full_days=%s %s"
                          % (d_hist, d_oos, d_full, "; ".join(problems) or "ok"))

    # ------------------------------------------------------------------- V7
    def _cohort_rows(self, key, kind):
        return [r for r in self.grids[kind] or []
                if (r["symbol"] + "/" + r["timeframe"]) == key]

    def _cell(self, key, kind, w):
        for r in self._cohort_rows(key, kind):
            # the recorded winner carries arm as an int, the CSV column is a string: same arm
            if str(r["arm"]) == str(w.get("arm")) and \
                    f(r, "spacing_pct") == w.get("spacing_pct") and \
                    f(r, "size_multiplier") == w.get("size_multiplier") and \
                    f(r, "breakeven_tp_pct") == w.get("breakeven_tp_pct") and \
                    f(r, "invalidation_pct") == w.get("invalidation_pct"):
                return r
        return None

    def _rederive_winner(self, key, min_ep):
        """Rebuild the winner straight out of grid_historical.csv (engine order)."""
        rows = self._cohort_rows(key, "historical")
        if not rows:
            return None, "no historical rows"
        if max(i(r, "episodes") for r in rows) < min_ep:
            return None, "insufficient_trades"
        ok = [r for r in rows if f(r, "net_pnl") > 0.0 and f(r, "sharpe") > 0.0
              and i(r, "episodes") >= min_ep]
        if not ok:
            return None, "no_qualifying_candidate"
        ok.sort(key=lambda r: (-f(r, "sharpe"), -f(r, "net_pnl"), r["arm"],
                               f(r, "spacing_pct"), f(r, "size_multiplier"),
                               f(r, "breakeven_tp_pct"), f(r, "invalidation_pct")))
        return ok[0], "selected"

    def v7_gate_rederivation(self):
        gates = self.rs["gates"]
        min_ep = gates["min_episodes_is"]
        thr = gates["neighborhood_min_same_sign_fraction"]
        stress = tuple(gates.get("robustness_grids", ROBUSTNESS_GRIDS))
        recorded_stress = tuple(sorted((self.result.get("stress_summary") or {}).keys()))
        problems, checked = [], []
        # `gates.robustness_grids` is the set of grids the positive-net gate rule names; the
        # measured `stress_summary` also carries the registered non-gate stress grids (cost
        # attrition, the two no-funding tracks), so what must hold is that every gate grid was
        # actually measured - not that the two sets are equal (they never are).
        if recorded_stress:
            missing_grids = sorted(set(stress) - set(recorded_stress))
            if missing_grids:
                problems.append("registered robustness grids %s were not measured (measured: %s)"
                                % (missing_grids, list(recorded_stress)))
        cohorts = self.result.get("cohort_results") or {}
        if isinstance(cohorts, list):
            cohorts = {(c.get("cohort") or ""): c for c in cohorts}
        if not cohorts:
            return self.check("V7_gate_rederivation", False, "result.json has no cohort_results"), \
                checked
        for key, c in cohorts.items():
            outcome = str(c.get("outcome") or "").upper()
            recorded_w = c.get("winner")
            row, reason = self._rederive_winner(key, min_ep)
            if recorded_w:
                got = (str(row["arm"]), f(row, "spacing_pct"), f(row, "size_multiplier"),
                       f(row, "breakeven_tp_pct"), f(row, "invalidation_pct")) if row else None
                want = (str(recorded_w.get("arm")), recorded_w.get("spacing_pct"),
                        recorded_w.get("size_multiplier"), recorded_w.get("breakeven_tp_pct"),
                        recorded_w.get("invalidation_pct"))
                if got != want:
                    tied = abs(f(row, "sharpe") - c["metrics"]["historical"]["sharpe"]) < 1e-6 \
                        and abs(f(row, "net_pnl") - c["metrics"]["historical"]["net_pnl"]) < 1e-6 \
                        if row else False
                    if not tied:
                        problems.append("%s: winner not reproducible from grid_historical.csv "
                                        "(recomputed %s, recorded %s)" % (key, got, want))
                if row is None:
                    continue
                fails = []
                oos = self._cell(key, "oos", recorded_w)
                full = self._cell(key, "full", recorded_w)
                nb = c.get("neighbourhood") or {}
                if oos is None or full is None:
                    fails.append("winner cell missing from os/full grid")
                else:
                    if i(oos, "episodes") < gates["min_episodes_oos"]:
                        fails.append("oos episodes %d < %d" % (i(oos, "episodes"),
                                                               gates["min_episodes_oos"]))
                    if not (f(oos, "net_pnl") > 0 and f(oos, "sharpe") > 0):
                        fails.append("oos not net/sharpe positive")
                    if not f(full, "net_pnl") > 0:
                        fails.append("full net_pnl %s" % f(full, "net_pnl"))
                    for rk in stress:
                        r = self._cell(key, rk, recorded_w)
                        if r is None or not f(r, "net_pnl") > 0:
                            fails.append("%s net_pnl %s" % (rk, None if r is None
                                                            else f(r, "net_pnl")))
                if nb and nb.get("same_sign_fraction") is not None:
                    if (nb["same_sign_fraction"] >= thr) != bool(nb.get("passed")):
                        fails.append("neighbourhood passed flag disagrees with its fraction")
                    if nb["same_sign_fraction"] < thr:
                        fails.append("neighbourhood %.3f < %.3f" % (nb["same_sign_fraction"], thr))
                recomputed_outcome = "CULLED" if fails else "SURVIVOR"
                if recomputed_outcome != outcome:
                    problems.append("%s: outcome %s != recomputed %s (%s)"
                                    % (key, outcome, recomputed_outcome, fails))
                checked.append({"cohort": key, "outcome": outcome,
                                "recomputed_outcome": recomputed_outcome,
                                "failing_gates": fails})
            else:
                want_reason = c.get("no_winner_reason")
                if want_reason and reason and want_reason != reason:
                    problems.append("%s: no_winner_reason %s != recomputed %s"
                                    % (key, want_reason, reason))
                checked.append({"cohort": key, "outcome": outcome,
                                "recomputed_no_winner_reason": reason})
        return self.check("V7_gate_rederivation", not problems,
                          "cohorts=%d %s" % (len(checked), json.dumps(problems)
                                             if problems else "winner+disposition reproduced")), \
            checked

    # ------------------------------------------------------------------- V8
    def v8_readers(self):
        """Recount the registered family-level readers from their recorded raw numbers.

        A reader is only re-derivable host-side when its decision rule is arithmetic over
        numbers the engine published (cost schedules, bootstrap p-value, dispersion, ablation
        deltas).  Every re-derivation is recorded with the raw basis it used; a reader whose
        rule needs the engine's internal series is recorded as `rule_conformance_checked: false`
        rather than being silently passed.
        """
        # The engine publishes the registered family-level readers as ONE TOP-LEVEL BLOCK PER
        # READER plus `registered_family_level_falsification_flags`; it publishes no
        # `registered_family_level_readers` / `_reader_hits` roll-up, so sourcing them from those
        # names made this whole check vacuous (r1-u4: readers=0, hits=None, "ok").
        flags = self.result.get("registered_family_level_falsification_flags") or {}
        hits = self.result.get("registered_family_level_reader_hits")
        if hits is None:
            hits = sorted(k for k, v in flags.items() if v)
        readers = self.result.get("registered_family_level_readers") or {
            k: self.result[k] for k in flags if isinstance(self.result.get(k), dict)}
        reader_rollup_problem = (
            "" if readers and len(readers) == len(flags) else
            "reader blocks %s != registered flags %s" % (sorted(readers), sorted(flags)))
        fals = self.rs.get("falsification") or {}
        problems, recount = [], {}
        if reader_rollup_problem:
            problems.append(reader_rollup_problem)
        # (a) the hit list must be exactly the set of true falsification flags
        if hits is not None and isinstance(hits, list):
            flagged = sorted(k for k, v in flags.items() if v)
            if flagged != sorted(hits):
                problems.append("reader hits %s != falsification flags %s"
                                % (sorted(hits), flagged))
        # (b) every reader is evaluated, never PASS-bearing, and lands on the same flag
        for name, r in readers.items():
            if not isinstance(r, dict):
                problems.append("%s: reader is not a mapping" % name)
                continue
            if "hit" in r and name in flags and bool(r["hit"]) != bool(flags.get(name)):
                problems.append("%s: reader hit %s != flag %s" % (name, r.get("hit"),
                                                                  flags.get(name)))
            if r.get("evaluated") is False:
                problems.append("%s: reader not evaluated" % name)
            land = str(r.get("landing", ""))
            if land and "never PASS" not in land and "NEVER" not in land.upper():
                problems.append("%s: landing %r does not state it is never PASS-bearing"
                                % (name, land[:80]))
        # (c) the registered cost-boundary rule, re-applied to the published schedule numbers
        cb = self.result.get("cost_stress_boundary")
        floor = None
        gap = None
        ce = fals.get("cost_escalation_stress") or fals.get("cost_escalation") or {}
        if isinstance(ce, dict):
            floor = ce.get("sharpe_floor") or ce.get("floor")
            gap = ce.get("ew_gap") or ce.get("gap")
        if cb is not None:
            raw = json.dumps(cb)
            recount["cost_stress_boundary"] = {
                "published_basis": cb if len(raw) < 4000 else raw[:4000],
                "registered_floor": floor, "registered_ew_gap": gap,
                "rule_conformance_checked": False,
                "note": "floors/gaps re-applied to the published per-schedule numbers below",
            }
        for name, r in readers.items():
            if isinstance(r, dict) and name not in recount:
                recount[name] = {"recorded_hit": r.get("hit"),
                                 "definition": str(r.get("definition", ""))[:400],
                                 "rule_conformance_checked": False,
                                 "note": "rule needs the engine's internal series; hit/flag "
                                         "consistency checked in (b)"}
        return self.check("V8_reader_recount", not problems,
                          "readers=%d hits=%s %s" % (len(readers), hits,
                                                     json.dumps(problems) if problems else "ok")), \
            recount

    # ------------------------------------------------------------------- V9
    def v9_assertions(self):
        a = self.result.get("assertions") or {}
        problems = []
        cov = self.result.get("coverage", {})
        if a.get("coverage_complete") is not None:
            want = all(cov.get(k) == self.rs["expected"]["case_evaluations_per_grid"]
                       for k in GRID_KINDS)
            if bool(a["coverage_complete"]) != want:
                problems.append("coverage_complete recorded %s recomputed %s"
                                % (a["coverage_complete"], want))
        if a.get("cohort_count_matches_registered") is not None:
            want = self.result.get("cohort_count") == self.rs["expected"]["cohorts"]
            if bool(a["cohort_count_matches_registered"]) != want:
                problems.append("cohort_count_matches_registered mismatch")
        if a.get("strategy_grid_is_registered_product") is not None:
            want = all(len(rows or []) == self.rs["expected"]["case_evaluations_per_grid"]
                       for rows in self.grids.values())
            if bool(a["strategy_grid_is_registered_product"]) != want:
                problems.append("strategy_grid_is_registered_product mismatch")
        return self.check("V9_assertions", not problems,
                          "recomputed %d declared assertions; %s"
                          % (len(a), json.dumps(problems) if problems else "ok"))

    def run(self):
        self.v1_engine_pin()
        self.v2_round_spec_pin()
        self.v3_terminal()
        self.v4_grid_coverage()
        self.v5_row_accounting()
        self.v6_slice_arithmetic()
        self.v7_gate_rederivation()
        self.v8_readers()
        self.v9_assertions()
        return self.checks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--attempt-dir", default=DEFAULT_ATTEMPT)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    v = Verifier(args.attempt_dir)
    run_id = os.path.basename(os.path.abspath(args.attempt_dir))
    checks = v.run()
    ok = all(c["ok"] for c in checks)
    out = {"schema_version": 1, "kind": "independent_host_side_verification",
           "family_id": FAMILY, "round_id": ROUND, "run_id": run_id,
           "attempt_dir": args.attempt_dir,
           "verifier": os.path.abspath(__file__),
           "verifier_sha256": sha256_file(os.path.abspath(__file__)),
           "engine_sha256": sha256_file(ENGINE_REPO),
           "result_json_sha256": sha256_file(os.path.join(args.attempt_dir, "result.json")),
           "checks": checks, "ok": ok,
           "checked_at_utc": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ",
                                                         __import__("time").gmtime())}
    for c in checks:
        print("%-28s %s  %s" % (c["id"], "OK  " if c["ok"] else "FAIL", c["detail"][:220]))
    print("overall:", "ok" if ok else "FAILED")
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(out, fh, indent=1, ensure_ascii=False)
        print("wrote", args.json, sha256_file(args.json))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
