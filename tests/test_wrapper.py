"""`token-sprout-claude` — the process wrapper editors launch Claude through."""

import json
import os
import socket
import subprocess
import sys

import pytest

from token_sprout import cli
from token_sprout.cli import main

PORT = 18966  # distinct from test_run's port


def _wrapper(env_overrides: dict, *args: str) -> subprocess.CompletedProcess:
    """Run the wrapper the way an editor does: a fresh process, real exec."""
    env = {**os.environ, "TOKEN_SPROUT_PORT": str(PORT), **env_overrides}
    for key in [k for k, v in env.items() if v is None]:
        del env[key]
    return subprocess.run(
        [sys.executable, "-c", "from token_sprout.cli import claude_wrapper_main as m; raise SystemExit(m())", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


REPORT = (
    "import json, os, sys;"
    "print(json.dumps({'base_url': os.environ.get('ANTHROPIC_BASE_URL'),"
    "'tool_search': os.environ.get('ENABLE_TOOL_SEARCH'), 'args': sys.argv[1:]}))"
)


@pytest.fixture(autouse=True)
def _stop_shared_proxy(sprout_home):
    yield
    main(["stop", "--port", str(PORT)])


def test_routes_claude_through_the_shared_proxy(sprout_home):
    result = _wrapper({"ANTHROPIC_BASE_URL": None}, sys.executable, "-c", REPORT, "SECRET_PROMPT")
    assert result.returncode == 0, result.stderr
    # stdout is exactly the wrapped program's output: editors parse it
    child = json.loads(result.stdout)
    assert child == {
        "base_url": f"http://127.0.0.1:{PORT}",
        "tool_search": "true",
        "args": ["SECRET_PROMPT"],
    }
    assert "SECRET_PROMPT" not in result.stderr
    assert cli._proxy_health(PORT)


def test_existing_base_url_is_respected(sprout_home):
    result = _wrapper({"ANTHROPIC_BASE_URL": "https://gateway.example"}, sys.executable, "-c", REPORT)
    assert json.loads(result.stdout)["base_url"] == "https://gateway.example"
    assert "ANTHROPIC_BASE_URL is already set" in result.stderr
    assert not cli._proxy_health(PORT)  # no proxy started for a bypass


@pytest.mark.parametrize("name", ["CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "TOKEN_SPROUT_DISABLE"])
def test_third_party_providers_and_opt_out_bypass(sprout_home, name):
    result = _wrapper({"ANTHROPIC_BASE_URL": None, name: "1"}, sys.executable, "-c", REPORT)
    assert json.loads(result.stdout)["base_url"] is None
    assert f"{name} is set" in result.stderr


def test_fails_open_when_a_stranger_holds_the_port(sprout_home):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    port = srv.getsockname()[1]
    try:
        result = _wrapper(
            {"ANTHROPIC_BASE_URL": None, "TOKEN_SPROUT_PORT": str(port)},
            sys.executable, "-c", REPORT,
        )
    finally:
        srv.close()
    assert result.returncode == 0
    assert json.loads(result.stdout)["base_url"] is None  # Claude still starts
    assert "without the plant" in result.stderr


def test_invalid_port_setting_bypasses(sprout_home):
    result = _wrapper({"ANTHROPIC_BASE_URL": None, "TOKEN_SPROUT_PORT": "80"}, sys.executable, "-c", REPORT)
    assert json.loads(result.stdout)["base_url"] is None
    assert "not a port number" in result.stderr


def test_exit_code_and_signals_belong_to_claude(sprout_home):
    # exec, not a child: the wrapper's exit status is the program's own
    result = _wrapper({"ANTHROPIC_BASE_URL": None}, sys.executable, "-c", "raise SystemExit(7)")
    assert result.returncode == 7


def test_missing_executable_is_reported(sprout_home, tmp_path):
    result = _wrapper({"ANTHROPIC_BASE_URL": "https://gateway.example"}, str(tmp_path / "no-claude"))
    assert result.returncode == 127
    assert "could not start no-claude" in result.stderr


def test_command_line_detection(monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/opt/bin/claude" if name == "claude" else None)
    assert cli._wrapper_command(["/x/claude", "--verbose"]) == ["/x/claude", "--verbose"]
    # no bundled binary: the editor passes only Claude's own arguments
    assert cli._wrapper_command(["--output-format", "stream-json"]) == [
        "/opt/bin/claude", "--output-format", "stream-json",
    ]
    assert cli._wrapper_command([]) == ["/opt/bin/claude"]
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    assert cli._wrapper_command(["--version"]) is None


def test_usage_without_claude(monkeypatch, capsys):
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    assert cli.claude_wrapper_main(["--version"]) == 2
    assert "usage: token-sprout-claude" in capsys.readouterr().err
