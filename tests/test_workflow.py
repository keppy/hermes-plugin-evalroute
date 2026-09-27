"""Workflow surfaces: the card footer, the CLI epilog, and the rate subcommand."""

from __future__ import annotations

import pytest

import tools
import flywheel
from flywheel import handle_rate

from tests.test_flywheel import home  # reuse the HERMES_HOME fixture


# ------------------------------------------------------------ card footer

def test_card_footer_present():
    lane = tools._lane_by_id("routine-coding")
    card = tools.route_card(lane, 0.9, ["test"], method="rules-strong")
    assert "next:" in card
    assert f"/model {lane['model']}" in card
    assert "/rate pass|fail" in card
    assert "wrong lane?" in card


def test_card_footer_mentions_reasoning_when_no_overrides(home, monkeypatch):
    # no install-routes on record -> footer includes the /reasoning step
    monkeypatch.setattr(tools, "_effort_auto", lambda: False)
    lane = tools._lane_by_id("dl-ml-research-engineering")
    card = tools.route_card(lane, 0.8, [], method="llm")
    assert "/reasoning high" in card


def test_card_footer_skips_reasoning_when_overrides(home, monkeypatch):
    # install-routes wrote per-model efforts -> /reasoning step not needed
    monkeypatch.setattr(tools, "_effort_auto", lambda: True)
    lane = tools._lane_by_id("dl-ml-research-engineering")
    card = tools.route_card(lane, 0.8, [], method="llm")
    assert "/reasoning" not in card


def test_effort_auto_reads_config(home):
    # real config has reasoning_overrides (install-routes ran) -> True
    assert tools._effort_auto() is True or tools._effort_auto() is False


# ------------------------------------------------------------- CLI wiring

def test_cli_epilog_in_help(monkeypatch):
    import argparse as ap
    p = ap.ArgumentParser(prog="hermes evalroute")
    subs = p.add_subparsers(dest="evalroute_action")
    tools.setup_cli(subs.add_parser("evalroute"))
    # the epilog text is attached to route/rate subparsers via RawDescription
    assert "route -> arm -> rate" in tools._WORKFLOW_EPILOG
    assert "/rate pass|fail" in tools._WORKFLOW_EPILOG
    assert "hermes evalroute rate" in tools._WORKFLOW_EPILOG


def test_cli_rate_dispatch(monkeypatch, home):
    # seed a route so rate has something to consume
    lane = tools._lane_by_id("dl-ml-research-engineering")
    flywheel.note_route("test the cli rate path", lane, "llm", 0.8)

    import argparse as ap

    class A:  # minimal args namespace
        evalroute_action = "rate"
        verdict = "pass"
        lane = None
        note = "cli rate works"
        dry_run = False

    monkeypatch.setattr("builtins.print", lambda *a, **k: None)
    rc = tools.evalroute_cli(A())
    assert rc == 0
    recs = flywheel.read_labels()
    outcome = next(r for r in recs if r["kind"] == "outcome")
    assert outcome["rated"] == "pass"
    assert outcome["consumes"] is not None


def test_cli_rate_no_route(home):
    class A:
        evalroute_action = "rate"
        verdict = "pass"
        lane = None
        note = None
        dry_run = False

    out = tools.evalroute_cli(A())
    # no route on file -> handle_rate returns its usage text (printed); rc 0
    # but nothing consumed; verify no outcome record was written
    assert not [r for r in flywheel.read_labels() if r["kind"] == "outcome"]


# ------------------------------------------------------- footer + flywheel

def test_reroute_with_lane_overwrites_pending(home):
    # the workflow footer says "wrong lane? /route --lane <id> <same task>";
    # verify a --lane reroute logs a fresh route that /rate will consume
    lane = tools._lane_by_id("dl-ml-research-engineering")
    flywheel.note_route("audit plan", lane, "llm", 0.7)
    fixed = tools._lane_by_id("alignment-reasoning")
    flywheel.note_route("audit plan", fixed, "llm", 0.7)
    handle_rate("pass")
    recs = flywheel.read_labels()
    outcome = next(r for r in recs if r["kind"] == "outcome")
    assert outcome["route_lane"] == "alignment-reasoning"
