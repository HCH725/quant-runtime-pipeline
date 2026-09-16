#!/usr/bin/env python3
"""Function-level similarity between sibling strategy runners, to find reusable blocks."""
import ast, difflib, os, sys

SCRIPTS = "/Users/hong/workspace/quant-runtime-pipeline/container/scripts"

def funcs(path):
    src = open(path).read()
    tree = ast.parse(src)
    out = {}
    lines = src.splitlines()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = "\n".join(lines[node.lineno - 1: node.end_lineno])
        elif isinstance(node, ast.ClassDef):
            out["class:" + node.name] = "\n".join(lines[node.lineno - 1: node.end_lineno])
    return out

E = funcs(os.path.join(SCRIPTS, "50_strategy_e_run.py"))
for other in ("40_strategy_d_run.py", "40_strategy_c_run.py", "30_strategy_b_run.py"):
    O = funcs(os.path.join(SCRIPTS, other))
    print(f"===== E vs {other}: E has {len(E)} units, {other} has {len(O)} =====")
    identical, similar, missing = [], [], []
    for name, body in E.items():
        if name in O:
            r = difflib.SequenceMatcher(None, body, O[name]).ratio()
            if r > 0.999:
                identical.append((name, len(body)))
            elif r > 0.80:
                similar.append((name, round(r, 3), len(body)))
            else:
                similar.append((name, round(r, 3), len(body)))
        else:
            missing.append((name, len(body)))
    print(f"  identical: {len(identical)} units, {sum(n for _, n in identical)} chars")
    print(f"    {[n for n, _ in identical]}")
    print("  shared-but-differing (name, ratio, chars):")
    for n, r, c in sorted(similar, key=lambda t: -t[2])[:25]:
        print(f"    {n:32s} {r:6.3f} {c}")
    print("  E-only (name, chars):")
    for n, c in sorted(missing, key=lambda t: -t[1]):
        print(f"    {n:32s} {c}")
    print()
