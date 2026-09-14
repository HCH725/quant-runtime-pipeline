#!/usr/bin/env python3
"""Selector / neighbourhood replay over the u2 attempt's real historical grid (card t_9afe04ad).

Read-only.  Reads a COPY of `attempts/<run>/artifacts/grid_historical.csv` plus the attempt's
`run-spec.json`, runs the fixed selector (`select_cohort_winner`) and the fixed
`cohort_neighbourhood` on the real production rows, and - for the old/new differential - also
runs the PRE-FIX expression over the same rows to show the production crash reproduces.

The attempt is an immutable FAILED technical attempt (`verdict_hint: INCOMPLETE`): this replay
is a code-path check on its artifacts, never a scientific result, never a verdict input.

Usage (inside the qlib container, production interpreter):
  SB_ENGINE_PATH=/scripts/30_strategy_b_run.py /opt/venv/bin/python \
      replay_selector_neighbourhood.py <run-spec.json> <grid_historical.copy.csv> <out.json>
"""
import csv
import hashlib
import importlib.util
import json
import os
import sys

ENGINE = os.environ.get("SB_ENGINE_PATH", "/scripts/30_strategy_b_run.py")
INT_FIELDS = ("ema_fast", "ema_slow", "wf_train_days", "wf_test_days", "ema_pair_index",
              "walk_forward_index", "episodes")
FLOAT_FIELDS = ("spacing_pct", "size_multiplier", "breakeven_tp_pct", "invalidation_pct",
                "base_quote", "net_pnl", "sharpe")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def load_engine(path):
    spec = importlib.util.spec_from_file_location("sb_engine", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def rows_of(path):
    """CSV -> the row shape `record()` writes (same scalar types the engine compares)."""
    rows = []
    with open(path, newline="") as fh:
        for raw in csv.DictReader(fh):
            row = dict(raw)
            for key in INT_FIELDS:
                row[key] = int(row[key])
            for key in FLOAT_FIELDS:
                text = row[key]
                row[key] = None if text in ("", "None") else float(text)
            rows.append(row)
    return rows


def legacy_tie_break_key(sb, row, axes):
    """The pre-fix expression, verbatim: composite axes indexed with cell_key scalars."""
    return tuple(axes[a].index(sb.cell_key(row)[i]) for i, a in enumerate(sb.AXES))


def axis_key_json(sb, row):
    return [list(v) if isinstance(v, tuple) else v for v in sb.axis_key(row)]


def main():
    if len(sys.argv) != 4:
        sys.stderr.write("usage: replay_selector_neighbourhood.py <run-spec> <grid.csv> <out.json>\n")
        return 2
    spec_path, grid_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
    with open(spec_path) as fh:
        spec = json.load(fh)
    sb = load_engine(ENGINE)
    axes = sb.axis_values(spec)
    rows = rows_of(grid_path)
    out = {
        "schema_version": 1,
        "kind": "strategy_b_v2_selector_neighbourhood_replay",
        "card": "t_9afe04ad",
        "note": ("read-only replay of the immutable FAILED u2 attempt's artifacts - a code-path "
                 "check, never a scientific result, never a verdict input"),
        "engine_path": ENGINE,
        "engine_sha256": sha256_file(ENGINE),
        "engine_version": sb.ENGINE_VERSION,
        "selector_version": sb.SELECTOR_VERSION,
        "run_spec_path": spec_path,
        "run_spec_sha256": sha256_file(spec_path),
        "grid_csv_path": grid_path,
        "grid_csv_sha256": sha256_file(grid_path),
        "rows_total": len(rows),
        "registered_axes": {a: len(axes[a]) for a in sb.AXES},
        "cohorts": {},
    }
    checks = {"all_real_rows_map_to_registered_indices": True,
              "registered_keys_unique_per_cohort": True,
              "selector_ran_without_error": True,
              "neighbourhood_ran_without_error": True,
              "legacy_expression_errors_on_real_rows": {}}
    for sym, tf in sorted({(r["symbol"], r["timeframe"]) for r in rows}):
        label = "%s/%s" % (sym, tf)
        hist = [r for r in rows if (r["symbol"], r["timeframe"]) == (sym, tf)
                and r["window_kind"] == "historical"]
        keys, legacy_errors = set(), {}
        for r in hist:
            keys.add(sb.tie_break_key(r, axes))
            try:
                legacy_tie_break_key(sb, r, axes)
            except ValueError as exc:
                legacy_errors[str(exc)] = legacy_errors.get(str(exc), 0) + 1
        if len(keys) != len(hist):
            checks["registered_keys_unique_per_cohort"] = False
        if any(len(k) != len(sb.AXES) for k in keys):
            checks["all_real_rows_map_to_registered_indices"] = False
        entry = {"historical_rows": len(hist),
                 "distinct_cells": len({sb.cell_key(r) for r in hist}),
                 "distinct_registered_keys": len(keys),
                 "legacy_pre_fix_errors": legacy_errors}
        checks["legacy_expression_errors_on_real_rows"][label] = sum(legacy_errors.values())
        winner, reason = sb.select_cohort_winner(hist, spec)
        entry["selector_reason"] = reason
        entry["winner_cell_key"] = None if winner is None else list(sb.cell_key(winner))
        entry["winner_axis_key"] = None if winner is None else axis_key_json(sb, winner)
        entry["winner_tie_break_key"] = None if winner is None else list(
            sb.tie_break_key(winner, axes))
        if winner is not None:
            entry["neighbourhood"] = sb.cohort_neighbourhood(hist, winner, spec)
        out["cohorts"][label] = entry
    out["checks"] = checks
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w") as fh:
        json.dump(out, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(json.dumps({k: out[k] for k in ("rows_total", "registered_axes", "checks")},
                     indent=2, sort_keys=True))
    print("cohorts: %s" % ", ".join("%s reason=%s neighbours=%s" % (
        label, e["selector_reason"],
        (e.get("neighbourhood") or {}).get("neighbours")) for label, e in
        sorted(out["cohorts"].items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
