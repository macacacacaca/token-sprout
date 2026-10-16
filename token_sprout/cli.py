"""Token Sprout's proxy, shell integration, and plant UI commands.

argparse only — no extra dependency for the CLI layer in v0.1.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

from . import __version__, game, state

DEFAULT_UPSTREAM = "https://api.anthropic.com"
STATUSLINE_BAR_WIDTH = 10
_CLAUDE_BLOCK_START = "# >>> token-sprout claude >>>"
_CLAUDE_BLOCK_END = "# <<< token-sprout claude <<<"


def _cmd_init(args: argparse.Namespace) -> int:
    path = state.init_home()
    print(f"🌱 Plant state initialized: {path}")
    print()
    print("Next steps:")
    print("  1. token-sprout install-claude")
    print("  2. Open a new terminal and launch normally: claude")
    print("  3. Optional: token-sprout install-statusline")
    return 0


def _safe_upstream_display(upstream: str) -> str:
    """Describe an upstream without printing credentials, paths, or queries."""
    try:
        parsed = urlsplit(upstream)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return "<custom upstream>"
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = f":{parsed.port}" if parsed.port is not None else ""
        return f"{parsed.scheme}://{host}{port}"
    except (TypeError, ValueError):
        return "<custom upstream>"


def _warn_if_foreign_upstream(upstream: str) -> None:
    if upstream != DEFAULT_UPSTREAM:
        print(
            f"⚠️  Forwarding to a non-Anthropic upstream: "
            f"{_safe_upstream_display(upstream)}\n"
            "    Your requests and credentials will go there, not to Anthropic.",
            file=sys.stderr,
        )


def _bind_listener(port: int):
    """Bind 127.0.0.1:port up front, so a lost race fails fast and visibly."""
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        sock.close()
        raise
    sock.set_inheritable(True)
    return sock


def _daemonize() -> None:
    """Detach from the launching session (double fork); returns in the daemon.

    The launcher exits immediately, so whoever started it (`run`, the editor
    wrapper) can wait for it without keeping a child process around.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    if os.fork() > 0:
        os._exit(0)
    os.setsid()  # no controlling terminal: closing it cannot SIGHUP us
    if os.fork() > 0:
        os._exit(0)
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)


def _cmd_proxy(args: argparse.Namespace) -> int:
    import uvicorn

    from .proxy import create_app

    _warn_if_foreign_upstream(args.upstream)

    listener = None
    if args.background:
        # Bind before detaching: losing a race to another session's proxy is
        # an immediate, visible failure instead of a silent dead daemon.
        try:
            listener = _bind_listener(args.port)
        except OSError as exc:
            print(f"error: cannot listen on 127.0.0.1:{args.port}: {exc.strerror}", file=sys.stderr)
            return 1
        _daemonize()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
    )
    # Log policy (spec §5.1): our own method/path/status lines only.
    # httpx logs full upstream URLs at INFO — silence it.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    state.init_home()
    # A previous crash may have left a stale in-flight count.
    try:
        state.clear_active_requests()
    except state.StateBusyError:
        # Startup bookkeeping is best-effort; a stuck state lock must never
        # prevent the proxy from serving requests.
        logging.getLogger("token_sprout.proxy").warning(
            "plant bookkeeping failed (request unaffected)"
        )

    print(
        f"Token Sprout proxy 🌿  http://127.0.0.1:{args.port}  ->  "
        f"{_safe_upstream_display(args.upstream)}"
    )
    print("Point Claude Code at it (per-session):")
    print(f"  ANTHROPIC_BASE_URL=http://127.0.0.1:{args.port} claude")
    print("⚠️  Do not put ANTHROPIC_BASE_URL in your shell rc file — Claude Code")
    print("    will break whenever this proxy is not running.")
    print()
    # uvicorn re-raises SIGTERM after its graceful shutdown, which ends the
    # process before Python would flush a redirected stdout (proxy.log).
    sys.stdout.flush()

    config = uvicorn.Config(
        create_app(upstream=args.upstream, listen_port=args.port),
        # Spec §5.1: localhost only, no override flag in v0.1.
        host="127.0.0.1",
        port=args.port,
        access_log=False,
        log_level="warning",
        # Bound the graceful drain: without this uvicorn waits forever for
        # in-flight streams, and `stop`'s SIGTERM would wait on them before
        # the state writer flushes. Cancelled streams settle with the last
        # usage seen (spec §5.1 interrupt policy), then the lifespan
        # shutdown flushes the writer.
        timeout_graceful_shutdown=3,
    )
    server = uvicorn.Server(config)
    with contextlib.suppress(KeyboardInterrupt):
        server.run(sockets=[listener] if listener is not None else None)
    return 0 if server.started else 3  # uvicorn's own startup-failure code


def _proxy_health(port: int) -> bool:
    """True only when the listener on the port is our verified proxy."""
    return _proxy_identity(port) is not None


def _proxy_identity(port: int) -> dict | None:
    """The health response of a listener that proves it holds our 0600
    secret via a port-bound HMAC challenge; None for anything else.

    A rogue local process (e.g. another OS user who grabbed 127.0.0.1:PORT
    first) could answer the health path with a canned ``{"app":
    "token-sprout"}`` and lure `run` into handing it Claude Code's
    credentials. Requiring a fresh HMAC proof over the configured port and a
    random nonce means a real proxy on another port cannot be used as a proof
    oracle. A same-user process can read the secret too, but it can already
    read your API key and OAuth tokens, so that boundary is out of scope."""
    import hmac
    import secrets

    import httpx

    from .proxy import HEALTH_PATH

    secret = state.read_secret()
    if not secret:
        # No secret yet ⇒ we've never started a proxy, so trust nothing on
        # the port. `run` then treats it as occupied-by-a-stranger and
        # refuses rather than forwarding credentials.
        return None
    nonce = secrets.token_hex(16)
    try:
        resp = httpx.get(
            f"http://127.0.0.1:{port}{HEALTH_PATH}",
            params={"nonce": nonce},
            timeout=1.0,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return None
    expected = state.health_proof(secret, port, nonce)
    if (
        isinstance(data, dict)
        and data.get("app") == "token-sprout"
        and data.get("port") == port
        and hmac.compare_digest(str(data.get("proof", "")), expected)
    ):
        return data
    return None


def _port_occupied(port: int) -> bool:
    """True when *something* answers on the port (health already failed)."""
    import httpx

    try:
        httpx.get(f"http://127.0.0.1:{port}/", timeout=1.0)
        return True
    except httpx.ConnectError:
        return False  # nothing listening
    except httpx.HTTPError:
        # Something accepted the connection but doesn't speak plain HTTP
        # (Redis, a TLS-only server, ...) — the port is still taken, and
        # spawning our proxy on it would just fail to bind.
        return True


PROXY_START_SECONDS = 15
STRANGER_GRACE_SECONDS = 2


class ProxyUnavailable(RuntimeError):
    """The shared proxy could not be verified or started."""


def _launch_proxy_daemon(port: int, upstream: str) -> None:
    """Start `proxy --background`; the launcher returns once it detached."""
    with state.open_proxy_log() as log_file:
        subprocess.run(
            [
                sys.executable, "-m", "token_sprout.cli",
                "proxy", "--port", str(port), "--upstream", upstream, "--background",
            ],
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            timeout=PROXY_START_SECONDS,
            check=False,
        )


def _ensure_shared_proxy(port: int, upstream: str) -> str:
    """Reuse or start the one proxy every session shares.

    Returns "reused" or "started"; raises ProxyUnavailable otherwise. The
    proxy outlives the session that started it — other terminals and editor
    windows may be using it — until `token-sprout stop` or logout.
    """
    state.init_home()
    if _proxy_health(port):
        return "reused"
    launched = False
    if not _port_occupied(port):
        _launch_proxy_daemon(port, upstream)
        launched = True
    # Either we just launched it, or something holds the port — possibly a
    # proxy another session is starting this very moment. Give it a moment.
    budget = PROXY_START_SECONDS if launched else STRANGER_GRACE_SECONDS
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        if _proxy_health(port):
            return "started" if launched else "reused"
        time.sleep(0.15)
    if launched:
        raise ProxyUnavailable(f"the proxy did not start (see {state.proxy_log_path()})")
    raise ProxyUnavailable(f"port {port} is in use by something that isn't token-sprout")


def _note(message: str) -> None:
    """Status lines go to stderr: stdout belongs to the wrapped command."""
    print(message, file=sys.stderr)


def _cmd_run(args: argparse.Namespace) -> int:
    """Ensure the shared proxy is up, then run an explicit command with
    ANTHROPIC_BASE_URL scoped to that child only. The proxy keeps running
    afterwards for other sessions; `token-sprout stop` ends it."""
    port = args.port
    base_url = f"http://127.0.0.1:{port}"
    state.init_home()
    # `run` spawns the proxy with stdout/stderr redirected to a log file, so
    # the proxy's own warning would be invisible — surface it here too.
    _warn_if_foreign_upstream(args.upstream)

    command = list(args.command or [])
    had_separator = bool(command) and command[0] == "--"
    if had_separator:
        command = command[1:]
    if not command:
        _report_missing_run_command()
        return 2
    if not had_separator:
        # argparse REMAINDER swallows everything after the first positional,
        # so `run claude --port 9` silently hands --port to claude while the
        # proxy stays on the default port. Say so instead of guessing.
        stray = sorted({"--port", "--upstream"} & set(command[1:]))
        if stray:
            print(
                f"note: {', '.join(stray)} after the command goes to the command, "
                "not to token-sprout; put it first "
                "(token-sprout run --port N -- ...)",
                file=sys.stderr,
            )

    # Fail fast, before spawning a proxy we'd immediately tear down again.
    # shutil.which resolves both PATH lookups and explicit paths.
    if shutil.which(command[0]) is None:
        _report_missing_command(command[0])
        return 127

    try:
        outcome = _ensure_shared_proxy(port, args.upstream)
    except ProxyUnavailable as exc:
        # Refuse rather than hand credentials to an unverified listener.
        print(f"error: {exc} (try --port)", file=sys.stderr)
        return 1
    if outcome == "started":
        _note(
            f"🌿 Started the Token Sprout proxy on port {port}; it keeps running "
            "for your other sessions (token-sprout stop ends it)"
        )
    else:
        _note(f"🌿 Using the Token Sprout proxy on port {port}")

    env = dict(os.environ)
    env["ANTHROPIC_BASE_URL"] = base_url
    # Claude Code disables dynamic tool search for non-first-party base URLs
    # unless this is explicitly enabled. Preserve an intentional user value.
    env.setdefault("ENABLE_TOOL_SEARCH", "true")
    # Arguments can contain a prompt or other private text. Never echo them
    # into terminal scrollback or logs.
    _note(f"→ {Path(command[0]).name} (arguments hidden; ANTHROPIC_BASE_URL={base_url})")

    child = None
    try:
        child = subprocess.Popen(command, env=env)
    except FileNotFoundError:
        # Race: resolved by the pre-check above, then vanished.
        _report_missing_command(command[0])
        return 127
    except KeyboardInterrupt:
        # Ctrl+C in the sliver between fork and the handlers below: the
        # terminal delivered SIGINT to the child too. Reap it and exit like
        # an interrupted command instead of dying with a traceback.
        if child is not None:
            with contextlib.suppress(Exception):
                child.terminate()
                child.wait(timeout=5)
        return 130

    def forward(signum: int, _frame: object) -> None:
        with contextlib.suppress(ProcessLookupError):
            child.send_signal(signum)

    # After spawning (so the child keeps default handlers): Ctrl+C belongs to
    # the foreground command, and a closing terminal or an editor stopping us
    # must reach the command instead of orphaning it.
    previous = {signal.SIGINT: signal.signal(signal.SIGINT, signal.SIG_IGN)}
    for signum in (signal.SIGTERM, signal.SIGHUP):
        previous[signum] = signal.signal(signum, forward)
    try:
        return child.wait()
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _cmd_stop(args: argparse.Namespace) -> int:
    """Stop the shared proxy — only after it proves it is ours."""
    port = args.port
    identity = _proxy_identity(port)
    if identity is None:
        if _port_occupied(port):
            print(
                f"error: port {port} is in use by something that isn't token-sprout; "
                "leaving it alone",
                file=sys.stderr,
            )
            return 1
        print(f"No Token Sprout proxy is running on port {port}.")
        return 0
    pid = identity.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 1:
        print(
            "error: this proxy predates `token-sprout stop`; end the session "
            "that started it instead",
            file=sys.stderr,
        )
        return 1
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if _proxy_identity(port) is None:
            print(
                f"Stopped the Token Sprout proxy on port {port}. Claude sessions "
                "that were using it need a restart."
            )
            return 0
        time.sleep(0.15)
    print(f"error: the proxy (pid {pid}) did not stop in time", file=sys.stderr)
    return 1


# --- Editor process wrapper ----------------------------------------------------

WRAPPER_PORT_ENV = "TOKEN_SPROUT_PORT"
_THIRD_PARTY_PROVIDERS = (
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() not in ("", "0", "false", "no", "off")


def _wrapper_port(env: dict) -> int | None:
    raw = env.get(WRAPPER_PORT_ENV, "").strip()
    if not raw:
        return 8000
    return int(raw) if raw.isdigit() and 1024 <= int(raw) <= 65535 else None


def _wrapper_bypass_reason(env: dict) -> str | None:
    """Why this launch must not be routed through the proxy, if at all."""
    if env.get("ANTHROPIC_BASE_URL"):
        return "ANTHROPIC_BASE_URL is already set"
    for name in _THIRD_PARTY_PROVIDERS:
        if _truthy(env.get(name)):
            return f"{name} is set"
    if _truthy(env.get("TOKEN_SPROUT_DISABLE")):
        return "TOKEN_SPROUT_DISABLE is set"
    if _wrapper_port(env) is None:
        return f"{WRAPPER_PORT_ENV} is not a port number"
    return None


def _wrapper_command(args: list[str]) -> list[str] | None:
    """The Claude command line an editor handed us.

    VS Code's Claude Code extension passes the bundled executable first and
    its own arguments after it; when it has no bundled binary it passes only
    the arguments, so fall back to `claude` on PATH.
    """
    if args and os.path.isabs(args[0]):
        return args  # exec reports a missing or non-executable file itself
    claude = shutil.which("claude")
    if claude is None:
        return None
    return [claude, *args]


def claude_wrapper_main(argv: list[str] | None = None) -> int:
    """`token-sprout-claude <claude> [args...]` — the process wrapper for
    editors (VS Code: the claudeCode.claudeProcessWrapper setting).

    Fail-open by construction: when the shared proxy cannot be verified or
    started, Claude Code still starts, directly and without the plant. It
    writes only to stderr (stdout carries the editor's protocol), never
    prints the arguments, and replaces itself with Claude (exec), so stdio
    and signals reach Claude untouched.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    command = _wrapper_command(args)
    if command is None:
        print(
            "usage: token-sprout-claude <claude-executable> [arguments...]\n"
            "Set this file as Claude Code's process wrapper in VS Code "
            "(Token Sprout: Connect Claude Code).",
            file=sys.stderr,
        )
        return 2

    env = dict(os.environ)
    reason = _wrapper_bypass_reason(env)
    port = _wrapper_port(env)
    if reason is None and port is not None:
        try:
            _ensure_shared_proxy(port, DEFAULT_UPSTREAM)
        except ProxyUnavailable as exc:
            reason = str(exc)
        except Exception as exc:  # noqa: BLE001 - never block Claude
            reason = f"unexpected {type(exc).__name__}"
    if reason is None:
        env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{port}"
        env.setdefault("ENABLE_TOOL_SEARCH", "true")
    else:
        print(f"token-sprout: starting Claude Code without the plant: {reason}", file=sys.stderr)

    try:
        os.execvpe(command[0], command, env)
    except OSError as exc:
        print(
            f"token-sprout: could not start {Path(command[0]).name}: {exc.strerror or exc}",
            file=sys.stderr,
        )
    return 127


def _report_missing_command(name: str) -> None:
    if name == "claude":
        print(
            "error: 'claude' is not on your PATH.\n\n"
            "Token Sprout wraps Claude Code for you — install the CLI first:\n"
            "  curl -fsSL https://claude.ai/install.sh | bash\n\n"
            "Already have it under a different name or path? Point run at it:\n"
            "  token-sprout run -- /path/to/claude\n\n"
            "Or wrap any other Anthropic API client:\n"
            "  token-sprout run -- aider",
            file=sys.stderr,
        )
    else:
        print(f"error: command not found: {name}", file=sys.stderr)


def _report_missing_run_command() -> None:
    print(
        "error: token-sprout run requires an explicit command.\n\n"
        "Use:\n"
        "  token-sprout run -- claude\n\n"
        "For the normal Claude Code muscle memory, install the managed shell integration:\n"
        "  token-sprout install-claude\n"
        "Then open a new terminal and type: claude",
        file=sys.stderr,
    )


def _shell_rc_path(shell: str | None = None) -> Path:
    """Resolve the current shell's rc file; override exists for isolated tests."""
    override = os.environ.get("TOKEN_SPROUT_SHELL_RC")
    if override:
        return Path(override)
    shell = shell or Path(os.environ.get("SHELL", "")).name
    if shell == "zsh":
        zdotdir = os.environ.get("ZDOTDIR")
        return (Path(zdotdir).expanduser() if zdotdir else Path.home()) / ".zshrc"
    if shell == "bash":
        # macOS login shells read .bash_profile, not .bashrc.
        return Path.home() / (".bash_profile" if sys.platform == "darwin" else ".bashrc")
    raise ValueError("only zsh and bash are supported; pass --shell zsh or --shell bash")


def _without_claude_block(text: str) -> tuple[str, int]:
    start_marker = re.compile(rf"(?m)^{re.escape(_CLAUDE_BLOCK_START)}$")
    end_marker = re.compile(rf"(?m)^{re.escape(_CLAUDE_BLOCK_END)}$")
    starts = len(start_marker.findall(text))
    ends = len(end_marker.findall(text))
    if starts != ends:
        raise ValueError("shell rc contains an incomplete Token Sprout managed block")
    pattern = re.compile(
        rf"(?ms)^{re.escape(_CLAUDE_BLOCK_START)}\n.*?"
        rf"^{re.escape(_CLAUDE_BLOCK_END)}\n?"
    )
    clean, removed = pattern.subn("", text)
    if removed != starts:
        raise ValueError("shell rc contains malformed Token Sprout managed blocks")
    return clean, removed


def _has_foreign_claude_override(text: str) -> bool:
    alias = re.compile(r"(?m)^\s*alias\s+claude\s*=")
    function = re.compile(
        r"(?m)^\s*(?:function\s+)?claude\s*(?:\(\s*\))?\s*\{"
    )
    return bool(alias.search(text) or function.search(text))


def _atomic_write_text(path: Path, text: str) -> None:
    """Atomic replace for user-owned config files (shell rc, settings.json).

    Preserves the existing file's mode (unlike state files, these are not
    ours to force to 0600 — only new files default to it). Resolves symlinks
    first: os.replace would otherwise swap out the symlink itself, silently
    detaching a dotfiles-managed rc/settings file from its repo.
    """
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = (path.stat().st_mode & 0o777) if path.exists() else state.PRIVATE_FILE_MODE
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


def _claude_shell_block(token_sprout: str, claude: str, *, force: bool) -> str:
    definition = (
        "claude() {\n"
        f"  {shlex.quote(token_sprout)} run -- {shlex.quote(claude)} \"$@\"\n"
        "}"
    )
    prefix = (
        f"{_CLAUDE_BLOCK_START}\n"
        "# Managed by `token-sprout install-claude`; safe to remove with\n"
        "# `token-sprout uninstall-claude`. No API URL or credential is stored.\n"
    )
    if force:
        body = (
            "unalias claude 2>/dev/null || true\n"
            "unset -f claude 2>/dev/null || true\n"
            "unfunction claude 2>/dev/null || true\n"
            f"eval {shlex.quote(definition)}\n"
        )
    else:
        body = (
            "if alias claude >/dev/null 2>&1 || "
            "typeset -f claude >/dev/null 2>&1; then\n"
            "  printf '%s\\n' 'token-sprout: existing claude alias/function "
            "left unchanged; run install-claude --force to override.' >&2\n"
            "else\n"
            f"  eval {shlex.quote(definition)}\n"
            "fi\n"
        )
    return prefix + body + f"{_CLAUDE_BLOCK_END}\n"


def _cmd_install_claude(args: argparse.Namespace) -> int:
    """Make plain `claude` enter through `token-sprout run` safely."""
    try:
        rc_path = _shell_rc_path(args.shell)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    token_sprout = shutil.which("token-sprout")
    real_claude = shutil.which("claude")
    if not token_sprout:
        print("error: token-sprout is not on PATH.", file=sys.stderr)
        return 1
    if not real_claude:
        _report_missing_command("claude")
        return 1
    token_sprout = os.path.abspath(token_sprout)
    real_claude = os.path.abspath(real_claude)

    try:
        existing = rc_path.read_text() if rc_path.exists() else ""
        clean, previous_blocks = _without_claude_block(existing)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"error: cannot safely update {rc_path}: {exc}", file=sys.stderr)
        return 1
    if _has_foreign_claude_override(clean) and not args.force:
        print(
            f"error: {rc_path} already defines a claude alias or function.\n"
            "Token Sprout will not override it; re-run with --force only if intentional.",
            file=sys.stderr,
        )
        return 1

    prefix = clean.rstrip("\n")
    block = _claude_shell_block(token_sprout, real_claude, force=args.force)
    updated = (prefix + "\n\n" if prefix else "") + block
    try:
        _atomic_write_text(rc_path, updated)
    except OSError as exc:
        print(f"error: could not write {rc_path}: {exc}", file=sys.stderr)
        return 1

    verb = "updated" if previous_blocks else "installed"
    print(f"🌿 Plain `claude` integration {verb} in {rc_path}")
    print(f"   Claude executable: {real_claude}")
    print("Open a new terminal, then launch normally:")
    print("  claude")
    print()
    print("Inside Claude Code, /plant shows the state; tracking already started at launch.")
    return 0


def _cmd_uninstall_claude(args: argparse.Namespace) -> int:
    """Remove only Token Sprout's managed shell block, never Claude itself."""
    try:
        rc_path = _shell_rc_path(args.shell)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not rc_path.exists():
        print(f"No Token Sprout claude integration found in {rc_path}")
        return 0
    try:
        existing = rc_path.read_text()
        clean, removed = _without_claude_block(existing)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"error: cannot safely update {rc_path}: {exc}", file=sys.stderr)
        return 1
    if not removed:
        print(f"No Token Sprout claude integration found in {rc_path}")
        return 0
    updated = clean.rstrip("\n") + ("\n" if clean.strip() else "")
    try:
        _atomic_write_text(rc_path, updated)
    except OSError as exc:
        print(f"error: could not write {rc_path}: {exc}", file=sys.stderr)
        return 1
    print(f"Removed Token Sprout's claude integration from {rc_path}")
    print("The official Claude executable was not changed.")
    return 0


def _claude_settings_path() -> Path:
    """Where Claude Code reads user settings from. Honors CLAUDE_CONFIG_DIR
    (Claude Code's own relocation env var) so a moved config still works."""
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(config_dir) if config_dir else Path.home() / ".claude"
    return base / "settings.json"


def _statusline_command(always: bool) -> str:
    """Build an absolute, PATH-independent command string for settings.json.

    Prefers the installed `token-sprout` console script (short, readable);
    falls back to `<this python> -m token_sprout.cli` when it isn't on PATH
    (e.g. running from source). Both resolve to an absolute path so Claude
    Code can run it regardless of its own PATH.
    """
    console = shutil.which("token-sprout")
    if console:
        base = shlex.quote(console)
    else:
        base = f"{shlex.quote(sys.executable)} -m token_sprout.cli"
    cmd = f"{base} statusline"
    if always:
        cmd += " --always"
    return cmd


def _is_token_sprout_statusline(command: object) -> bool:
    """Recognize only commands that actually invoke our statusline action."""
    if not isinstance(command, str):
        return False
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    if len(parts) in {2, 3}:
        executable = Path(parts[0]).name
        if (
            executable == "token-sprout"
            and parts[1] == "statusline"
            and (len(parts) == 2 or parts[2] == "--always")
        ):
            return True
    if len(parts) in {4, 5}:
        executable = Path(parts[0]).name.lower()
        if (
            executable.startswith("python")
            and parts[1:4] == ["-m", "token_sprout.cli", "statusline"]
            and (len(parts) == 4 or parts[4] == "--always")
        ):
            return True
    return False


def _read_settings(path: Path) -> dict | None:
    if not path.exists():
        return {}
    try:
        settings = json.loads(path.read_text())
    except (OSError, UnicodeError, ValueError):
        print(
            f"error: {path} is not valid JSON or is not readable — fix it first; "
            "Token Sprout left it untouched.",
            file=sys.stderr,
        )
        return None
    if not isinstance(settings, dict):
        print(f"error: {path} is not a JSON object.", file=sys.stderr)
        return None
    return settings


def _atomic_write_settings(path: Path, settings: dict) -> None:
    _atomic_write_text(path, json.dumps(settings, indent=2, ensure_ascii=False) + "\n")


def _cmd_install_statusline(args: argparse.Namespace) -> int:
    """Write the statusLine entry into ~/.claude/settings.json, merging with
    any existing settings and refusing to clobber a foreign statusLine."""
    path = _claude_settings_path()
    command = _statusline_command(args.always)

    # Self-test the command exactly the way Claude Code will run it (through
    # a shell, session JSON on stdin) before committing it to settings.
    try:
        check = subprocess.run(
            command, shell=True, input=b"{}", capture_output=True, timeout=10
        )
    except Exception:
        print(
            "warning: could not self-test the statusline command; installing anyway.",
            file=sys.stderr,
        )
    else:
        if check.returncode != 0:
            detail = check.stderr.decode(errors="replace").strip()
            print(
                f"error: the statusline command failed a self-test:\n  {command}\n"
                f"  {detail}",
                file=sys.stderr,
            )
            return 1

    settings = _read_settings(path)
    if settings is None:
        return 1

    existing = settings.get("statusLine")
    if isinstance(existing, dict):
        existing_cmd = str(existing.get("command", ""))
        is_ours = _is_token_sprout_statusline(existing_cmd)
        if not is_ours and not args.force:
            print(
                "You already have a statusLine configured:\n"
                f"  {existing_cmd}\n"
                "Token Sprout won't overwrite it. Re-run with --force to replace it,\n"
                "or add this to your statusLine manually:\n"
                f"  {command}",
                file=sys.stderr,
            )
            return 1
    elif existing is not None and not args.force:
        print(
            "You already have a statusLine configured in an unfamiliar format.\n"
            "Token Sprout left it untouched; re-run with --force only if intentional.",
            file=sys.stderr,
        )
        return 1

    settings["statusLine"] = {
        "type": "command",
        "command": command,
        "refreshInterval": 1,
    }

    try:
        _atomic_write_settings(path, settings)
    except OSError as exc:
        print(f"error: could not write {path}: {exc}", file=sys.stderr)
        return 1

    print(f"🌿 Statusline installed in {path}")
    print(f"   command: {command}")
    print()
    print("Interact once or restart Claude Code to load it. Workspace trust must be")
    print("enabled. The plant appears while Claude uses tokens and hides when idle.")
    return 0


def _cmd_uninstall_statusline(args: argparse.Namespace) -> int:
    """Remove only a statusLine command that is exactly ours."""
    path = _claude_settings_path()
    settings = _read_settings(path)
    if settings is None:
        return 1
    existing = settings.get("statusLine")
    if existing is None:
        print(f"No Token Sprout statusline found in {path}")
        return 0
    existing_cmd = existing.get("command") if isinstance(existing, dict) else None
    if not _is_token_sprout_statusline(existing_cmd):
        print(
            f"error: {path} contains a statusLine not owned by Token Sprout; "
            "it was left untouched.",
            file=sys.stderr,
        )
        return 1
    del settings["statusLine"]
    try:
        _atomic_write_settings(path, settings)
    except OSError as exc:
        print(f"error: could not write {path}: {exc}", file=sys.stderr)
        return 1
    print(f"Removed Token Sprout's statusline from {path}")
    print("All other Claude Code settings were preserved.")
    return 0


def _cmd_statusline(args: argparse.Namespace) -> int:
    """One line for Claude Code's statusLine hook.

    Default behavior: visible only while tokens are being consumed
    (active_requests > 0); prints nothing when idle so the line disappears.
    ``--always`` keeps a quiet compact version on screen instead.
    Claude Code pipes session JSON on stdin — drained and ignored; the
    plant state file is our only input.
    """
    if not sys.stdin.isatty():
        try:
            # Drain as bytes: the session JSON may contain non-ASCII while
            # the default locale encoding is e.g. C/POSIX — a text-mode
            # read() would crash with UnicodeDecodeError on every refresh.
            getattr(sys.stdin, "buffer", sys.stdin).read()
        except (OSError, ValueError):
            pass

    from . import ascii_art

    s = state.load_state()
    thinking = s["active_requests"] > 0
    if not thinking and not args.always:
        return 0  # empty output -> statusline hidden

    live = s.get("live_tokens_estimate", 0) if thinking else 0
    view = game.plant_view(s, pending_exp=live)
    if view["is_bloom"]:
        plant = f"{ascii_art.stage_glyph('bloom')} in bloom"
    else:
        # "all units laid out" per the user's choice: repeat the emoji.
        pile = ascii_art.pile(view["stage"], view["count"])
        plant = f"{pile} {view['count']}/{view['units_per_stage']}".lstrip()

    if thinking:
        percent = view["unit_progress_percent"]
        filled = percent * STATUSLINE_BAR_WIDTH // 100
        bar = "█" * filled + "░" * (STATUSLINE_BAR_WIDTH - filled)
        print(f"{plant} · 💧 [{bar}] {percent}% · +{live:,} tokens")
    else:
        print(plant)
    return 0


def _cmd_watch(args: argparse.Namespace) -> int:
    if args.json:
        # Editor feed: strictly read-only, so no init_home() here.
        from . import feed

        return feed.run()

    from . import ui

    state.init_home()
    return ui.watch()


def _cmd_status(args: argparse.Namespace) -> int:
    from . import ascii_art

    s = state.load_state()
    view = game.plant_view(s)
    print(f"Token Sprout 🌿  generation {view['generation']}")
    if view["is_bloom"]:
        print(f"Plant : {ascii_art.stage_glyph('bloom')} in bloom — the next tokens start a new generation")
    else:
        pile = ascii_art.pile(view["stage"], view["count"])
        pile_display = pile or "—"
        print(
            f"Plant : {pile_display}  ({view['stage']} {view['count']}/{view['units_per_stage']})"
        )
        print(f"Growth: {ascii_art.growth_line(view)}")
    print(
        f"Tokens: {s['total_tokens']:,} total"
        f"  (input {s['total_input_tokens']:,}"
        f" · output {s['total_output_tokens']:,}"
        f" · cache_creation {s['total_cache_creation_tokens']:,}"
        f" · cache_read {s['total_cache_read_tokens']:,})"
    )
    if s["last_request_finished_at"]:
        print(
            f"Last  : {s['last_request_tokens']:,} tokens"
            f" at {s['last_request_finished_at']}"
        )
    else:
        print("Last  : no requests seen yet")
    print(f"Active: {s['active_requests']} request(s) in flight")
    return 0


def _cmd_reset(args: argparse.Namespace) -> int:
    if not args.force:
        try:
            answer = input("Reset your plant to a fresh seed? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print("Aborted.")
            return 1
    try:
        state.reset()
    except state.StateBusyError:
        print("error: plant state is busy; try reset again.", file=sys.stderr)
        return 1
    print("Plant reset. 🌱")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="token-sprout",
        description="Grow a tiny terminal plant with the tokens you spend on AI coding agents.",
    )
    parser.add_argument("--version", action="version", version=f"token-sprout {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Initialize ~/.token-sprout/ and plant_state.json")
    p_init.set_defaults(func=_cmd_init)

    p_proxy = sub.add_parser("proxy", help="Start the local pass-through proxy (127.0.0.1 only)")
    p_proxy.add_argument("--port", type=int, default=8000)
    # Hidden: routing to an arbitrary upstream is a credential footgun and
    # contradicts the "exactly one place: Anthropic" promise. Kept for tests
    # and advanced use; warned about loudly at runtime.
    p_proxy.add_argument("--upstream", default=DEFAULT_UPSTREAM, help=argparse.SUPPRESS)
    # Internal: how `run` and the editor wrapper start the shared proxy.
    p_proxy.add_argument("--background", action="store_true", help=argparse.SUPPRESS)
    p_proxy.set_defaults(func=_cmd_proxy)

    p_stop = sub.add_parser("stop", help="Stop the shared background proxy")
    p_stop.add_argument("--port", type=int, default=8000)
    p_stop.set_defaults(func=_cmd_stop)

    p_run = sub.add_parser(
        "run",
        help="One-terminal mode: ensure the proxy is running, then run a "
        "command wired to it",
    )
    p_run.add_argument("--port", type=int, default=8000)
    p_run.add_argument("--upstream", default=DEFAULT_UPSTREAM, help=argparse.SUPPRESS)
    p_run.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command to run after --, for example: -- claude",
    )
    p_run.set_defaults(func=_cmd_run)

    p_install_claude = sub.add_parser(
        "install-claude",
        help="Make plain `claude` launch through Token Sprout",
    )
    p_install_claude.add_argument("--shell", choices=["zsh", "bash"])
    p_install_claude.add_argument(
        "--force",
        action="store_true",
        help="Override an existing claude alias/function in the shell rc",
    )
    p_install_claude.set_defaults(func=_cmd_install_claude)

    p_uninstall_claude = sub.add_parser(
        "uninstall-claude",
        help="Remove Token Sprout's plain-claude shell integration",
    )
    p_uninstall_claude.add_argument("--shell", choices=["zsh", "bash"])
    p_uninstall_claude.set_defaults(func=_cmd_uninstall_claude)

    p_watch = sub.add_parser("watch", help="Live plant view with watering animation")
    p_watch.add_argument(
        "--json",
        action="store_true",
        help="Stream one read-only JSON snapshot per change (for editor integrations)",
    )
    p_watch.set_defaults(func=_cmd_watch)

    p_statusline = sub.add_parser(
        "statusline",
        help="One-line plant for Claude Code's statusLine (shown while thinking)",
    )
    p_statusline.add_argument(
        "--always",
        action="store_true",
        help="Also show a compact line while idle (default: hidden when idle)",
    )
    p_statusline.set_defaults(func=_cmd_statusline)

    p_install = sub.add_parser(
        "install-statusline",
        help="Add the plant to Claude Code's statusLine in ~/.claude/settings.json",
    )
    p_install.add_argument(
        "--always",
        action="store_true",
        help="Also show a compact line while idle (default: hidden when idle)",
    )
    p_install.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing non-Token-Sprout statusLine",
    )
    p_install.set_defaults(func=_cmd_install_statusline)

    p_uninstall_statusline = sub.add_parser(
        "uninstall-statusline",
        help="Remove only Token Sprout's Claude Code statusLine setting",
    )
    p_uninstall_statusline.set_defaults(func=_cmd_uninstall_statusline)

    p_status = sub.add_parser("status", help="Show tokens / EXP / level / stage / generation")
    p_status.set_defaults(func=_cmd_status)

    p_reset = sub.add_parser("reset", help="Reset the plant to a fresh seed")
    p_reset.add_argument("--force", action="store_true", help="Skip the confirmation prompt")
    p_reset.set_defaults(func=_cmd_reset)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
