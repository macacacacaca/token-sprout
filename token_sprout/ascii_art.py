"""Per-stage plant glyphs and the display strings every view shares.

The plant is drawn with a single emoji per stage, repeated to show how many
units have piled up in the current stage (seed ×4 = 🌰🌰🌰🌰). No large
ASCII art — the plant lives inline in the Claude Code status line, the
`watch` panel, and the editor feed (`watch --json`).
"""

from __future__ import annotations

from . import game

STAGE_EMOJI = {
    "seed": "🌰",
    "sprout": "🌱",
    "leaf": "🪴",
    "bud": "🌷",
    "bloom": "🌸",
}


def stage_glyph(stage: str) -> str:
    """Return the emoji for ``stage``, falling back to the seed glyph."""
    return STAGE_EMOJI.get(stage, STAGE_EMOJI["seed"])


def pile(stage: str, count: int, sep: str = "") -> str:
    """The current stage repeated ``count`` times — the visible pile."""
    return sep.join([stage_glyph(stage)] * max(0, count))


def growth_line(view: dict) -> str:
    """Shared countdown line: tokens to the next unit and to bloom."""
    return (
        f"{view['to_next_unit']:,} tokens to the next {stage_glyph(view['stage'])}"
        f" · {view['to_bloom']:,} to bloom"
    )


def transition(prev: dict, cur: dict) -> dict | None:
    """The stage-up / bloom / new-generation event between two states."""
    if cur["generation"] > prev["generation"]:
        return {"kind": "generation", "generation": cur["generation"]}
    prev_view = game.plant_view(prev)
    cur_view = game.plant_view(cur)
    if cur_view["is_bloom"] and not prev_view["is_bloom"]:
        return {"kind": "bloom"}
    if cur["generation"] == prev["generation"] and cur_view["level"] > prev_view["level"]:
        return {"kind": "stage_up", "stage": cur_view["stage"]}
    return None


def describe_transition(event: dict | None) -> str | None:
    """One-line notice text for a ``transition()`` event."""
    if event is None:
        return None
    bloom_glyph = stage_glyph("bloom")
    if event["kind"] == "generation":
        return f"{bloom_glyph} Bloomed! Generation {event['generation']} begins..."
    if event["kind"] == "bloom":
        return f"{bloom_glyph} In bloom!"
    return f"✨ Grew into {event['stage']}!"


def transition_notice(prev: dict, cur: dict) -> str | None:
    """One-line stage-up / bloom / new-generation notice between two states."""
    return describe_transition(transition(prev, cur))
