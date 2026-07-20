"""Tests for the code-review hardening pass (P1/P2/P3).

- Background state writer preserves durable order, coalesces live jobs, and
  survives failures.
- `run`'s port-bound health check rejects forged and cross-port-relayed proof.
- Duplicate response headers survive the proxy.
- Non-Anthropic upstream is warned about.
"""

import stat
import threading

import httpx
import pytest
from starlette.testclient import TestClient

from token_sprout import state
from token_sprout.cli import (
    DEFAULT_UPSTREAM,
    _proxy_health,
    _warn_if_foreign_upstream,
)
from token_sprout.proxy import HEALTH_PATH, _StateWriter, _safe_log_path, create_app


# ---- [P1a] background state writer -----------------------------------------

def test_state_writer_applies_jobs_in_order():
    w = _StateWriter()
    try:
        results = []
        w.submit(results.append, 1)
        w.submit(results.append, 2)
        w.flush()
        assert results == [1, 2]
    finally:
        w.stop()


def test_state_writer_survives_a_failing_job():
    w = _StateWriter()
    try:
        marker = []

        def boom():
            raise RuntimeError("bookkeeping blew up")

        w.submit(boom)
        w.submit(marker.append, "after")
        w.flush()
        # the failing job did not kill the thread
        assert marker == ["after"]
    finally:
        w.stop()


def test_state_writer_coalesces_replaceable_live_updates():
    w = _StateWriter()
    entered = threading.Event()
    release = threading.Event()
    results = []

    def block_worker():
        entered.set()
        release.wait(timeout=2)

    try:
        w.submit(block_worker)
        assert entered.wait(timeout=1)
        for value in range(1000):
            w.submit_latest("live_tokens_estimate", results.append, value)
        release.set()
        w.flush()
        assert results == [999]
    finally:
        release.set()
        w.stop()


# ---- [P1b] forged-proxy defense --------------------------------------------

def test_health_endpoint_proves_it_holds_the_secret(sprout_home):
    state.init_home()
    app = create_app(upstream="https://upstream.test")
    with TestClient(app) as client:
        resp = client.get(HEALTH_PATH, params={"nonce": "nonce-xyz"})
    secret = state.read_secret()
    data = resp.json()
    assert data["app"] == "token-sprout"
    assert data["port"] == 8000
    assert data["proof"] == state.health_proof(secret, 8000, "nonce-xyz")


def test_proxy_health_false_when_no_secret_yet(sprout_home):
    state.init_home()  # does not create a secret
    assert state.read_secret() is None
    # returns before making any network call
    assert _proxy_health(8000) is False


def test_proxy_health_rejects_forged_app_banner(sprout_home, monkeypatch):
    state.init_home()
    state.ensure_secret()

    class Forged:
        status_code = 200

        def json(self):
            return {"app": "token-sprout"}  # no proof — a rogue listener

    monkeypatch.setattr(httpx, "get", lambda *a, **k: Forged())
    assert _proxy_health(8000) is False


def test_proxy_health_rejects_wrong_proof(sprout_home, monkeypatch):
    state.init_home()
    state.ensure_secret()

    class WrongProof:
        status_code = 200

        def json(self):
            return {"app": "token-sprout", "port": 8000, "proof": "deadbeef"}

    monkeypatch.setattr(httpx, "get", lambda *a, **k: WrongProof())
    assert _proxy_health(8000) is False


def test_proxy_health_accepts_valid_proof(sprout_home, monkeypatch):
    state.init_home()
    secret = state.ensure_secret()

    def fake_get(url, params=None, timeout=None):
        class Ok:
            status_code = 200

            def json(self):
                return {
                    "app": "token-sprout",
                    "port": 8000,
                    "proof": state.health_proof(secret, 8000, params["nonce"]),
                }

        return Ok()

    monkeypatch.setattr(httpx, "get", fake_get)
    assert _proxy_health(8000) is True


def test_proxy_health_rejects_cross_port_relay(sprout_home, monkeypatch):
    """A real proxy on 8001 cannot authenticate a fake listener on 8000."""
    state.init_home()
    app = create_app(upstream="https://upstream.test", listen_port=8001)
    with TestClient(app) as genuine_proxy:
        monkeypatch.setattr(
            httpx,
            "get",
            lambda *args, params=None, **kwargs: genuine_proxy.get(
                HEALTH_PATH, params=params
            ),
        )
        assert _proxy_health(8000) is False


@pytest.mark.skipif(
    not hasattr(stat, "S_IMODE") or __import__("os").name == "nt",
    reason="POSIX file permissions",
)
def test_secret_file_is_0600(sprout_home):
    state.ensure_secret()
    mode = stat.S_IMODE(state.secret_path().stat().st_mode)
    assert mode == 0o600


# ---- [P2a] duplicate response headers --------------------------------------

def test_duplicate_response_headers_are_preserved(sprout_home):
    state.init_home()

    async def handler(request: httpx.Request) -> httpx.Response:
        await request.aread()
        return httpx.Response(
            200,
            headers=[
                ("content-type", "application/json"),
                ("warning", '199 - "first"'),
                ("warning", '199 - "second"'),
            ],
            content=b"{}",
        )

    app = create_app(upstream="https://upstream.test", transport=httpx.MockTransport(handler))
    with TestClient(app) as client:
        resp = client.post(
            "/v1/messages",
            json={"model": "m", "max_tokens": 1, "messages": []},
            headers={"x-api-key": "sk-x", "anthropic-version": "2023-06-01"},
        )

    assert resp.headers.get_list("warning") == ['199 - "first"', '199 - "second"']


# ---- [P3] foreign-upstream warning -----------------------------------------

def test_warns_on_non_anthropic_upstream(capsys):
    _warn_if_foreign_upstream("http://evil.example")
    assert "non-Anthropic upstream" in capsys.readouterr().err


def test_foreign_upstream_warning_never_echoes_credentials_or_query(capsys):
    _warn_if_foreign_upstream(
        "https://user:SECRET_PASSWORD@evil.example:9443/private?token=SECRET_QUERY"
    )
    warning = capsys.readouterr().err
    assert "https://evil.example:9443" in warning
    assert "SECRET_PASSWORD" not in warning
    assert "SECRET_QUERY" not in warning
    assert "/private" not in warning


def test_request_path_for_logs_escapes_controls_and_is_bounded():
    safe = _safe_log_path("/ok\nFAKE\t\x1b[31m" + "x" * 1000)
    assert "\n" not in safe
    assert "\t" not in safe
    assert "\x1b" not in safe
    assert "\\x0a" in safe
    assert "\\x09" in safe
    assert "\\x1b" in safe
    assert len(safe) <= 513


def test_no_warning_for_default_upstream(capsys):
    _warn_if_foreign_upstream(DEFAULT_UPSTREAM)
    assert capsys.readouterr().err == ""
