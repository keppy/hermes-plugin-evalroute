"""Tests for routes_from_report (measured routes.yaml generation)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
import yaml

import routes_from_report as rfr

HEADER = ["lane", "model", "effort", "n", "pend", "trunc", "err", "pass_", "lo",
          "hi", "cov", "passk", "try_usd", "succ_usd", "vmin", "allin", "known",
          "med_out", "p50s"]


def _row(lane, model, effort="medium", n=10, cov="0.90", allin="0.05"):
    return dict(zip(HEADER, [lane, model, effort, n, "0", "0", "0", "0.8",
                            "0.7", "0.9", cov, "0.7", "0.02", "0.04", "0.1",
                            allin, "True", "1000", "12.0"]))


def _write_csv(tmp_path, rows):
    p = tmp_path / "report.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        w.writerows(rows)
    return p


def test_lane_slug_aliases(tmp_path):
    p = _write_csv(tmp_path, [
        _row("math", "qwen/qwen3.8-max", effort="max"),
        _row("hard agentic coding", "z-ai/glm-5.3", effort="max"),
    ])
    out = tmp_path / "routes.gen.yaml"
    assert rfr.generate(p, out, None) == 0
    doc = yaml.safe_load(out.read_text(encoding="utf-8"))
    ids = [l["id"] for l in doc["lanes"]]
    assert "math-first-principles" in ids
    assert "hard-agentic-coding" in ids


def test_routing_rule_lowest_allin_within_cov(tmp_path):
    # flash is cheaper but 10pts lower coverage -> GLM 5.3 must win the lane.
    p = _write_csv(tmp_path, [
        _row("math", "z-ai/glm-5.3-flash", cov="0.40", allin="0.01"),
        _row("math", "qwen/qwen3.8-max", cov="0.90", allin="0.05"),
    ])
    out = tmp_path / "routes.gen.yaml"
    assert rfr.generate(p, out, None) == 0
    doc = yaml.safe_load(out.read_text(encoding="utf-8"))
    math = next(l for l in doc["lanes"] if l["id"] == "math-first-principles")
    assert math["model"] == "qwen/qwen3.8-max"
    assert math["provenance"].startswith("measured 10 tasks")


def test_existing_classifier_fields_preserved(tmp_path):
    p = _write_csv(tmp_path, [_row("prose", "moonshotai/kimi-k3", effort="medium")])
    existing = tmp_path / "routes.yaml"
    existing.write_text(yaml.safe_dump({
        "version": 1,
        "lanes": [{
            "id": "prose", "label": "Prose",
            "keywords": ["blog post", "essay"],
            "match_hint": "human is the checker",
            "model": "moonshotai/kimi-k3", "effort": "medium",
            "escalation": "claude-opus-5-5",
            "provenance": "priors 2026-09-26",
            "notes": "untested prior",
        }],
    }, sort_keys=False), encoding="utf-8")
    out = tmp_path / "routes.gen.yaml"
    assert rfr.generate(p, out, existing) == 0
    doc = yaml.safe_load(out.read_text(encoding="utf-8"))
    prose = next(l for l in doc["lanes"] if l["id"] == "prose")
    assert prose["keywords"] == ["blog post", "essay"]
    assert prose["escalation"] == "claude-opus-5-5"
    assert prose["notes"] == "untested prior"
    assert prose["label"] == "Prose"
    assert prose["provenance"].startswith("measured")


def test_unmeasured_lanes_carried_over_verbatim(tmp_path):
    p = _write_csv(tmp_path, [_row("prose", "moonshotai/kimi-k3")])
    existing = tmp_path / "routes.yaml"
    existing.write_text(yaml.safe_dump({
        "version": 1,
        "lanes": [{
            "id": "orchestration", "label": "Orchestrator / subagents",
            "keywords": ["orchestrate"], "match_hint": "plans",
            "model": "claude-opus-5-5", "effort": "high",
            "escalation": None, "provenance": "priors 2026-09-26",
            "notes": "image 2 only",
        }],
    }, sort_keys=False), encoding="utf-8")
    out = tmp_path / "routes.gen.yaml"
    assert rfr.generate(p, out, existing) == 0
    doc = yaml.safe_load(out.read_text(encoding="utf-8"))
    orch = next(l for l in doc["lanes"] if l["id"] == "orchestration")
    assert orch["provenance"] == "priors 2026-09-26"  # untouched, not "measured"


def test_empty_report_refuses(tmp_path):
    p = tmp_path / "report.csv"
    p.write_text(",".join(HEADER) + "\n", encoding="utf-8")
    out = tmp_path / "routes.gen.yaml"
    assert rfr.generate(p, out, None) == 1


def test_generated_file_loads_in_tool(tmp_path, monkeypatch):
    """The generated YAML must satisfy the plugin's own loader/validator.

    Uses the documented flow: regenerate FROM an existing routes.yaml so
    classifier fields are preserved, then load and classify with it.
    """
    p = _write_csv(tmp_path, [
        _row("math", "qwen/qwen3.8-max", effort="max"),
        _row("web research", "moonshotai/kimi-k3", effort="medium"),
    ])
    existing = tmp_path / "routes.yaml"
    existing.write_text(yaml.safe_dump({
        "version": 1,
        "lanes": [
            {"id": "web-research", "label": "Web research",
             "keywords": ["web research", "citations"], "match_hint": "search",
             "model": "moonshotai/kimi-k3", "effort": "medium",
             "provenance": "priors 2026-09-26"},
            {"id": "math-first-principles", "label": "Math", "keywords": ["proof"],
             "model": "qwen/qwen3.8-max", "effort": "max",
             "provenance": "priors 2026-09-26"},
        ],
    }, sort_keys=False), encoding="utf-8")
    out = tmp_path / "routes.gen.yaml"
    assert rfr.generate(p, out, existing) == 0
    tools_mod = __import__("tools")
    monkeypatch.setattr(tools_mod, "ROUTES_FILE", out)
    monkeypatch.setattr(tools_mod, "_LLM_LANES", None)  # reset the lazy cache
    lanes = tools_mod._load_routes()
    assert {l["id"] for l in lanes} == {"math-first-principles", "web-research"}
    lane, conf, hits = tools_mod.classify("web research on competitors with citations")
    assert lane["id"] == "web-research"
    assert lane["provenance"].startswith("measured")
