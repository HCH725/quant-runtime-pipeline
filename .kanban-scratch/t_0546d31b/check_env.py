#!/usr/bin/env python3
"""Print the interpreter + numpy availability for a candidate host interpreter."""
import sys

print("interpreter:", sys.executable, sys.version.split()[0])
try:
    import numpy as np
    print("numpy:", np.__version__)
except Exception as exc:                                    # pragma: no cover - probe
    print("numpy: MISSING (%s)" % exc)
