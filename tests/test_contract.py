"""Contract between this plugin and the evalroute library it adapts.

The nine contract names must resolve on the installed library, the plugin's
CONTRACT_VERSION must track the library's, and a mismatched library must
refuse to register instead of wiring half-working handlers.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import evalroute
import evalroute.contract as contract
import pytest

ROOT = Path(__file__).resolve().parents[1]

NINE_NAMES = (
    ("routing", "set_llm_facade"),
    ("routing", "evalroute_route"),
    ("routing", "handle_route_command"),
    ("cli", "setup_cli"),
    ("cli", "evalroute_cli"),
    ("flywheel", "handle_rate"),
    ("flywheel", "on_pre_command"),
    ("flywheel", "on_post_llm_call"),
    ("schemas", "EVALROUTE_ROUTE"),
)


def load_plugin_package():
    spec = importlib.util.spec_from_file_location(
        "evalroute_plugin_contract_test", ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_nine_contract_names_resolve():
    import evalroute.cli  # noqa: F401
    import evalroute.flywheel  # noqa: F401
    import evalroute.routing  # noqa: F401
    import evalroute.schemas  # noqa: F401

    for module_name, attr in NINE_NAMES:
        module = getattr(evalroute, module_name)
        obj = getattr(module, attr)
        assert obj is not None, f"evalroute.{module_name}.{attr}"
        if attr == "EVALROUTE_ROUTE":
            assert isinstance(obj, dict)
        else:
            assert callable(obj), f"evalroute.{module_name}.{attr}"


def test_plugin_contract_matches_lib():
    plugin = load_plugin_package()
    assert plugin.PLUGIN_CONTRACT == contract.CONTRACT_VERSION


def test_mismatched_contract_refuses_to_register(monkeypatch):
    plugin = load_plugin_package()
    monkeypatch.setattr(contract, "CONTRACT_VERSION", 2)
    with pytest.raises(RuntimeError) as exc:
        plugin.register(FakeCtx())
    msg = str(exc.value)
    assert ("evalroute library contract 2 != plugin contract 1; "
            "update hermes-plugin-evalroute (installed evalroute "
            f"{evalroute.__version__})") == msg


class FakeCtx:
    def __getattr__(self, name):
        raise AssertionError(f"register() must not touch ctx before the guard: {name}")
