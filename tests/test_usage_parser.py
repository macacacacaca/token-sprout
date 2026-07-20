import json

from token_sprout.usage_parser import Usage, parse_non_streaming


def _body(usage) -> bytes:
    return json.dumps(
        {
            "id": "msg_x",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "hi"}],
            "usage": usage,
        }
    ).encode()


def test_full_usage_object():
    usage = parse_non_streaming(
        _body(
            {
                "input_tokens": 11,
                "output_tokens": 7,
                "cache_creation_input_tokens": 3,
                "cache_read_input_tokens": 100,
            }
        )
    )
    assert usage == Usage(11, 7, 3, 100)
    assert usage.grand_total == 121


def test_missing_cache_fields_default_to_zero():
    usage = parse_non_streaming(_body({"input_tokens": 5, "output_tokens": 2}))
    assert usage == Usage(5, 2, 0, 0)


def test_no_usage_object_returns_none():
    assert parse_non_streaming(b'{"id": "msg_x", "type": "message"}') is None


def test_invalid_json_returns_none():
    assert parse_non_streaming(b"event: message_start\ndata: {}\n\n") is None
    assert parse_non_streaming(b"") is None


def test_non_dict_json_returns_none():
    assert parse_non_streaming(b"[1, 2, 3]") is None
    assert parse_non_streaming(b'"usage"') is None


def test_malformed_field_values_coerced_to_zero():
    usage = parse_non_streaming(
        _body({"input_tokens": "many", "output_tokens": -5, "cache_read_input_tokens": True})
    )
    assert usage == Usage(0, 0, 0, 0)
