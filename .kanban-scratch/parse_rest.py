#!/usr/bin/env python3
"""Dump kanban_show spillover: comments, runs, events, parents, children."""
import json
import sys

path = sys.argv[1]
with open(path) as fh:
    data = json.load(fh)

print("=== RUNS ===")
for r in data.get("runs") or []:
    print(json.dumps({k: r.get(k) for k in
                      ("id", "attempt", "status", "outcome", "started_at",
                       "ended_at", "summary", "error", "worker_pid")}, ensure_ascii=False))

print("\n=== COMMENTS ===")
for c in data.get("comments") or []:
    print(json.dumps(c, ensure_ascii=False)[:3000])
    print("---")

print("\n=== EVENTS (tail 60) ===")
for e in (data.get("events") or [])[-60:]:
    print(json.dumps(e, ensure_ascii=False)[:800])

print("\n=== PARENTS ===")
print(json.dumps(data.get("parents"), ensure_ascii=False)[:4000])

print("\n=== CHILDREN ===")
print(json.dumps(data.get("children"), ensure_ascii=False)[:4000])
