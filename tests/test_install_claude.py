"""Managed plain-`claude` shell integration."""

import os
import shlex
import subprocess

import pytest

from token_sprout.cli import (
    _CLAUDE_BLOCK_END,
    _CLAUDE_BLOCK_START,
    _shell_rc_path,
    main,
)


@pytest.fixture
def shell_setup(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin dir"
    bin_dir.mkdir()
    token_sprout = bin_dir / "token-sprout"
    claude = bin_dir / "claude"
    token_sprout.write_text("#!/bin/sh\nexit 0\n")
    claude.write_text("#!/bin/sh\nexit 0\n")
    token_sprout.chmod(0o755)
    claude.chmod(0o755)

    rc_path = tmp_path / ".zshrc"
    monkeypatch.setenv("TOKEN_SPROUT_SHELL_RC", str(rc_path))
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return rc_path, token_sprout, claude


def test_install_preserves_rc_and_official_claude(shell_setup, capsys):
    rc_path, _, claude = shell_setup
    rc_path.write_text("export KEEP_ME=yes\n")
    rc_path.chmod(0o640)
    original_claude = claude.read_bytes()

    assert main(["install-claude"]) == 0

    installed = rc_path.read_text()
    assert installed.startswith("export KEEP_ME=yes\n\n")
    assert installed.count(_CLAUDE_BLOCK_START) == 1
    assert installed.count(_CLAUDE_BLOCK_END) == 1
    assert "ANTHROPIC_BASE_URL" not in installed
    assert str(claude) in installed
    assert claude.read_bytes() == original_claude
    assert rc_path.stat().st_mode & 0o777 == 0o640
    assert "Open a new terminal" in capsys.readouterr().out


def test_install_is_idempotent(shell_setup, capsys):
    rc_path, _, _ = shell_setup
    assert main(["install-claude"]) == 0
    first = rc_path.read_text()
    capsys.readouterr()

    assert main(["install-claude"]) == 0
    second = rc_path.read_text()

    assert second == first
    assert second.count(_CLAUDE_BLOCK_START) == 1
    assert "updated" in capsys.readouterr().out


def test_new_rc_is_private(shell_setup):
    rc_path, _, _ = shell_setup

    assert main(["install-claude"]) == 0

    assert rc_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "foreign",
    [
        "alias claude='claude --dangerously-skip-permissions'\n",
        "claude() { command claude --model opus \"$@\"; }\n",
        "function claude { command claude \"$@\"; }\n",
    ],
)
def test_install_refuses_foreign_override(shell_setup, capsys, foreign):
    rc_path, _, _ = shell_setup
    rc_path.write_text(foreign)

    assert main(["install-claude"]) == 1
    assert rc_path.read_text() == foreign
    assert "already defines" in capsys.readouterr().err


def test_force_keeps_foreign_override_but_appends_ours(shell_setup):
    rc_path, _, _ = shell_setup
    rc_path.write_text("alias claude='command claude'\n")

    assert main(["install-claude", "--force"]) == 0

    installed = rc_path.read_text()
    assert "alias claude='command claude'" in installed
    assert installed.index("alias claude") < installed.index(_CLAUDE_BLOCK_START)


def test_sourced_alias_is_left_unchanged_without_force(shell_setup, tmp_path, capsys):
    rc_path, _, _ = shell_setup
    sourced = tmp_path / "aliases.zsh"
    sourced.write_text("alias claude='printf sourced-alias'\n")
    rc_path.write_text(f"source {shlex.quote(str(sourced))}\n")

    assert main(["install-claude"]) == 0
    result = subprocess.run(
        [
            "/bin/zsh",
            "-c",
            f"source {shlex.quote(str(rc_path))}; alias claude",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "sourced-alias" in result.stdout
    assert "existing claude alias/function left unchanged" in result.stderr
    capsys.readouterr()


def test_force_overrides_alias_loaded_from_sourced_file(shell_setup, tmp_path):
    rc_path, token_sprout, claude = shell_setup
    log_path = tmp_path / "forced-args.txt"
    token_sprout.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$@\" > {shlex.quote(str(log_path))}\n"
    )
    token_sprout.chmod(0o755)
    sourced = tmp_path / "aliases.zsh"
    sourced.write_text("alias claude='printf sourced-alias'\n")
    rc_path.write_text(f"source {shlex.quote(str(sourced))}\n")

    assert main(["install-claude", "--force"]) == 0
    subprocess.run(
        [
            "/bin/zsh",
            "-c",
            f"source {shlex.quote(str(rc_path))}; claude --resume session-123",
        ],
        check=True,
    )

    assert log_path.read_text().splitlines() == [
        "run",
        "--",
        str(claude),
        "--resume",
        "session-123",
    ]


def test_zsh_respects_zdotdir(tmp_path, monkeypatch):
    monkeypatch.delenv("TOKEN_SPROUT_SHELL_RC", raising=False)
    monkeypatch.setenv("ZDOTDIR", str(tmp_path / "zsh config"))
    assert _shell_rc_path("zsh") == tmp_path / "zsh config" / ".zshrc"


def test_macos_bash_uses_login_profile(tmp_path, monkeypatch):
    monkeypatch.delenv("TOKEN_SPROUT_SHELL_RC", raising=False)
    monkeypatch.setattr("token_sprout.cli.Path.home", lambda: tmp_path)
    monkeypatch.setattr("token_sprout.cli.sys.platform", "darwin")
    assert _shell_rc_path("bash") == tmp_path / ".bash_profile"


def test_managed_function_forwards_all_claude_arguments(shell_setup, tmp_path):
    rc_path, token_sprout, claude = shell_setup
    log_path = tmp_path / "args.txt"
    token_sprout.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$@\" > {shlex.quote(str(log_path))}\n"
    )
    token_sprout.chmod(0o755)
    assert main(["install-claude"]) == 0

    subprocess.run(
        [
            "/bin/zsh",
            "-c",
            f"source {shlex.quote(str(rc_path))}; claude --resume session-123",
        ],
        check=True,
    )

    assert log_path.read_text().splitlines() == [
        "run",
        "--",
        str(claude),
        "--resume",
        "session-123",
    ]


@pytest.mark.skipif(not os.path.exists("/bin/bash"), reason="bash unavailable")
def test_managed_function_is_valid_in_bash(shell_setup, tmp_path):
    rc_path, token_sprout, claude = shell_setup
    log_path = tmp_path / "bash-args.txt"
    token_sprout.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$@\" > {shlex.quote(str(log_path))}\n"
    )
    token_sprout.chmod(0o755)

    assert main(["install-claude", "--shell", "bash"]) == 0
    subprocess.run(
        [
            "/bin/bash",
            "-c",
            f"source {shlex.quote(str(rc_path))}; claude --resume bash-session",
        ],
        check=True,
    )

    assert log_path.read_text().splitlines() == [
        "run",
        "--",
        str(claude),
        "--resume",
        "bash-session",
    ]


def test_uninstall_removes_only_managed_block(shell_setup):
    rc_path, _, claude = shell_setup
    rc_path.write_text("export KEEP_ME=yes\n")
    original_claude = claude.read_bytes()
    assert main(["install-claude"]) == 0

    assert main(["uninstall-claude"]) == 0

    assert rc_path.read_text() == "export KEEP_ME=yes\n"
    assert claude.read_bytes() == original_claude


def test_incomplete_managed_block_is_never_rewritten(shell_setup, capsys):
    rc_path, _, _ = shell_setup
    original = f"export KEEP_ME=yes\n{_CLAUDE_BLOCK_START}\n"
    rc_path.write_text(original)

    assert main(["install-claude"]) == 1
    assert rc_path.read_text() == original
    assert "incomplete" in capsys.readouterr().err


def test_nested_managed_blocks_are_never_rewritten(shell_setup, capsys):
    rc_path, _, _ = shell_setup
    original = (
        f"{_CLAUDE_BLOCK_START}\n"
        f"{_CLAUDE_BLOCK_START}\n"
        f"{_CLAUDE_BLOCK_END}\n"
        f"{_CLAUDE_BLOCK_END}\n"
    )
    rc_path.write_text(original)

    assert main(["install-claude"]) == 1
    assert rc_path.read_text() == original
    assert "malformed" in capsys.readouterr().err


def test_install_writes_through_symlinked_rc(shell_setup, tmp_path):
    """Dotfiles managers (stow/chezmoi) symlink rc files; the atomic write
    must land in the real file, never replace the symlink itself."""
    rc_path, _, _ = shell_setup
    real = tmp_path / "dotfiles" / "zshrc"
    real.parent.mkdir()
    real.write_text("export KEEP_ME=yes\n")
    rc_path.symlink_to(real)

    assert main(["install-claude"]) == 0

    assert rc_path.is_symlink()  # the manager's link survives
    content = real.read_text()
    assert content.startswith("export KEEP_ME=yes")
    assert _CLAUDE_BLOCK_START in content
