#!/usr/bin/env python3
"""Best-effort mirror of formal survivor leaderboard artifacts.

Qlib results remain the only source of truth.  This module copies a small, traceable
subset into the private validated-survivor research repository and never changes the
leaderboard or any pipeline verdict.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import survivor_evidence as se  # noqa: E402
import survivor_index as si  # noqa: E402


DEFAULT_REPO = os.path.expanduser("~/workspace/validated-survivor-research")
COMMIT_MESSAGE = "feat: sync validated survivor artifacts"


def _json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


def _warning(message):
    return "WARNING: " + message


def _legacy_baseline_compatible(current, desired):
    """Accept only the known additive full.avg_trades_per_year baseline change."""
    try:
        current_value = json.loads(current)
        desired_value = json.loads(desired)
    except (TypeError, ValueError):
        return False
    if not isinstance(current_value, dict) or not isinstance(desired_value, dict):
        return False
    current_full = current_value.get("full")
    desired_full = desired_value.get("full")
    if not isinstance(current_full, dict) or not isinstance(desired_full, dict):
        return False
    if "avg_trades_per_year" in current_full or "avg_trades_per_year" not in desired_full:
        return False
    expected = json.loads(json.dumps(desired_value))
    del expected["full"]["avg_trades_per_year"]
    return current_value == expected


def _result(ok, result, warnings=None, changed=None, commit_sha=None):
    return {
        "ok": bool(ok),
        "result": result,
        "warnings": list(warnings or []),
        "changed_paths": list(changed or []),
        "commit_sha": commit_sha,
    }


def _git(repo, *args):
    try:
        return subprocess.run(["git", "-C", repo, *args], capture_output=True,
                              text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return exc


def _git_ok(repo, *args):
    proc = _git(repo, *args)
    if isinstance(proc, Exception):
        return None, str(proc)
    if proc.returncode:
        return None, (proc.stderr.strip() or proc.stdout.strip() or "git command failed")
    return proc.stdout.strip(), None


def _git_detail(proc):
    if isinstance(proc, Exception):
        return str(proc)
    return proc.stderr.strip() or proc.stdout.strip() or "git command failed"


def _load_sources(results_root):
    """Read the derived index and the already-published canonical leaderboard."""
    index, problems = si.build(results_root)
    if problems:
        raise ValueError("survivor index refused source: %s" % "; ".join(problems))
    if not isinstance(index, dict) or not isinstance(index.get("survivors"), list):
        raise ValueError("survivor index has no survivor list")

    by_id = {}
    for entry in index["survivors"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("survivor_id"), str):
            raise ValueError("survivor index contains an invalid survivor entry")
        survivor_id = entry["survivor_id"]
        if survivor_id in by_id:
            raise ValueError("survivor index contains duplicate survivor_id %r" % survivor_id)
        by_id[survivor_id] = entry

    board_path = os.path.join(si.write_boundary(results_root), se.LEADERBOARD_NAME)
    if not os.path.isfile(board_path):
        raise ValueError("canonical leaderboard missing: %s" % board_path)
    with open(board_path) as fh:
        board = json.load(fh)
    if not isinstance(board, dict) or not isinstance(board.get("entries"), list):
        raise ValueError("canonical leaderboard entries is not a list")

    entries = []
    seen = set()
    for board_entry in board["entries"]:
        if not isinstance(board_entry, dict):
            raise ValueError("canonical leaderboard contains a non-object entry")
        survivor_id = board_entry.get("survivor_id")
        if (not isinstance(survivor_id, str) or not survivor_id
                or survivor_id in seen or survivor_id not in by_id):
            raise ValueError("leaderboard/index survivor identity mismatch: %r" % survivor_id)
        seen.add(survivor_id)
        entries.append(board_entry)
    return entries, by_id, _read(board_path)


def _evidence_files(results_root, board_entry, index_entry, warnings):
    """Return only the two compact evidence files after a durable package check."""
    if board_entry.get("evidence_package_status") != "PRESENT":
        return {}

    survivor_id = board_entry["survivor_id"]
    try:
        _manifest, problems = se.check_package(results_root, survivor_id, index_entry)
    except Exception as exc:
        problems = [str(exc)]
    if problems:
        warnings.append(_warning("evidence package %s refused: %s"
                                 % (survivor_id, "; ".join(problems))))
        return {}

    package = se.package_dir(results_root, survivor_id)
    manifest_path = os.path.join(package, se.MANIFEST_NAME)
    aggregate_path = os.path.join(package, se.AGGREGATE_NAME)
    if not (os.path.isfile(manifest_path) and os.path.isfile(aggregate_path)):
        warnings.append(_warning("evidence package %s passed check but its compact files are "
                                 "missing" % survivor_id))
        return {}
    return {
        "survivors/%s/evidence-manifest.json" % survivor_id: _read(manifest_path),
        "survivors/%s/aggregate.csv" % survivor_id: _read(aggregate_path),
    }


def _desired_files(results_root, entries, index_by_id, board_bytes, warnings):
    """Build the exact managed mirror layout without recomputing any ranking."""
    files = {"leaderboard/leaderboard.json": board_bytes}
    immutable = set()
    for board_entry in entries:
        survivor_id = board_entry["survivor_id"]
        baseline = "survivors/%s/baseline.json" % survivor_id
        files[baseline] = _json_bytes(index_by_id[survivor_id])
        immutable.add(baseline)
        evidence = _evidence_files(results_root, board_entry, index_by_id[survivor_id], warnings)
        files.update(evidence)
        immutable.update(evidence)
    return files, immutable


def _preflight(repo):
    """Require a clean identity of the target branch, but never repair it automatically."""
    target = os.path.realpath(os.path.abspath(os.path.expanduser(os.fspath(repo))))
    if not os.path.isdir(target):
        return target, _warning("private repo missing: %s" % target)

    top, error = _git_ok(target, "rev-parse", "--show-toplevel")
    if error or os.path.realpath(top or "") != target:
        return target, _warning("target is not the expected git worktree: %s"
                                % (error or target))
    branch, error = _git_ok(target, "branch", "--show-current")
    if error or branch != "main":
        return target, _warning("private repo must be on main")
    remote, error = _git_ok(target, "remote", "get-url", "origin")
    if error or not remote:
        return target, _warning("private repo has no usable origin")
    head, error = _git_ok(target, "rev-parse", "HEAD")
    if error or not head:
        return target, _warning("cannot read private repo HEAD: %s" % (error or "missing"))
    remote_head, error = _git_ok(target, "ls-remote", "origin", "refs/heads/main")
    if error or not remote_head:
        return target, _warning("cannot verify origin/main: %s" % (error or "missing"))
    remote_sha = remote_head.split()[0]
    if remote_sha != head:
        return target, _warning("local main and origin/main differ; refusing auto "
                                "merge/rebase/reset")
    return target, None


def _plan(target, files, immutable):
    """Return changed paths and refuse immutable conflicts before any write."""
    changed = []
    warnings = []
    conflicts = []
    for relative, body in sorted(files.items()):
        path = os.path.join(target, relative)
        if os.path.lexists(path) and (os.path.islink(path) or not os.path.isfile(path)):
            message = _warning("managed path is not a regular file: %s" % relative)
            if relative in immutable:
                conflicts.append(message)
            else:
                warnings.append(message)
            continue

        current = _read(path) if os.path.isfile(path) else None
        legacy_baseline = (relative.startswith("survivors/")
                           and relative.endswith("/baseline.json"))
        compatible_baseline = (legacy_baseline and current is not None
                               and _legacy_baseline_compatible(current, body))
        if relative in immutable and current is not None and current != body \
                and not compatible_baseline:
            conflicts.append(_warning("immutable managed file conflict: %s" % relative))
            continue
        if current != body and not compatible_baseline:
            changed.append(relative)
    return changed, warnings, conflicts


def _atomic_write(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".survivor-export-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def export(results_root, repo=None):
    """Mirror one canonical leaderboard rebuild into the optional private git repository."""
    warnings = []
    changed = []
    try:
        root = os.path.abspath(os.fspath(results_root))
        entries, index_by_id, board_bytes = _load_sources(root)
        files, immutable = _desired_files(root, entries, index_by_id, board_bytes, warnings)

        target, preflight_warning = _preflight(DEFAULT_REPO if repo is None else repo)
        if preflight_warning:
            return _result(False, "warning", warnings + [preflight_warning])

        changed, plan_warnings, conflicts = _plan(target, files, immutable)
        warnings.extend(plan_warnings)
        if conflicts:
            return _result(False, "conflict", warnings + conflicts)
        if not changed:
            return _result(not warnings, "unchanged" if not warnings else "warning", warnings)

        for relative in changed:
            _atomic_write(os.path.join(target, relative), files[relative])

        add = _git(target, "add", "--", *changed)
        if isinstance(add, Exception) or add.returncode:
            return _result(False, "warning", warnings + [_warning("git add failed: %s"
                                                                   % _git_detail(add))],
                           changed)

        commit = _git(target, "commit", "--only", "-m", COMMIT_MESSAGE, "--", *changed)
        if isinstance(commit, Exception) or commit.returncode:
            return _result(False, "warning", warnings + [_warning("git commit failed: %s"
                                                                   % _git_detail(commit))],
                           changed)

        commit_sha, error = _git_ok(target, "rev-parse", "HEAD")
        if error or not commit_sha:
            return _result(False, "warning", warnings + [_warning("cannot read new commit: %s"
                                                                   % (error or "missing"))],
                           changed)

        push = _git(target, "push", "origin", "main")
        if isinstance(push, Exception) or push.returncode:
            return _result(False, "warning", warnings + [_warning("git push failed: %s"
                                                                   % _git_detail(push))],
                           changed, commit_sha)

        remote_head, error = _git_ok(target, "ls-remote", "origin", "refs/heads/main")
        if error or not remote_head or remote_head.split()[0] != commit_sha:
            return _result(False, "warning", warnings + [_warning("origin/main readback mismatch")],
                           changed, commit_sha)

        if warnings:
            return _result(False, "warning", warnings, changed, commit_sha)
        return _result(True, "exported", warnings, changed, commit_sha)
    except Exception as exc:
        return _result(False, "warning", warnings + [_warning("export refused: %s" % exc)], changed)


def main(argv=None):
    parser = argparse.ArgumentParser(description="mirror canonical validated survivors")
    parser.add_argument("--results-root", required=True)
    parser.add_argument("--repo")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = export(args.results_root, args.repo)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("private-export=%s changed=%d" % (result["result"], len(result["changed_paths"])))
        for warning in result["warnings"]:
            print(warning, file=sys.stderr)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
