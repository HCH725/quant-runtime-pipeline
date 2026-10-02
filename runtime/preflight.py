#!/usr/bin/env python3
"""Execution preflight P1-P10 for the quant runtime pipeline.

Contract: /Users/hong/workspace/quant-runtime-pipeline/QUANT_RUNTIME_PIPELINE_IMPLEMENTATION_CONTRACT.md section 16.
Default mode does not start container system or auto-create containers; will start
existing stopped containers (P5). Stdlib only, no state written (P3/P7 probes create
and immediately remove a hidden temp file).
Exit codes: 0 = every *evaluated* check PASS, 1 = at least one FAIL, 2 = usage error.

Examples:
    python3 runtime/preflight.py                       # environment checks P1-P8 only
    python3 runtime/preflight.py --json                # same, machine readable
    python3 runtime/preflight.py --recover --json      # opt-in reboot recovery, then P1-P8
    python3 runtime/preflight.py --launch --attempt-dir /Volumes/ExpansionDrive/qlib-results/<f>/rounds/<r>/attempts/<u>
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import parameter_contract as pc  # noqa: E402  (same directory; pure stdlib)
# The contract-terminal round-verdict rule is NOT re-implemented here: the launch gate delegates to
# the exact function the handoff scheduler and the reconciler release on, so "is this round
# decided?" cannot drift between the launch gate and the pipeline that already reads it.
from production_handoff import round_verdict_token  # noqa: E402  (same directory; pure stdlib)

DEFAULT_EXPANSION = "/Volumes/ExpansionDrive"
DEFAULT_CONTAINER = "qlib-run"
DEFAULT_IMAGE = "qlib:0.9.7-arm64"
DEFAULT_QLIB_VERSION = "0.9.7"
DEFAULT_HOST_SCRIPTS = "/Users/hong/workspace/qlib-apple-container/scripts"  # host source of the container's ro /scripts mount
VENV_PYTHON = "/opt/venv/bin/python"  # the interpreter that actually has qlib (see contract P8)
TERMINALS = ("DONE", "FAILED", "INCOMPLETE")


def run(cmd, timeout=60):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    except FileNotFoundError:
        return 127, "", "not found: %s" % cmd[0]
    except subprocess.TimeoutExpired:
        return 124, "", "timeout: %s" % " ".join(cmd)


def host_boot_id():
    """Stable per-boot identifier (shared contract with terminal_evidence.py/reconcile.py)."""
    rc, out, _ = run(["sysctl", "-n", "kern.boottime"])
    if rc == 0 and "sec" in out:
        sec = out.split("sec =")[1].split(",")[0].strip()
        return "boot-%s" % sec
    return "boot-unknown"


def container_info(name):
    # ponytail: --all needed to see stopped containers; without it recovery
    # misidentifies stopped-as-absent and fails closed instead of healing.
    rc, out, err = run(["container", "ls", "--format", "json", "--all"])
    if rc != 0:
        return None, err.strip() or "container ls failed"
    try:
        items = json.loads(out)
    except ValueError as exc:
        return None, "unparsable container ls json: %s" % exc
    for item in items:
        if item.get("id") == name or item.get("configuration", {}).get("id") == name:
            return item, None
    return None, "container %r not listed" % name


def check(checks, cid, status, layer, detail):
    checks.append({"id": cid, "status": status, "layer": layer, "detail": detail})


def probe_write(path):
    probe = os.path.join(path, ".preflight-probe-%d.tmp" % os.getpid())
    try:
        with open(probe, "w") as fh:
            fh.write("probe\n")
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        return False, str(exc)
    finally:
        if os.path.exists(probe):
            try:
                os.unlink(probe)
            except OSError:
                pass
    return True, "wrote+removed %s" % probe


def p2_raw(checks, name, raw):
    """P2: raw exists + readable, and is read-only **as seen by the container**.

    The host user owns the export, so a host-side `test -w` is always true and would misjudge
    THIS check (the ro-ness is a mount property enforced inside the container).
    """
    exists = os.path.isdir(raw) and os.access(raw, os.R_OK)
    rc, out, err = run(["container", "exec", name, "sh", "-c",
                        "if touch /data/raw/__preflight_probe__ 2>/dev/null; then "
                        "rm -f /data/raw/__preflight_probe__; echo WRITABLE; else echo READ_ONLY; fi"])
    probe = out.strip().splitlines()[-1] if out.strip() else "no_output"
    ok = exists and rc == 0 and probe == "READ_ONLY"
    check(checks, "P2", "PASS" if ok else "FAIL", "shared-layer",
          "exists+readable=%s container_write_probe=%s (must be READ_ONLY)" % (exists, probe))
    return raw


def p1_p3(checks, expansion):
    p1 = os.path.isdir(expansion) and os.access(expansion, os.R_OK | os.X_OK)
    try:
        listing = sorted(os.listdir(expansion))[:4] if p1 else []
    except OSError:
        listing = []
    check(checks, "P1", "PASS" if p1 else "FAIL", "shared-layer",
          "mounted+readable: %s (%s)" % (expansion, ", ".join(listing) or "n/a"))

    raw = os.path.join(expansion, "market-data-raw")
    results = os.path.join(expansion, "qlib-results")
    if os.path.isdir(results):
        ok, detail = probe_write(results)
    else:
        ok, detail = False, "missing: %s" % results
    check(checks, "P3", "PASS" if ok else "FAIL", "shared-layer", "results writable: %s" % detail)
    return raw, results


def system_status():
    """Parse `container system status` -> (rc, status_word)."""
    rc, out, _ = run(["container", "system", "status"])
    status = ""
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "status":
            status = parts[1]
    return rc, status


def _wait_until(pred, timeout_s=60, interval_s=5):
    """Poll pred() until truthy or timeout. First probe is immediate (no initial sleep)."""
    end = time.time() + timeout_s
    while True:
        if pred():
            return True
        if time.time() >= end:
            return False
        time.sleep(interval_s)


def recover_execution_plane(expansion, name):
    """Opt-in reboot recovery (only called with --recover). Ordered, fail-closed.

    Order: ExpansionDrive present/readable -> `container system start` if the
    Apple Container system is down (then verify running) -> `container start`
    if qlib-run exists but is stopped (then verify running). A missing
    container is fail-closed: never silently recreated here (no documented
    safe auto-create; runbook container/scripts/run_phase4.sh is a manual
    operator procedure, not an unattended cron action).
    Returns {"attempted": True, "ok": bool, "fail_reason": str|None, "actions": [...] }.
    """
    actions = []
    if not (os.path.isdir(expansion) and os.access(expansion, os.R_OK | os.X_OK)):
        return {"attempted": True, "ok": False, "fail_reason": "expansion_missing",
                "actions": actions}

    def _system_running():
        rc, status = system_status()
        return rc == 0 and status == "running"

    if not _system_running():
        rc2, _out2, err2 = run(["container", "system", "start"], timeout=180)
        actions.append({"step": "system_start", "rc": rc2,
                        "detail": ((_out2 or "").strip().splitlines()[-1:]
                                   if (_out2 or "").strip() else [])
                        or (err2.strip().splitlines()[-1:] if err2.strip() else [])})
        # ponytail: poll instead of a fixed sleep; a cold system takes tens of seconds.
        if not _wait_until(_system_running, timeout_s=120, interval_s=5):
            _, status = system_status()
            return {"attempted": True, "ok": False, "fail_reason": "system_start_failed",
                    "actions": actions, "system_status": status or "unknown"}
    info, why = container_info(name)
    if info is None:
        # ponytail: fail-closed on absent container; never auto `container run`
        # (creation needs the exact manual mount set; wrong auto-create would
        # corrupt identity P6/P8). Operator recreates per runbook, then re-run.
        return {"attempted": True, "ok": False, "fail_reason": "container_absent",
                "actions": actions, "detail": why}

    def _container_running():
        info2, _ = container_info(name)
        return (info2 or {}).get("status", {}).get("state") == "running"

    state = info.get("status", {}).get("state")
    if state != "running":
        rc3, _out3, err3 = run(["container", "start", name], timeout=120)
        actions.append({"step": "container_start", "rc": rc3,
                        "detail": ((_out3 or "").strip().splitlines()[-1:]
                                   if (_out3 or "").strip() else [])
                        or (err3.strip().splitlines()[-1:] if err3.strip() else [])})
        if not _wait_until(_container_running, timeout_s=60, interval_s=5):
            info, why = container_info(name)
            return {"attempted": True, "ok": False, "fail_reason": "container_start_failed",
                    "actions": actions, "detail": why}
    return {"attempted": True, "ok": True, "fail_reason": None, "actions": actions}


def p4_p6(checks, name, image):
    rc, out, _ = run(["container", "system", "status"])
    status = ""
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "status":
            status = parts[1]
    check(checks, "P4", "PASS" if (rc == 0 and status == "running") else "FAIL", "shared-layer",
          "container system status=%s (rc=%d)" % (status or "unknown", rc))

    info, why = container_info(name)
    state = (info or {}).get("status", {}).get("state")
    # Default preflight: for an existing stopped container, attempt a safe
    # `container start` + recheck.  System start and auto-create are NOT
    # part of default mode — those live only in recover_execution_plane()
    # (--recover).
    if info and state != "running":
        rc_s, _out_s, _err_s = run(["container", "start", name], timeout=120)
        if rc_s == 0:
            info, why = container_info(name)
            state = (info or {}).get("status", {}).get("state")
    check(checks, "P5", "PASS" if state == "running" else "FAIL", "shared-layer",
          "%s state=%s%s" % (name, state or "absent", "" if info else " (%s)" % why))
    return info


def p6_image(checks, info, image):
    if not info:
        check(checks, "P6", "FAIL", "shared-layer", "no container info (P5 failed)")
        return
    cfg = info.get("configuration", {})
    ref = cfg.get("image", {}).get("reference") or cfg.get("image", {}).get("descriptor", {}).get("annotations", {}).get(
        "com.apple.containerization.image.name")
    platform = cfg.get("platform", {})
    rosetta = cfg.get("rosetta")
    ok = ref == image and platform.get("os") == "linux" and platform.get("architecture") == "arm64" and not rosetta
    check(checks, "P6", "PASS" if ok else "FAIL", "shared-layer",
          "image=%s os=%s arch=%s rosetta=%s" % (ref, platform.get("os"), platform.get("architecture"), rosetta))


def p7_p8(checks, name, qlib_version):
    rc, out, err = run(["container", "exec", name, "sh", "-c",
                        "touch /qlib/work/.preflight-probe && rm -f /qlib/work/.preflight-probe && echo work_ok"],
                       timeout=120)
    check(checks, "P7", "PASS" if (rc == 0 and "work_ok" in out) else "FAIL", "card-local",
          "/qlib/work writable: %s" % ((out.strip() or err.strip())[-160:] or "no output"))

    rc, out, err = run(["container", "exec", name, VENV_PYTHON, "-c",
                        "import sys, qlib; print(sys.executable); print(qlib.__version__)"], timeout=180)
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    got_exe = lines[0] if lines else ""
    got_ver = lines[1] if len(lines) > 1 else ""
    ok = rc == 0 and got_exe == VENV_PYTHON and got_ver == qlib_version
    check(checks, "P8", "PASS" if ok else "FAIL", "shared-layer",
          "%s -c 'import qlib' -> exe=%s version=%s (rc=%d)%s" % (
              VENV_PYTHON, got_exe or "n/a", got_ver or "n/a", rc,
              "" if ok else " | stderr: %s" % (err.strip().splitlines()[-1] if err.strip() else "none")))


def script_host_path(path, host_scripts):
    """Host-side location of a run-spec `script.path` (contract 16.2 P10), or None.

    The container's `/scripts` is a ro mount of the host scripts directory (runbook step 5 /
    container/scripts/run_phase4.sh), so a `/scripts/<name>` path is resolved through that existing
    mapping; an absolute host path is taken as-is. No resolver layer - one documented mapping.
    """
    if not path:
        return None
    if os.path.isfile(path):
        return path
    if host_scripts and path.startswith("/scripts/"):
        mapped = os.path.join(host_scripts, path[len("/scripts/"):])
        if os.path.isfile(mapped):
            return mapped
    return None


def _nonempty_ownership(doc):
    return [key for key in ("task_id", "kanban_task_id", "kanban_board")
            if key in doc and doc[key] is not None and doc[key] != ""]


def round_spec_contract_problem(attempt_dir):
    """None when the attempt's round-spec carries a valid v1.8 parameter_contract, else why not.

    v1.8 launch-gate extension of P10 (contract 16.2 / 26.1): a non-legacy family whose frozen
    round-spec has no generic parameter_contract fails closed in every post-survivor consumer,
    i.e. after the whole compute, so the gate refuses it before the compute starts.  The
    pre-schema Strategy A v2 bridge still resolves inside load_contract_from_round_spec, so the
    legacy family keeps passing (v1.8 backward compatibility).
    """
    round_spec_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(attempt_dir))),
                                   "round-spec.json")
    if not os.path.isfile(round_spec_path):
        return "missing %s" % round_spec_path
    try:
        with open(round_spec_path) as fh:
            spec = json.load(fh)
    except ValueError as exc:
        return "%s is not valid JSON: %s" % (round_spec_path, exc)
    problems = pc.validate_round_spec_contract(spec)
    if problems:
        return "; ".join(problems)
    return None


def round_terminal_verdict(attempt_dir, family):
    """The contract-terminal verdict token of the attempt's OWN round, else None (fail-closed).

    Contract 7.3/9.4: `verdict.json` is per ROUND, so the launch decision about a round may only
    read *that attempt's own* round - never an earlier round of the same family.  The token rule
    itself is the handoff scheduler's own function (`production_handoff.round_verdict_token`):
    `family_id` must match the family directory, `round_id` must be absent or name this round, a
    card-free (direct) family can never be closed by card-owned evidence, both sides' ownership ids
    must agree when present, and only the contract-terminal tokens count.

    A malformed, foreign or partial verdict is therefore NOT terminal authority: it reads as
    missing (exactly like the pipeline's release logic reads it), so it refuses nothing - and a
    legitimate same-round technical retry before the round's verdict remains possible.
    """
    attempt = Path(attempt_dir).resolve()
    return round_verdict_token(attempt.parents[1], attempt.parents[3].name, family)


def p9_p10(checks, attempt_dir, host_scripts=DEFAULT_HOST_SCRIPTS):
    if not attempt_dir:
        check(checks, "P9", "NA", "-", "no --attempt-dir; launch gate NOT evaluated")
        check(checks, "P10", "NA", "-", "no --attempt-dir; launch gate NOT evaluated")
        return
    present = [t for t in TERMINALS if os.path.exists(os.path.join(attempt_dir, t))]
    check(checks, "P9", "FAIL" if present else "PASS", "-",
          "terminal sentinel present: %s (INV-15 forbids re-run)" % present if present
          else "no terminal sentinel in %s" % attempt_dir)

    spec_path = os.path.join(attempt_dir, "run-spec.json")
    if not os.path.isfile(spec_path):
        check(checks, "P10", "FAIL", "card-local", "missing %s" % spec_path)
        return
    try:
        with open(spec_path) as fh:
            spec = json.load(fh)
    except ValueError as exc:
        check(checks, "P10", "FAIL", "card-local", "run-spec.json not valid JSON: %s" % exc)
        return
    # A direct family is owned by its immutable family/round/run path, not a Kanban card.
    family_path = Path(attempt_dir).resolve().parents[3] / "family.json"
    try:
        with open(family_path) as fh:
            family = json.load(fh)
    except (OSError, ValueError):
        family = {}
    direct = isinstance(family, dict) and isinstance(family.get("handoff"), dict) and \
        family["handoff"].get("execution") == "direct_hermes"
    required = ("schema_version", "family_id", "round_id", "run_id", "script")
    if not direct:
        required += ("task_id", "kanban_board")  # grandfathered frozen attempts
    missing = [k for k in required if k not in spec]
    if missing:
        check(checks, "P10", "FAIL", "card-local", "run-spec.json missing keys: %s" % missing)
        return
    if direct and (family.get("family_id") != spec.get("family_id") or
                   family.get("family_id") != Path(attempt_dir).resolve().parents[3].name or
                   spec.get("round_id") != Path(attempt_dir).resolve().parents[1].name or
                   spec.get("run_id") != Path(attempt_dir).resolve().name or
                   _nonempty_ownership(spec)):
        check(checks, "P10", "FAIL", "card-local", "direct family/run identity mismatch")
        return
    if direct:
        # The frozen round-spec is part of the launch gate too: a direct family must carry its own
        # family/round identity and no Kanban ownership, otherwise Qlib would start on evidence that
        # C4 later rejects as mapping_mismatch (same ownership rule as reconcile.mapping_problems).
        round_path = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(attempt_dir))), "round-spec.json")
        try:
            with open(round_path) as fh:
                round_spec = json.load(fh)
        except (OSError, ValueError):
            round_spec = None
        problems = []
        if not isinstance(round_spec, dict):
            problems.append("round-spec.json missing or unreadable")
        else:
            if round_spec.get("family_id") != family.get("family_id"):
                problems.append("round-spec family_id mismatch")
            if round_spec.get("round_id") != Path(attempt_dir).resolve().parents[1].name:
                problems.append("round-spec round_id mismatch")
            owned = _nonempty_ownership(round_spec)
            if owned:
                problems.append("direct round-spec has Kanban ownership %s" % owned)
        if problems:
            check(checks, "P10", "FAIL", "card-local",
                  "direct round-spec identity mismatch: %s" % "; ".join(problems))
            return
    script = spec.get("script") or {}
    sha = script.get("sha256")
    path = script.get("path")
    if not sha:
        check(checks, "P10", "FAIL", "card-local", "run-spec present; script.sha256 missing")
        return
    resolved = script_host_path(path, host_scripts)
    if not resolved:
        check(checks, "P10", "FAIL", "card-local",
              "run-spec present; script.sha256=%s NOT VERIFIED - script.path %r not readable from host "
              "and not resolvable via /scripts -> %s; launch gate must not pass unverified" % (sha, path, host_scripts))
        return
    import hashlib
    h = hashlib.sha256()
    with open(resolved, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    got = h.hexdigest()
    ok = ("sha256:" + got) == sha or got == sha
    detail = ("run-spec present; script.sha256=%s recomputed=%s (%s)%s"
              % (sha, got, resolved, "" if ok else " MISMATCH"))
    if ok:
        # Terminal authority boundary (2026-10-03; contract 8/16.2): a round whose own verdict.json
        # carries a contract-terminal verdict is a DECIDED, closed round.  A disposition/remediation
        # session is not authorised to create, launch or retry any attempt in it - so the launch
        # gate refuses a same-round attempt outright (in the live incident a corrected
        # serialization retry u2 was materialised and launched in a round that had already
        # published PASS, which is exactly the case this refuses).  A same-round technical retry
        # remains legal BEFORE that verdict exists; a further iteration needs a new round_id.
        closed = round_terminal_verdict(attempt_dir, family)
        if closed:
            ok = False
            detail += (" | terminal authority boundary: round %s already carries contract-terminal "
                       "verdict %s, so the round is closed and this launch/retry of another attempt "
                       "in it is not authorised (a same-round retry is legal only before the round's "
                       "verdict; a further iteration needs a new round_id)"
                       % (Path(attempt_dir).resolve().parents[1].name, closed))
        # v1.8 launch gate (contract 26.1): the frozen round-spec must already carry a valid
        # generic parameter_contract - validated BEFORE any compute, not after it.
        contract_problem = round_spec_contract_problem(attempt_dir)
        if contract_problem:
            ok = False
            detail += " | round-spec parameter_contract: %s" % contract_problem
    check(checks, "P10", "PASS" if ok else "FAIL", "card-local", detail)


def main():
    ap = argparse.ArgumentParser(description="Contract section 16 preflight P1-P10")
    ap.add_argument("--expansion", default=DEFAULT_EXPANSION)
    ap.add_argument("--container", default=DEFAULT_CONTAINER)
    ap.add_argument("--image", default=DEFAULT_IMAGE)
    ap.add_argument("--qlib-version", default=DEFAULT_QLIB_VERSION)
    ap.add_argument("--attempt-dir", default=None, help="attempt dir for P9/P10")
    ap.add_argument("--host-scripts", default=os.environ.get("QLIB_HOST_SCRIPTS", DEFAULT_HOST_SCRIPTS),
                    help="host directory mounted read-only as the container's /scripts (P10 sha resolution)")
    ap.add_argument("--launch", action="store_true", help="require --attempt-dir (full launch gate)")
    ap.add_argument("--recover", action="store_true",
                    help="opt-in reboot recovery before checks (ordered, fail-closed; default does not start system or auto-create but will start existing stopped containers)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.launch and not args.attempt_dir:
        sys.stderr.write("usage error: --launch requires --attempt-dir (P9/P10 cannot be evaluated)\n")
        return 2

    if args.recover:
        recovery = recover_execution_plane(args.expansion, args.container)
    else:
        recovery = {"attempted": False, "ok": True, "fail_reason": None, "actions": []}

    # Finding B: when --recover is used and recovery fails, fail closed
    # immediately. Recovery failure is a gate failure per §16.5 ordering.
    if recovery.get("attempted") and not recovery.get("ok"):
        report = {
            "schema_version": 1,
            "kind": "preflight",
            "contract_section": "16",
            "checked_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "host_boot_id": host_boot_id(),
            "attempt_dir": args.attempt_dir,
            "launch_gate": "not_evaluated",
            "recovery": recovery,
            "overall": "FAIL",
            "checks": [],
        }
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print("recovery: ok=%s fail_reason=%s actions=%d" % (
                recovery.get("ok"), recovery.get("fail_reason"), len(recovery.get("actions") or [])))
            print("overall: FAIL (recovery failed: %s)" % recovery.get("fail_reason"))
        return 1

    checks = []
    raw, _results = p1_p3(checks, args.expansion)
    info = p4_p6(checks, args.container, args.image)
    p2_raw(checks, args.container, raw)          # needs the container running
    p6_image(checks, info, args.image)
    p7_p8(checks, args.container, args.qlib_version)
    p9_p10(checks, args.attempt_dir, args.host_scripts)
    checks.sort(key=lambda c: int(c["id"][1:]))

    evaluated = [c for c in checks if c["status"] != "NA"]
    failed = [c for c in evaluated if c["status"] != "PASS"]
    report = {
        "schema_version": 1,
        "kind": "preflight",
        "contract_section": "16",
        "checked_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "host_boot_id": host_boot_id(),
        "attempt_dir": args.attempt_dir,
        "launch_gate": "evaluated" if args.attempt_dir else "not_evaluated",
        "recovery": recovery,
        "overall": "FAIL" if failed else "PASS",
        "checks": checks,
    }
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        if recovery.get("attempted"):
            print("recovery: ok=%s fail_reason=%s actions=%d" % (
                recovery.get("ok"), recovery.get("fail_reason"), len(recovery.get("actions") or [])))
        for c in checks:
            print("%-4s %-4s %-12s %s" % (c["id"], c["status"], c["layer"], c["detail"]))
        print("overall: %s (evaluated=%d failed=%d launch_gate=%s)" % (
            report["overall"], len(evaluated), len(failed), report["launch_gate"]))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
