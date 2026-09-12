#!/usr/bin/env python3
"""Environment baseline for the Qlib runtime (Phase 5; local-only evidence)."""
import json
import platform
import sys
from pathlib import Path

import importlib

MODS = ["qlib", "numpy", "pandas", "pyarrow", "lightgbm", "scipy", "sklearn", "mlflow"]


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    payload = {
        "python": sys.version,
        "python_version": platform.python_version(),
        "executable": sys.executable,
        "machine": platform.machine(),
        "system": platform.system(),
        "libc": list(platform.libc_ver()),
        "platform": platform.platform(),
        "modules": {},
    }
    for m in MODS:
        try:
            mod = importlib.import_module(m)
            payload["modules"][m] = {"import": "PASS", "version": getattr(mod, "__version__", "n/a")}
        except Exception as e:  # noqa: BLE001
            payload["modules"][m] = {"import": "FAIL", "error": f"{type(e).__name__}: {e}"}
    text = json.dumps(payload, indent=2)
    print(text)
    if out:
        out.write_text(text)
    failed = [k for k, v in payload["modules"].items() if v["import"] != "PASS"]
    if failed:
        raise SystemExit(f"import failures: {failed}")


if __name__ == "__main__":
    main()
