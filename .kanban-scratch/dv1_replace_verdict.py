"""Replace the just-published verdict.json with the completed draft (turnover block).

The published verdict was written minutes earlier by THIS attempt and never announced; its
turnover_and_leverage block carried null fills/turnover because the cohort metrics block does not
carry them. This script verifies the exact pre-state hash, replaces atomically, and prints both
hashes so the rewrite is auditable.
"""
import hashlib
import json
import os
import shutil
import sys

DST = ("/Volumes/ExpansionDrive/qlib-results/utc-clock-hour-seasonality-perp-panel-v1/rounds/"
       "utc-clock-hour-seasonality-perp-panel-v1-r1/verdict.json")
SRC = "/tmp/d_v1_verdict_full.json"
EXPECTED_OLD = "6663da6fff61cb1debc246cabc411cf5c616cfeac1208a56e215b986ab89c1ab"


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


new = json.load(open(SRC))                       # validate the replacement first
old_sha = sha(DST)
if old_sha != EXPECTED_OLD:
    sys.exit("pre-state %s != reviewed baseline %s - refusing" % (old_sha, EXPECTED_OLD))
tmp = DST + ".tmp"
shutil.copyfile(SRC, tmp)
with open(tmp) as fh:
    json.load(fh)
os.replace(tmp, DST)                             # atomic on the same filesystem
back = json.load(open(DST))
print("replaced verdict.json")
print("old sha256: %s" % old_sha)
print("new sha256: %s" % sha(DST))
print("read back: verdict=%s claimable=%s level_00=%s fills(BNB)=%s"
      % (back["verdict"], back["performance_claimable"], back["dca_layer_histogram"]["level_00"],
         back["turnover_and_leverage_full_window_winner_cell"]["BNBUSDT/1h"]["fills"]))
