"""Exercise developer backend features independently of a staged beta profile."""
import pytest


@pytest.fixture(autouse=True)
def development_features(monkeypatch):
    from agr import release_profile
    monkeypatch.setattr(release_profile, "character_beta", lambda: False)
