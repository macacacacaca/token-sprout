"""Transparent pass-through proxy for the Anthropic API.

Design rules (spec §5.1) — do not weaken these when editing:

- Catch-all: any method, any path, forwarded as raw bytes. The proxy knows
  nothing about the Anthropic request schema and must never parse or
  re-serialize a request body.
- Credentials (x-api-key / authorization) pass through untouched: never
  read, never logged, never persisted. The proxy has no concept of its own
  credentials, which is what makes both API-key and subscription-OAuth
  Claude Code logins work.
- Fail-open: usage parsing and plant bookkeeping must never break
  forwarding. Nothing on the forwarding path awaits plant logic.
- Upstream errors are forwarded verbatim — no retries, no rewriting.
- Logs carry method, path, status and duration only — never bodies, never
  auth headers.
- Binding to 127.0.0.1 is enforced in cli.py (no override flag in v0.1).

Usage extraction covers both response shapes: non-streaming JSON (buffered
copy, parsed at the end) and SSE streams (parsed incrementally — the whole
stream is never buffered). Parsing always runs on a copy of the bytes
being forwarded; the client-facing stream is untouched either way.
"""

from __future__ import annotations

import contextlib
import json
import logging
import queue
import threading
import time

import httpx
from starlette.applications import Starlette
from starlette.requests import ClientDisconnect, Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route, request_response

from . import __version__, state, usage_parser

DEFAULT_UPSTREAM = "https://api.anthropic.com"

# The single deliberate exception to pass-through transparency: lets
# `token-sprout run` verify that whatever is listening on the port is really
# our proxy before reusing it. The Anthropic API has no `/__` paths, so this
# can never shadow a real endpoint.
HEALTH_PATH = "/__token_sprout__/health"

_HOP_BY_HOP = {
    b"connection",
    b"keep-alive",
    b"proxy-authenticate",
    b"proxy-authorization",
    b"te",
    b"trailer",
    b"transfer-encoding",
    b"upgrade",
}
# host: httpx derives it from the upstream URL.
# accept-encoding: forced to identity so the tee parser sees plain bytes
# (spec §5.1 header policy); bandwidth loss is negligible for this use.
_REQUEST_SKIP = _HOP_BY_HOP | {b"host", b"accept-encoding"}

_UPSTREAM_ERROR_BODY = (
    b'{"type":"error","error":{"type":"api_error",'
    b'"message":"token-sprout: could not reach the upstream Anthropic API"}}'
)

log = logging.getLogger("token_sprout.proxy")


def _safe_log_path(path: str, limit: int = 512) -> str:
    """Escape terminal/control characters and bound request-path log size."""
    pieces: list[str] = []
    length = 0
    truncated = False
    for character in path:
        codepoint = ord(character)
        if character.isprintable() and codepoint != 0x7F:
            piece = character
        elif codepoint <= 0xFF:
            piece = f"\\x{codepoint:02x}"
        else:
            piece = f"\\u{codepoint:04x}"
        if length + len(piece) > limit:
            truncated = True
            break
        pieces.append(piece)
        length += len(piece)
    return "".join(pieces) + ("…" if truncated else "")


class _StateWriter:
    """Serializes plant-state writes on a background thread.

    Spec §5.1 forbids the forwarding path from awaiting plant logic. State
    writes take a file lock and touch the disk — fast, but a slow disk, an
    NFS home, or lock contention could stall the async event loop (blocking
    *every* in-flight request, not just this one). So the forwarding path
    only ever does an O(1), disk-free queue operation, and this thread does
    the actual writing. Durable lifecycle jobs stay FIFO; replaceable live
    estimates are coalesced by key. It also *is* the single writer the state
    concurrency protocol assumes.
    """

    def __init__(self) -> None:
        self._q: queue.Queue = queue.Queue()
        self._latest: dict[str, tuple] = {}
        self._latest_lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._run, name="token-sprout-state", daemon=True
        )
        self._thread.start()

    def submit(self, fn, *args) -> None:
        # Unbounded put: never blocks, never touches the disk on this side.
        self._q.put(("call", fn, args))

    def submit_latest(self, key: str, fn, *args) -> None:
        """Queue at most one pending job for a replaceable live value.

        Durable lifecycle/settlement jobs use ``submit`` and are never
        coalesced.  Only high-frequency display estimates use this method,
        so a slow disk cannot grow one queue entry every 0.5 seconds.
        """
        with self._latest_lock:
            already_queued = key in self._latest
            self._latest[key] = (fn, args)
        if not already_queued:
            self._q.put(("latest", key))

    def flush(self) -> None:
        """Block until every submitted write has been applied. Called at
        proxy shutdown (off the forwarding path)."""
        self._q.join()

    def stop(self) -> None:
        self._q.put(None)

    # Durable jobs may not be dropped (spec §5.2), so the queue is unbounded
    # by design; if writes stall for minutes (second proxy on the same state
    # home, NFS), at least say so instead of growing silently.
    _DEPTH_WARN_THRESHOLD = 512
    _DEPTH_WARN_INTERVAL = 60.0

    def _run(self) -> None:
        next_depth_warn = 0.0
        while True:
            item = self._q.get()
            depth = self._q.qsize()
            if depth >= self._DEPTH_WARN_THRESHOLD:
                now = time.monotonic()
                if now >= next_depth_warn:
                    next_depth_warn = now + self._DEPTH_WARN_INTERVAL
                    log.warning(
                        "state write queue depth %d — writes are stalling", depth
                    )
            try:
                if item is None:
                    return
                if item[0] == "latest":
                    _, key = item
                    with self._latest_lock:
                        job = self._latest.pop(key, None)
                    if job is None:
                        continue
                    fn, args = job
                else:
                    _, fn, args = item
                try:
                    fn(*args)
                except Exception:
                    # No exc_info / no args: nothing request-derived may reach
                    # the log.
                    log.warning("plant bookkeeping failed (request unaffected)")
            finally:
                self._q.task_done()


def _outbound_headers(raw: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    headers = [(k, v) for k, v in raw if k.lower() not in _REQUEST_SKIP]
    headers.append((b"accept-encoding", b"identity"))
    return headers


# server/date: uvicorn adds its own pair; forwarding upstream's too would
# send duplicate headers to the client.
_RESPONSE_SKIP = _HOP_BY_HOP | {b"server", b"date"}


def _inbound_headers(raw: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    # Return raw (bytes, bytes) pairs, not a dict: a transparent proxy must
    # preserve duplicate response headers (set-cookie, warning, link, and
    # anything Anthropic adds later). request-id and anthropic-ratelimit-*
    # pass through here so client backoff logic keeps working.
    return [(k, v) for k, v in raw if k.lower() not in _RESPONSE_SKIP]


class _AnyMethodEndpoint:
    """Wrap a request handler as ASGI so Starlette applies no method list."""

    def __init__(self, handler) -> None:
        self._app = request_response(handler)

    async def __call__(self, scope, receive, send) -> None:
        await self._app(scope, receive, send)


def create_app(
    upstream: str = DEFAULT_UPSTREAM,
    transport: httpx.AsyncBaseTransport | None = None,
    listen_port: int = 8000,
) -> Starlette:
    """Build the proxy ASGI app. ``transport`` is injectable for tests."""
    client = httpx.AsyncClient(
        base_url=upstream,
        # read=None: a single streaming inference request can run for many
        # minutes on high-effort models. Killing it would break the client.
        timeout=httpx.Timeout(10.0, read=None, write=None, pool=None),
        # No connection cap: the client decides its own concurrency. httpx's
        # default (max_connections=100) combined with pool=None would park
        # request #101 in an *unbounded* pool wait — a silent hang the client
        # would never have hit without the proxy in between.
        limits=httpx.Limits(max_connections=None, max_keepalive_connections=20),
        transport=transport,
    )

    writer = _StateWriter()
    # Created here (not per-request) so the secret file exists before the
    # server accepts connections — `run` reads it to challenge us.
    secret = state.ensure_secret()

    # Live in-flight token estimates, one entry per streaming inference
    # request, summed into state.live_tokens_estimate on a throttle so the
    # statusline counter ticks while Claude is thinking. Single event loop —
    # no locking needed here.
    live_estimates: dict[int, int] = {}
    last_live_write = 0.0
    live_write_interval = 0.5

    async def forward(request: Request) -> Response:
        path = request.url.path
        log_path = _safe_log_path(path)
        if path == HEALTH_PATH:
            # Prove we hold the 0600 secret and were configured for this port.
            # Port binding prevents a genuine proxy elsewhere from becoming a
            # signing oracle for a rogue listener. See cli._proxy_health.
            body = {
                "app": "token-sprout",
                "version": __version__,
                "port": listen_port,
            }
            nonce = request.query_params.get("nonce")
            if nonce:
                body["proof"] = state.health_proof(secret, listen_port, nonce)
            return Response(content=json.dumps(body), media_type="application/json")
        raw_path = request.scope.get("raw_path", path.encode("utf-8"))
        query_string = request.scope.get("query_string", b"")
        raw_target = raw_path + (b"?" + query_string if query_string else b"")
        target = httpx.URL(raw_path=raw_target)
        # Only the bare inference endpoint feeds the plant:
        # /v1/messages/count_tokens also returns input_tokens but is not
        # real spend (spec §5.1 usage 擷取規格).
        is_inference = request.method == "POST" and path == "/v1/messages"
        started = time.monotonic()

        if is_inference:
            writer.submit(state.request_started)

        upstream_req = client.build_request(
            request.method,
            target,
            headers=_outbound_headers(request.headers.raw),
            content=request.stream(),
        )
        try:
            upstream_resp = await client.send(upstream_req, stream=True)
        except ClientDisconnect:
            # The client hung up while its request body was still uploading
            # (Esc mid-prompt). request.stream() raises ClientDisconnect,
            # which httpx re-raises unwrapped — it is NOT an httpx.HTTPError,
            # so without this branch the request_started increment above
            # would never be settled. Nobody is left to read the response.
            if is_inference:
                writer.submit(state.request_finished, None)
            log.info("%s %s -> client disconnected during upload", request.method, log_path)
            return Response(status_code=499)
        except httpx.HTTPError as exc:
            if is_inference:
                writer.submit(state.request_finished, None)
            log.warning(
                "%s %s -> upstream unreachable (%s)",
                request.method, log_path, type(exc).__name__,
            )
            return Response(
                content=_UPSTREAM_ERROR_BODY,
                status_code=502,
                media_type="application/json",
            )
        except BaseException:
            # Cancellation or anything unforeseen: settle the in-flight
            # counter before propagating, so no failure mode can leak it.
            if is_inference:
                writer.submit(state.request_finished, None)
            raise

        collector = None
        if is_inference and upstream_resp.status_code == 200:
            collector = usage_parser.make_collector(
                upstream_resp.headers.get("content-type", "")
            )

        async def upstream_chunks():
            if upstream_resp.is_stream_consumed:
                # Preloaded responses (e.g. mock transports in tests) carry
                # their whole body already; a live network response never
                # takes this branch.
                yield upstream_resp.content
                return
            # aiter_raw: bytes exactly as received from upstream, so the
            # upstream content-length / content-encoding headers we
            # forwarded stay valid.
            async for chunk in upstream_resp.aiter_raw():
                yield chunk

        live_key = id(upstream_resp)

        async def relay():
            nonlocal collector, last_live_write
            try:
                async for chunk in upstream_chunks():
                    if collector is not None:
                        try:
                            collector.feed(chunk)
                            live_estimates[live_key] = collector.snapshot()
                            now = time.monotonic()
                            if now - last_live_write >= live_write_interval:
                                last_live_write = now
                                writer.submit_latest(
                                    "live_tokens_estimate",
                                    state.set_live_estimate,
                                    sum(live_estimates.values()),
                                )
                        except Exception:
                            # fail-open: stop parsing, keep forwarding
                            collector = None
                            live_estimates.pop(live_key, None)
                            log.warning("usage parsing failed (request unaffected)")
                    yield chunk
            finally:
                # Runs after the last byte was yielded to the client (also on
                # client disconnect). Everything below is fail-open.
                await upstream_resp.aclose()
                usage = None
                if collector is not None:
                    try:
                        usage = collector.finish()
                    except Exception:
                        log.warning("usage parsing failed (request unaffected)")
                live_estimates.pop(live_key, None)
                if is_inference:
                    writer.submit(
                        state.request_finished,
                        usage,
                        sum(live_estimates.values()),
                    )
                log.info(
                    "%s %s -> %d (%.2fs)",
                    request.method, log_path,
                    upstream_resp.status_code,
                    time.monotonic() - started,
                )

        response = StreamingResponse(relay(), status_code=upstream_resp.status_code)
        # Assign raw pairs directly: Starlette's headers= param takes a dict
        # (which would collapse duplicates). raw_headers preserves multi-value
        # headers exactly as upstream sent them.
        response.raw_headers = _inbound_headers(upstream_resp.headers.raw)
        return response

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        try:
            yield
        finally:
            # Off the forwarding path: drain queued writes, stop the thread,
            # close the client.
            writer.flush()
            writer.stop()
            await client.aclose()

    return Starlette(
        routes=[Route("/{path:path}", _AnyMethodEndpoint(forward))],
        lifespan=lifespan,
    )
