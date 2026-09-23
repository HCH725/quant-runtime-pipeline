#!/opt/homebrew/bin/python3
"""Minimal n8n -> host action bridge (Phase 2C1, card t_e886543c).

One fixed request file, one fixed response file, ONE allowlisted action.
Claim `production_handoff.request.json` by same-filesystem rename (so a
duplicate launchd wake cannot double-run the same request), and ONLY if the
tiny allowlisted payload validates, invoke the EXISTING scheduler wrapper
`~/.hermes/scripts/quant_production_handoff.py` - which runpy-calls canonical
`runtime/production_handoff.py`. No handoff logic is duplicated here.

Fail closed: unknown / malformed / oversize / wrong-field-set payloads write
a rejection response and invoke NOTHING. Both paths are module constants -
no path ever comes from request data. The child environment is built fresh
(HOME + PATH only), never inherited, so no Hermes fence vars or secrets can
leak into the action.

Response is a NON-AUTHORITATIVE read-back only (temp+rename): schema,
request_id, action, started_at_utc, finished_at_utc, exit_code, status,
stdout/stderr (byte-bounded). Correlate a response with its request by
`request_id`; a stale response may persist between requests. No queue, no
second state store, no daemon - launchd `ai.quant.n8n-host-bridge` wakes this
script via WatchPaths on the request file (runtime/ai.quant.n8n-host-bridge.plist;
semantics + evidence: N8N_CONTROL_PLANE.md section 7.3).

Exit codes: 0 = no-op (no request) or action finished with rc 0; 1 = rejected
request, action failure, action timeout, or internal error (fail closed).
"""
import errno
import json
import os
import secrets
import signal
import stat
import subprocess
import sys
import time

# The ONLY paths this script ever touches. Never derived from request data.
REQUEST_PATH = "/Users/hong/workspace/n8n/files/control/production_handoff.request.json"
RESPONSE_PATH = "/Users/hong/workspace/n8n/files/control/production_handoff.response.json"
# Claim = same-directory rename (same filesystem by construction) to a per-PID name:
# the rename source (the request path) is the single serialization point - of all
# duplicate wakes for ONE request exactly one rename succeeds, the rest hit
# FileNotFoundError and no-op. Per-PID destinations additionally ensure a second
# request arriving while a run is still in flight can never clobber (and destroy
# the unread bytes of) that in-flight claim.
CLAIM_SUFFIX = ".claimed.%d"  # % os.getpid()

SCHEMA = "quant-control-action/v1"
RESPONSE_SCHEMA = "quant-control-action-response/v1"
ACTION = "production_handoff_once"
ALLOWED_KEYS = ("schema", "action", "request_id")

# The ONLY allowed action: the existing thin scheduler wrapper (runpy -> canonical
# runtime/production_handoff.py). Hardcoded; no field of the request may alter it.
WRAPPER = "/Users/hong/.hermes/scripts/quant_production_handoff.py"
ACTION_CMD = ["/opt/homebrew/bin/python3", WRAPPER]
# Fresh minimal env (hermes CLI lives on ~/.local/bin). Never os.environ: the
# launchd/manual context must not donate HERMES_* fence vars or credentials.
ACTION_ENV = {
    "HOME": "/Users/hong",
    "PATH": "/Users/hong/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
}
ACTION_TIMEOUT_S = 600  # bounded subprocess timeout; the wrapper itself caps each CLI call at 180s

MAX_REQUEST_BYTES = 4096  # oversize requests fail closed before JSON parsing
MAX_REQUEST_ID = 128  # non-empty bounded string
MAX_ECHO = 128  # rejected `action` echoed into the response, char-bounded
MAX_OUTPUT_BYTES = 4096  # stdout/stderr byte cap in the response


class _ClaimRejected(Exception):
    """The fixed request path exists but is unsafe to claim."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def now_utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _clip_chars(text, cap):
    return text if len(text) <= cap else text[:cap] + "...[truncated]"


def _clip_bytes(stream, cap=MAX_OUTPUT_BYTES):
    """Byte-accurate cap; slicing may split a UTF-8 sequence, so decode replace."""
    if not isinstance(stream, str):
        stream = "" if stream is None else str(stream)
    raw = stream.encode("utf-8", "replace")
    if len(raw) <= cap:
        return stream
    return raw[:cap].decode("utf-8", "replace") + "\n...[truncated]"


def _as_text(part):
    if part is None:
        return ""
    if isinstance(part, bytes):
        return part.decode("utf-8", "replace")
    return str(part)


def _claim_path():
    return REQUEST_PATH + (CLAIM_SUFFIX % os.getpid())


def _claim():
    """Atomically claim the request; returns the claim path, or None = no-op."""
    claim_path = _claim_path()
    try:
        source = os.lstat(REQUEST_PATH)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _ClaimRejected("error_internal") from exc
    if stat.S_ISDIR(source.st_mode):
        # Never rename a directory into the claim namespace: cleanup must not
        # recurse into attacker-controlled contents. Leave this unclaimed and
        # emit a rejection response from main().
        raise _ClaimRejected("rejected_non_regular_file")
    try:
        os.rename(REQUEST_PATH, claim_path)
        return claim_path
    except FileNotFoundError:
        # No request, or a duplicate wake lost the rename race - both are no-ops.
        return None


def _read_claim(claim_path):
    """(bytes, reject_reason). Bounded read; reject non-regular files safely."""
    try:
        initial = os.lstat(claim_path)
    except FileNotFoundError:
        return b"", "error_internal"
    except OSError:
        return b"", "error_internal"
    if stat.S_ISLNK(initial.st_mode) or not stat.S_ISREG(initial.st_mode):
        return b"", "rejected_non_regular_file"

    flags = os.O_RDONLY | os.O_NONBLOCK
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    fd = -1
    try:
        fd = os.open(claim_path, flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            return b"", "rejected_non_regular_file"
        return b"", "error_internal"
    try:
        current = os.fstat(fd)
        if not stat.S_ISREG(current.st_mode):
            return b"", "rejected_non_regular_file"
        with os.fdopen(fd, "rb", closefd=True) as fh:
            fd = -1
            raw = fh.read(MAX_REQUEST_BYTES + 1)
    except OSError:
        return b"", "error_internal"
    finally:
        if fd != -1:
            os.close(fd)
    if len(raw) > MAX_REQUEST_BYTES:
        return b"", "rejected_oversize"
    return raw, None


def _valid_request_id(value):
    return (
        isinstance(value, str)
        and 0 < len(value) <= MAX_REQUEST_ID
        and all(not 0xD800 <= ord(char) <= 0xDFFF for char in value)
    )


def _validate(raw):
    """(reject_reason, request_id, action). reason None = accepted."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError):
        return "rejected_malformed_json", None, None
    if not isinstance(data, dict):
        # Well-formed JSON that is not the schema object is still malformed for us.
        return "rejected_malformed_json", None, None
    rid = data.get("request_id")
    rid_ok = _valid_request_id(rid)
    act = data.get("action")
    act_echo = _clip_chars(act, MAX_ECHO) if isinstance(act, str) else None
    rid_echo = rid if rid_ok else None
    if set(data) != set(ALLOWED_KEYS):
        return "rejected_field_set", rid_echo, act_echo
    if data.get("schema") != SCHEMA:
        return "rejected_schema", rid_echo, act_echo
    if act != ACTION:  # missing, wrong type, or simply not the one allowlisted action
        return "rejected_unknown_action", rid_echo, act_echo
    if not rid_ok:
        return "rejected_request_id", None, act_echo
    return None, rid, act


def _run_action(cmd, env, timeout):
    proc = subprocess.Popen(
        cmd, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        # The wrapper may spawn hermes/child processes. Kill the whole session,
        # not only the direct wrapper, before producing the timeout response.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        out, err = proc.communicate()
        if not out:
            out = getattr(exc, "output", None)
        if not err:
            err = getattr(exc, "stderr", None)
        raise subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err) from None
    return proc.returncode, out or "", err or ""


def _dispatch(runner, rid, act, started):
    """(response_payload, ok). The allowlisted action - nothing else can reach here."""
    try:
        rc, out, err = runner(ACTION_CMD, ACTION_ENV, ACTION_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        return _response(rid, act, started, None, "action_timeout",
                         _as_text(getattr(exc, "stdout", None)),
                         _as_text(getattr(exc, "stderr", None))), False
    except OSError as exc:  # interpreter/wrapper vanished - fail closed, never substitute another command
        return _response(rid, act, started, None, "error_internal", "", str(exc)), False
    status = "ok" if rc == 0 else "action_failed"
    return _response(rid, act, started, rc, status, out, err), rc == 0


def _response(rid, act, started, exit_code, status, stdout, stderr):
    return {
        "schema": RESPONSE_SCHEMA,
        "request_id": rid,
        "action": act,
        "started_at_utc": started,
        "finished_at_utc": now_utc(),
        "exit_code": exit_code,
        "status": status,
        "stdout": _clip_bytes(stdout),
        "stderr": _clip_bytes(stderr),
    }


def _open_response_temp():
    """Create a same-directory response temp without following symlinks."""
    directory = os.path.dirname(RESPONSE_PATH) or "."
    basename = os.path.basename(RESPONSE_PATH)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    for _ in range(32):
        token = secrets.token_hex(8)
        tmp = os.path.join(directory, basename + ".tmp." + token)
        try:
            return tmp, os.open(tmp, flags, 0o600)
        except OSError as exc:
            if exc.errno in (errno.EEXIST, errno.ELOOP):
                continue
            raise
    raise FileExistsError("could not allocate a response temp path")


def _write_response(payload):
    """Atomic temp+rename; a failed write never leaves a partial response behind."""
    tmp = None
    fd = None
    try:
        tmp, fd = _open_response_temp()
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fd = None
            json.dump(payload, fh, ensure_ascii=True, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, RESPONSE_PATH)  # temp+rename; destination symlink is replaced
        if not stat.S_ISREG(os.lstat(RESPONSE_PATH).st_mode):
            raise OSError("response path is not a regular file")
    except Exception:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp is not None:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
        raise


def _remove_claim(claim_path):
    """Remove only the claimed directory entry; never recursively delete."""
    try:
        claimed = os.lstat(claim_path)
    except FileNotFoundError:
        return
    if stat.S_ISDIR(claimed.st_mode):
        # Directories are refused before rename. If a TOCTOU race still puts
        # one in the claim namespace, leave its contents untouched rather than
        # recursively deleting attacker-controlled data.
        return
    try:
        os.unlink(claim_path)
    except OSError:
        pass


def main(runner=None):
    runner = runner or _run_action
    started = now_utc()
    try:
        claim_path = _claim()
    except _ClaimRejected as exc:
        _write_response(_response(None, None, started, None, exc.reason, "", ""))
        return 1
    if claim_path is None:
        return 0  # no request = no-op, no response
    started = now_utc()
    ok = False
    written = False
    try:
        try:
            raw, reason = _read_claim(claim_path)
        except OSError as exc:
            payload = _response(None, None, started, None, "error_internal", "", str(exc))
        else:
            if reason is not None:
                payload = _response(None, None, started, None, reason, "", "")
            else:
                reason, rid, act = _validate(raw)
                if reason is not None:
                    payload = _response(rid, act, started, None, reason, "", "")
                else:
                    payload, ok = _dispatch(runner, rid, act, started)
        _write_response(payload)
        written = True
    finally:
        # Clean up the claimed request only after its response was written; on an
        # unservable write the claim stays on disk as evidence instead of silently
        # destroying the request. ponytail: a claim orphaned by a killed run is never
        # read again - at most one stale file per crash can accumulate in control/,
        # there is deliberately no janitor (ceiling: crash rate; manual `rm` if ever needed).
        if written:
            _remove_claim(claim_path)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
