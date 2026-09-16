#!/usr/bin/env python3
"""Print the preflight check rows compactly + diff two run-spec documents (u1 vs u2)."""
import json
import sys

mode = sys.argv[1]
if mode == "preflight":
    d = json.load(open(sys.argv[2]))
    print("overall=%s launch_gate=%s rc_row=%d checks=%d" %
          (d.get("overall"), d.get("launch_gate"), len(d.get("checks", [])),
           len(d.get("checks", []))))
    for c in d.get("checks", []):
        print("  %-4s %-5s %-13s %s" % (c.get("id"), c.get("status"), c.get("layer"),
                                        str(c.get("detail"))[:110]))
elif mode == "diff":
    a = json.load(open(sys.argv[2]))
    b = json.load(open(sys.argv[3]))

    def flat(o, pre=""):
        out = {}
        if isinstance(o, dict):
            for k, v in o.items():
                out.update(flat(v, "%s.%s" % (pre, k)))
        elif isinstance(o, list):
            for i, v in enumerate(o):
                out.update(flat(v, "%s[%d]" % (pre, i)))
        else:
            out[pre] = o
        return out

    fa, fb = flat(a), flat(b)
    keys = sorted(set(fa) | set(fb))
    diffs = [k for k in keys if fa.get(k, "<absent>") != fb.get(k, "<absent>")]
    print("diff keys: %d / %d" % (len(diffs), len(keys)))
    for k in diffs:
        print("  %s\n    u1: %s\n    u2: %s" % (k, str(fa.get(k, "<absent>"))[:160],
                                                str(fb.get(k, "<absent>"))[:160]))
else:
    print("mode must be preflight|diff")
    sys.exit(2)
