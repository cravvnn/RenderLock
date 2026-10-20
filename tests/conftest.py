"""Exercise developer backend features independently of a staged beta profile."""
import os
from pathlib import Path
import tempfile

import pytest


def pytest_configure(config):
    """Keep both fixture files and subprocess temporary files in this checkout."""
    root = Path(__file__).resolve().parents[1] / '.pytest_tmp'
    process_temp = root / 'process-temp'
    process_temp.mkdir(parents=True, exist_ok=True)
    os.environ['TEMP'] = os.environ['TMP'] = str(process_temp)
    tempfile.tempdir = str(process_temp)
    if config.option.basetemp is None:
        config.option.basetemp = str(root / 'fixtures')


@pytest.fixture(autouse=True)
def development_features(monkeypatch):
    from agr import release_profile
    monkeypatch.setattr(release_profile, "character_beta", lambda: False)
