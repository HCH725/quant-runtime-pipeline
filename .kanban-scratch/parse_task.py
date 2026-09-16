#!/usr/bin/env python3
"""Parse a kanban_show spillover JSON and print human-readable extracts."""
import json
import sys

path = sys.argv[1]
with open(path) as fh:
    data = json.load(fh)

task = data.get("task", {})
print("=== TASK ===")
for k in ("id", "title", "status", "assignee", "workspace_kind", "workspace_path",
          "created_by", "priority", "completion_contract", "created_at", "updated_at"):
    if k in task:
        print(f"{k}: {task[k]}")

print("\n=== BODY ===")
print(task.get("body", ""))

print("\n=== TOP-LEVEL KEYS ===")
print(sorted(data.keys()))
