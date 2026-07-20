import pytest


@pytest.fixture(autouse=True)
def sprout_home(tmp_path, monkeypatch):
    """Every test gets an isolated ~/.token-sprout equivalent."""
    home = tmp_path / "sprout-home"
    monkeypatch.setenv("TOKEN_SPROUT_HOME", str(home))
    return home
