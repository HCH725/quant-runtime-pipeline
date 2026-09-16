"""Publish the reviewed verdict draft atomically (O_EXCL: refuse to overwrite, INV-4)."""
import json
import os
import shutil
import sys

SRC = "/tmp/d_v1_verdict.json"
DST = ("/Volumes/ExpansionDrive/qlib-results/utc-clock-hour-seasonality-perp-panel-v1/rounds/"
       "utc-clock-hour-seasonality-perp-panel-v1-r1/verdict.json")

data = json.load(open(SRC))          # validate before publishing
tmp = DST + ".tmp"
shutil.copyfile(SRC, tmp)
with open(tmp) as fh:
    json.load(fh)                     # re-validate the staged copy
try:
    fd = os.open(DST, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
except FileExistsError:
    os.unlink(tmp)
    sys.exit("refusing to overwrite existing %s (INV-4)" % DST)
try:
    with open(tmp) as src:
        os.write(fd, src.read().encode())
finally:
    os.close(fd)
    os.unlink(tmp)
with open(DST) as fh:
    back = json.load(fh)              # read back what actually landed
print("published %s (%d bytes)" % (DST, os.path.getsize(DST)))
print("read back: verdict=%s claimable=%s survivors=%s level_00=%s"
      % (back["verdict"], back["performance_claimable"], back["cohort_outcome_counts"],
         back["dca_layer_histogram"]["level_00"]))
print("assertions all true:", back["assertions"]["all_true"], "| sentinel sha:",
      back["terminal_sentinel_sha256"])
