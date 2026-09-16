#!/usr/bin/env python3
"""Read back a counts-validator JSON report and print the failing parts (card t_50c28da5)."""
import json
import sys

d = json.load(open(sys.argv[1]))
print("ok:", d.get("ok"))
print("problems:", json.dumps(d.get("problems"), indent=1, ensure_ascii=False)[:3000])
print("self_test_failed:", d.get("self_test_failed"))
for c in d.get("self_test", []):
    if not c["detected"]:
        print("UNDETECTED:", c["case"], c["problems"])
print("extra:", json.dumps(d.get("extra"), indent=1)[:1200])
print("computed:", json.dumps(d.get("computed"), indent=1))
