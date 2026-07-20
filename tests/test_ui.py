"""Light rendering tests for the watch panel — no real terminal needed."""

import io

from rich.console import Console

from token_sprout import ascii_art, game, ui
from token_sprout.state import default_state


def _render(view) -> str:
    console = Console(file=io.StringIO(), width=80, force_terminal=False)
    console.print(view)
    return console.file.getvalue()


def test_pile_repeats_the_stage_emoji():
    assert ascii_art.pile("seed", 0) == ""
    assert ascii_art.pile("seed", 4) == "🌰🌰🌰🌰"
    assert ascii_art.pile("sprout", 1) == "🌱"
    assert ascii_art.pile("leaf", 3) == "🪴🪴🪴"


def test_unknown_stage_falls_back_to_seed_glyph():
    assert ascii_art.stage_glyph("???") == ascii_art.stage_glyph("seed")


def test_build_view_renders_each_growth_stage():
    for progress, stage in zip(game.STAGE_UNIT_COSTS, game.GROWTH_STAGES):
        s = default_state()
        s["current_exp"] = progress
        out = _render(ui.build_view(s, frame=0))
        assert "Token Sprout" in out
        assert stage in out
        assert ascii_art.stage_glyph(stage) in out


def test_build_view_bloom():
    s = default_state()
    s["current_exp"] = game.BLOOM_PROGRESS
    out = _render(ui.build_view(s, frame=0))
    assert "in bloom" in out
    assert "generation 2" in out  # next generation hint


def test_thinking_state_shows_animation_line():
    s = default_state()
    s["active_requests"] = 2
    out = _render(ui.build_view(s, frame=1))
    assert "Thinking" in out
    assert "2 requests in flight" in out


def test_idle_state_and_notice():
    s = default_state()
    out = _render(ui.build_view(s, frame=0, notice="✨ Grew into sprout!"))
    assert "Idle" in out
    assert "Grew into sprout" in out


def test_transition_notice_stage_up():
    prev = default_state()  # seed ×0
    cur = default_state()
    cur["current_exp"] = game.STAGE_UNIT_COSTS[1]  # sprout ×1
    cur["level"] = 2
    assert "Grew into sprout" in ui._transition_notice(prev, cur)


def test_transition_notice_bloom_and_generation():
    prev = default_state()
    bloom = default_state()
    bloom["current_exp"] = game.BLOOM_PROGRESS
    bloom["level"] = game.MAX_LEVEL
    assert "bloom" in ui._transition_notice(prev, bloom).lower()

    gen2 = default_state()
    gen2["generation"] = 2
    assert "Generation 2" in ui._transition_notice(prev, gen2)

    assert ui._transition_notice(prev, default_state()) is None
