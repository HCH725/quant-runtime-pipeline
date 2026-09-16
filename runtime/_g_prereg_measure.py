#!/usr/bin/env python3
"""Pre-registration measurements for family bitcoin-intraday-time-series-momentum-volume-session-2026-08-31.

READ-ONLY host-side measurement (never writes /data/raw, never writes /results).  Produces
the measured facts that the immutable round-spec registers BEFORE any computation:
  * the base-bar grid of each candidate timeframe (contiguity, first/last bar, gaps)
  * the canonical instrument metadata of BTCUSDT
  * the funding series split (modelled / official) of the registered window
  * the 48 half-hour slot volume profile of the UTC day and the stability of the
    volume-anchored session start under the trailing windows this family may register

usage: /opt/homebrew/bin/python3 runtime/_g_prereg_measure.py [--json <out.json>]
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import statistics
import time

RAW = "/Volumes/ExpansionDrive/market-data-raw/binance/usdm"
SYMBOL = "BTCUSDT"
WINDOW_START_MS = 1640995200000  # 2022-01-01T00:00:00Z
WINDOW_END_MS = 1789171200000    # 2026-09-12T00:00:00Z exclusive
INTERVALS = ("5m", "15m", "30m", "1h")
MS_30M = 1_800_000
SLOTS = 48


def monthly_files(interval):
    d = os.path.join(RAW, "klines", SYMBOL, interval)
    return [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith(".jsonl.gz")]


def iter_bars(interval):
    for path in monthly_files(interval):
        with gzip.open(path, "rt") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                ms = int(r["open_time_ms"])
                if ms < WINDOW_START_MS or ms >= WINDOW_END_MS:
                    continue
                yield ms, int(r["close_time_ms"]), float(r["open"]), float(r["high"]), \
                    float(r["low"]), float(r["close"]), float(r["volume"])


def grid_report(interval):
    want = {"5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000}[interval]
    n = 0
    first = last = None
    first_close = last_close = None
    off_grid = 0
    gaps = 0
    missing = 0
    worst = []
    prev = None
    volumes = 0.0
    zero_vol = 0
    for ms, cms, o, h, l, c, v in iter_bars(interval):
        n += 1
        if first is None:
            first, first_close = ms, cms
        if prev is not None:
            step = ms - prev
            if step != want:
                off_grid += 1
                if step > want:
                    gaps += 1
                    missing += (step // want) - 1
                    if len(worst) < 5:
                        worst.append({"after_open_ms": prev, "next_open_ms": ms,
                                      "missing_bars": (step // want) - 1})
        prev = ms
        last, last_close = ms, cms
        volumes += v
        if v == 0.0:
            zero_vol += 1
    return {"interval": interval, "want_step_ms": want, "bars_in_window": n,
            "first_open_time_ms": first, "last_open_time_ms": last,
            "first_bar_close_time_ms": first_close, "last_bar_close_time_ms": last_close,
            "close_delta_ms": (first_close - first) if first is not None else None,
            "off_grid_steps": off_grid, "gap_steps": gaps, "missing_bars_total": missing,
            "first_gaps": worst, "zero_volume_bars": zero_vol,
            "bars_expected_if_contiguous": (WINDOW_END_MS - WINDOW_START_MS) // want,
            "total_volume": volumes}


def instrument_metadata():
    with open(os.path.join(RAW, "instruments", "usdm-perp-instruments.json")) as fh:
        doc = json.load(fh)
    for inst in doc["instruments"]:
        f = inst["fields"]
        if f.get("raw_symbol") == SYMBOL:
            return {k: f[k] for k in ("id", "raw_symbol", "type", "base_currency", "quote_currency",
                                      "settlement_currency", "is_inverse", "price_increment",
                                      "size_increment", "lot_size", "maker_fee", "taker_fee",
                                      "margin_init", "margin_maint", "min_notional",
                                      "max_quantity", "multiplier")}
    raise SystemExit("instrument %s not found" % SYMBOL)


def funding_report():
    path = os.path.join(RAW, "funding", SYMBOL, "%s-funding.jsonl.gz" % SYMBOL)
    obs = []
    with gzip.open(path, "rt") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            ms = int(r.get("funding_time_ms", r.get("time_ms", 0)))
            if ms < WINDOW_START_MS or ms >= WINDOW_END_MS:
                continue
            obs.append((ms, r))
    obs.sort(key=lambda x: x[0])
    modelled = [o for o in obs if o[1].get("mark_price") in (None, "", 0)]
    official = [o for o in obs if o[1].get("mark_price") not in (None, "", 0)]
    steps = [obs[i + 1][0] - obs[i][0] for i in range(len(obs) - 1)]
    return {"observations": len(obs),
            "modelled_funding": len(modelled), "official_funding": len(official),
            "modelled_first": modelled[0][0] if modelled else None,
            "modelled_last": modelled[-1][0] if modelled else None,
            "official_first": official[0][0] if official else None,
            "official_last": official[-1][0] if official else None,
            "first_obs_ms": obs[0][0] if obs else None,
            "last_obs_ms": obs[-1][0] if obs else None,
            "step_min_ms": min(steps) if steps else None,
            "step_max_ms": max(steps) if steps else None,
            "field_names": sorted(obs[0][1].keys()) if obs else []}


def slot_profile():
    """48 half-hour UTC slots: volume totals and the stability of a volume-anchored start."""
    day_vol = {}           # day_index (ms // MS_PER_DAY) -> [48 volumes]
    bars_per_slot = {}     # (day, slot) -> bars seen
    for ms, cms, o, h, l, c, v in iter_bars("5m"):
        day = ms // 86_400_000
        slot = (ms % 86_400_000) // MS_30M
        row = day_vol.setdefault(day, [0.0] * SLOTS)
        row[slot] += v
        bars_per_slot[(day, slot)] = bars_per_slot.get((day, slot), 0) + 1
    days = sorted(day_vol)
    mean_profile = [0.0] * SLOTS
    for d in days:
        for s in range(SLOTS):
            mean_profile[s] += day_vol[d][s] / len(days)
    ranked = sorted(range(SLOTS), key=lambda s: (-mean_profile[s], s))
    # trailing-window argmax per day (as the engine will compute it: strictly prior days,
    # ties resolved to the SMALLEST slot), for the trailing windows this family may register
    stability = {}
    for w in (7, 30, 90):
        anchors = []
        open_days = 0
        for i, d in enumerate(days):
            if i < w:
                continue
            prof = [0.0] * SLOTS
            for j in range(i - w, i):
                for s in range(SLOTS):
                    prof[s] += day_vol[days[j]][s] / w
            anchor = min(range(SLOTS), key=lambda s: (-prof[s], s))
            anchors.append(anchor)
            open_days += 1
        distinct = sorted(set(anchors))
        counts = {a: anchors.count(a) for a in distinct}
        stability["trail%d" % w] = {
            "evaluated_days": open_days, "distinct_anchor_slots": len(distinct),
            "anchor_slot_share_top5": sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5],
            "anchor_slot_min": min(anchors) if anchors else None,
            "anchor_slot_max": max(anchors) if anchors else None,
        }
    bars_per_slot_values = list(bars_per_slot.values())
    return {"days": len(days), "first_day_ms": days[0] if days else None,
            "last_day_ms": days[-1] if days else None,
            "mean_slot_volume_top6": [{"slot": s, "start_utc": "%02d:%02d" % (s // 2, (s % 2) * 30),
                                       "mean_volume": mean_profile[s]} for s in ranked[:6]],
            "mean_slot_volume_bottom4": [{"slot": s, "start_utc": "%02d:%02d" % (s // 2, (s % 2) * 30),
                                          "mean_volume": mean_profile[s]} for s in ranked[-4:]],
            "bars_per_slot": {"min": min(bars_per_slot_values), "max": max(bars_per_slot_values),
                              "mean": statistics.fmean(bars_per_slot_values),
                              "expected_if_complete": 6},
            "anchors": stability}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    t0 = time.time()
    out = {"schema_version": 1, "symbol": SYMBOL,
           "window_start_ms": WINDOW_START_MS, "window_end_ms": WINDOW_END_MS,
           "raw_root_kind": "binance/usdm (read-only canonical raw store)",
           "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "bar_grids": {}, "instrument": instrument_metadata(), "funding": funding_report(),
           "slot_profile": slot_profile(), "runtime_s": None}
    for interval in INTERVALS:
        out["bar_grids"][interval] = grid_report(interval)
        print(json.dumps(out["bar_grids"][interval], indent=1))
    print(json.dumps({k: out[k] for k in ("instrument", "funding")}, indent=1))
    print(json.dumps(out["slot_profile"], indent=1)[:4000])
    out["runtime_s"] = round(time.time() - t0, 1)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(out, fh, indent=1, sort_keys=True)
        print("wrote", args.json)


if __name__ == "__main__":
    main()
