#!/usr/bin/env python3
"""Pre-freeze feasibility probe of the Strategy L (Conformal Kelly) engine.

Measures, with the SAME kernel the engine inlines, the event supply / coverage / gross-cap and
dial structure of every registered scale-estimator arm on the registered local universe, BEFORE
the round-spec is frozen.  Event supply only: no PnL, no DCA, no Sharpe is computed here.

usage:  /opt/homebrew/bin/python3 runtime/_l_probe.py [--out <json>]
"""
import argparse
import gzip
import importlib.util
import json
import os
import time

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(REPO, "container", "scripts", "120_conformal_kelly_run.py")
RAW = "/Volumes/ExpansionDrive/market-data-raw/binance/usdm"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT")
START, END = "2022-01-01", "2026-09-11"
MS_PER_DAY = 86400000

spec = importlib.util.spec_from_file_location("l_engine", ENGINE)
eng = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eng)


def utc_ms(date_str):
    import calendar
    import time as _t
    return int(calendar.timegm(_t.strptime(date_str + " 00:00:00", "%Y-%m-%d %H:%M:%S"))) * 1000


def load_closes(symbol):
    d = os.path.join(RAW, "klines", symbol, "1d")
    lo, hi = utc_ms(START), utc_ms(END) + MS_PER_DAY - 1
    rows = []
    for name in sorted(os.listdir(d)):
        if not name.endswith(".jsonl.gz"):
            continue
        month = name[len(symbol) + 4:-len(".jsonl.gz")]
        if not (START[:7] <= month <= END[:7]):
            continue
        with gzip.open(os.path.join(d, name), "rt") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                ms = int(rec["open_time_ms"])
                if ms < lo or ms > hi:
                    continue
                rows.append((ms, float(rec["close"])))
    rows.sort()
    return np.array([r[0] for r in rows], dtype=np.int64), np.array([r[1] for r in rows],
                                                                    dtype=np.float64)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(REPO, ".kanban-scratch", "l_parts",
                                                  "l_probe.json"))
    args = ap.parse_args()
    t0 = time.time()
    times, closes = {}, {}
    for s in SYMBOLS:
        times[s], closes[s] = load_closes(s)
    n = min(len(closes[s]) for s in SYMBOLS)
    assert all(len(closes[s]) == n for s in SYMBOLS), [len(closes[s]) for s in SYMBOLS]
    out = {"bars": int(n), "first_ms": int(times[SYMBOLS[0]][0]),
           "last_ms": int(times[SYMBOLS[0]][-1]), "symbols": list(SYMBOLS),
           "engine_sha256": eng.sha256_file(ENGINE), "warmup_bars": eng.SIGNAL_WARMUP_BARS,
           "arms": {}, "wall_seconds": None}
    hist_end_ms = utc_ms("2025-09-30") + MS_PER_DAY - 1
    oos_start_ms = utc_ms("2025-10-01")
    for arm in eng.SCALE_ARMS:
        ta = time.time()
        core = eng._panel_arm_core(arm, {s: closes[s] for s in SYMBOLS})
        state, w = core["state"], core["w"]
        labels = core["labels"]
        per = {}
        for i, m in enumerate(labels):
            st = state[:, i]
            hist_mask = times[m] <= hist_end_ms
            oos_mask = times[m] >= oos_start_ms
            ev = eng._fresh_events(state[:, [i]])[:, 0]
            per[m] = {
                "long_bars": int((st > 0).sum()), "short_bars": int((st < 0).sum()),
                "flat_bars": int((st == 0).sum()),
                "events": int((ev != 0).sum()),
                "events_historical": int((ev[hist_mask] != 0).sum()),
                "events_oos": int((ev[oos_mask] != 0).sum()),
                "coverage_rate": core["path"][m]["coverage_rate"],
                "coverage_bars": core["path"][m]["coverage_bars"],
                "defined_bars": core["path"][m]["defined_bars"],
                "fits": core["path"][m]["fits"],
                "dial_below_gate_bars": int(np.count_nonzero(
                    np.isfinite(core["dial"][:, i]) & (core["dial"][:, i] < eng.DIAL_GATE)))}
        g = core["gross_pre"]
        out["arms"][arm] = {
            "per_symbol": per,
            "defined_bars": int(core["defined_bars"]),
            "gross_cap_binding_rate": round(float(np.mean(g > eng.GROSS_CAP)), 6),
            "gross_pre_cap_max": round(float(g.max()), 6),
            "gross_pre_cap_mean": round(float(g.mean()), 6),
            "wall_seconds": round(time.time() - ta, 2)}
        print("[%s] defined=%d cap_bind=%.4f gross_max=%.3f events=%s"
              % (arm, core["defined_bars"], float(np.mean(g > eng.GROSS_CAP)), float(g.max()),
                 {m: per[m]["events"] for m in labels}), flush=True)
    out["wall_seconds"] = round(time.time() - t0, 2)
    out["aligned_grid"] = bool(np.array_equal(times[SYMBOLS[0]], times[SYMBOLS[1]]))
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    tmp = args.out + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(json.dumps(out, indent=2, sort_keys=True) + "\n")
    os.rename(tmp, args.out)
    print("wrote %s in %.1fs" % (args.out, time.time() - t0))


if __name__ == "__main__":
    main()
