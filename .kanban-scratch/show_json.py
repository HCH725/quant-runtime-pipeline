#!/usr/bin/env python3
"""Ad-hoc readback helper (card t_50c28da5). Read-only inspection of C evidence."""
import json
import sys

p = sys.argv[1] if len(sys.argv) > 1 else "evidence/strategy-c-v1-r1-run-20260915.json"
d = json.load(open(p))
print(json.dumps(d, indent=1, ensure_ascii=False)[: int(sys.argv[2]) if len(sys.argv) > 2 else 8000])
