import json
import os
import stat
from pathlib import Path

import pytest

from token_sprout import game, state
from token_sprout.usage_parser import Usage


def test_init_creates_default_state(sprout_home):
    path = state.init_home()
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["level"] == 1
    assert data["stage"] == "seed"
    assert data["generation"] == 1
    assert data["active_requests"] == 0


@pytest.mark.skipif(os.name == "nt", reason="POSIX file permissions")
def test_private_files_and_directory_are_0700_and_0600(sprout_home):
    state.init_home()
    state.ensure_secret()
    with state.open_proxy_log() as log:
        log.write(b"safe log line\n")
    state.update_state(lambda data: data.__setitem__("total_tokens", 1))

    assert stat.S_IMODE(state.home_dir().stat().st_mode) == 0o700
    for path in (
        state.state_path(),
        state.secret_path(),
        state.proxy_log_path(),
        state._lock_path(),
    ):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX file permissions")
def test_init_repairs_permissions_from_older_install(sprout_home):
    state.home_dir().mkdir(mode=0o755)
    for filename in ("plant_state.json", "proxy.secret", "proxy.log", "state.lock"):
        path = state.home_dir() / filename
        path.write_text("{}" if filename == "plant_state.json" else "legacy")
        path.chmod(0o644)

    state.init_home()

    assert stat.S_IMODE(state.home_dir().stat().st_mode) == 0o700
    for filename in ("plant_state.json", "proxy.secret", "proxy.log", "state.lock"):
        assert stat.S_IMODE((state.home_dir() / filename).stat().st_mode) == 0o600


def test_init_is_idempotent(sprout_home):
    state.init_home()
    state.update_state(lambda s: s.__setitem__("total_tokens", 42))
    state.init_home()  # must not overwrite existing state
    assert state.load_state()["total_tokens"] == 42


def test_load_missing_file_returns_default(sprout_home):
    assert state.load_state()["level"] == 1


def test_load_corrupted_file_returns_default(sprout_home):
    state.init_home()
    state.state_path().write_text("{ this is not json")
    assert state.load_state()["level"] == 1


def test_load_ignores_unknown_keys_and_rederives_view(sprout_home):
    state.init_home()
    state.state_path().write_text(json.dumps({"level": 3, "stage": "leaf", "evil": "x"}))
    loaded = state.load_state()
    assert loaded["level"] == 1
    assert loaded["stage"] == "seed"
    assert "evil" not in loaded


def test_load_normalizes_valid_json_with_bad_field_types(sprout_home):
    state.init_home()
    state.state_path().write_text(
        json.dumps(
            {
                "version": "0.0.1",
                "generation": 0,
                "current_exp": "oops",
                "active_requests": -3,
                "total_tokens": True,
                "last_request_started_at": 123,
            }
        )
    )
    loaded = state.load_state()
    assert loaded["version"] == state.STATE_VERSION
    assert loaded["generation"] == 1
    assert loaded["current_exp"] == 0
    assert loaded["active_requests"] == 0
    assert loaded["total_tokens"] == 0
    assert loaded["last_request_started_at"] is None
    assert game.plant_view(loaded)["stage"] == "seed"


def test_state_lock_timeout_is_bounded(sprout_home, monkeypatch):
    from filelock import FileLock

    state.init_home()
    monkeypatch.setattr(state, "STATE_LOCK_TIMEOUT_SECONDS", 0.01)
    with FileLock(str(state._lock_path())):
        with pytest.raises(state.StateBusyError):
            state.request_started()


def test_example_state_matches_derived_game_view():
    example_path = Path(__file__).parents[1] / "examples" / "plant_state.example.json"
    example = json.loads(example_path.read_text())
    view = game.plant_view(example)
    assert example["stage"] == view["stage"]
    assert example["level"] == view["level"]
    assert example["current_exp"] == (
        example["total_input_tokens"]
        + example["total_output_tokens"]
        + example["total_cache_creation_tokens"]
    )


def test_request_lifecycle(sprout_home):
    state.init_home()
    state.request_started()
    mid = state.load_state()
    assert mid["active_requests"] == 1
    assert mid["last_request_started_at"] is not None

    state.request_finished(Usage(10, 5, 2, 30))
    done = state.load_state()
    assert done["active_requests"] == 0
    assert done["last_request_finished_at"] is not None
    assert done["total_tokens"] == 47
    assert done["current_exp"] == 17  # 10 + 5 + 2, cache_read excluded


def test_request_finished_without_usage_only_decrements(sprout_home):
    state.init_home()
    state.request_started()
    state.request_finished(None)
    done = state.load_state()
    assert done["active_requests"] == 0
    assert done["total_tokens"] == 0


def test_active_requests_never_negative(sprout_home):
    state.init_home()
    state.request_finished(None)
    assert state.load_state()["active_requests"] == 0


def test_clear_active_requests(sprout_home):
    state.init_home()
    state.request_started()
    state.request_started()
    state.clear_active_requests()
    assert state.load_state()["active_requests"] == 0


def test_reset(sprout_home):
    state.init_home()
    state.request_started()
    state.request_finished(Usage(100, 100, 0, 0))
    state.reset()
    s = state.load_state()
    assert s["total_tokens"] == 0
    assert s["level"] == 1
    assert s["generation"] == 1


def test_try_load_state_signals_damage_instead_of_defaulting(sprout_home):
    assert state.try_load_state() is None  # missing file
    state.init_home()
    assert state.try_load_state() is not None
    state.state_path().write_text("[]")  # valid JSON, wrong shape
    assert state.try_load_state() is None


def test_update_state_quarantines_corrupt_file_before_rebuilding(sprout_home):
    state.init_home()
    state.state_path().write_text("{ this is not json")

    state.request_started()

    corrupt = state.state_path().with_name("plant_state.json.corrupt")
    assert corrupt.read_text() == "{ this is not json"  # progress recoverable
    s = state.load_state()
    assert s["active_requests"] == 1  # rebuilt from defaults, then mutated
    if os.name == "posix":
        assert corrupt.stat().st_mode & 0o777 == 0o600


def test_update_state_preserves_corrupt_file_when_quarantine_fails(sprout_home):
    state.init_home()
    original = "{ damaged but valuable progress"
    state.state_path().write_text(original)
    corrupt = state.state_path().with_name("plant_state.json.corrupt")
    corrupt.mkdir()  # os.replace(file, directory) must fail on every platform

    with pytest.raises(OSError):
        state.request_started()

    assert state.state_path().read_text() == original
    assert corrupt.is_dir()
