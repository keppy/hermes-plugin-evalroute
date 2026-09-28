"""Test setup.

The plugin root is `hermes-plugin-evalroute` — not a valid Python identifier —
so a test runner that walks the package tree reaches for the root
``__init__.py`` as a top-level module. Putting the plugin root on ``sys.path``
here (before any test module is imported) is what makes its top-level
``import schemas`` / ``import tools`` fallback resolve.
"""

from __future__ import annotations

import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

@pytest.fixture(autouse=True)
def isolated_hermes_home(tmp_path, monkeypatch):
    """No test may read or write the user's live profile or labels."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import flywheel
    flywheel._MEMORY.clear()
    yield
    flywheel._MEMORY.clear()
