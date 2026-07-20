"""token-sprout watch — the plant terminal panel.

Read-only consumer of plant_state.json (spec §5.2: the UI never takes the
lock and never writes). The plant is shown as a pile of stage emoji (one per
current-stage unit already merged); no large ASCII art. Stage-ups and blooms show a
one-line notice — no effects.
"""

from __future__ import annotations

import time

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from . import ascii_art, game, state

MIN_WIDTH = 44
MIN_HEIGHT = 12
FPS = 5  # animation tick == state poll rate (spec §5.3: 4–10 Hz)
NOTICE_SECONDS = 4.0

# Two-line droplet blocks, cycled while requests are in flight.
DROPLET_FRAMES = [
    ("      💧            ", "            💧      "),
    ("          💧        ", "     💧         💧  "),
    ("    💧        💧    ", "         💧         "),
    ("         💧         ", "   💧          💧   "),
]
_IDLE_BLOCK = ("", "")


def _pile_line(view: dict) -> str:
    """Stage emoji laid out one-per-unit, space-separated so they're easy to
    count."""
    if view["is_bloom"]:
        return ascii_art.stage_glyph("bloom")
    return ascii_art.pile(view["stage"], view["count"], sep=" ")


def build_view(s: dict, frame: int, notice: str | None = None) -> Panel:
    thinking = s["active_requests"] > 0
    view = game.plant_view(s)
    drops = DROPLET_FRAMES[frame % len(DROPLET_FRAMES)] if thinking else _IDLE_BLOCK

    body: list[Text] = []
    for line in drops:
        body.append(Text(line, style="bright_blue"))
    body.append(Text(_pile_line(view), style="green"))
    body.append(Text())

    if view["is_bloom"]:
        body.append(Text(f"{ascii_art.stage_glyph('bloom')} in bloom", style="bold magenta"))
        body.append(Text(f"Next tokens start generation {view['generation'] + 1}", style="dim"))
    else:
        body.append(
            Text(f"{view['stage']}  ·  {view['count']}/{view['units_per_stage']}")
        )
        body.append(Text(ascii_art.growth_line(view), style="dim"))
    body.append(Text())

    if thinking:
        n = s["active_requests"]
        body.append(
            Text(f"Thinking... ({n} request{'s' if n > 1 else ''} in flight)", style="bold cyan")
        )
    else:
        body.append(Text("Idle — waiting for tokens", style="dim"))
    body.append(Text(f"Total: {s['total_tokens']:,} tokens", style="dim"))

    if notice:
        body.append(Text())
        body.append(Text(notice, style="bold yellow"))

    return Panel(
        Group(*body),
        title=f"Token Sprout 🌿  generation {view['generation']}",
        subtitle="Ctrl+C to exit",
        width=MIN_WIDTH,
    )


def _too_small(size) -> bool:
    return size.width < MIN_WIDTH or size.height < MIN_HEIGHT


def _transition_notice(prev: dict, cur: dict) -> str | None:
    bloom_glyph = ascii_art.stage_glyph("bloom")
    if cur["generation"] > prev["generation"]:
        return f"{bloom_glyph} Bloomed! Generation {cur['generation']} begins..."
    prev_view = game.plant_view(prev)
    cur_view = game.plant_view(cur)
    if cur_view["is_bloom"] and not prev_view["is_bloom"]:
        return f"{bloom_glyph} In bloom!"
    if cur["generation"] == prev["generation"] and cur_view["level"] > prev_view["level"]:
        return f"✨ Grew into {cur_view['stage']}!"
    return None


def watch() -> int:
    console = Console()
    prev = state.load_state()
    notice: str | None = None
    notice_until = 0.0
    frame = 0
    try:
        with Live(console=console, refresh_per_second=FPS, transient=False) as live:
            while True:
                # A damaged read keeps showing the previous good frame
                # instead of snapping to a fresh seed (spec §5.2).
                loaded = state.try_load_state()
                s = loaded if loaded is not None else prev
                now = time.monotonic()
                new_notice = _transition_notice(prev, s)
                if new_notice:
                    notice = new_notice
                    notice_until = now + NOTICE_SECONDS
                prev = s

                if _too_small(console.size):
                    live.update(
                        Text(f"Terminal too small — need at least {MIN_WIDTH}x{MIN_HEIGHT}")
                    )
                else:
                    live.update(build_view(s, frame, notice if now < notice_until else None))
                frame += 1
                time.sleep(1 / FPS)
    except KeyboardInterrupt:
        return 0
