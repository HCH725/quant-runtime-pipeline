#!/usr/bin/env python3
"""Best-effort compact mirror of formal survivor leaderboard entries.

Qlib results remain canonical. This module only mirrors small, traceable files into the
private validated-survivor research repo; it never changes ranking or verdicts.
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


def _result(ok, result, warnings=None, changed=None, commit_sha=None):
    return {"ok": bool(ok), "result": result, "warnings": list(warnings or []),
            "changed_paths": list(changed or []), "commit_sha": commit_sha}


def _warning(text):
    return "WARNING: " + text


def _load_sources(results_root):
    index, problems = si.build(results_root)
    if problems:
        raise ValueError("survivor index refused source: %s" % "; ".join(problems))
    board_path = os.path.join(si.write_boundary(results_root), se.LEADERBOARD_NAME)
    if not os.path.isfile(board_path):
        raise ValueError("canonical leaderboard missing: %s" % board_path)
    with open(board_path) as fh:
        board = json.load(fh)
    entries = board.get("entries")
    if not isinstance(entries, list):
        raise ValueError("leaderboard entries is not a list")
    by_id = {entry["survivor_id"]: entry for entry in index.get("survivors", [])
             if isinstance(entry, dict) and isinstance(entry.get("survivor_id"), str)}
    if len(by_id) != len(index.get("survivors", [])):
        raise ValueError("survivor index contains invalid/duplicate IDs")
    seen = set()
    for entry in entries:
        sid = entry.get("survivor_id") if isinstance(entry, dict) else None
        if not isinstance(sid, str) or not sid or sid in seen or sid not in by_id:
            raise ValueError("leaderboard/index survivor identity mismatch: %r" % sid)
        seen.add(sid)
    return entries, by_id, _read(board_path)


def _evidence_files(results_root, board_entry, index_entry, warnings):
    if board_entry.get("evidence_package_status") != "PRESENT":
        return {}
    sid = board_entry["survivor_id"]
    try:
        _manifest, problems = se.check_package(results_root, sid, index_entry)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        problems = [str(exc)]
    if problems:
        warnings.append(_warning("section 28 package %s refused: %s" % (sid, "; ".join(problems))))
        return {}
    package = se.package_dir(results_root, sid)
    return {
        "survivors/%s/evidence-manifest.json" % sid: _read(os.path.join(package, se.MANIFEST_NAME)),
        "survivors/%s/aggregate.csv" % sid: _read(os.path.join(package, se.AGGREGATE_NAME)),
    }


def _desired(results_root, entries, by_id, board_bytes, warnings):
    files = {"leaderboard/leaderboard.json": board_bytes}
    immutable = set()
    for board_entry in entries:
        sid = board_entry["survivor_id"]
        baseline = "survivors/%s/baseline.json" % sid
        files[baseline] = _json_bytes(by_id[sid])
        immutable.add(baseline)
        for rel, body in _evidence_files(results_root, board_entry, by_id[sid], warnings).items():
            files[rel] = body
            immutable.add(rel)
    return files, immutable


def _preflight(repo):
    repo = os.path.realpath(os.path.abspath(os.path.expanduser(repo)))
    if not os.path.isdir(repo):
        return repo, _warning("private repo missing: %s" % repo)
    top, err = _git_ok(repo, "rev-parse", "--show-toplevel")
    if err or os.path.realpath(top or "") != repo:
        return repo, _warning("target is not the expected git worktree: %s" % (err or repo))
    branch, err = _git_ok(repo, "branch", "--show-current")
    if err or branch != "main":
        return repo, _warning("private repo must be on main")
    remote, err = _git_ok(repo, "remote", "get-url", "origin")
    if err or not remote:
        return repo, _warning("private repo has no usable origin")
    head, err = _git_ok(repo, "rev-parse", "HEAD")
    if err:
        return repo, _warning("cannot read private repo HEAD: %s" % err)
    remote_head, err = _git_ok(repo, "ls-remote", "origin", "refs/heads/main")
    if err or not remote_head:
        return repo, _warning("cannot verify origin/main: %s" % (err or "missing"))
    if remote_head.split()[0] != head:
        return repo, _warning("local main and origin/main differ; refusing auto merge/rebase/reset")
    return repo, None


def _plan(repo, desired, immutable):
    changed, warnings = [], []
    for rel, body in sorted(desired.items()):
        path = os.path.join(repo, rel)
        if os.path.lexists(path) and (os.path.islink(path) or not os.path.isfile(path)):
            warnings.append(_warning("managed path is not a regular file: %s" % rel))
            continue
        current = _read(path) if os.path.isfile(path) else None
        if current is not None and current != body and rel in immutable:
            warnings.append(_warning("immutable managed file conflict: %s" % rel))
            continue
        status, err = _git_ok(repo, "status", "--porcelain", "--", rel)
        if err:
            warnings.append(_warning("cannot inspect managed path %s: %s" % (rel, err)))
            continue
        if current != body or status:
            changed.append(rel)
    return changed, warnings


def _atomic_write(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".mirror-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(body)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def export(results_root, repo=None):
    warnings = []
    try:
        root = os.path.abspath(os.path.expanduser(results_root))
        entries, by_id, board_bytes = _load_sources(root)
        desired, immutable = _desired(root, entries, by_id, board_bytes, warnings)
        target, problem = _preflight(repo or DEFAULT_REPO)
        if problem:
            return _result(False, "warning", warnings + [problem])
        changed, problems = _plan(target, desired, immutable)
        if problems:
            return _result(False, "conflict", warnings + problems)
        if not changed:
            return _result(True, "unchanged", warnings)

        for rel in changed:
            _atomic_write(os.path.join(target, rel), desired[rel])

        add = _git(target, "add", "--", *changed)
        if isinstance(add, Exception) or add.returncode:
            detail = str(add) if isinstance(add, Exception) else (add.stderr.strip() or add.stdout.strip())
            return _result(False, "warning", warnings + [_warning("git add failed: %s" % detail)], changed)
        commit = _git(target, "commit", "--only", "-m", COMMIT_MESSAGE, "--", *changed)
        if isinstance(commit, Exception) or commit.returncode:
            detail = str(commit) if isinstance(commit, Exception) else (commit.stderr.strip() or commit.stdout.strip())
            return _result(False, "warning", warnings + [_warning("git commit failed: %s" % detail)], changed)
        commit_sha, err = _git_ok(target, "rev-parse", "HEAD")
        if err:
            return _result(False, "warning", warnings + [_warning("cannot read new commit: %s" % err)], changed)
        push = _git(target, "push", "origin", "main")
        if isinstance(push, Exception) or push.returncode:
            detail = str(push) if isinstance(push, Exception) else (push.stderr.strip() or push.stdout.strip())
            return _result(False, "warning", warnings + [_warning("git push failed: %s" % detail)], changed, commit_sha)
        remote_head, err = _git_ok(target, "ls-remote", "origin", "refs/heads/main")
        if err or not remote_head or remote_head.split()[0] != commit_sha:
            return _result(False, "warning", warnings + [_warning("origin/main readback mismatch")], changed, commit_sha)
        return _result(True, "exported", warnings, changed, commit_sha)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        return _result(False, "warning", warnings + [_warning("export refused: %s" % exc)])


def main(argv=None):
    ap = argparse.ArgumentParser(description="mirror canonical validated survivors")
    ap.add_argument("--results-root", required=True)
    ap.add_argument("--repo")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
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
