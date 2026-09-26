"""Contract tests: what plugin.yaml declares vs what register() actually does."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_plugin_package():
    spec = importlib.util.spec_from_file_location(
        "evalroute_plugin_under_test", ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeContext:
    def __init__(self):
        self.tools: dict[str, dict] = {}
        self.skills: dict[str, Path] = {}
        self.hooks: dict[str, list] = {}
        self.commands: dict[str, dict] = {}
        self.cli_commands: dict[str, dict] = {}

    def register_tool(self, name, toolset=None, schema=None, handler=None, **_):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler}

    def register_skill(self, name, path, **_):
        self.skills[name] = Path(path)

    def register_hook(self, name, callback, **_):
        self.hooks.setdefault(name, []).append(callback)

    def register_command(self, name, handler, description="", args_hint="", **_):
        self.commands[name] = {"handler": handler, "description": description}

    def register_cli_command(self, name, help="", setup_fn=None, handler_fn=None,
                             description="", **_):
        self.cli_commands[name] = {"setup_fn": setup_fn, "handler_fn": handler_fn}


@pytest.fixture(scope="module")
def manifest() -> dict:
    return yaml.safe_load((ROOT / "plugin.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ctx() -> FakeContext:
    context = FakeContext()
    load_plugin_package().register(context)
    return context


def test_declared_tools_match_registered(manifest, ctx):
    assert sorted(manifest["provides_tools"]) == sorted(ctx.tools)


def test_declared_hooks_match_registered(manifest, ctx):
    assert sorted(manifest.get("provides_hooks", [])) == sorted(ctx.hooks)


def test_schema_names_match_tool_names(ctx):
    for name, entry in ctx.tools.items():
        assert entry["schema"]["name"] == name
        assert entry["toolset"] == "evalroute"
        assert callable(entry["handler"])
        for req in entry["schema"]["parameters"].get("required", []):
            assert req in entry["schema"]["parameters"]["properties"]


def test_bundled_skill_registered(ctx):
    assert "evalroute-routing" in ctx.skills
    text = ctx.skills["evalroute-routing"].read_text(encoding="utf-8")
    assert text.startswith("---") and "name: evalroute-routing" in text


def test_route_slash_command_registered(ctx):
    assert "route" in ctx.commands
    assert ctx.commands["route"]["handler"].__module__.endswith("tools")


def test_cli_command_registered(ctx):
    assert "evalroute" in ctx.cli_commands
    assert callable(ctx.cli_commands["evalroute"]["setup_fn"])
    assert callable(ctx.cli_commands["evalroute"]["handler_fn"])
