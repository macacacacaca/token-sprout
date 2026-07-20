"""Statusline live preview, emoji pile, and ten-cell progress bar."""

from token_sprout import game, state
from token_sprout.cli import main
from token_sprout.usage_parser import SseUsageCollector, Usage

from test_sse_parser import SSE_STREAM


def test_hidden_when_idle(sprout_home, capsys):
    state.init_home()
    assert main(["statusline"]) == 0
    assert capsys.readouterr().out == ""


def test_shows_pile_and_live_counter_while_thinking(sprout_home, capsys):
    state.init_home()
    # Settled at sprout ×2 plus 129k toward the next sprout; estimated
    # +5,000 previews 134k/200k, which rounds to 67%.
    state.update_state(
        lambda s: s.__setitem__(
            "current_exp",
            game.STAGE_UNIT_COSTS[1] * 2 + 129_000,
        )
    )
    state.request_started()
    state.set_live_estimate(5_000)
    main(["statusline"])
    assert capsys.readouterr().out.strip() == (
        "🌱🌱 2/20 · 💧 [██████░░░░] 67% · +5,000 tokens"
    )


def test_always_flag_shows_pile_while_idle(sprout_home, capsys):
    state.init_home()
    main(["statusline", "--always"])
    out = capsys.readouterr().out
    assert "🌰" not in out
    assert "0/20" in out
    assert "💧" not in out  # no watering marker while idle
    assert "[" not in out  # quiet --always mode has no progress bar


def test_bloom_line(sprout_home, capsys):
    state.init_home()
    state.update_state(lambda s: s.__setitem__("current_exp", game.BLOOM_PROGRESS))
    state.request_started()
    main(["statusline"])
    assert "in bloom" in capsys.readouterr().out


def test_live_estimate_cleared_when_last_request_finishes(sprout_home):
    state.init_home()
    state.request_started()
    state.set_live_estimate(500)
    state.request_finished(Usage(10, 5, 0, 0), live_estimate=0)
    s = state.load_state()
    assert s["live_tokens_estimate"] == 0
    assert s["active_requests"] == 0


def test_sse_snapshot_grows_during_stream():
    c = SseUsageCollector()
    assert c.snapshot() == 0

    first_event = SSE_STREAM.split(b"\n\n")[0] + b"\n\n"
    c.feed(first_event)
    assert c.snapshot() >= 32

    text = b"x" * 400
    delta = (
        b'data: {"type":"content_block_delta","index":0,'
        b'"delta":{"type":"text_delta","text":"' + text + b'"}}\n\n'
    )
    before = c.snapshot()
    c.feed(delta)
    grown = c.snapshot() - before
    assert 80 <= grown <= 130

    c.feed(
        b'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},'
        b'"usage":{"output_tokens":120}}\n\n'
    )
    assert c.finish() == Usage(25, 120, 7, 900)


def test_statusline_survives_undecodable_stdin(sprout_home, monkeypatch):
    """The session JSON is drained as bytes: a non-UTF-8 locale plus
    multibyte input must not crash the hook on every refresh."""
    import io

    state.init_home()

    class FakeStdin:
        def __init__(self):
            self.buffer = io.BytesIO("café 🌸".encode() + b"\xff\xfe")

        @staticmethod
        def isatty():
            return False

        @staticmethod
        def read():
            raise UnicodeDecodeError("ascii", b"\xff", 0, 1, "ordinal not in range")

    monkeypatch.setattr("sys.stdin", FakeStdin())
    assert main(["statusline"]) == 0
