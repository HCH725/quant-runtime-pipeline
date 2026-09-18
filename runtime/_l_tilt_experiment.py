#!/usr/bin/env python3
"""Structural comparison of the two candidate readings of the registered materiality tilt.

Rule A (absolute):  |w_i| >= GROSS_CAP / n         (the equal share of the CAPPED book)
Rule B (relative):  |w_i| >= (1/n) * sum_j |w_j|   (the equal share of the CURRENT book; the
                                                   scale-invariant generalisation of the
                                                   registered K-family `w_i >= 1/n` tilt)

Event supply only - no PnL, no DCA, no Sharpe.  Measured BEFORE the round-spec is frozen.
"""
import importlib.util
import json
import os
import sys

import numpy as np

REPO = "/Users/hong/workspace/quant-runtime-pipeline"
ENGINE = os.path.join(REPO, "container", "scripts", "120_conformal_kelly_run.py")
sys.path.insert(0, os.path.join(REPO, "runtime"))
spec = importlib.util.spec_from_file_location("l_engine", ENGINE)
eng = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eng)

import _l_probe as probe  # noqa: E402  (its loaders only)

SYMBOLS = probe.SYMBOLS
times, closes = {}, {}
for s in SYMBOLS:
    times[s], closes[s] = probe.load_closes(s)

HIST_END = eng.utc_ms("2025-09-30") + eng.MS_PER_DAY - 1
OOS_START = eng.utc_ms("2025-10-01")

out = {}
for arm in eng.SCALE_ARMS:
    core = eng._panel_arm_core(arm, {s: closes[s] for s in SYMBOLS})
    w = core["w"]
    n4 = w.shape[1]
    gross = np.abs(w).sum(axis=1, keepdims=True)
    rel = np.divide(np.abs(w), np.maximum(gross / n4, 1e-300))
    stateA = np.where(np.isfinite(w) & (np.abs(w) >= eng.GROSS_CAP / n4 - 1e-12),
                      np.sign(w), 0.0).astype(np.int64)
    stateB = np.where(np.isfinite(w) & (np.abs(w) >= gross / n4 - 1e-12),
                      np.sign(w), 0.0).astype(np.int64)
    stateA[:eng.SIGNAL_WARMUP_BARS, :] = 0
    stateB[:eng.SIGNAL_WARMUP_BARS, :] = 0
    per = {}
    for i, m in enumerate(core["labels"]):
        hm = times[m] <= HIST_END
        om = times[m] >= OOS_START
        row = {}
        for tag, st in (("A", stateA), ("B", stateB)):
            ev = eng._fresh_events(st[:, [i]])[:, 0]
            row[tag] = {"on_bars": int((st[:, i] != 0).sum()),
                        "events_full": int((ev != 0).sum()),
                        "events_hist": int((ev[hm] != 0).sum()),
                        "events_oos": int((ev[om] != 0).sum()),
                        "long": int((st[:, i] > 0).sum()), "short": int((st[:, i] < 0).sum())}
        # theoretical bound on episodes: how many state entries can a rail ever see
        per[m] = row
    out[arm] = per
    print("[%s]" % arm)
    for m in core["labels"]:
        a, b = per[m]["A"], per[m]["B"]
        print("   %-8s A on=%4d ev=%3d/%3d/%3d   B on=%4d ev=%3d/%3d/%3d"
              % (m, a["on_bars"], a["events_hist"], a["events_oos"], a["events_full"],
                 b["on_bars"], b["events_hist"], b["events_oos"], b["events_full"]), flush=True)

with open(os.path.join(REPO, ".kanban-scratch", "l_parts", "l_tilt_experiment.json"), "w") as fh:
    fh.write(json.dumps(out, indent=2, sort_keys=True) + "\n")
print("wrote l_tilt_experiment.json")
