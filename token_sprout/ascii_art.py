"""Per-stage plant glyphs.

The plant is drawn with a single emoji per stage, repeated to show how many
units have piled up in the current stage (seed ×4 = 🌰🌰🌰🌰). No large
ASCII art — the plant lives inline in the Claude Code status line and the
`watch` panel.
"""

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
