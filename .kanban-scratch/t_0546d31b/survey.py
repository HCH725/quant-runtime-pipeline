#!/usr/bin/env python3
"""Read-only survey helper for card t_0546d31b."""
import json, os, subprocess, sys

ROOT = "/Volumes/ExpansionDrive/qlib-results"

def show(path, label):
    print(f"=== {label}: {path} ===")
    try:
        with open(path, "rb") as f:
            raw = f.read()
        print(raw.decode("utf-8", "replace"))
    except Exception as e:
        print(f"  <error: {e}>")
    print()

show(os.path.join(ROOT, "stochastic-rsi-renko-2026-08-31", "family.json"), "family.json")

# inline spec file if present
for cand in ("round-spec.json", "state.json"):
    p = os.path.join(ROOT, "stochastic-rsi-renko-2026-08-31", cand)
    if os.path.exists(p):
        show(p, cand)

# handoff candidates index
try:
    with open(os.path.join(ROOT, "_handoff", "candidates.json")) as f:
        d = json.load(f)
    print("=== _handoff/candidates.json shape ===")
    print("type:", type(d).__name__)
    if isinstance(d, dict):
        print("keys:", list(d)[:30])
        for k, v in d.items():
            if isinstance(v, list):
                print(f"  {k}: list len {len(v)}")
                for item in v[:200]:
                    if isinstance(item, dict):
                        blob = json.dumps(item, ensure_ascii=False)
                        if "stochastic-rsi-renko" in blob:
                            print("  MATCH:", json.dumps(item, ensure_ascii=False)[:4000])
            else:
                print(f"  {k}: {str(v)[:300]}")
    print()
except Exception as e:
    print("candidates.json error:", e)

print("=== _handoff/bodies/stochastic-rsi-renko-2026-08-31.md (head/tail) ===")
p = os.path.join(ROOT, "_handoff", "bodies", "stochastic-rsi-renko-2026-08-31.md")
try:
    with open(p) as f:
        txt = f.read()
    print("bytes:", len(txt))
    print(txt[:1500])
    print("...<snip>...")
    print(txt[-1500:])
except Exception as e:
    print("error:", e)
