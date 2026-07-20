from token_sprout import game
from token_sprout.state import default_state
from token_sprout.usage_parser import Usage

PER_UNIT = game.TOKENS_PER_UNIT  # 10,000
PER_STAGE = game.UNITS_PER_STAGE  # 20
SEED_COST, SPROUT_COST, LEAF_COST, BUD_COST = game.STAGE_UNIT_COSTS


def test_food_policy_excludes_cache_read():
    usage = Usage(10, 20, 5, 1_000_000)
    assert game.exp_from_usage(usage) == 35


def test_absorb_accumulates_totals_and_last_request():
    s = default_state()
    game.absorb(s, Usage(10, 20, 5, 1000))
    assert s["total_input_tokens"] == 10
    assert s["total_output_tokens"] == 20
    assert s["total_cache_creation_tokens"] == 5
    assert s["total_cache_read_tokens"] == 1000
    assert s["total_tokens"] == 1035  # grand total across all categories
    assert s["last_request_tokens"] == 1035
    assert s["current_exp"] == 35  # cache_read excluded from food


def test_fresh_generation_has_no_earned_seed_yet():
    view = game.plant_view(default_state())
    assert view["stage"] == "seed"
    assert view["count"] == 0
    assert view["is_bloom"] is False


def test_first_seed_costs_ten_thousand_food_tokens():
    s = default_state()
    s["current_exp"] = PER_UNIT
    view = game.plant_view(s)
    assert view["stage"] == "seed"
    assert view["count"] == 1


def test_pending_food_previews_growth_without_mutating_state():
    s = default_state()
    # Settled at sprout ×2 plus 129k toward the next sprout. The 5k live
    # estimate previews 134k/200k, which is 67%.
    s["current_exp"] = SPROUT_COST * 2 + 129_000

    view = game.plant_view(s, pending_exp=5_000)

    assert view["stage"] == "sprout"
    assert view["count"] == 2
    assert view["unit_progress_percent"] == 67
    assert s["current_exp"] == SPROUT_COST * 2 + 129_000


def test_pending_food_after_bloom_previews_next_generation():
    s = default_state()
    s["current_exp"] = game.BLOOM_PROGRESS

    view = game.plant_view(s, pending_exp=PER_UNIT)

    assert view["generation"] == 2
    assert view["stage"] == "seed"
    assert view["count"] == 1
    assert s["generation"] == 1


def test_units_pile_up_within_a_stage():
    s = default_state()
    # each PER_UNIT of food adds one seed
    game.absorb(s, Usage(PER_UNIT * 3, 0, 0, 0))
    view = game.plant_view(s)
    assert view["stage"] == "seed"
    assert view["count"] == 3


def test_twenty_units_advance_into_next_stage():
    s = default_state()
    game.absorb(s, Usage(PER_UNIT * PER_STAGE, 0, 0, 0))  # 20 units -> sprout ×1
    view = game.plant_view(s)
    assert view["stage"] == "sprout"
    assert view["count"] == 1
    assert s["level"] == 2


def test_stage_progression_across_all_growth_stages():
    s = default_state()
    thresholds = [
        (0, "seed"),
        (SPROUT_COST - 1, "seed"),
        (SPROUT_COST, "sprout"),
        (LEAF_COST - 1, "sprout"),
        (LEAF_COST, "leaf"),
        (BUD_COST - 1, "leaf"),
        (BUD_COST, "bud"),
        (game.BLOOM_PROGRESS - 1, "bud"),
    ]
    for progress, expected in thresholds:
        s["current_exp"] = progress
        assert game.plant_view(s)["stage"] == expected


def test_bloom_at_full_progress():
    s = default_state()
    s["current_exp"] = game.BLOOM_PROGRESS  # exactly 10,000 * 20**4
    view = game.plant_view(s)
    assert view["is_bloom"] is True
    assert view["stage"] == "bloom"
    assert view["count"] is None
    assert view["to_bloom"] == 0


def test_stage_unit_costs_compound_twenty_to_one():
    assert game.STAGE_UNIT_COSTS == (10_000, 200_000, 4_000_000, 80_000_000)
    assert game.BLOOM_PROGRESS == 1_600_000_000


def test_one_big_feeding_can_jump_multiple_stages():
    s = default_state()
    game.absorb(s, Usage(0, LEAF_COST * 6, 0, 0))
    view = game.plant_view(s)
    assert view["stage"] == "leaf"
    assert view["count"] == 6


def test_bloom_persists_until_next_feeding_then_new_generation():
    s = default_state()
    s["current_exp"] = game.BLOOM_PROGRESS
    s["level"] = game.MAX_LEVEL
    s["stage"] = "bloom"
    s["total_tokens"] = 500_000

    # feeding while already in bloom rolls the generation and resets progress
    game.absorb(s, Usage(PER_UNIT, 0, 0, 0))

    assert s["generation"] == 2
    view = game.plant_view(s)
    assert view["stage"] == "seed"
    assert view["count"] == 1  # the feeding lands in the new generation
    assert s["total_tokens"] == 510_000  # lifetime totals kept


def test_to_next_unit_and_stage_countdowns():
    s = default_state()
    s["current_exp"] = PER_UNIT + 500  # 1 full unit + partway into the 2nd
    view = game.plant_view(s)
    assert view["to_next_unit"] == PER_UNIT - 500
    # seed stage ends at unit 20 -> 20*10,000 = 200,000
    assert view["to_next_stage"] == 200_000 - (PER_UNIT + 500)


def test_zero_food_settlement_does_not_roll_generation():
    s = default_state()
    s["current_exp"] = game.BLOOM_PROGRESS
    # A settled response whose usage parsed to all zeros (or cache_read only,
    # which never feeds) must not consume the bloom — same guard as
    # plant_view's pending_exp preview.
    game.absorb(s, Usage(0, 0, 0, 500))
    assert s["generation"] == 1
    assert game.plant_view(s)["is_bloom"] is True
    assert s["total_cache_read_tokens"] == 500
