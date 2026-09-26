"""Flywheel: label routes and outcomes as part of daily workflow.

Everything lands in ONE append-only JSONL file inside the Hermes home
(``<home>/evalroute/labels.jsonl``), never in the plugin dir (wiped on
reinstall). Record kinds:

  route           a route card was issued (/route, /evalroute route, tool)
  model_switch    /model used after a route - rejection if it differs
  effort_switch   /reasoning used after a route
  lane_correction /rate --lane X: the route's lane was wrong
  outcome         /rate pass|fail on the last route

Privacy: no response bodies or conversation content are ever logged. The
route record carries the task text the user typed to /route (that IS the
label); turn records are not persisted at all - the last-seen model is kept
in memory only, for /rate's correlation.

Correlation caveat: register_command handlers get no session id, so /rate
attributes to the most recent route THIS PROCESS saw (memory first, then the
file). In a CLI session the plugin process is the session's process, so this
is exact; in a busy gateway it is "the route you most recently asked for",
which is what you mean when you type /rate anyway.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    from . import tools
except ImportError:  # pragma: no cover - pytest imports the plugin root as a top-level module
    import tools  # type: ignore

# ------------------------------------------------------------------ storage


def labels_path() -> Path:
    """<hermes home>/evalroute/labels.jsonl (profile-safe; never inside the plugin dir)."""
    try:
        from hermes_constants import get_hermes_home
        home = Path(get_hermes_home())
    except Exception:
        home = Path.home() / ".hermes"
    d = home / "evalroute"
    d.mkdir(parents=True, exist_ok=True)
    return d / "labels.jsonl"


def _append(record: dict[str, Any]) -> None:
    record["ts"] = time.time()
    record["iso"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with labels_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_labels() -> list[dict[str, Any]]:
    p = labels_path()
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


# ------------------------------------------------- in-process correlation

_MEMORY: dict[str, Any] = {"route": None, "turn": None}


def note_route(task: str, lane: dict[str, Any], method: str, conf: float) -> None:
    """A route card was issued; persist the assignment and remember it for /rate."""
    record = {
        "kind": "route", "task": task[:500], "lane": lane["id"], "lane_label": lane["label"],
        "model": lane["model"], "effort": lane["effort"], "method": method,
        "confidence": round(float(conf), 2),
    }
    _append(record)
    _MEMORY["route"] = dict(record)


def note_turn(session_id: Optional[str], model: Optional[str]) -> None:
    """Last-seen (session, model) from post_llm_call; memory only, never persisted."""
    if session_id and model:
        _MEMORY["turn"] = {"session_id": session_id, "model": model, "ts": time.time()}


def _last_route() -> Optional[dict[str, Any]]:
    """Most recent route not yet consumed by an outcome/skip.

    Memory first, then the file. A route is consumed when a later record
    (outcome or skip) carries its ts as ``consumes``; /rate skip must be
    durable, so consumption is judged from the file, not memory.
    """
    if _MEMORY.get("route"):
        return _MEMORY["route"]
    records = read_labels()
    consumed = {r.get("consumes") for r in records if r.get("consumes")}
    for rec in reversed(records):
        if rec.get("kind") == "route" and rec.get("ts") not in consumed:
            return rec
    return None


def _actual_model(route: Optional[dict[str, Any]]) -> Optional[str]:
    """The model actually used: last turn seen in this process, else last /model switch."""
    turn = _MEMORY.get("turn")
    if turn and time.time() - turn.get("ts", 0) < 14 * 3600:
        return turn.get("model")
    for rec in reversed(read_labels()):
        if rec.get("kind") == "model_switch" and route and rec.get("ts", 0) >= route.get("ts", 0):
            return rec.get("new_model")
    return route.get("model") if route else None


# ------------------------------------------------------------------- hooks

_MODEL_CMD = "model"
_REASONING_CMD = "reasoning"


def on_pre_command(command: str, args_raw: str = "", session_key: Optional[str] = None,
                   platform: Optional[str] = None, **_) -> None:
    """Observer for /model and /reasoning: implicit route feedback."""
    cmd = (command or "").strip().lower()
    if cmd not in (_MODEL_CMD, _REASONING_CMD):
        return
    route = _last_route()
    args = (args_raw or "").strip()
    if not args:
        return
    if cmd == _MODEL_CMD:
        tokens = args.split()
        new_model = next((t for t in tokens if not t.startswith("-")), tokens[0] if tokens else "")
        rec: dict[str, Any] = {"kind": "model_switch", "new_model": new_model,
                               "session_key": session_key or ""}
        if route:
            rec["prev_route_lane"] = route.get("lane")
            rec["prev_route_model"] = route.get("model")
            rec["rejection"] = (new_model != route.get("model"))
        _append(rec)
    else:  # /reasoning
        level = args.split()[0].lower()
        if level in ("show", "hide", "full", "clamp"):
            return  # display queries are not effort changes
        rec = {"kind": "effort_switch", "new_effort": level, "session_key": session_key or ""}
        if route:
            rec["prev_route_lane"] = route.get("lane")
            rec["prev_route_effort"] = route.get("effort")
        _append(rec)


def on_post_llm_call(session_id: Optional[str] = None, model: Optional[str] = None,
                     **_) -> None:
    """Keep the last-seen model for /rate correlation. Nothing persisted."""
    note_turn(session_id, model)


# ------------------------------------------------------------------ /rate

_RATINGS = {"pass", "fail", "p", "f", "skip"}


def handle_rate(raw_args: str) -> str:
    """`/rate pass|fail [--lane <id>] [--note ...]` - label the last routed task."""
    args = (raw_args or "").split()
    if not args or args[0].lower() not in _RATINGS:
        return ("usage: /rate pass|fail [--lane <lane-id>] [--note <text>]\n"
                "labels the most recent /route in this session (actual model is taken "
                "from the last turn; effort from the route unless /reasoning changed it)")
    rating = args[0].lower()
    if rating in ("p", "f"):
        rating = "pass" if rating == "p" else "fail"
    lane_fix = ""
    note = ""
    i = 1
    while i < len(args):
        if args[i] == "--lane" and i + 1 < len(args):
            lane_fix = args[i + 1]
            i += 2
        elif args[i] == "--note" and i + 1 < len(args):
            note = " ".join(args[i + 1:])
            break
        else:
            i += 1

    route = _last_route()
    if route is None:
        return "evalroute: no route on record yet - run /route <task> first; nothing rated."
    if rating == "skip":
        _append({"kind": "outcome", "rated": "skip", "route_lane": route.get("lane"),
                 "consumes": route.get("ts"), "note": note[:300]})
        _MEMORY["route"] = None  # skip consumes the route so the next /rate doesn't re-file it
        return "route skipped (no verdict); the pending route was consumed."

    # Effort in effect: the route's, unless a /reasoning switch landed after it.
    effort = route.get("effort")
    for rec in reversed(read_labels()):
        if rec.get("kind") == "effort_switch" and rec.get("ts", 0) >= route.get("ts", 0):
            effort = rec.get("new_effort")
            break

    record = {
        "kind": "outcome", "rated": rating, "route_lane": route.get("lane"),
        "route_model": route.get("model"), "route_effort": route.get("effort"),
        "actual_model": _actual_model(route), "actual_effort": effort,
        "method": route.get("method"), "confidence": route.get("confidence"),
        "consumes": route.get("ts"),
    }
    if lane_fix:
        if tools._lane_by_id(lane_fix) is None:
            known = ", ".join(l["id"] for l in tools._load_routes())
            return f"evalroute: unknown lane {lane_fix!r}; known lanes: {known}"
        record["lane_correction"] = lane_fix
    if note:
        record["note"] = note[:300]
    _append(record)
    _MEMORY["route"] = None  # outcome consumes the pending route

    if lane_fix:
        _append({"kind": "lane_correction", "from_lane": route.get("lane"),
                 "to_lane": lane_fix, "task": route.get("task", "")[:200]})
        return (f"logged: {rating} (lane corrected {route.get('lane')} -> {lane_fix}). "
                "The correction also feeds the classifier's keyword table.")
    arm = f"{record['actual_model'] or '?'} @ {effort or '?'}"
    return (f"logged: {rating} for lane {route.get('lane')} on arm {arm}. "
            f"{_counts_summary()}")


def _counts_summary() -> str:
    try:
        stats = aggregate(read_labels())
        lane_stats = stats["lanes"].get("last_lane")  # not tracked; cheap overall line instead
    except Exception:
        return ""
    n = stats.get("outcomes", 0)
    routes = stats.get("routes", 0)
    return f"({routes} routes, {n} rated outcomes on file)"
