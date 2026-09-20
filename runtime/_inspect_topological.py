from __future__ import annotations
import json
import os
import subprocess
from pathlib import Path

family = "cross-sectional-topological-anomaly-score-intraday-equity-return-predictability-2026-09-02"
roots = [Path("/Volumes/ExpansionDrive/qlib-results"), Path("/Users/hong/workspace/quant-runtime-pipeline/.kanban-scratch")]
for root in roots:
    print(f"ROOT {root} exists={root.exists()}")
    if not root.exists():
        continue
    hits = []
    try:
        for p in root.iterdir():
            if family in p.name or p.name.startswith("_topological"):
                hits.append(p)
    except OSError as exc:
        print("ITER_ERROR", repr(exc))
        continue
    for p in sorted(hits, key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True):
        print("HIT", p, "dir" if p.is_dir() else "file", "mtime", p.stat().st_mtime)
        if p.is_dir():
            try:
                for child in sorted(p.iterdir(), key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True)[:30]:
                    print(" CHILD", child, "dir" if child.is_dir() else "file", "mtime", child.stat().st_mtime)
            except OSError as exc:
                print(" CHILD_ERROR", repr(exc))

family_root = Path("/Volumes/ExpansionDrive/qlib-results") / family
print("FAMILY_ROOT", family_root, "exists", family_root.exists())
if family_root.exists():
    print("TREE")
    for p in sorted(family_root.rglob("*"), key=lambda x: (len(x.relative_to(family_root).parts), str(x))):
        rel = p.relative_to(family_root)
        if len(rel.parts) <= 5:
            print(" TREE_ITEM", rel, "dir" if p.is_dir() else "file", p.stat().st_size, p.stat().st_mtime)
    for p in sorted(family_root.rglob("state.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            d = json.loads(p.read_text())
        except Exception as exc:
            print("STATE", p, "READ_ERROR", repr(exc))
            continue
        print("STATE", p, json.dumps({k:d.get(k) for k in ("stage", "status", "run_id", "updated_at", "phase", "message")}, sort_keys=True))
    for name in ("round-spec.json", "family.json", "verdict.json", "survivor-bundle.json", "reconciliation.json", "terminal-evidence.json"):
        for p in family_root.rglob(name):
            print("ARTIFACT", p, p.stat().st_size, p.stat().st_mtime)

print("PROCESSES")
try:
    rows = subprocess.check_output(["ps", "-axo", "pid=,ppid=,etime=,stat=,command="], text=True).splitlines()
except Exception as exc:
    print("PS_ERROR", repr(exc))
else:
    for row in rows:
        if any(token in row.lower() for token in ("topological", "qlib-run", "container exec", "170_topological")):
            print(" PROC", row)
