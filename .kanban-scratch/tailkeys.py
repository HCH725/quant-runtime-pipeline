#!/usr/bin/env python3
"""Print the remaining top-level keys of the C round-spec template verbatim (card t_50c28da5)."""
import json
import sys

p = "runtime/templates/strategy_c_v1_round_spec.template.json"
d = json.load(open(p))
keys = list(d)
start = keys.index(sys.argv[1]) if len(sys.argv) > 1 else 0
for k in keys[start:]:
    print("### %s" % k)
    print(json.dumps(d[k], indent=1, ensure_ascii=False)[:4000])
    print()
