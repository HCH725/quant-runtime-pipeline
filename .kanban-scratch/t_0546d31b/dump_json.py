#!/usr/bin/env python3
"""Dump the key structure of a JSON document (keys + short values) for reading back evidence."""
import json
import sys

path = sys.argv[1]
try:
    d = json.load(open(path))
except Exception as exc:
    print("cannot read %s: %s" % (path, exc))
    sys.exit(1)
print("== %s" % path)


def walk(o, pre=""):
    if isinstance(o, dict):
        for k, v in o.items():
            if isinstance(v, (dict, list)):
                print("%s%s: %s" % (pre, k, type(v).__name__))
                walk(v, pre + "  ")
            else:
                print("%s%s: %s" % (pre, k, str(v)[:120]))
    elif isinstance(o, list):
        print("%s[%d items] first=%s" % (pre, len(o), str(o[0])[:120] if o else ""))


walk(d)
