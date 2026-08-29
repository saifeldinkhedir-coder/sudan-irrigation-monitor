"""
Shared fixtures.

`ee_env` injects the deterministic mock Earth Engine backend (src/mock_ee.py) in
place of the real `ee`, rebinds the modules that import it, and tears the whole
thing back down afterwards so no other test file inherits the mock. This is what
lets the Earth-Engine-facing assembly run offline in a test without polluting the
pure-logic tests.
"""

import importlib
import os
import sys

import pytest

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)


@pytest.fixture
def ee_env():
    import mock_ee
    saved = sys.modules.get("ee")
    sys.modules["ee"] = mock_ee
    os.environ["EE_PROJECT"] = "mock-project"

    import nutrition_climate_ground
    import engine
    import attribution
    importlib.reload(nutrition_climate_ground)
    importlib.reload(engine)
    importlib.reload(attribution)

    try:
        yield mock_ee
    finally:
        if saved is None:
            sys.modules.pop("ee", None)
        else:
            sys.modules["ee"] = saved
        importlib.reload(nutrition_climate_ground)
        importlib.reload(engine)
        importlib.reload(attribution)
