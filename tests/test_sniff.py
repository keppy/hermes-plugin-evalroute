"""Tests for the tier-2 first-turn sniff (pre_llm_call advisory)."""

from __future__ import annotations

import pytest

import sniff
import tools
import flywheel

from tests.test_flywheel import home  # noqa: F401  (isolates HERMES_HOME)


@pytest.fixture(autouse=True)
def _isolated_ledger(home):
    """The sniff reads the flywheel ledger (cross-session continuation
    notices), so every sniff test runs against an isolated labels dir and
    a clean in-memory route."""
    flywheel._MEMORY["route"] = None
    yield
    flywheel._MEMORY["route"] = None


def _first_turn(msg, model="qwen/qwen3.8-max", platform="cli"):
    return sniff.sniff(
        session_id="s1", user_message=msg, is_first_turn=True,
        model=model, platform=platform,
    )


def test_sniff_silent_on_match():
    # qwen/qwen3.8-max IS the math lane's model -> no advice needed.
    out = _first_turn("prove the theorem with a full derivation and a lemma")
    assert out is None


def test_sniff_silent_on_low_confidence():
    # One keyword hit ("proof") only -> below MIN_HITS -> silent.
    out = _first_turn("a proof of concept for the dashboard", model="someone/other")
    assert out is None


def test_sniff_advises_on_mismatch():
    # Math-flavored opener running on a non-math model, 2+ hits.
    out = _first_turn("prove the theorem, give a full derivation and a lemma", model="z-ai/glm-5.3")
    assert out is not None and "context" in out
    assert "/route" in out["context"]
    assert "advisory" in out["context"].lower()
    assert "qwen/qwen3.8-max" in out["context"]


def test_sniff_bare_slug_matches_prefixed_model():
    # `z-ai/glm-5.3` routed vs active `glm-5.3` -> same model, silent.
    out = _first_turn(
        "migrate the monolith: multi-file repo-wide hard agentic change with a build failure",
        model="glm-5.3",
    )
    assert out is None


def test_snift_only_first_turn():
    out = sniff.sniff(
        session_id="s1", user_message="prove the theorem with a derivation and a lemma",
        is_first_turn=False, model="z-ai/glm-5.3", platform="cli",
    )
    assert out is None


def test_sniff_platform_gated():
    out = sniff.sniff(
        session_id="s1", user_message="prove the theorem with a derivation and a lemma",
        is_first_turn=True, model="z-ai/glm-5.3", platform="telegram",
    )
    assert out is None


def test_sniff_never_raises_on_multimodal_list():
    parts = [{"type": "text", "text": "prove the theorem, derivation, lemma"},
             {"type": "image_url", "image_url": {"url": "x"}}]
    out = sniff.sniff(
        session_id="s1", user_message=parts, is_first_turn=True,
        model="z-ai/glm-5.3", platform="cli",
    )
    assert out is not None


def test_sniff_never_raises_on_broken_table(monkeypatch):
    def boom(_):
        raise ValueError("routes.yaml is broken")
    monkeypatch.setattr(tools, "classify", boom)
    out = _first_turn("prove the theorem with a derivation and a lemma", model="z-ai/glm-5.3")
    assert out is None


# ------------------------------------------ cross-session continuation

def test_sniff_flags_cross_session_pending_route():
    # a route armed by a previous process, never rated -> the first turn of
    # the NEXT session gets one continuation advisory
    lane = tools._lane_by_id("dl-ml-research-engineering")
    flywheel.note_route("build the exemplar harness", lane, "llm", 0.8)
    flywheel._MEMORY["route"] = None  # simulate a different process
    out = _first_turn("continue building the harness", model="z-ai/glm-5.3")
    assert out is not None and "pending" in out["context"].lower()
    assert "/rate" in out["context"]
    assert "do NOT" in out["context"]


def test_sniff_own_route_this_process_is_not_a_notice():
    # `/route` before turn 1 in THIS process is the normal workflow: the
    # in-memory route is the user's own, so no pending-route advisory fires
    # (and no mismatch either - the session is on the routed model).
    lane = tools._lane_by_id("dl-ml-research-engineering")
    flywheel.note_route("build the exemplar harness", lane, "llm", 0.8)
    out = _first_turn("build the exemplar harness", model="z-ai/glm-5.3")
    assert out is None


def test_sniff_file_only_route_says_session_unknown():
    # A route on file with no in-memory counterpart: another process armed
    # it, so the notice names the id and says ownership is unknown.
    lane = tools._lane_by_id("dl-ml-research-engineering")
    flywheel.note_route("build the exemplar harness", lane, "llm", 0.8)
    flywheel._MEMORY["route"] = None
    out = _first_turn("continue building the harness", model="z-ai/glm-5.3")
    assert out is not None and "session unknown" in out["context"]
    assert flywheel.read_labels()[0]["id"] in out["context"]


def test_sniff_notice_supersedes_mismatch():
    # pending route AND a strong mismatch signal -> the notice wins:
    # a filed route is stronger evidence than a keyword classification
    lane = tools._lane_by_id("dl-ml-research-engineering")
    flywheel.note_route("audit the rl training plan", lane, "llm", 0.8)
    flywheel._MEMORY["route"] = None
    out = _first_turn("prove the theorem, give a full derivation and a lemma",
                      model="z-ai/glm-5.3")
    assert out is not None and "pending" in out["context"].lower()
    assert "qwen/qwen3.8-max" not in out["context"]


def test_sniff_notice_not_first_turn():
    lane = tools._lane_by_id("dl-ml-research-engineering")
    flywheel.note_route("build the exemplar harness", lane, "llm", 0.8)
    flywheel._MEMORY["route"] = None
    out = sniff.sniff(session_id="s1", user_message="continue the harness",
                      is_first_turn=False, model="z-ai/glm-5.3", platform="cli")
    assert out is None
