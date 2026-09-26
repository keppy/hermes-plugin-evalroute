"""Tests for the flywheel: route/outcome labeling and observed-provenance aggregation.

All tests point HERMES_HOME at a tmp dir so labels never touch the real
<home>/evalroute/labels.jsonl.
"""

from __future__ import annotations

import json

import pytest

import flywheel
import routes_from_labels as rfl
import tools


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    flywheel._MEMORY.clear()
    yield tmp_path
    flywheel._MEMORY.clear()


def _labels_file(home):
    return home / "evalroute" / "labels.jsonl"


def test_route_label_written(home):
    lane, conf, hits, method = tools.route_for(
        "fix the NaN loss in our GRPO run, loss curve spikes at 2k steps")
    flywheel.note_route("fix the NaN loss", lane, method, conf)
    rec = json.loads(_labels_file(home).read_text().strip())
    assert rec["kind"] == "route"
    assert rec["lane"] == "dl-ml-research-engineering"
    assert rec["model"] == "z-ai/glm-5.3"
    assert rec["method"] == "rules-strong"
    assert rec["ts"] > 0


def test_rate_requires_route(home):
    out = flywheel.handle_rate("pass")
    assert "no route on record" in out


def test_rate_pass_correlates_last_seen_model(home):
    lane, conf, _, method = tools.route_for("write a blog post about our launch")
    flywheel.note_route("blog post", lane, method, conf)
    flywheel.on_post_llm_call(session_id="s1", model="moonshotai/kimi-k3")
    out = flywheel.handle_rate("pass")
    assert out.startswith("logged: pass")
    rec = [json.loads(l) for l in _labels_file(home).read_text().splitlines()][-1]
    assert rec["kind"] == "outcome" and rec["rated"] == "pass"
    assert rec["actual_model"] == "moonshotai/kimi-k3"
    assert rec["route_lane"] == "prose"


def test_rate_consumes_route(home):
    lane, conf, _, method = tools.route_for("write a blog post")
    flywheel.note_route("blog", lane, method, conf)
    flywheel.handle_rate("pass")
    out = flywheel.handle_rate("pass")
    assert "no route on record" in out  # consumed; must route again first


def test_model_switch_after_route_logs_rejection(home):
    lane, conf, _, method = tools.route_for("prove the theorem, derivation, lemma")
    flywheel.note_route("theorem", lane, method, conf)
    flywheel.on_pre_command(command="model", args_raw="gpt-6-sol", session_key="s1")
    rec = json.loads(_labels_file(home).read_text().strip().splitlines()[-1])
    assert rec["kind"] == "model_switch"
    assert rec["new_model"] == "gpt-6-sol"
    assert rec["rejection"] is True  # route said qwen/qwen3.8-max


def test_model_switch_matching_route_is_not_rejection(home):
    lane, conf, _, method = tools.route_for("prove the theorem, derivation, lemma")
    flywheel.note_route("theorem", lane, method, conf)
    flywheel.on_pre_command(command="model", args_raw="qwen/qwen3.8-max", session_key="s1")
    rec = json.loads(_labels_file(home).read_text().strip().splitlines()[-1])
    assert rec["rejection"] is False


def test_reasoning_display_query_not_logged(home):
    flywheel.on_pre_command(command="reasoning", args_raw="show", session_key="s1")
    assert not _labels_file(home).exists()


def test_rate_lane_correction_feeds_classifier_data(home):
    lane, conf, _, method = tools.route_for("a proof of concept for the dashboard")
    flywheel.note_route("poc", lane, method, conf)
    out = flywheel.handle_rate("fail --lane routine-coding")
    assert "lane corrected" in out
    recs = [json.loads(l) for l in _labels_file(home).read_text().splitlines()]
    assert any(r["kind"] == "lane_correction" and r["to_lane"] == "routine-coding"
               for r in recs)


def test_rate_rejects_unknown_lane(home):
    lane, conf, _, method = tools.route_for("blog post")
    flywheel.note_route("blog", lane, method, conf)
    out = flywheel.handle_rate("fail --lane nonsense")
    assert "unknown lane" in out


def test_rate_skip_consumes(home):
    lane, conf, _, method = tools.route_for("blog post")
    flywheel.note_route("blog", lane, method, conf)
    out = flywheel.handle_rate("skip")
    assert "skipped" in out
    assert "no route" in flywheel.handle_rate("pass")


# ---------------------------------------------------------- aggregation

import time as _time

_NOW = _time.time()


def _seed(home, records):
    flywheel.labels_path()  # the plugin's own helper mkdirs <home>/evalroute/
    with _labels_file(home).open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def test_aggregate_stats(home):
    _seed(home, [
        {"kind": "route", "lane": "prose", "model": "moonshotai/kimi-k3", "effort": "medium",
         "method": "rules-strong", "confidence": 1.0, "ts": _NOW - 100},
        {"kind": "outcome", "rated": "pass", "route_lane": "prose",
         "actual_model": "moonshotai/kimi-k3", "actual_effort": "medium", "ts": _NOW - 90},
        {"kind": "outcome", "rated": "fail", "route_lane": "prose",
         "actual_model": "z-ai/glm-5.3", "actual_effort": "high", "ts": _NOW - 80},
        {"kind": "lane_correction", "from_lane": "math-first-principles",
         "to_lane": "orchestration", "task": "x", "ts": _NOW - 70},
    ])
    stats = rfl.aggregate(flywheel.read_labels())
    assert stats["routes"] == 1 and stats["outcomes"] == 2
    assert stats["lane_corrections"] == 1
    prose = stats["lanes"]["prose"]
    assert prose["outcomes"] == 2
    assert prose["pass_rate"] == 0.5
    assert prose["arms"]["moonshotai/kimi-k3@medium"]["passes"] == 1
    assert prose["arms"]["z-ai/glm-5.3@high"]["fails"] == 1


def test_aggregate_stale_cutoff(home):
    _seed(home, [
        {"kind": "outcome", "rated": "pass", "route_lane": "prose",
         "actual_model": "m", "actual_effort": "low", "ts": 1.0},  # epoch 1970: stale
    ])
    stats = rfl.aggregate(flywheel.read_labels())
    assert stats["outcomes"] == 0


def test_merge_observed_does_not_touch_measured(home, tmp_path):
    routes = tmp_path / "routes.yaml"
    routes.write_text(
        "version: 1\n"
        "lanes:\n"
        "  - id: prose\n"
        "    label: Prose\n"
        "    model: moonshotai/kimi-k3\n"
        "    effort: medium\n"
        "    provenance: measured 10 tasks, cov 0.9, all-in $0.04/succ, 2026-09-26\n"
        "    keywords: [blog post]\n", encoding="utf-8")
    _seed(home, [
        {"kind": "outcome", "rated": "fail", "route_lane": "prose",
         "actual_model": "moonshotai/kimi-k3", "actual_effort": "medium", "ts": _NOW - 90},
    ])
    lanes, applied = rfl.merge_observed(routes, flywheel.read_labels())
    assert applied == 0  # measured row untouched
    prose = next(l for l in lanes if l["id"] == "prose")
    assert prose["provenance"].startswith("measured")


def test_merge_observed_flips_failing_recommended_arm(home, tmp_path):
    routes = tmp_path / "routes.yaml"
    routes.write_text(
        "version: 1\n"
        "lanes:\n"
        "  - id: prose\n"
        "    label: Prose\n"
        "    model: moonshotai/kimi-k3\n"
        "    effort: medium\n"
        "    provenance: priors 2026-09-26\n"
        "    keywords: [blog post]\n", encoding="utf-8")
    _seed(home, [
        {"kind": "outcome", "rated": "fail", "route_lane": "prose",
         "actual_model": "moonshotai/kimi-k3", "actual_effort": "medium", "ts": _NOW - 90},
        {"kind": "outcome", "rated": "fail", "route_lane": "prose",
         "actual_model": "moonshotai/kimi-k3", "actual_effort": "medium", "ts": _NOW - 80},
        {"kind": "outcome", "rated": "pass", "route_lane": "prose",
         "actual_model": "claude-opus-5-5", "actual_effort": "high", "ts": _NOW - 70},
    ])
    lanes, applied = rfl.merge_observed(routes, flywheel.read_labels())
    assert applied == 1
    prose = next(l for l in lanes if l["id"] == "prose")
    assert prose["provenance"].startswith("observed")
    assert prose["model"] == "claude-opus-5-5" and prose["effort"] == "high"
    assert "observed flip" in prose["notes"]


def test_merge_observed_no_flip_on_single_fail(home, tmp_path):
    # One failure is not flip evidence; the route stands, provenance becomes observed.
    routes = tmp_path / "routes.yaml"
    routes.write_text(
        "version: 1\n"
        "lanes:\n"
        "  - id: prose\n"
        "    label: Prose\n"
        "    model: moonshotai/kimi-k3\n"
        "    effort: medium\n"
        "    provenance: priors 2026-09-26\n", encoding="utf-8")
    _seed(home, [
        {"kind": "outcome", "rated": "fail", "route_lane": "prose",
         "actual_model": "moonshotai/kimi-k3", "actual_effort": "medium", "ts": _NOW - 90},
    ])
    lanes, applied = rfl.merge_observed(routes, flywheel.read_labels())
    prose = next(l for l in lanes if l["id"] == "prose")
    assert prose["model"] == "moonshotai/kimi-k3"  # unchanged
    assert prose["provenance"].startswith("observed")
