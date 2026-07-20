"""SSE usage collector tests — fed with realistically shaped event streams."""

from token_sprout.usage_parser import SseUsageCollector, Usage

# A sanitized /v1/messages SSE stream shaped from the captured session:
# usage lives in message_start (input + cache) and message_delta (output).
SSE_STREAM = (
    b'event: message_start\n'
    b'data: {"type":"message_start","message":{"id":"msg_1","type":"message",'
    b'"role":"assistant","content":[],"model":"claude-opus-4-8",'
    b'"usage":{"input_tokens":25,"output_tokens":1,'
    b'"cache_creation_input_tokens":7,"cache_read_input_tokens":900}}}\n'
    b'\n'
    b'event: content_block_start\n'
    b'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n'
    b'\n'
    b'event: ping\n'
    b'data: {"type": "ping"}\n'
    b'\n'
    b'event: content_block_delta\n'
    b'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"SECRET_COMPLETION_TEXT"}}\n'
    b'\n'
    b'event: content_block_stop\n'
    b'data: {"type":"content_block_stop","index":0}\n'
    b'\n'
    b'event: message_delta\n'
    b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":42}}\n'
    b'\n'
    b'event: message_stop\n'
    b'data: {"type":"message_stop"}\n'
    b'\n'
)

EXPECTED = Usage(
    input_tokens=25,
    output_tokens=42,
    cache_creation_input_tokens=7,
    cache_read_input_tokens=900,
)


def test_full_stream_in_one_chunk():
    c = SseUsageCollector()
    c.feed(SSE_STREAM)
    assert c.finish() == EXPECTED


def test_stream_fed_byte_by_byte():
    """Event boundaries split across chunks must not matter."""
    c = SseUsageCollector()
    for i in range(len(SSE_STREAM)):
        c.feed(SSE_STREAM[i : i + 1])
    assert c.finish() == EXPECTED


def test_crlf_line_endings():
    c = SseUsageCollector()
    c.feed(SSE_STREAM.replace(b"\n", b"\r\n"))
    assert c.finish() == EXPECTED


def test_disconnect_after_message_start_settles_partial():
    """User pressed Esc mid-stream: settle with the last usage seen."""
    first_event = SSE_STREAM.split(b"\n\n")[0] + b"\n\n"
    c = SseUsageCollector()
    c.feed(first_event)
    assert c.finish() == Usage(25, 1, 7, 900)  # output from message_start


def test_disconnect_mid_line_flushes_pending():
    # cut in the middle of the message_delta data line — the complete
    # message_start must still count
    cut = SSE_STREAM.find(b'"output_tokens":42')
    c = SseUsageCollector()
    c.feed(SSE_STREAM[:cut])
    assert c.finish() == Usage(25, 1, 7, 900)


def test_multiple_message_delta_takes_cumulative_max():
    extra = (
        b'data: {"type":"message_delta","delta":{},"usage":{"output_tokens":10}}\n\n'
        b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":42}}\n\n'
    )
    c = SseUsageCollector()
    c.feed(SSE_STREAM.split(b"event: message_delta")[0])
    c.feed(extra)
    assert c.finish() == EXPECTED


def test_generated_text_mentioning_event_names_is_ignored():
    tricky = (
        b'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta",'
        b'"text":"the message_start and message_delta events say output_tokens: 99999"}}\n\n'
    )
    c = SseUsageCollector()
    c.feed(SSE_STREAM)
    c.feed(tricky)
    assert c.finish() == EXPECTED


def test_error_event_mid_stream_is_ignored():
    c = SseUsageCollector()
    c.feed(SSE_STREAM.split(b"event: message_delta")[0])
    c.feed(b'event: error\ndata: {"type":"error","error":{"type":"overloaded_error"}}\n\n')
    assert c.finish() == Usage(25, 1, 7, 900)


def test_no_usage_events_returns_none():
    c = SseUsageCollector()
    c.feed(b'event: ping\ndata: {"type": "ping"}\n\n')
    assert c.finish() is None


def test_garbage_returns_none():
    c = SseUsageCollector()
    c.feed(b"\x00\xff not sse at all \x00" * 100)
    assert c.finish() is None


def test_non_data_fields_ignored():
    c = SseUsageCollector()
    c.feed(b"retry: 3000\nid: 7\n: comment line\n" + SSE_STREAM)
    assert c.finish() == EXPECTED
