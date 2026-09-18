#!/usr/bin/env python3
"""Rule C (pure sign) structural measurement: event supply of the record's signed allocation."""
import importlib.util
import os
import sys

import numpy as np

REPO = "/Users/hong/workspace/quant-runtime-pipeline"
spec = importlib.util.spec_from_file_location(
    "l_engine", os.path.join(REPO, "container/scripts/120_conformal_kelly_run.py"))
eng = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eng)
sys.path.insert(0, os.path.join(REPO, "runtime"))
import _l_probe as probe  # noqa: E402

times, closes = {}, {}
for s in probe.SYMBOLS:
    times[s], closes[s] = probe.load_closes(s)
HIST_END = eng.utc_ms("2025-09-30") + eng.MS_PER_DAY - 1
OOS_START = eng.utc_ms("2025-10-01")
for arm in eng.SCALE_ARMS:
    core = eng._panel_arm_core(arm, {s: closes[s] for s in probe.SYMBOLS})
    w = core["w"]
    state = np.where(np.isfinite(w) * (np.abs(w) > 1e-12), np.sign(w), 0.0).astype(np.int64)
    state[:eng.SIGNAL_WARMUP_BARS, :] = 0
    print("[%s]" % arm)
    for i, m in enumerate(core["labels"]):
        hm = times[m] <= HIST_END
        om = times[m] >= OOS_START
        ev = eng._fresh_events(state[:, [i]])[:, 0]
        on = int((state[:, i] != 0).sum())
        print("   %-8s on=%4d long=%4d short=%4d events hist=%3d oos=%3d full=%3d"
              % (m, on, int((state[:, i] > 0).sum()), int((state[:, i] < 0).sum()),
                 int((ev[hm] != 0).sum()), int((ev[om] != 0).sum()), int((ev != 0).sum())))
