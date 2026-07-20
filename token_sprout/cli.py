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


def _cmd_proxy(args: argparse.Namespace) -> int:
    import uvicorn

    from .proxy import create_app

    _warn_if_foreign_upstream(args.upstream)

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

    uvicorn.run(
        create_app(upstream=args.upstream, listen_port=args.port),
        # Spec §5.1: localhost only, no override flag in v0.1.
        host="127.0.0.1",
        port=args.port,
        access_log=False,
        log_level="warning",
        # Bound the graceful drain: without this uvicorn waits forever for
        # in-flight streams, and `run`'s SIGTERM→5s→SIGKILL budget would
        # hard-kill us before the state writer flushes. Cancelled streams
        # settle with the last usage seen (spec §5.1 interrupt policy),
        # then the lifespan shutdown flushes the writer.
        timeout_graceful_shutdown=3,
    )
    return 0


def _proxy_health(port: int) -> bool:
    """True only when the listener on the port proves it holds our 0600
    secret via a port-bound HMAC challenge.

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
        return False
    nonce = secrets.token_hex(16)
    try:
        resp = httpx.get(
            f"http://127.0.0.1:{port}{HEALTH_PATH}",
            params={"nonce": nonce},
            timeout=1.0,
        )
        if resp.status_code != 200:
            return False
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return False
    expected = state.health_proof(secret, port, nonce)
    return (
        data.get("app") == "token-sprout"
        and data.get("port") == port
        and hmac.compare_digest(str(data.get("proof", "")), expected)
    )


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


def _cmd_run(args: argparse.Namespace) -> int:
    """One-terminal mode: ensure the proxy is up, then run an explicit command
    with ANTHROPIC_BASE_URL scoped to that child only, clean up on exit. A
    proxy we find already running is reused and left running; only a proxy we
    spawned ourselves is stopped afterwards."""
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

    proxy_proc = None
    if _proxy_health(port):
        print(f"🌿 Reusing the Token Sprout proxy already running on port {port}")
    else:
        if _port_occupied(port):
            print(
                f"error: port {port} is in use by something that isn't token-sprout "
                f"(try --port)",
                file=sys.stderr,
            )
            return 1
        log_path = state.proxy_log_path()
        with state.open_proxy_log() as log_file:
            proxy_proc = subprocess.Popen(
                [
                    sys.executable, "-m", "token_sprout.cli",
                    "proxy", "--port", str(port), "--upstream", args.upstream,
                ],
                stdout=log_file,
                stderr=subprocess.STDOUT,
                # own session: Ctrl+C aimed at the foreground command must
                # never reach the proxy
                start_new_session=True,
            )
        deadline = time.monotonic() + 15
        while not _proxy_health(port):
            if proxy_proc.poll() is not None or time.monotonic() > deadline:
                proxy_proc.terminate()
                print(f"error: proxy failed to start — see {log_path}", file=sys.stderr)
                return 1
            time.sleep(0.15)
        print(f"🌿 Token Sprout proxy started on port {port} (log: {log_path})")

    env = dict(os.environ)
    env["ANTHROPIC_BASE_URL"] = base_url
    # Claude Code disables dynamic tool search for non-first-party base URLs
    # unless this is explicitly enabled. Preserve an intentional user value.
    env.setdefault("ENABLE_TOOL_SEARCH", "true")
    # Arguments can contain a prompt or other private text. Never echo them
    # into terminal scrollback or logs.
    print(
        f"→ {Path(command[0]).name} (arguments hidden; "
        f"ANTHROPIC_BASE_URL={base_url})"
    )

    exit_code = 1
    try:
        child = None
        try:
            child = subprocess.Popen(command, env=env)
            # After spawning (so the child keeps default handlers): ignore
            # Ctrl+C in the wrapper — the foreground command owns it, and we
            # must stay alive to clean up the proxy afterwards.
            previous_sigint = signal.signal(signal.SIGINT, signal.SIG_IGN)
        except FileNotFoundError:
            # Race: resolved by the pre-check above, then vanished.
            _report_missing_command(command[0])
            return 127
        except KeyboardInterrupt:
            # Ctrl+C in the sliver between fork and SIG_IGN: the terminal
            # delivered SIGINT to the child too. Reap it and exit like an
            # interrupted command instead of dying with a traceback.
            if child is not None:
                with contextlib.suppress(Exception):
                    child.terminate()
                    child.wait(timeout=5)
            return 130
        try:
            exit_code = child.wait()
        finally:
            signal.signal(signal.SIGINT, previous_sigint)
    finally:
        if proxy_proc is not None:
            proxy_proc.terminate()
            try:
                proxy_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proxy_proc.kill()
    return exit_code


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
    p_proxy.set_defaults(func=_cmd_proxy)

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
