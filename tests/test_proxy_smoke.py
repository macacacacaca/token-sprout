"""Smoke tests for the pass-through proxy against a mock upstream.

These verify the forwarding contract and fail-open behavior. The real
verification of transparency happens in Milestone 0's live checklist with
an actual Claude Code session — do not grow this file into a full proxy
test suite (spec §8).
"""

import json
import logging

import httpx
import pytest
from starlette.testclient import TestClient

from token_sprout import state
from token_sprout.proxy import create_app

CANNED_RESPONSE = {
    "id": "msg_test",
    "type": "message",
    "role": "assistant",
    "content": [{"type": "text", "text": "SECRET_COMPLETION"}],
    "model": "claude-opus-4-8",
    "stop_reason": "end_turn",
    "usage": {
        "input_tokens": 11,
        "output_tokens": 7,
        "cache_creation_input_tokens": 3,
        "cache_read_input_tokens": 100,
    },
}

REQUEST_BODY = {
    "model": "claude-opus-4-8",
    "max_tokens": 64,
    "messages": [{"role": "user", "content": "SECRET_PROMPT"}],
}

REQUEST_HEADERS = {
    "x-api-key": "sk-ant-SECRET_KEY",
    "anthropic-version": "2023-06-01",
}


def make_client(handler):
    app = create_app(upstream="https://upstream.test", transport=httpx.MockTransport(handler))
    return TestClient(app)


def recording_handler(seen):
    async def handler(request: httpx.Request) -> httpx.Response:
        await request.aread()
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = request.content
        return httpx.Response(200, json=CANNED_RESPONSE)

    return handler


def test_forwards_request_and_response_verbatim(sprout_home):
    state.init_home()
    seen = {}
    with make_client(recording_handler(seen)) as client:
        resp = client.post("/v1/messages", json=REQUEST_BODY, headers=REQUEST_HEADERS)

    assert resp.status_code == 200
    assert resp.json() == CANNED_RESPONSE
    # request reached upstream unmodified
    assert seen["url"] == "https://upstream.test/v1/messages"
    assert json.loads(seen["body"]) == REQUEST_BODY
    # auth + version headers pass through untouched; encoding forced to identity
    assert seen["headers"]["x-api-key"] == "sk-ant-SECRET_KEY"
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["headers"]["accept-encoding"] == "identity"


def test_request_body_bytes_are_forwarded_verbatim(sprout_home):
    state.init_home()
    raw_body = b'{"escaped":"\\u2603","spacing": [1,  2],"unknown":true}'
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = await request.aread()
        return httpx.Response(204)

    with make_client(handler) as client:
        resp = client.post(
            "/future-endpoint",
            content=raw_body,
            headers={**REQUEST_HEADERS, "content-type": "application/json"},
        )

    assert resp.status_code == 204
    assert seen["body"] == raw_body


def test_usage_is_counted_and_state_holds_no_secrets(sprout_home):
    state.init_home()
    with make_client(recording_handler({})) as client:
        client.post("/v1/messages", json=REQUEST_BODY, headers=REQUEST_HEADERS)

    s = state.load_state()
    assert s["active_requests"] == 0
    assert s["total_input_tokens"] == 11
    assert s["total_output_tokens"] == 7
    assert s["total_cache_creation_tokens"] == 3
    assert s["total_cache_read_tokens"] == 100
    assert s["total_tokens"] == 121
    assert s["current_exp"] == 21  # cache_read excluded from EXP

    raw = state.state_path().read_text()
    assert "SECRET_PROMPT" not in raw
    assert "SECRET_COMPLETION" not in raw
    assert "SECRET_KEY" not in raw


def test_count_tokens_endpoint_is_not_counted(sprout_home):
    state.init_home()

    async def handler(request: httpx.Request) -> httpx.Response:
        await request.aread()
        return httpx.Response(200, json={"input_tokens": 9999})

    with make_client(handler) as client:
        resp = client.post(
            "/v1/messages/count_tokens", json=REQUEST_BODY, headers=REQUEST_HEADERS
        )

    assert resp.status_code == 200
    assert resp.json() == {"input_tokens": 9999}
    assert state.load_state()["total_tokens"] == 0


def test_other_paths_are_forwarded(sprout_home):
    state.init_home()

    async def handler(request: httpx.Request) -> httpx.Response:
        await request.aread()
        return httpx.Response(200, json={"data": [{"id": "claude-opus-4-8"}]})

    with make_client(handler) as client:
        resp = client.get("/v1/models?limit=5", headers=REQUEST_HEADERS)

    assert resp.status_code == 200
    assert resp.json()["data"][0]["id"] == "claude-opus-4-8"
    assert state.load_state()["total_tokens"] == 0


def test_encoded_request_target_is_preserved(sprout_home):
    state.init_home()
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["raw_path"] = request.url.raw_path
        return httpx.Response(204)

    with make_client(handler) as client:
        resp = client.get("/v1/a%2Fb?x=a%2Fb&x=1+2", headers=REQUEST_HEADERS)

    assert resp.status_code == 204
    assert seen["raw_path"] == b"/v1/a%2Fb?x=a%2Fb&x=1+2"


def test_unknown_http_method_is_forwarded(sprout_home):
    state.init_home()
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        return httpx.Response(204)

    with make_client(handler) as client:
        resp = client.request("TRACE", "/future-endpoint", headers=REQUEST_HEADERS)

    assert resp.status_code == 204
    assert seen["method"] == "TRACE"


def test_upstream_errors_pass_through_verbatim(sprout_home):
    state.init_home()
    error_body = {"type": "error", "error": {"type": "authentication_error", "message": "x"}}

    async def handler(request: httpx.Request) -> httpx.Response:
        await request.aread()
        return httpx.Response(401, json=error_body)

    with make_client(handler) as client:
        resp = client.post("/v1/messages", json=REQUEST_BODY, headers=REQUEST_HEADERS)

    assert resp.status_code == 401
    assert resp.json() == error_body
    s = state.load_state()
    assert s["active_requests"] == 0
    assert s["total_tokens"] == 0  # error responses feed nothing


def test_fail_open_when_bookkeeping_explodes(sprout_home, monkeypatch):
    """Plant logic failures must never affect forwarding (spec §5.1 rule 7)."""
    state.init_home()

    def boom(*args, **kwargs):
        raise RuntimeError("state layer is broken")

    monkeypatch.setattr("token_sprout.proxy.state.request_started", boom)
    monkeypatch.setattr("token_sprout.proxy.state.request_finished", boom)

    with make_client(recording_handler({})) as client:
        resp = client.post("/v1/messages", json=REQUEST_BODY, headers=REQUEST_HEADERS)

    assert resp.status_code == 200
    assert resp.json() == CANNED_RESPONSE


def test_fail_open_when_state_lock_is_held(sprout_home, monkeypatch):
    from filelock import FileLock

    state.init_home()
    monkeypatch.setattr(state, "STATE_LOCK_TIMEOUT_SECONDS", 0.01)
    with FileLock(str(state._lock_path())):
        with make_client(recording_handler({})) as client:
            resp = client.post(
                "/v1/messages", json=REQUEST_BODY, headers=REQUEST_HEADERS
            )

    assert resp.status_code == 200
    assert resp.json() == CANNED_RESPONSE


def test_sse_stream_forwarded_verbatim_and_counted(sprout_home):
    from test_sse_parser import SSE_STREAM

    state.init_home()

    async def handler(request: httpx.Request) -> httpx.Response:
        await request.aread()
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=SSE_STREAM,
        )

    with make_client(handler) as client:
        resp = client.post("/v1/messages", json=REQUEST_BODY, headers=REQUEST_HEADERS)

    assert resp.status_code == 200
    assert resp.content == SSE_STREAM  # byte-identical passthrough

    s = state.load_state()
    assert s["active_requests"] == 0
    assert s["total_input_tokens"] == 25
    assert s["total_output_tokens"] == 42
    assert s["total_cache_creation_tokens"] == 7
    assert s["total_cache_read_tokens"] == 900
    assert s["current_exp"] == 74  # 25 + 42 + 7, cache_read excluded

    raw = state.state_path().read_text()
    assert "SECRET_COMPLETION_TEXT" not in raw


def test_request_logs_contain_no_body_headers_or_usage(sprout_home, caplog):
    state.init_home()
    caplog.set_level(logging.INFO, logger="token_sprout.proxy")

    with make_client(recording_handler({})) as client:
        client.post("/v1/messages", json=REQUEST_BODY, headers=REQUEST_HEADERS)

    rendered = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name == "token_sprout.proxy"
    )
    assert "SECRET_PROMPT" not in rendered
    assert "SECRET_COMPLETION" not in rendered
    assert "SECRET_KEY" not in rendered
    assert "usage:" not in rendered
    assert "POST /v1/messages -> 200" in rendered


def test_upstream_unreachable_returns_502(sprout_home):
    state.init_home()

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with make_client(handler) as client:
        resp = client.post("/v1/messages", json=REQUEST_BODY, headers=REQUEST_HEADERS)

    assert resp.status_code == 502
    assert resp.json()["error"]["type"] == "api_error"
    assert state.load_state()["active_requests"] == 0


def test_client_disconnect_during_upload_settles_counter(sprout_home):
    """Esc while the request body is still uploading: starlette raises
    ClientDisconnect (NOT an httpx.HTTPError) out of client.send — the
    in-flight counter must still be settled or the statusline shows
    "thinking" forever."""
    import anyio

    state.init_home()

    async def handler(request: httpx.Request) -> httpx.Response:
        await request.aread()  # forces the request stream to be consumed
        return httpx.Response(200, json=CANNED_RESPONSE)

    app = create_app(
        upstream="https://upstream.test", transport=httpx.MockTransport(handler)
    )

    sent = []

    async def drive():
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/v1/messages",
            "raw_path": b"/v1/messages",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 8000),
        }
        messages = [
            {"type": "http.request", "body": b'{"partial', "more_body": True},
            {"type": "http.disconnect"},
        ]

        async def receive():
            return messages.pop(0)

        async def send(message):
            sent.append(message)

        await app(scope, receive, send)

    anyio.run(drive)

    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 499  # client-closed-request, nobody reads it

    # The settlement goes through the background writer thread; give it a
    # moment, then require both bookkeeping jobs to have landed.
    import time as _time

    deadline = _time.monotonic() + 2
    while _time.monotonic() < deadline:
        s = state.load_state()
        if s["active_requests"] == 0 and s["last_request_finished_at"]:
            break
        _time.sleep(0.02)
    s = state.load_state()
    assert s["last_request_started_at"] is not None  # request_started ran
    assert s["last_request_finished_at"] is not None  # ...and was settled
    assert s["active_requests"] == 0
