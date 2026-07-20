"""`install-statusline` writes settings.json; `run` guides when claude is absent."""

import json

import pytest

from token_sprout.cli import (
    _is_token_sprout_statusline,
    _report_missing_command,
    _statusline_command,
    main,
)


@pytest.fixture
def claude_home(tmp_path, monkeypatch):
    """Redirect ~/.claude to a tmp dir via Claude Code's own env var."""
    d = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(d))
    return d


def _settings(claude_home) -> dict:
    return json.loads((claude_home / "settings.json").read_text())


def test_installs_into_fresh_settings(sprout_home, claude_home, capsys):
    assert main(["install-statusline"]) == 0
    sl = _settings(claude_home)["statusLine"]
    assert sl["type"] == "command"
    assert "statusline" in sl["command"]
    assert sl["refreshInterval"] == 1
    assert "installed" in capsys.readouterr().out


def test_preserves_existing_settings(sprout_home, claude_home):
    claude_home.mkdir(parents=True)
    (claude_home / "settings.json").write_text(
        json.dumps({"model": "opus", "theme": "dark-daltonized"})
    )
    assert main(["install-statusline"]) == 0
    data = _settings(claude_home)
    assert data["model"] == "opus"
    assert data["theme"] == "dark-daltonized"
    assert "statusLine" in data


def test_updates_our_own_statusline_without_force(sprout_home, claude_home):
    claude_home.mkdir(parents=True)
    (claude_home / "settings.json").write_text(
        json.dumps(
            {"statusLine": {"type": "command", "command": "/old/path/token-sprout statusline"}}
        )
    )
    assert main(["install-statusline"]) == 0
    # rewritten to the freshly discovered command (self-healing a moved venv)
    assert "/old/path" not in _settings(claude_home)["statusLine"]["command"]


def test_refuses_foreign_statusline_without_force(sprout_home, claude_home, capsys):
    claude_home.mkdir(parents=True)
    original = {"statusLine": {"type": "command", "command": "starship prompt"}}
    (claude_home / "settings.json").write_text(json.dumps(original))
    assert main(["install-statusline"]) == 1
    assert "won't overwrite" in capsys.readouterr().err
    # file left untouched
    assert _settings(claude_home) == original


def test_force_overwrites_foreign_statusline(sprout_home, claude_home):
    claude_home.mkdir(parents=True)
    (claude_home / "settings.json").write_text(
        json.dumps({"statusLine": {"type": "command", "command": "starship prompt"}})
    )
    assert main(["install-statusline", "--force"]) == 0
    assert "token" in _settings(claude_home)["statusLine"]["command"].lower()


def test_token_sprout_text_alone_does_not_claim_foreign_statusline():
    assert not _is_token_sprout_statusline("echo token-sprout")
    assert not _is_token_sprout_statusline("starship token_sprout statusline")
    assert not _is_token_sprout_statusline("token-sprout statusline && starship prompt")
    assert _is_token_sprout_statusline("/old/path/token-sprout statusline --always")
    assert _is_token_sprout_statusline(
        "/venv/bin/python -m token_sprout.cli statusline --always"
    )


def test_uninstall_removes_only_our_statusline(sprout_home, claude_home):
    claude_home.mkdir(parents=True)
    (claude_home / "settings.json").write_text(
        json.dumps(
            {
                "model": "opus",
                "statusLine": {
                    "type": "command",
                    "command": "/old/path/token-sprout statusline",
                },
            }
        )
    )

    assert main(["uninstall-statusline"]) == 0
    assert _settings(claude_home) == {"model": "opus"}


def test_uninstall_refuses_foreign_statusline(sprout_home, claude_home, capsys):
    claude_home.mkdir(parents=True)
    original = {
        "theme": "dark",
        "statusLine": {"type": "command", "command": "echo token-sprout"},
    }
    (claude_home / "settings.json").write_text(json.dumps(original))

    assert main(["uninstall-statusline"]) == 1
    assert _settings(claude_home) == original
    assert "not owned" in capsys.readouterr().err


def test_refuses_to_touch_unparseable_settings(sprout_home, claude_home, capsys):
    claude_home.mkdir(parents=True)
    (claude_home / "settings.json").write_text("{ not valid json")
    assert main(["install-statusline"]) == 1
    assert "not valid JSON" in capsys.readouterr().err
    # left byte-for-byte intact
    assert (claude_home / "settings.json").read_text() == "{ not valid json"


def test_always_flag_is_baked_into_command(sprout_home, claude_home):
    assert main(["install-statusline", "--always"]) == 0
    assert _settings(claude_home)["statusLine"]["command"].endswith("statusline --always")


def test_statusline_command_uses_console_script_when_present(monkeypatch):
    monkeypatch.setattr("token_sprout.cli.shutil.which", lambda name: "/usr/local/bin/token-sprout")
    assert _statusline_command(False) == "/usr/local/bin/token-sprout statusline"


def test_statusline_command_falls_back_to_python_module(monkeypatch):
    monkeypatch.setattr("token_sprout.cli.shutil.which", lambda name: None)
    cmd = _statusline_command(False)
    assert "-m token_sprout.cli statusline" in cmd


def test_statusline_command_quotes_paths_with_spaces(monkeypatch):
    monkeypatch.setattr("token_sprout.cli.shutil.which", lambda name: "/Apps/My Tools/token-sprout")
    cmd = _statusline_command(False)
    assert "'/Apps/My Tools/token-sprout'" in cmd


def test_missing_claude_message_is_actionable(capsys):
    _report_missing_command("claude")
    err = capsys.readouterr().err
    assert "install.sh" in err
    assert "run -- /path/to/claude" in err


def test_missing_other_command_message(capsys):
    _report_missing_command("aider")
    assert "command not found: aider" in capsys.readouterr().err
