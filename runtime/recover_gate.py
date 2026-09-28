#!/opt/homebrew/bin/python3
"""Host restart/recovery gate for the two existing Apple Container services.

launchd job: ai.quant.recover-gate (RunAtLoad + StartInterval 300, no persistent
daemon; see runtime/ai.quant.recover-gate.plist).  Each run is short-lived and
does exactly four things, then exits:

  1. ensure the Apple Container system is running (bounded wait),
  2. ensure the EXISTING n8n container is running — a missing container fails
     closed and is never created/rebuilt — and verify
     http://127.0.0.1:5678/healthz/readiness,
  3. inspect the EXISTING qlib-run state only (`container ls --all`, no
     subprocess): when the container system was already running and qlib-run
     is running, log qlib=already_running and skip preflight entirely — the
     healthy interval is a cheap no-op, not a P1-P8 sweep every 5 minutes.
     Only qlib-run stopped OR the container system having to be recovered in
     step 1 delegates to the canonical runtime/preflight.py --recover --json
     (ExpansionDrive present, container system start, qlib-run start, image /
     version / raw / results checks stay in preflight.py instead of being
     duplicated here); a missing qlib-run fails closed here, never created,
  4. append one line to LOG and exit (0 = healthy, 1 = any gate failed).

Healthy runs change nothing (idempotent no-op).  No new daemon/service/DB/
workflow/cron, no Qlib semantics change.
"""
import fcntl
import json
import os
import sqlite3
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import preflight as pf  # noqa: E402  (canonical helpers: run/system_status/container_info/_wait_until)

N8N = "n8n"
N8N_DB = "/Users/hong/workspace/n8n/data/database.sqlite"
QLIB = "qlib-run"
READINESS = "http://127.0.0.1:5678/healthz/readiness"
LOG = "/Users/hong/quant-dashboard/logs/recover_gate.log"
LOCK = "/Users/hong/quant-dashboard/logs/recover_gate.lock"
LOG_CAP = 1 << 20  # ponytail: size cap only - drop the whole file when exceeded
                    # (no rotation infra). Ceiling = 1 MiB per log.


def log(line):
    """One timestamped line; logging must never turn a healthy run into a failure."""
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > LOG_CAP:
            os.remove(LOG)
        with open(LOG, "a") as fh:
            fh.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), line))
    except OSError:
        pass


def _system_running():
    rc, status = pf.system_status()
    return rc == 0 and status == "running"


def ensure_system():
    if _system_running():
        return "running"
    rc, _out, _err = pf.run(["container", "system", "start"], timeout=180)
    # ponytail: poll with preflight's own bounded wait; a cold system takes tens of seconds.
    if pf._wait_until(_system_running, timeout_s=120, interval_s=5):
        return "started"
    return "start_failed(rc=%d)" % rc


def _n8n_state(name):
    info, _why = pf.container_info(name)
    return (info or {}).get("status", {}).get("state")


def _readiness_ok():
    try:
        with urllib.request.urlopen(READINESS, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def _prepare_n8n_db():
    """Only while n8n is stopped: drain/check the existing DB before clearing sidecars."""
    # mode=rw fails closed if the DB is missing instead of creating an empty one.
    conn = sqlite3.connect("file:%s?mode=rw" % N8N_DB, uri=True, timeout=5)
    try:
        if conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone() != (0, 0, 0):
            return False
        if conn.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            return False
        if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            return False
    finally:
        conn.close()
    for suffix in ("-wal", "-shm"):
        try:
            os.remove(N8N_DB + suffix)
        except FileNotFoundError:
            pass
    return True

def _n8n_mount_type(info):
    config = info.get("configuration")
    mounts = config.get("mounts") if isinstance(config, dict) else None
    if not isinstance(mounts, list):
        return None
    types = [m.get("type") for m in mounts
             if isinstance(m, dict) and m.get("destination") == "/home/node/.n8n"]
    if len(types) != 1 or not isinstance(types[0], dict):
        return None
    kind = types[0]
    if kind.keys() == {"virtiofs"}:
        return "virtiofs"
    if kind.keys() == {"volume"}:
        return "volume"
    return None

def ensure_n8n(name):
    info, why = pf.container_info(name)
    if info is None:
        # fail closed: never `container run` from an unattended gate (rebuilding needs
        # the exact documented mount/env set; see preflight.recover_execution_plane).
        return "missing_fail_closed(%s)" % (why or "no detail")
    state = info.get("status", {}).get("state")
    if state != "running":
        if state != "stopped":
            return "state_fail_closed(%s)" % state
        mount_type = _n8n_mount_type(info)
        if mount_type is None:
            return "mount_fail_closed(missing_ambiguous_or_unknown)"
        if mount_type == "virtiofs":
            try:
                if not _prepare_n8n_db():
                    return "sqlite_prep_failed(checkpoint_or_integrity)"
            except (sqlite3.Error, OSError) as exc:
                return "sqlite_prep_failed(%s)" % exc
        rc, _out, _err = pf.run(["container", "start", name], timeout=120)
        if not pf._wait_until(lambda: _n8n_state(name) == "running", timeout_s=60, interval_s=5):
            return "start_failed(rc=%d)" % rc
        started = "started"
    else:
        started = "already_running"
    if not pf._wait_until(_readiness_ok, timeout_s=90, interval_s=3):
        return "readiness_failed(%s)" % started
    return started


def qlib_preflight():
    rc, out, _err = pf.run([sys.executable, os.path.join(HERE, "preflight.py"),
                            "--recover", "--json"], timeout=420)
    try:
        report = json.loads(out)
    except ValueError:
        return rc, "unparsable(rc=%d)" % rc
    rec = report.get("recovery") or {}
    actions = ",".join(a.get("step", "?") for a in (rec.get("actions") or [])) or "noop"
    failed = ",".join(c.get("id", "?") for c in (report.get("checks") or [])
                      if c.get("status") == "FAIL") or "-"
    return rc, "%s(rc=%d actions=%s fail=%s failed=%s)" % (
        report.get("overall"), rc, actions, rec.get("fail_reason") or "-", failed)


def qlib_gate(system, name=QLIB):
    """(ok, logged_value): inspect EXISTING qlib-run state, delegate only if needed.

    Healthy path = one `container ls --all` and out (no subprocess preflight, no
    P1-P8 probes every interval).  Delegation to the canonical preflight happens
    only when there is something to recover/validate: qlib-run stopped, or the
    Apple Container system had to be recovered in ensure_system() above.
    Absent qlib-run fails closed here — never auto-created, never delegated to
    a recovery path that could be mistaken for permission to rebuild.
    """
    info, why = pf.container_info(name)
    if info is None:
        return False, "absent_fail_closed(%s)" % (why or "no detail")
    if system == "running" and (info.get("status", {}).get("state") == "running"):
        return True, "already_running"
    rc, detail = qlib_preflight()
    return (rc == 0 and detail.startswith("PASS")), "preflight:%s" % detail


def main():
    # argv exists only so the fail-closed branches can be exercised without touching
    # the real containers (recovery self-test: n8n name, then qlib-run name);
    # launchd passes no arguments.
    n8n_name = sys.argv[1] if len(sys.argv) > 1 else N8N
    qlib_name = sys.argv[2] if len(sys.argv) > 2 else QLIB
    problems = []
    try:
        fh = open(LOCK, "a")
    except OSError as exc:
        log("rc=1 problems=lock_unavailable(%s)" % exc)
        return 1
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            log("rc=0 skipped=previous_run_still_active")
            return 0
        system = ensure_system()
        if system not in ("running", "started"):
            problems.append("system=%s" % system)
        n8n = ensure_n8n(n8n_name)
        if n8n not in ("running", "started", "already_running"):
            problems.append("n8n=%s" % n8n)
        qlib_ok, qlib = qlib_gate(system, qlib_name)
        if not qlib_ok:
            problems.append("qlib=%s" % qlib)
        rc = 1 if problems else 0
        log("boot=%s system=%s n8n=%s qlib=%s rc=%d problems=%s" % (
            pf.host_boot_id(), system, n8n, qlib, rc, ";".join(problems) or "-"))
        return rc
    except Exception as exc:  # keep the gate observable instead of dying silently
        log("rc=1 problems=unexpected(%r)" % exc)
        return 1
    finally:
        fh.close()


if __name__ == "__main__":
    sys.exit(main())
