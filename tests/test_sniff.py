"""Tests for the tier-2 first-turn sniff (pre_llm_call advisory)."""

from __future__ import annotations

import sniff
import tools


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
