"""Behavior tests: classifier, route cards, tool handler, install-routes merge."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

import tools

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------ table

def test_routes_yaml_loads_and_has_unique_lanes():
    lanes = tools._load_routes()
    ids = [l["id"] for l in lanes]
    assert len(ids) == len(set(ids))
    assert len(lanes) >= 8
    for lane in lanes:
        assert lane["model"] and lane["effort"] and lane["label"]


def test_provenance_not_truncated_by_yaml_comment():
    # An unquoted provenance containing '#' gets truncated at the hash (parsed
    # as a comment). Regressed live in the prose row; keep every row whole.
    raw = yaml.safe_load((ROOT / "data" / "routes.yaml").read_text(encoding="utf-8"))
    for lane in raw["lanes"]:
        prov = lane.get("provenance", "")
        assert prov and not prov.rstrip().endswith("creative writing"), lane["id"]
        if "#" in prov:
            assert prov.count("#") >= 1 and not prov.endswith("writing"), lane["id"]



# -------------------------------------------------------------- classifier

@pytest.mark.parametrize("task,expected", [
    ("fix the NaN loss in our GRPO run, the loss curve spikes at step 2k", "dl-ml-research-engineering"),
    ("prove the number of labeled trees on 7 vertices; full derivation", "math-first-principles"),
    ("write a blog post about our launch", "prose"),
    ("refactor the auth middleware and add unit tests", "routine-coding"),
    ("migrate the monolith to the new schema, it's a multi-file repo-wide change", "hard-agentic-coding"),
    ("web research: who are the competitors in the eval tooling space, with citations", "web-research"),
    ("read this 80-page PDF spec and summarize the safety requirements", "long-doc-reading"),
    ("critique the paper's claim about reward hacking and Goodhart effects", "alignment-reasoning"),
    ("orchestrate three parallel agents to review the PR", "orchestration"),
])
def test_classify_lanes(task, expected):
    lane, conf, hits = tools.classify(task)
    assert lane["id"] == expected, f"got {lane['id']} (hits={hits})"
    assert conf > 0
    assert hits


def test_classify_no_hit_defaults_to_long_doc():
    lane, conf, hits = tools.classify("hello world")
    assert lane["id"] == "long-doc-reading"
    assert conf == 0.0 and hits == []


def test_classify_math_beats_prose_on_dual_signal():
    # "proof" (math) co-occurs with "blog post" (prose); math wins on stronger signal.
    lane, _, _ = tools.classify("write a blog post with a full proof of the theorem")
    assert lane["id"] == "math-first-principles"


# --------------------------------------------------------------- tool api

def test_tool_returns_json_with_card():
    out = json.loads(tools.evalroute_route({"task": "prove the Riemann hypothesis"}))
    assert out["lane"] == "math-first-principles"
    assert out["model"] == "qwen/qwen3.8-max"
    assert "route:" in out["card"] and "/model" in out["card"]
    assert out["provenance"].startswith("priors")


def test_tool_pinned_lane_skips_classification():
    out = json.loads(tools.evalroute_route({"task": "whatever", "lane": "prose"}))
    assert out["lane"] == "prose" and out["pinned"] is True


def test_tool_unknown_lane_is_actionable_error():
    out = json.loads(tools.evalroute_route({"task": "x", "lane": "nope"}))
    assert "known lanes" in out["error"]


def test_tool_empty_task_is_error():
    out = json.loads(tools.evalroute_route({"task": " "}))
    assert "error" in out


def test_tool_never_raises_on_missing_args():
    out = json.loads(tools.evalroute_route({}))
    assert "error" in out


# ------------------------------------------------------------ slash/CLI path

def test_route_command_happy_path():
    card = tools.handle_route_command("fix the NaN loss in our GRPO run")
    assert "lane: DL / ML research engineering" in card
    assert "/model z-ai/glm-5.3" in card


def test_route_command_lane_pinned():
    card = tools.handle_route_command("--lane prose draft something")
    assert "classification: lane pinned by caller" in card


def test_route_command_usage_on_empty():
    out = tools.handle_route_command("")
    assert out.startswith("usage: /route")


def test_route_command_error_is_string():
    out = tools.handle_route_command("--lane nope x")
    assert out.startswith("evalroute:")


# ---------------------------------------------------------- install-routes

def test_effort_extraction_prefers_higher_end_of_range():
    # "max / high" style entries and one model serving two lanes keep the higher.
    lane = {"model": "x", "effort": "Opus high, subagents low-medium"}
    assert tools._effort_for_override(lane) == "high"
    assert tools._effort_for_override({"model": "x", "effort": "low-medium"}) == "medium"
    assert tools._effort_for_override({"model": "x", "effort": "high-max"}) == "max"


def _fake_config_module(monkeypatch, fake_config, written=None, fail_on_write=False):
    """Point tools._config_module at a stub instead of importing hermes_cli."""
    class _Stub:
        @staticmethod
        def load_config():
            return fake_config

        @staticmethod
        def set_config_value(key, value):
            if fail_on_write:
                raise AssertionError("must not write")
            if written is not None:
                written[key] = value

    monkeypatch.setattr(tools, "_config_module", lambda: (_Stub.load_config, _Stub.set_config_value))


def test_install_routes_merge_logic(monkeypatch, capsys):
    """Merge keeps foreign keys, route keys win, higher effort wins per model."""
    fake_config = {"agent": {"reasoning_overrides": {
        "z-ai/glm-5.3": "medium",          # table wants max (serves hard-agentic max + dl-ml high)
        "someone/other-model": "low",      # not in route table -> kept
    }}}
    written = {}
    _fake_config_module(monkeypatch, fake_config, written)
    rc = tools.install_routes(dry_run=False)
    assert rc == 0
    assert "agent.reasoning_overrides" in written
    merged = json.loads(written["agent.reasoning_overrides"])
    assert merged["z-ai/glm-5.3"] == "max"            # higher of the two lanes it serves
    assert merged["someone/other-model"] == "low"      # foreign key kept
    assert merged["moonshotai/kimi-k3"] == "medium"
    assert merged["qwen/qwen3.8-max"] == "max"
    out = capsys.readouterr().out
    assert "~ z-ai/glm-5.3: medium -> max" in out
    assert "= someone/other-model: low (kept" in out


def test_install_routes_dry_run_writes_nothing(monkeypatch, capsys):
    fake_config = {"agent": {"reasoning_overrides": {}}}
    _fake_config_module(monkeypatch, fake_config, fail_on_write=True)
    rc = tools.install_routes(dry_run=True)
    assert rc == 0
    assert "dry run: nothing written" in capsys.readouterr().out


def test_install_routes_noop_when_matching(monkeypatch, capsys):
    desired = {}
    for lane in tools._load_routes():
        e = tools._effort_for_override(lane)
        cur = desired.get(lane["model"])
        if cur is None or tools._EFFORT_ORDER.index(e) > tools._EFFORT_ORDER.index(cur):
            desired[lane["model"]] = e
    fake_config = {"agent": {"reasoning_overrides": desired}}
    _fake_config_module(monkeypatch, fake_config, fail_on_write=True)
    rc = tools.install_routes(dry_run=False)
    assert rc == 0
    assert "nothing to do" in capsys.readouterr().out
