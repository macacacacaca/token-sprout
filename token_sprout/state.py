"""Local plant state.

Concurrency protocol (spec §5.2):
- Single writer: only the proxy process writes.
- Every write is temp file + atomic replace, so readers never see a
  half-written JSON.
- A file lock serializes concurrent read-modify-write cycles inside the
  proxy (parallel requests settling at the same time).
- Readers never take the lock. One-shot views fall back to defaults on an
  error, while the continuous watch UI keeps its previous good frame.

The only data that ever lands here is usage metadata and timestamps —
never prompts, completions, or credentials.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import secrets
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from filelock import FileLock, Timeout as FileLockTimeout

from . import game
from .usage_parser import Usage

STATE_VERSION = "0.1.0"
STATE_LOCK_TIMEOUT_SECONDS = 1.0
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
_PRIVATE_FILENAMES = (
    "plant_state.json",
    "plant_state.json.corrupt",
    "proxy.secret",
    "proxy.log",
    "state.lock",
)


class StateBusyError(RuntimeError):
    """The state lock stayed busy beyond the fail-open write budget."""


def default_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "generation": 1,
        "total_tokens": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cache_creation_tokens": 0,
        "total_cache_read_tokens": 0,
        "current_exp": 0,
        "level": 1,
        "stage": "seed",
        "active_requests": 0,
        "live_tokens_estimate": 0,
        "last_request_tokens": 0,
        "last_input_tokens": 0,
        "last_output_tokens": 0,
        "last_request_started_at": None,
        "last_request_finished_at": None,
    }


def home_dir() -> Path:
    return Path(os.environ.get("TOKEN_SPROUT_HOME", str(Path.home() / ".token-sprout")))


def state_path() -> Path:
    return home_dir() / "plant_state.json"


def _lock_path() -> Path:
    return home_dir() / "state.lock"


def secret_path() -> Path:
    return home_dir() / "proxy.secret"


def proxy_log_path() -> Path:
    return home_dir() / "proxy.log"


def _fchmod_nofollow(path: Path, mode: int, *, directory: bool = False) -> None:
    """chmod through a fresh O_NOFOLLOW descriptor — never a path lookup.

    A path-based chmod after a separate is_symlink() check is a TOCTOU race:
    a same-user process can swap the path for a symlink between the check and
    the chmod, redirecting the mode change to an arbitrary file. Linux has no
    lchmod, so the race-free form is open(O_NOFOLLOW) + fchmod (a symlink
    makes the open fail with ELOOP instead of being followed).
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    if directory:
        flags |= os.O_DIRECTORY
    try:
        fd = os.open(str(path), flags)
    except OSError:
        return
    try:
        info = os.fstat(fd)
        if directory or stat.S_ISREG(info.st_mode):
            os.fchmod(fd, mode)
    finally:
        os.close(fd)


def ensure_home_permissions() -> Path:
    """Create the private data directory and repair legacy permissions.

    Earlier development builds relied on the process umask, which could
    leave the directory and non-secret files readable by other local users.
    Repairs go through ``_fchmod_nofollow``; a same-user symlink must never
    let this helper chmod another file.
    """
    directory = home_dir()
    directory.mkdir(parents=True, exist_ok=True, mode=PRIVATE_DIR_MODE)
    if os.name == "posix":
        _fchmod_nofollow(directory, PRIVATE_DIR_MODE, directory=True)
        for filename in _PRIVATE_FILENAMES:
            _fchmod_nofollow(directory / filename, PRIVATE_FILE_MODE)
    return directory


def open_proxy_log():
    """Open proxy.log for append without ever creating a public file."""
    ensure_home_permissions()
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(proxy_log_path()), flags, PRIVATE_FILE_MODE)
    if os.name == "posix":
        os.fchmod(fd, PRIVATE_FILE_MODE)
    return os.fdopen(fd, "ab")


def ensure_secret() -> str:
    """Return the local proxy secret, creating a 0600 file on first use.

    This secret lets `token-sprout run` prove that whatever is listening on
    the port is really *our* proxy — via a port-bound HMAC challenge — before
    it hands that listener Claude Code's credentials. Only the owning OS user
    can read the 0600 file, so a rogue process running as another user that
    grabs the port cannot forge the proof. See cli._proxy_health.
    """
    ensure_home_permissions()
    existing = read_secret()
    if existing:
        return existing
    token = secrets.token_hex(32)
    # O_CREAT with mode 0600 so the secret is never briefly world-readable.
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(secret_path()), flags, PRIVATE_FILE_MODE)
    if os.name == "posix":
        os.fchmod(fd, PRIVATE_FILE_MODE)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    return token


def read_secret() -> str | None:
    try:
        token = secret_path().read_text().strip()
    except OSError:
        return None
    return token or None


def health_proof(secret: str, port: int, nonce: str) -> str:
    """Return a domain- and port-bound listener identity proof.

    Binding the configured listen port prevents a real proxy on another port
    from being used as a signing oracle by a fake listener on the target
    port.  The proxy must pass its startup configuration here, never a Host
    header or other request-controlled value.
    """
    payload = f"token-sprout-health-v1\0{port}\0{nonce}".encode()
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def init_home() -> Path:
    """Create the state directory and file if missing. Idempotent."""
    ensure_home_permissions()
    if not state_path().exists():
        _write(default_state())
    return state_path()


def try_load_state() -> dict[str, Any] | None:
    """Read and normalize state, or None when the file is missing/damaged.

    Continuous readers (``watch``) use the None signal to keep showing the
    previous good frame instead of snapping to a fresh seed (spec §5.2).
    """
    try:
        data = json.loads(state_path().read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return _normalize_state(data)


def load_state() -> dict[str, Any]:
    """Read and normalize state; missing or malformed data falls back safely.

    JSON syntax is only one way a state file can be damaged.  Valid JSON
    with strings in numeric fields, negative counters, or stale derived
    ``stage``/``level`` values must not be able to crash statusline/watch.
    """
    loaded = try_load_state()
    return default_state() if loaded is None else loaded


# Derived from default_state() so a new field cannot be forgotten here: a
# field missing from these sets would be silently reset to its default by
# _normalize_state on every load (update_state reloads before each mutation),
# so it could never accumulate. Non-bool int defaults are validated as
# non-negative counters; None defaults are timestamps. generation (>= 1) and
# the derived version/stage/level are handled explicitly below.
_NONNEGATIVE_INT_FIELDS = {
    key
    for key, value in default_state().items()
    if isinstance(value, int)
    and not isinstance(value, bool)
    and key not in ("generation", "level")
}
_TIMESTAMP_FIELDS = {key for key, value in default_state().items() if value is None}


def _is_nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _normalize_state(data: dict[str, Any]) -> dict[str, Any]:
    """Migrate known fields into the current, internally consistent schema."""
    base = default_state()
    generation = data.get("generation")
    if _is_nonnegative_int(generation) and generation >= 1:
        base["generation"] = generation
    for key in _NONNEGATIVE_INT_FIELDS:
        value = data.get(key)
        if _is_nonnegative_int(value):
            base[key] = value
    for key in _TIMESTAMP_FIELDS:
        value = data.get(key)
        if value is None or isinstance(value, str):
            base[key] = value

    # Schema version and derived presentation fields always reflect the
    # current code, even when loading an older or manually edited file.
    base["version"] = STATE_VERSION
    view = game.plant_view(base)
    base["stage"] = view["stage"]
    base["level"] = view["level"]
    return base


def _write(state: dict[str, Any]) -> None:
    ensure_home_permissions()
    fd, tmp = tempfile.mkstemp(dir=home_dir(), prefix=".plant_state.", suffix=".tmp")
    try:
        if os.name == "posix":
            os.fchmod(fd, PRIVATE_FILE_MODE)
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
        # mkstemp + the fchmod above already fixed the inode's mode; rename
        # keeps it. A path-based chmod here would only reopen a symlink race.
        os.replace(tmp, state_path())
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


def _quarantine_corrupt_state() -> None:
    """Set a damaged state file aside as ``plant_state.json.corrupt``.

    The writer must never rebuild-and-overwrite a corrupt file in place
    (spec §5.2): the plant restarts from defaults, but the damaged JSON —
    possibly days of accumulated progress — stays recoverable by hand.
    Only the most recent corrupt file is kept.
    """
    path = state_path()
    try:
        path.lstat()
    except FileNotFoundError:
        # A missing file is normal on first use; there is nothing to preserve.
        return

    # Do not suppress a failed quarantine. update_state must not continue and
    # overwrite the only recoverable copy with defaults. In the proxy this
    # exception is contained by the background writer's fail-open boundary.
    os.replace(path, path.with_name(path.name + ".corrupt"))


def update_state(mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """Bounded locked read-modify-write. Returns the state as written.

    A stuck external lock must not pin the background writer forever.  The
    proxy catches ``StateBusyError`` on its writer thread and drops only that
    bookkeeping job; forwarding remains unaffected.
    """
    # FileLock creates the lock file with mode=0600 and ensure_home_permissions
    # repairs it fd-safely; no path-based chmod here (symlink race).
    ensure_home_permissions()
    try:
        with FileLock(
            str(_lock_path()),
            timeout=STATE_LOCK_TIMEOUT_SECONDS,
            mode=PRIVATE_FILE_MODE,
        ):
            state = try_load_state()
            if state is None:
                _quarantine_corrupt_state()
                state = default_state()
            mutate(state)
            _write(state)
            return state
    except FileLockTimeout as exc:
        raise StateBusyError("plant state lock timed out") from exc


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def request_started() -> None:
    def mutate(state: dict[str, Any]) -> None:
        state["active_requests"] = state.get("active_requests", 0) + 1
        state["last_request_started_at"] = _now_iso()

    update_state(mutate)


def request_finished(usage: Usage | None = None, live_estimate: int | None = None) -> None:
    """Settle one inference request: decrement in-flight count and, when
    usage was extracted, feed the plant. ``live_estimate`` is the remaining
    in-flight estimate across the other still-running requests."""

    def mutate(state: dict[str, Any]) -> None:
        state["active_requests"] = max(0, state.get("active_requests", 0) - 1)
        state["last_request_finished_at"] = _now_iso()
        if live_estimate is not None:
            state["live_tokens_estimate"] = max(0, live_estimate)
        if state["active_requests"] == 0:
            state["live_tokens_estimate"] = 0
        if usage is not None:
            game.absorb(state, usage)

    update_state(mutate)


def set_live_estimate(total: int) -> None:
    """Throttled mid-stream update of the in-flight token estimate."""
    update_state(lambda state: state.__setitem__("live_tokens_estimate", max(0, total)))


def clear_active_requests() -> None:
    """Called at proxy startup: a previous crash may have left a stale count."""

    def mutate(state: dict[str, Any]) -> None:
        state["active_requests"] = 0
        state["live_tokens_estimate"] = 0

    update_state(mutate)


def reset() -> None:
    """Back to a fresh seed (generation 1, all totals cleared)."""
    update_state(lambda state: (state.clear(), state.update(default_state())))
