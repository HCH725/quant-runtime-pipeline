#!/usr/bin/env python3
"""Dump the structural shape (keys + short previews) of a JSON document (card t_50c28da5)."""
import json
import sys


def shape(o, path="$", depth=0):
    pad = "  " * depth
    if isinstance(o, dict):
        for k, v in o.items():
            if isinstance(v, (dict, list)):
                n = len(v)
                print("%s%s: %s(%d)" % (pad, k, type(v).__name__, n))
                if depth < 3:
                    shape(v, "%s.%s" % (path, k), depth + 1)
            else:
                print("%s%s: %r" % (pad, k, v))
    elif isinstance(o, list):
        for i, v in enumerate(o[:4]):
            if isinstance(v, (dict, list)):
                print("%s[%d] %s(%d)" % (pad, i, type(v).__name__, len(v)))
                if depth < 3:
                    shape(v, "%s[%d]" % (path, i), depth + 1)
            else:
                print("%s[%d] %r" % (pad, i, v))
        if len(o) > 4:
            print("%s... %d more" % (pad, len(o) - 4))


for p in sys.argv[1:]:
    print("=" * 70)
    print(p)
    print("=" * 70)
    try:
        shape(json.load(open(p)))
    except Exception as exc:  # noqa: BLE001
        print("ERR", exc)
