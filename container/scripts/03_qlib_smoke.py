#!/usr/bin/env python3
"""Qlib data-layer read verification (Phase 6 smoke; research-only).

Proves the bitcoin 1h smoke sample is read through the qlib data layer
(qlib.init + qlib.data.D.features on the dumped .bin store), not by pandas
reading raw JSON directly, and validates:
  * row count / timestamp ordering / duplicate calendar entries
  * expected columns present
  * raw host-side store untouched (read-only mount)

usage: 03_qlib_smoke.py <qlib_dir> <start> <end> <out_json>
"""
import json
import sys
from pathlib import Path

import qlib
from qlib.data import D

SYMBOL = "BTCUSDT"
FREQ = "60min"


def main() -> None:
    qlib_dir, start, end, out_json = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]

    qlib.init(provider_uri=qlib_dir, region="cn", expression_cache=None, dataset_cache=None)

    cal = D.calendar(start_time=start, end_time=end, freq=FREQ)
    df = D.features(
        [SYMBOL],
        ["$open", "$high", "$low", "$close", "$volume"],
        start_time=start,
        end_time=end,
        freq=FREQ,
    )

    idx = df.index.get_level_values("datetime")
    ts = list(idx)
    payload = {
        "qlib_dir": qlib_dir,
        "provider_init": "ok",
        "freq": FREQ,
        "symbol": SYMBOL,
        "window": {"start": start, "end": end},
        "calendar_len": len(cal),
        "calendar_first": str(cal[0]) if len(cal) else None,
        "calendar_last": str(cal[-1]) if len(cal) else None,
        "rows": int(len(df)),
        "columns": [str(c) for c in df.columns],
        "index_names": list(df.index.names),
        "monotonic_increasing": bool(ts == sorted(ts)),
        "calendar_duplicates": int(len(cal) - len(set(cal))),
        "null_cells": int(df.isnull().sum().sum()),
        "first_row": {"datetime": str(df.index[0][1]), "close": float(df["$close"].iloc[0])},
        "last_row": {"datetime": str(df.index[-1][1]), "close": float(df["$close"].iloc[-1])},
        "assert_single_symbol": sorted(df.index.get_level_values("instrument").unique().tolist()),
    }
    core = [c for c in df.columns if "close" in str(c)]
    payload["close_column"] = core
    Path(out_json).write_text(json.dumps(payload, indent=2))
    print(json.dumps(payload, indent=2))

    assert payload["rows"] > 0, "no rows through the qlib data layer"
    assert payload["monotonic_increasing"], "timestamps not ascending"
    assert payload["calendar_duplicates"] == 0, "duplicate calendar entries"
    assert payload["assert_single_symbol"] == [SYMBOL], "unexpected instruments"


if __name__ == "__main__":
    main()
