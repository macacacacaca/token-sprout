"""Extract usage metadata from Anthropic API responses.

Two collectors, one per response shape:

- ``JsonUsageCollector`` — non-streaming ``application/json`` bodies.
- ``SseUsageCollector`` — streaming ``text/event-stream`` bodies, parsed
  incrementally as chunks are forwarded (the proxy never buffers a whole
  SSE stream).

Where the numbers live on the wire:

- non-streaming: top-level ``usage`` object of the response JSON.
- SSE: ``message_start`` carries ``message.usage`` (input + cache fields,
  plus an initial output count); the final ``message_delta`` carries the
  cumulative ``usage.output_tokens``.

Disconnect policy (spec §5.1): if the stream is cut early (user pressed
Esc), we settle with the last usage seen — Anthropic bills the streamed
partial, so partial food is the consistent choice.

Everything here must fail soft: malformed input yields ``None`` (or is
skipped) and the caller keeps forwarding. Bodies are held in memory only
and are never logged or persisted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

# Guards memory against pathological bodies; oversized bodies are still
# forwarded by the proxy — they just aren't parsed (fail-open).
_JSON_CAP = 50 * 1024 * 1024
# A single SSE event (one data payload) should be tiny; cap hard.
_EVENT_CAP = 2 * 1024 * 1024


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def grand_total(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )


def _count_field(mapping: dict, key: str) -> int:
    value = mapping.get(key, 0)
    # bool is an int subclass; reject it along with anything non-int
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def parse_non_streaming(body: bytes) -> Usage | None:
    """Read the top-level ``usage`` object from a /v1/messages JSON response.

    Returns None when the body is not JSON, has no usage object, or the
    fields are malformed — never raises.
    """
    try:
        data = json.loads(body)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return None
    return Usage(
        input_tokens=_count_field(usage, "input_tokens"),
        output_tokens=_count_field(usage, "output_tokens"),
        cache_creation_input_tokens=_count_field(usage, "cache_creation_input_tokens"),
        cache_read_input_tokens=_count_field(usage, "cache_read_input_tokens"),
    )


class JsonUsageCollector:
    """Buffers a non-streaming JSON body and parses usage at the end."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._overflow = False

    def feed(self, chunk: bytes) -> None:
        if self._overflow:
            return
        if len(self._buf) + len(chunk) > _JSON_CAP:
            self._overflow = True
            self._buf.clear()
            return
        self._buf.extend(chunk)

    def finish(self) -> Usage | None:
        if self._overflow:
            return None
        return parse_non_streaming(bytes(self._buf))

    def snapshot(self) -> int:
        # non-streaming: nothing meaningful to show until the body is done
        return 0


class SseUsageCollector:
    """Incremental SSE parser that only extracts usage numbers.

    Feed it the exact bytes being forwarded, in whatever chunk sizes they
    arrive — event boundaries split across chunks, CRLF line endings,
    ``ping`` events and mid-stream ``error`` events are all handled.
    Everything that is not a ``message_start`` / ``message_delta`` usage
    field is ignored and discarded.
    """

    def __init__(self) -> None:
        self._line_buf = bytearray()  # partial line carried across chunks
        self._data = bytearray()  # data lines of the current event
        self._event_overflow = False
        self._seen_usage = False
        self._input_tokens = 0
        self._output_tokens = 0
        self._cache_creation = 0
        self._cache_read = 0
        # rough size of streamed output text, for the live counter only —
        # the settled numbers always come from message_start/message_delta
        self._approx_output_chars = 0

    def feed(self, chunk: bytes) -> None:
        self._line_buf.extend(chunk)
        while True:
            newline = self._line_buf.find(b"\n")
            if newline < 0:
                break
            line = bytes(self._line_buf[:newline])
            del self._line_buf[: newline + 1]
            if line.endswith(b"\r"):
                line = line[:-1]
            self._handle_line(line)
        if len(self._line_buf) > _EVENT_CAP:
            # runaway partial line — not SSE-shaped; stop accumulating
            self._line_buf.clear()

    def finish(self) -> Usage | None:
        # A cut stream may end without the terminating blank line: flush
        # whatever is pending and settle with the last usage seen.
        if self._line_buf:
            line = bytes(self._line_buf)
            self._line_buf.clear()
            if line.endswith(b"\r"):
                line = line[:-1]
            self._handle_line(line)
        self._dispatch()
        if not self._seen_usage:
            return None
        return Usage(
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            cache_creation_input_tokens=self._cache_creation,
            cache_read_input_tokens=self._cache_read,
        )

    def snapshot(self) -> int:
        """Approximate EXP-relevant tokens absorbed so far, for the live
        statusline counter. ~4 chars per output token; corrected by the
        real ``message_delta`` count once it arrives. Never authoritative —
        settled numbers come from ``finish()``.
        """
        est_output = max(self._output_tokens, self._approx_output_chars // 4)
        if not self._seen_usage and est_output == 0:
            return 0
        return self._input_tokens + self._cache_creation + est_output

    def _handle_line(self, line: bytes) -> None:
        if not line:
            self._dispatch()
            return
        if not line.startswith(b"data:"):
            # event: / id: / retry: / comment lines — the JSON payload's
            # own "type" field is authoritative, so these are ignored.
            return
        if self._event_overflow:
            return
        payload = line[5:]
        if payload.startswith(b" "):
            payload = payload[1:]
        if len(self._data) + len(payload) > _EVENT_CAP:
            self._event_overflow = True
            self._data.clear()
            return
        if self._data:
            self._data.extend(b"\n")
        self._data.extend(payload)

    def _dispatch(self) -> None:
        data = bytes(self._data)
        self._data.clear()
        overflow = self._event_overflow
        self._event_overflow = False
        if not data or overflow:
            return
        # Cheap pre-filter: skip json.loads for the high-volume events we
        # don't care about (content_block_delta etc.). False positives —
        # generated text that mentions these words — just get parsed and
        # ignored by the type check below.
        if b"message_start" not in data and b"message_delta" not in data:
            if b'"text_delta"' in data or b'"thinking_delta"' in data:
                # ~90 bytes of JSON envelope around the delta text; the
                # remainder approximates streamed output characters
                self._approx_output_chars += max(0, len(data) - 90)
            return
        try:
            payload = json.loads(data)
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        ptype = payload.get("type")
        if ptype == "message_start":
            message = payload.get("message")
            usage = message.get("usage") if isinstance(message, dict) else None
            if isinstance(usage, dict):
                self._seen_usage = True
                self._input_tokens = _count_field(usage, "input_tokens")
                self._cache_creation = _count_field(usage, "cache_creation_input_tokens")
                self._cache_read = _count_field(usage, "cache_read_input_tokens")
                self._output_tokens = max(
                    self._output_tokens, _count_field(usage, "output_tokens")
                )
        elif ptype == "message_delta":
            usage = payload.get("usage")
            if isinstance(usage, dict) and "output_tokens" in usage:
                self._seen_usage = True
                # cumulative on the wire; max() guards against out-of-order
                self._output_tokens = max(
                    self._output_tokens, _count_field(usage, "output_tokens")
                )


def make_collector(content_type: str):
    """Pick a collector for a 200 inference response, or None."""
    if content_type.startswith("application/json"):
        return JsonUsageCollector()
    if content_type.startswith("text/event-stream"):
        return SseUsageCollector()
    return None
