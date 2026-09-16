#!/usr/bin/env python3
"""Scan every shipped family round's terminal result.json for the exhaustion assertions.

Read-only. Prints family / round / attempt / assertion values + the min_entry_equity spread on
the full-window grid CSV when the assertion is present. Used to decide whether the Strategy F
flip-path defect is family-local or shared by the shipped A-E rounds.
"""
import csv
import glob
import json
import os

ROOT = "/Volumes/ExpansionDrive/qlib-results"
KEYS = ("no_entry_after_exhaustion", "entries_after_exhaustion")

for family in sorted(os.listdir(ROOT)):
    fam_dir = os.path.join(ROOT, family)
    if not os.path.isdir(fam_dir) or family.startswith("_"):
        continue
    for res in sorted(glob.glob(os.path.join(fam_dir, "rounds", "*", "attempts", "*", "result.json"))):
        attempt = os.path.basename(os.path.dirname(res))
        try:
            d = json.load(open(res))
        except Exception as exc:
            print("%-42s %-14s UNREADABLE %s" % (family, attempt, exc))
            continue
        a = d.get("assertions", {})
        shows = {k: a[k] for k in KEYS if k in a}
        sent = os.path.exists(os.path.join(os.path.dirname(res), "DONE"))
        extra = ""
        if "no_entry_after_exhaustion" in a:
            g = os.path.join(os.path.dirname(res), "artifacts", "grid_full.csv")
            if os.path.exists(g):
                vals = []
                with open(g, newline="") as fh:
                    for r in csv.DictReader(fh):
                        try:
                            vals.append(float(r["min_entry_equity"]))
                        except (KeyError, ValueError):
                            vals = None
                            break
                if vals:
                    extra = " | full-grid min_entry_equity min=%.4f n_le_0=%d/%d" % (
                        min(vals), sum(1 for v in vals if v <= 0.0), len(vals))
        bad = [k for k, v in a.items() if v is not True]
        print("%-42s %-30s sentinel=%s assertions=%d bad=%s %s%s"
              % (family, attempt, "DONE" if sent else "-", len(a), bad or "[]", shows, extra))
