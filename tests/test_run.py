"""`token-sprout run` — one-terminal mode."""

import json
import os
import sys

import httpx
import pytest
from starlette.testclient import TestClient

from token_sprout import state
from token_sprout.cli import main
from token_sprout.proxy import HEALTH_PATH, create_app

PORT = 18965  # uncommon port for the spawn test


@pytest.fixture(autouse=True)
def _stop_shared_proxy(sprout_home):
    """`run` leaves the shared proxy running by design; never leak it."""
    yield
    main(["stop", "--port", str(PORT)])


def test_health_endpoint_not_forwarded(sprout_home):
    state.init_home()
    upstream_called = {"hit": False}

    async def handler(request: httpx.Request) -> httpx.Response:
        upstream_called["hit"] = True
        return httpx.Response(200, json={})

    app = create_app(upstream="https://upstream.test", transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        resp = client.get(HEALTH_PATH)

    assert resp.status_code == 200
    assert resp.json()["app"] == "token-sprout"
    assert resp.json()["pid"] == os.getpid()  # lets `stop` find the proxy
    assert upstream_called["hit"] is False  # answered locally, never proxied


def test_run_shares_a_background_proxy_and_wires_env(
    sprout_home, tmp_path, monkeypatch, capsys
):
    out_file = tmp_path / "env.txt"
    monkeypatch.delenv("ENABLE_TOOL_SEARCH", raising=False)
    private_prompt = "SECRET_PROMPT_MUST_NOT_APPEAR"
    code = main(
        [
            "run",
            "--port", str(PORT),
            "--",
            sys.executable,
            "-c",
            "import json, os, pathlib, sys;"
            f"pathlib.Path({str(out_file)!r}).write_text(json.dumps({{"
            "'base_url': os.environ.get('ANTHROPIC_BASE_URL', 'MISSING'),"
            "'tool_search': os.environ.get('ENABLE_TOOL_SEARCH', 'MISSING'),"
            "'prompt': sys.argv[1]}))",
            private_prompt,
        ]
    )
    assert code == 0
    child = json.loads(out_file.read_text())
    assert child == {
        "base_url": f"http://127.0.0.1:{PORT}",
        "tool_search": "true",
        "prompt": private_prompt,
    }
    captured = capsys.readouterr()
    # stdout belongs to the wrapped command: every status line is on stderr
    assert captured.out == ""
    assert "arguments hidden" in captured.err
    assert private_prompt not in captured.out + captured.err
    # the proxy outlives the session for other terminals and editor windows
    health = httpx.get(f"http://127.0.0.1:{PORT}{HEALTH_PATH}", timeout=1.0).json()
    assert health["app"] == "token-sprout"

    # a second session reuses it instead of starting another
    assert main(["run", "--port", str(PORT), "--", sys.executable, "-c", "pass"]) == 0
    assert "Using the Token Sprout proxy" in capsys.readouterr().err
    assert httpx.get(f"http://127.0.0.1:{PORT}{HEALTH_PATH}", timeout=1.0).json()["pid"] == health["pid"]

    assert main(["stop", "--port", str(PORT)]) == 0
    with pytest.raises(httpx.HTTPError):
        httpx.get(f"http://127.0.0.1:{PORT}{HEALTH_PATH}", timeout=1.0)


def test_run_requires_explicit_command(sprout_home, capsys):
    code = main(["run", "--port", str(PORT)])
    assert code == 2
    err = capsys.readouterr().err
    assert "requires an explicit command" in err
    assert "token-sprout run -- claude" in err


def test_run_propagates_child_exit_code(sprout_home):
    code = main(["run", "--port", str(PORT), "--", sys.executable, "-c", "raise SystemExit(7)"])
    assert code == 7


def test_run_unknown_command(sprout_home, capsys):
    # the command is checked before any proxy is started
    code = main(["run", "--port", str(PORT), "--", "definitely-not-a-real-command-xyz"])
    assert code == 127
    assert "command not found" in capsys.readouterr().err


def test_run_preserves_explicit_tool_search_setting(sprout_home, tmp_path, monkeypatch):
    out_file = tmp_path / "tool-search.txt"
    monkeypatch.setenv("ENABLE_TOOL_SEARCH", "false")
    code = main(
        [
            "run",
            "--port",
            str(PORT),
            "--",
            sys.executable,
            "-c",
            "import os, pathlib;"
            f"pathlib.Path({str(out_file)!r}).write_text(os.environ['ENABLE_TOOL_SEARCH'])",
        ]
    )
    assert code == 0
    assert out_file.read_text() == "false"


def test_run_notes_flags_swallowed_by_remainder(sprout_home, capsys):
    # Without the -- separator, argparse REMAINDER hands --port to the child;
    # the user must at least be told the proxy ignored it.
    code = main(["run", "definitely-not-a-real-command-xyz", "--port", "9"])
    assert code == 127  # still fails on the missing command afterwards
    err = capsys.readouterr().err
    assert "note: --port" in err
    assert "put it first" in err


def test_port_occupied_by_non_http_listener(sprout_home):
    """A non-HTTP service on the port must read as occupied, not free —
    otherwise `run` spawns a proxy that is doomed to fail its bind."""
    import socket
    import threading

    from token_sprout.cli import _port_occupied

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def serve():
        try:
            conn, _ = srv.accept()
            conn.recv(1024)
            conn.sendall(b"NOT HTTP\n")
            conn.close()
        except OSError:
            pass

    threading.Thread(target=serve, daemon=True).start()
    try:
        assert _port_occupied(port) is True
    finally:
        srv.close()


def test_run_refuses_a_port_held_by_a_stranger(sprout_home, capsys):
    import socket

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        code = main(["run", "--port", str(port), "--", sys.executable, "-c", "pass"])
    finally:
        srv.close()
    assert code == 1
    assert "isn't token-sprout" in capsys.readouterr().err


def test_stop_without_a_proxy(sprout_home, capsys):
    assert main(["stop", "--port", str(PORT)]) == 0
    assert "No Token Sprout proxy" in capsys.readouterr().out


def test_stop_leaves_a_stranger_alone(sprout_home, capsys):
    import socket
    import threading

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    port = srv.getsockname()[1]

    def serve():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            conn.recv(1024)
            conn.sendall(b"HTTP/1.1 404 Not Found\r\ncontent-length: 0\r\n\r\n")
            conn.close()

    threading.Thread(target=serve, daemon=True).start()
    try:
        assert main(["stop", "--port", str(port)]) == 1
    finally:
        srv.close()
    assert "leaving it alone" in capsys.readouterr().err


def test_background_proxy_fails_fast_on_a_taken_port(sprout_home):
    import socket
    import subprocess

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        result = subprocess.run(
            [sys.executable, "-m", "token_sprout.cli", "proxy", "--port", str(port), "--background"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        srv.close()
    assert result.returncode == 1
    assert "cannot listen" in result.stderr
