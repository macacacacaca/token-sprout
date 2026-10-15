"""token-sprout watch --json — the plant as a line-delimited JSON feed.

Editor integrations (the VS Code extension) spawn this once and read one
JSON object per line. Every growth number comes from ``game.plant_view()``,
so no client re-derives stages or progress.

Strictly read-only (spec §5.2 and §15): it never takes the lock, never
writes, and never creates ``~/.token-sprout/``. A damaged read keeps the
previous good frame, exactly like ``watch``. It imports no rich, starlette,
or httpx, so a long-lived feed stays small.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Callable, TextIO

from . import __version__, ascii_art, game, state

PROTOCOL = 1
POLL_HZ = 5  # same cadence as the watch panel (spec §5.3: 4–10 Hz)


def payload(s: dict[str, Any], prev: dict[str, Any] | None = None) -> dict[str, Any]:
    """The display snapshot of one state, with an optional transition notice.

    While requests are in flight the view previews the live estimate,
    the same read-only preview the Claude Code status line shows.
    """
    thinking = s["active_requests"] > 0
    live = s["live_tokens_estimate"] if thinking else 0
    view = game.plant_view(s, pending_exp=live)
    event = None if prev is None else ascii_art.transition(prev, s)
    return {
        "protocol": PROTOCOL,
        "version": __version__,
        "thinking": thinking,
        "active_requests": s["active_requests"],
        "live_tokens_estimate": live,
        "glyph": ascii_art.stage_glyph(view["stage"]),
        "view": view,
        "totals": {
            "total": s["total_tokens"],
            "input": s["total_input_tokens"],
            "output": s["total_output_tokens"],
            "cache_creation": s["total_cache_creation_tokens"],
            "cache_read": s["total_cache_read_tokens"],
        },
        "last_request": {
            "tokens": s["last_request_tokens"],
            "finished_at": s["last_request_finished_at"],
        },
        # Structured for localizing clients; ``notice`` is the English text.
        "transition": event,
        "notice": ascii_art.describe_transition(event),
    }


def run(
    out: TextIO | None = None,
    *,
    poll_hz: int = POLL_HZ,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Emit a line whenever the snapshot changes; exit when orphaned.

    The first line is written immediately. Lines are ASCII-only JSON
    (emoji escaped), so the reader never depends on this process's locale.
    """
    stream = sys.stdout if out is None else out
    parent = os.getppid()
    prev = state.load_state()
    last_line = None
    try:
        while True:
            loaded = state.try_load_state()
            s = loaded if loaded is not None else prev
            line = json.dumps(payload(s, prev), separators=(",", ":"))
            prev = s
            if line != last_line:
                stream.write(line + "\n")
                stream.flush()
                last_line = line
            if os.getppid() != parent:
                return 0  # the editor that spawned us is gone
            sleep(1 / poll_hz)
    except KeyboardInterrupt:
        return 0
    except BrokenPipeError:
        if out is None:
            # The reader closed the pipe. Point stdout at devnull so the
            # interpreter's final flush cannot raise a second time.
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        return 0
