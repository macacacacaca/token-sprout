"""`watch --json`: the read-only line feed behind editor integrations."""

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from token_sprout import feed, game, state
from token_sprout.cli import main
from token_sprout.state import default_state


def _lines(buffer: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in buffer.getvalue().splitlines()]


def _stop_after(ticks: int, each=None):
    """A sleep stand-in that runs ``each`` per tick, then ends the loop."""
    calls = {"n": 0}

    def fake_sleep(_seconds: float) -> None:
        calls["n"] += 1
        if each is not None:
            each(calls["n"])
        if calls["n"] >= ticks:
            raise KeyboardInterrupt

    return fake_sleep


def test_idle_payload_is_the_settled_plant_view():
    s = default_state()
    s["current_exp"] = game.STAGE_UNIT_COSTS[1] * 3 + 10_000
    s["live_tokens_estimate"] = 99_999  # stale estimate: ignored while idle
    s["total_tokens"] = 1_234
    s["total_cache_read_tokens"] = 1_000

    p = feed.payload(s)

    assert p["protocol"] == feed.PROTOCOL
    assert p["thinking"] is False
    assert p["live_tokens_estimate"] == 0
    assert p["view"] == game.plant_view(s)
    assert p["glyph"] == "🌱"
    assert p["totals"]["total"] == 1_234
    assert p["totals"]["cache_read"] == 1_000
    assert p["transition"] is None
    assert p["notice"] is None


def test_thinking_payload_previews_like_the_statusline():
    s = default_state()
    s["current_exp"] = game.STAGE_UNIT_COSTS[1] * 2 + 129_000
    s["active_requests"] = 2
    s["live_tokens_estimate"] = 5_000

    p = feed.payload(s)

    assert p["thinking"] is True
    assert p["active_requests"] == 2
    assert p["live_tokens_estimate"] == 5_000
    assert p["view"] == game.plant_view(s, pending_exp=5_000)
    assert (p["view"]["count"], p["view"]["unit_progress_percent"]) == (2, 67)


def test_payload_reports_stage_up_notice():
    prev = default_state()
    cur = default_state()
    cur["current_exp"] = game.STAGE_UNIT_COSTS[1]
    p = feed.payload(cur, prev)
    assert p["transition"] == {"kind": "stage_up", "stage": "sprout"}
    assert "Grew into sprout" in p["notice"]


def test_payload_reports_bloom_and_new_generation():
    prev = default_state()
    bloom = default_state()
    bloom["current_exp"] = game.BLOOM_PROGRESS
    assert feed.payload(bloom, prev)["transition"] == {"kind": "bloom"}

    gen2 = default_state()
    gen2["generation"] = 2
    p = feed.payload(gen2, bloom)
    assert p["transition"] == {"kind": "generation", "generation": 2}
    assert "Generation 2" in p["notice"]


def test_payload_is_ascii_json():
    s = default_state()
    s["current_exp"] = game.BLOOM_PROGRESS
    line = json.dumps(feed.payload(s), separators=(",", ":"))
    assert line.isascii()
    assert json.loads(line)["glyph"] == "🌸"


def test_run_emits_first_frame_then_only_changes(sprout_home):
    state.init_home()

    def feed_once(tick: int) -> None:
        if tick == 2:
            state.request_started()

    out = io.StringIO()
    assert feed.run(out, sleep=_stop_after(4, feed_once)) == 0

    frames = _lines(out)
    assert [frame["thinking"] for frame in frames] == [False, True]


def test_run_keeps_previous_frame_when_state_is_damaged(sprout_home):
    state.init_home()
    state.update_state(lambda s: s.__setitem__("current_exp", 50_000))

    def corrupt(tick: int) -> None:
        if tick == 1:
            state.state_path().write_text("{ not json")

    out = io.StringIO()
    feed.run(out, sleep=_stop_after(3, corrupt))

    frames = _lines(out)
    assert len(frames) == 1  # damage did not snap the plant back to a seed
    assert frames[0]["view"]["count"] == 5


def test_run_never_creates_the_state_home(sprout_home):
    out = io.StringIO()
    feed.run(out, sleep=_stop_after(2))
    assert _lines(out)[0]["view"] == game.plant_view(default_state())
    assert not sprout_home.exists()


def test_run_exits_when_orphaned(sprout_home, monkeypatch):
    parents = iter([100, 100, 1])
    monkeypatch.setattr(feed.os, "getppid", lambda: next(parents))
    out = io.StringIO()
    assert feed.run(out, sleep=lambda _s: None) == 0


def test_run_exits_quietly_on_closed_pipe():
    class ClosedPipe(io.StringIO):
        def write(self, _text):
            raise BrokenPipeError

    assert feed.run(ClosedPipe(), sleep=_stop_after(5)) == 0


def test_watch_json_flag_dispatches_to_feed(monkeypatch):
    monkeypatch.setattr(feed, "run", lambda: 7)
    assert main(["watch", "--json"]) == 7


def test_watch_json_process_streams_a_parsable_first_line(sprout_home):
    proc = subprocess.Popen(
        [sys.executable, "-m", "token_sprout.cli", "watch", "--json"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        first = json.loads(proc.stdout.readline())
    finally:
        proc.kill()
        proc.communicate(timeout=10)
    assert first["protocol"] == feed.PROTOCOL
    assert first["view"]["stage"] == "seed"
    assert not sprout_home.exists()


@pytest.mark.parametrize("module", ["rich", "starlette", "httpx", "uvicorn"])
def test_feed_does_not_import_heavy_dependencies(module):
    code = f"import sys, token_sprout.feed; print({module!r} in sys.modules)"
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False"


# The VS Code extension's unit tests parse this same file, so a protocol
# change on either side fails a test until both agree.
CONTRACT = (
    Path(__file__).resolve().parent.parent
    / "vscode-extension" / "test" / "fixtures" / "snapshots.json"
)


def _contract_scenarios() -> dict:
    def scenario(**changes):
        s = default_state()
        s.update(changes)
        return s

    idle = scenario(
        current_exp=game.STAGE_UNIT_COSTS[1] * 2 + 129_000,
        total_tokens=1_234_567,
        total_input_tokens=200_000,
        total_output_tokens=300_000,
        total_cache_creation_tokens=100_000,
        total_cache_read_tokens=634_567,
        last_request_tokens=1_284,
        last_request_finished_at="2026-10-15T08:41:19+00:00",
    )
    fresh = scenario()
    return {
        "fresh_seed": feed.payload(fresh),
        "idle_sprout": feed.payload(idle),
        "thinking_sprout": feed.payload(dict(idle, active_requests=2, live_tokens_estimate=5_000)),
        "bloom": feed.payload(scenario(current_exp=game.BLOOM_PROGRESS, generation=3)),
        "stage_up": feed.payload(scenario(current_exp=game.STAGE_UNIT_COSTS[1]), fresh),
    }


def test_payloads_match_the_extension_contract_fixture():
    if not CONTRACT.exists():
        pytest.skip("VS Code extension sources are not part of this checkout")
    expected = json.loads(CONTRACT.read_text(encoding="utf-8"))
    actual = _contract_scenarios()
    assert sorted(actual) == sorted(expected)
    for name, payload in actual.items():
        # The release string is not part of the protocol.
        payload = {**payload, "version": expected[name]["version"]}
        assert payload == expected[name], name
