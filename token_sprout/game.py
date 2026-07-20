"""token → growth units → stage / generation.

Growth model (spec §5.4): the plant grows through 20:1 hierarchical merges.
Every ``TOKENS_PER_UNIT`` of food creates one seed; collecting
``UNITS_PER_STAGE`` units merges them into one unit of the next stage
(seed ×20 → sprout ×1, sprout ×20 → leaf ×1, and so on). Food stays
cumulative for the whole generation and resets only after bloom, when the
next feeding starts a new generation.

Food policy: cache_read_input_tokens is tracked and displayed but does NOT
feed the plant — Claude Code's cache reads can reach millions of tokens a
day and would trivialize the curve.

Pure functions over the state dict — no I/O here, so everything is unit
testable without touching the filesystem.
"""

from __future__ import annotations

from typing import Any

from .usage_parser import Usage

# Growth knobs (user-chosen: 20:1 merges, reset after bloom).
TOKENS_PER_UNIT = 10_000  # food tokens for one seed
UNITS_PER_STAGE = 20      # current-stage units merged into one next-stage unit

# Stages that accumulate units and merge upward, in order, then the terminal
# bloom. seed ×20 → sprout ×1; sprout ×20 → leaf ×1; and so on.
GROWTH_STAGES = ["seed", "sprout", "leaf", "bud"]
BLOOM = "bloom"
STAGES = GROWTH_STAGES + [BLOOM]
MAX_LEVEL = len(STAGES)  # 5

# Food cost of one unit at each stage: 10k, 200k, 4m, 80m.
STAGE_UNIT_COSTS = tuple(
    TOKENS_PER_UNIT * UNITS_PER_STAGE**stage_index
    for stage_index in range(len(GROWTH_STAGES))
)

# Food tokens to bloom: 10,000 × 20⁴ = 1,600,000,000.
BLOOM_PROGRESS = STAGE_UNIT_COSTS[-1] * UNITS_PER_STAGE


def exp_from_usage(usage: Usage) -> int:
    """Food policy: input + output + cache_creation. cache_read excluded."""
    return (
        usage.input_tokens
        + usage.output_tokens
        + usage.cache_creation_input_tokens
    )


def _resolve(progress: int) -> tuple[str, int, int | None]:
    """Map cumulative food (this generation) to (stage, level, count).

    ``count`` is the number of current-stage units already merged (0..19),
    or None at bloom. Zero occurs only before the first seed is earned.
    """
    if progress >= BLOOM_PROGRESS:
        return BLOOM, MAX_LEVEL, None

    stage_index = 0
    for candidate in range(1, len(GROWTH_STAGES)):
        if progress < STAGE_UNIT_COSTS[candidate]:
            break
        stage_index = candidate

    count = int(progress // STAGE_UNIT_COSTS[stage_index])
    return GROWTH_STAGES[stage_index], stage_index + 1, count


def plant_view(state: dict[str, Any], pending_exp: int = 0) -> dict[str, Any]:
    """Everything the UI needs, optionally previewing estimated live food.

    ``pending_exp`` is display-only: it projects the same generation rollover
    and growth that a later ``absorb`` would perform without mutating state.
    Settled views pass zero and remain authoritative.
    """
    progress = state["current_exp"]
    generation = state["generation"]
    if isinstance(pending_exp, bool) or not isinstance(pending_exp, int):
        pending_exp = 0
    pending_exp = max(0, pending_exp)
    if pending_exp and progress >= BLOOM_PROGRESS:
        generation += 1
        progress = 0
    progress += pending_exp

    stage, level, count = _resolve(progress)
    is_bloom = stage == BLOOM
    if is_bloom:
        to_next_stage = 0
        to_next_unit = 0
        unit_progress_percent = 100
    else:
        stage_index = level - 1
        unit_cost = STAGE_UNIT_COSTS[stage_index]
        next_stage_start = unit_cost * UNITS_PER_STAGE
        to_next_stage = next_stage_start - progress
        unit_remainder = progress % unit_cost
        to_next_unit = unit_cost - unit_remainder
        unit_progress_percent = min(
            99,
            (unit_remainder * 100 + unit_cost // 2) // unit_cost,
        )
    return {
        "generation": generation,
        "stage": stage,
        "level": level,
        "count": count,
        "units_per_stage": UNITS_PER_STAGE,
        "is_bloom": is_bloom,
        "unit_progress_percent": unit_progress_percent,
        "to_next_unit": to_next_unit,
        "to_next_stage": to_next_stage,
        "to_bloom": max(0, BLOOM_PROGRESS - progress),
    }


def absorb(state: dict[str, Any], usage: Usage) -> None:
    """Feed one request's usage into the plant. Mutates ``state`` in place.

    A plant already in bloom starts the next generation on its next *real*
    feeding: generation +1, progress reset, lifetime totals kept. A zero-food
    settlement (a response whose usage parsed to all zeros, or cache_read
    only) must not roll the generation — same guard as plant_view's preview.
    """
    food = exp_from_usage(usage)
    if food and state["current_exp"] >= BLOOM_PROGRESS:
        state["generation"] += 1
        state["current_exp"] = 0

    state["total_input_tokens"] += usage.input_tokens
    state["total_output_tokens"] += usage.output_tokens
    state["total_cache_creation_tokens"] += usage.cache_creation_input_tokens
    state["total_cache_read_tokens"] += usage.cache_read_input_tokens
    state["total_tokens"] += usage.grand_total

    state["last_input_tokens"] = usage.input_tokens
    state["last_output_tokens"] = usage.output_tokens
    state["last_request_tokens"] = usage.grand_total

    state["current_exp"] += food
    stage, level, _ = _resolve(state["current_exp"])
    state["stage"] = stage
    state["level"] = level
