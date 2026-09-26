"""Handlers for the evalroute plugin.

Everything here is local file math over ``data/routes.yaml``: no network, no
credentials, no paid path. The two paid things this plugin could do — the
evalroute harness itself and the Hermes shim — stay out of the Hermes venv
(point ``EVALROUTE_PYTHON`` at the harness's own environment and run it via
the terminal; see the bundled skill).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

PLUGIN_DIR = Path(__file__).resolve().parent
ROUTES_FILE = PLUGIN_DIR / "data" / "routes.yaml"

# "low-medium" -> "medium" is a policy choice: the higher of a starting range,
# because reasoning_overrides has one slot per model and under-routing effort
# is the costlier miss (coverage, not verbosity).
_EFFORT_RE = re.compile(r"(none|minimal|low|medium|high|xhigh|max)")

_LLM_LANES = None  # lazy-loaded routes cache


def _load_routes() -> list[dict[str, Any]]:
    """Parse and minimally validate data/routes.yaml; raise on structural errors."""
    global _LLM_LANES
    if _LLM_LANES is None:
        raw = yaml.safe_load(ROUTES_FILE.read_text(encoding="utf-8")) or {}
        lanes = raw.get("lanes")
        if not isinstance(lanes, list) or not lanes:
            raise ValueError(f"{ROUTES_FILE} has no lanes list")
        seen: set[str] = set()
        for lane in lanes:
            for field in ("id", "label", "model", "effort"):
                if not lane.get(field):
                    raise ValueError(f"lane entry missing required field {field!r}: {lane}")
            if lane["id"] in seen:
                raise ValueError(f"duplicate lane id {lane['id']!r}")
            seen.add(lane["id"])
        _LLM_LANES = lanes
    return _LLM_LANES


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


_PLUGIN_LLM = None  # stashed ctx.llm facade, set at register() time


def set_llm_facade(facade) -> None:
    """Stash the host-owned LLM facade from register(ctx). None = no fallback."""
    global _PLUGIN_LLM
    _PLUGIN_LLM = facade


# Negation cues: a keyword hit preceded by one of these within a few words is
# not evidence for the lane. "not usually hard math though" must NOT count as
# a math hit — this exact phrasing shipped a life-assistant description to the
# math-first-principles route.
_NEGATION_CUES = {
    "not", "no", "never", "without", "except", "isn't", "isnt", "aren't", "arent",
    "don't", "dont", "doesn't", "doesnt", "avoid", "rarely", "seldom", "hardly",
    "unusual", "instead", "rather", "n't",
}
_NEG_WINDOW = 4  # words before the keyword to scan for a cue


def _hit(keyword: str, text: str) -> bool:
    """Word-boundary, negation-guarded match of a normalized keyword.

    Substring matching made 'rl' hit 'world'; boundaries kill that class of
    false positive. Negation guard kills 'not usually hard math' as a math
    signal. Multi-word phrases match as phrases ('unit test').
    """
    kw = _norm(keyword)
    if not kw:
        return False
    m = re.search(rf"(?:^| )({re.escape(kw)})(?: |$)", text)
    if m is None:
        return False
    # Words before the hit, up to the negation window.
    before = text[:m.start()].split()
    if any(w in _NEGATION_CUES for w in before[-_NEG_WINDOW:]):
        return False
    return True


def _lane_by_id(lane_id: str) -> dict[str, Any] | None:
    return next((l for l in _load_routes() if l["id"] == lane_id), None)


# Thresholds for escalating from rules to the host-owned LLM classifier.
# HIGH_CONF = rules are trusted outright. BELOW that, a lane with several
# distinct keyword hits is still trusted (evidence beats paraphrase); a single
# hit is not — that's where "researchy" descriptions with zero vocab signal
# lived, and where "not usually hard math" misrouted before the negation
# guard.
_LLM_HIGH_CONF = 0.75
_LLM_MIN_HITS = 2

_LLM_SCHEMA = {
    "type": "object",
    "properties": {
        "lane": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["lane"],
}

_LLM_SYSTEM = (
    "You are a task router. Classify the user's task description into exactly "
    "one lane from the provided list. Judge by what the work IS, not by which "
    "words appear: a description of an assistant's duties is an ORCHESTRATION "
    "task (it plans and delegates); negated capabilities ('not usually hard "
    "math') are evidence AGAINST a lane, not for it. Return JSON: "
    '{"lane": "<id>", "confidence": 0-1}.'
)


def _llm_fallback(task: str, lanes: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float]:
    """Classify via the host-owned LLM facade (ctx.llm). Returns (lane, conf).

    Failures (no facade, LLM error, unparseable/unknown lane) return (None, 0)
    and the caller falls through to the deterministic result.
    """
    if _PLUGIN_LLM is None:
        return None, 0.0
    try:
        lane_list = "\n".join(
            f'- {l["id"]}: {l["label"]} — {l.get("match_hint", "")}' for l in lanes)
        instructions = (
            "Classify this task description into exactly one lane.\n\n"
            f"Lanes:\n{lane_list}\n\nTask:\n{task}"
        )
        result = _PLUGIN_LLM.complete_structured(
            instructions=instructions,
            input=[{"type": "text", "text": task}],
            json_schema=_LLM_SCHEMA,
            schema_name="evalroute_lane",
            system_prompt=_LLM_SYSTEM,
            temperature=0.0,
            max_tokens=256,
            timeout=30.0,
        )
        parsed = getattr(result, "parsed", None) or {}
        lane_id = str(parsed.get("lane", "")).strip()
        conf = float(parsed.get("confidence", 0.0) or 0.0)
        lane = _lane_by_id(lane_id)
        if lane is None or conf <= 0:
            return None, 0.0
        return lane, min(1.0, conf)
    except Exception as exc:
        logger.warning("evalroute LLM fallback failed: %s", exc)
        return None, 0.0


def route_for(task: str) -> tuple[dict[str, Any], float, list[str], str]:
    """Full routing decision: rules first, LLM fallback when rules are weak.

    Returns (lane, confidence, hits, method) where method is
    'rules-strong' | 'rules-weak' | 'llm' | 'default'. Rules are trusted only
    with >= _LLM_MIN_HITS DISTINCT keyword hits on the winning lane (a lone
    'proof' in 'proof of concept' is not evidence); confidence alone is not
    a strength signal (1 hit on 1 lane computes conf 1.0). Otherwise the
    host-owned LLM classifies (a paraphrase like 'manage life, writing, and
    researchy tasks' has zero keyword signal and needs it). LLM failure falls
    back to the weak rules result anyway.
    """
    lanes = _load_routes()
    lane, conf, hits = classify(task)
    distinct = len(set(hits))
    if distinct >= _LLM_MIN_HITS:
        return lane, conf, hits, "rules-strong"
    # Rules are weak: paraphrase, or a single ambiguous hit. Escalate.
    llm_lane, llm_conf = _llm_fallback(task, lanes)
    if llm_lane is not None:
        return llm_lane, llm_conf, hits, "llm"
    if hits:
        return lane, conf, hits, "rules-weak"
    return lane, conf, hits, "default"


def classify(task: str) -> tuple[dict[str, Any], float, list[str]]:
    """Rules-first lane classification.

    Returns ``(lane, confidence, hits)``. Keyword hits score per lane; the
    highest total wins. Every lane is checked (a "proof ... blog post" goes to
    math, not prose, because math's keywords outrank on the stronger signal).
    No lane hits -> long-doc-reading with confidence 0.0 (the input-heavy
    default; the card says so). Confidence is hits[top]/hits[all], bounded to 1.
    """
    text = _norm(task)
    scores: dict[str, list[str]] = {}
    for lane in _load_routes():
        hits = [kw for kw in lane.get("keywords", []) if _hit(kw, text)]
        if hits:
            scores[lane["id"]] = hits
    if not scores:
        # Input-heavy default; a generated table may not contain that lane
        # (no keywords carried over), so fall back to the first lane rather
        # than returning None.
        fallback = _lane_by_id("long-doc-reading") or _load_routes()[0]
        return fallback, 0.0, []
    top_id = max(scores, key=lambda k: len(scores[k]))
    total = sum(len(h) for h in scores.values())
    conf = min(1.0, len(scores[top_id]) / total) if total else 0.0
    return _lane_by_id(top_id), conf, scores[top_id]


def route_card(lane: dict[str, Any], conf: float, hits: list[str],
               pinned: bool = False, method: str = "rules") -> str:
    """Render the human-readable route card."""
    lines = [
        f"lane: {lane['label']} ({lane['id']})",
        f"route: {lane['model']} @ {lane['effort']}   <- run: /model {lane['model']}",
    ]
    if lane.get("escalation"):
        lines.append(f"escalation: {lane['escalation']} (when coverage gaps or the task turns out harder)")
    if pinned:
        lines.append("classification: lane pinned by caller")
    elif method == "llm":
        lines.append(f"classification: LLM fallback ({conf:.2f}) - rules had weak signal "
                     f"({'no keyword hit' if not hits else 'single ambiguous hit'})")
    elif hits:
        shown = ", ".join(sorted(set(hits))[:4])
        more = "" if len(set(hits)) <= 4 else f" (+{len(set(hits)) - 4} more)"
        lines.append(f"classification: rules match ({conf:.2f}) on: {shown}{more}")
    else:
        lines.append("classification: no keyword hit - defaulted to long-doc-reading; "
                     "pass --lane <id> to pin, or say the task in more words")
    lines.append(f"basis: {lane.get('provenance', 'unknown')}")
    if lane.get("notes"):
        lines.append(f"note: {lane['notes']}")
    lines.append(f"why here: {lane.get('match_hint', '')}")
    return "\n".join(lines)


def _tool_result(card: str, lane: dict[str, Any], conf: float, pinned: bool,
                 method: str = "rules") -> str:
    """JSON envelope for the model-facing tool: card for the human, fields for the agent."""
    return json.dumps({
        "lane": lane["id"],
        "lane_label": lane["label"],
        "model": lane["model"],
        "effort": lane["effort"],
        "escalation": lane.get("escalation"),
        "confidence": round(conf, 2),
        "classification_method": method,
        "pinned": pinned,
        "provenance": lane.get("provenance", ""),
        "card": card,
    }, ensure_ascii=False)


def evalroute_route(args: dict[str, Any], **_) -> str:
    """Handler for the evalroute_route tool. Never raises; errors are JSON."""
    try:
        task = (args.get("task") or "").strip()
        if not task:
            return json.dumps({"error": "task is required: a short description of the work"})
        lane_id = (args.get("lane") or "").strip()
        if lane_id:
            lane = _lane_by_id(lane_id)
            if lane is None:
                known = ", ".join(l["id"] for l in _load_routes())
                return json.dumps({"error": f"unknown lane {lane_id!r}; known lanes: {known}"})
            card = route_card(lane, 1.0, [], pinned=True)
            return _tool_result(card, lane, 1.0, pinned=True, method="pinned")
        lane, conf, hits, method = route_for(task)
        try:  # flywheel label; logging must never break the card
            from . import flywheel as _fw
            _fw.note_route(task, lane, method, conf)
        except Exception:
            pass
        return _tool_result(route_card(lane, conf, hits, method=method), lane, conf,
                            pinned=False, method=method)
    except Exception as exc:  # route table broken -> actionable error, not a crash
        return json.dumps({"error": f"evalroute: {exc}"})


# ---------------------------------------------------------------- slash + CLI

def _card_for_args(raw_args: str) -> str:
    """Shared body for /route and `hermes evalroute route`."""
    parts = (raw_args or "").split()
    lane_id = ""
    task = (raw_args or "").strip()
    if parts and parts[0] == "--lane":
        if len(parts) < 3:
            raise ValueError("usage: route [--lane <lane-id>] <task description>")
        lane_id, task = parts[1], " ".join(parts[2:])
    lane = _lane_by_id(lane_id) if lane_id else None
    if lane is not None:
        return route_card(lane, 1.0, [], pinned=True)
    if lane_id:
        known = ", ".join(l["id"] for l in _load_routes())
        raise ValueError(f"unknown lane {lane_id!r}; known lanes: {known}")
    lane_obj, conf, hits, method = route_for(task)
    try:  # flywheel label; logging must never break the card
        from . import flywheel as _fw
        _fw.note_route(task, lane_obj, method, conf)
    except Exception:
        pass
    return route_card(lane_obj, conf, hits, method=method)


def handle_route_command(raw_args: str) -> str:
    """Handler for /route (ctx.register_command)."""
    try:
        if not (raw_args or "").strip():
            lanes = _load_routes()
            return ("usage: /route [--lane <lane-id>] <task>\n"
                    "lanes: " + ", ".join(l["id"] for l in lanes))
        return _card_for_args(raw_args)
    except Exception as exc:
        return f"evalroute: {exc}"


# ------------------------------------------------------- install-routes (CLI)

_VALID_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
_EFFORT_ORDER = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]


def _effort_for_override(lane: dict[str, Any]) -> str:
    """One effort value per model for agent.reasoning_overrides.

    A model serving two lanes keeps the HIGHER effort: reasoning_overrides has
    one slot per model id, and under-routing effort is the costlier miss
    (coverage, not verbosity). So take the highest effort mentioned in the
    lane's effort string, not the last one ("Opus high, subagents low-medium"
    -> high).
    """
    found = _EFFORT_RE.findall(str(lane.get("effort", "medium")))
    ranked = [e for e in _EFFORT_ORDER if e in found]
    return ranked[-1] if ranked else "medium"


def _config_module():
    """Import hermes_cli.config lazily (registration must not need it)."""
    from hermes_cli.config import load_config, set_config_value
    return load_config, set_config_value


def install_routes(dry_run: bool = False) -> int:
    """Write the route table's effort column into agent.reasoning_overrides.

    Runs inside the `hermes` process (plugin CLI command), so it calls
    set_config_value in-process rather than shelling out. Merges with existing
    overrides: keys the route table names win; keys it doesn't name are kept.
    """
    load_config, set_config_value = _config_module()

    lanes = _load_routes()
    desired: dict[str, str] = {}
    for lane in lanes:
        model, effort = lane["model"], _effort_for_override(lane)
        if effort not in _VALID_EFFORTS:
            print(f"  skipping {model}: bad effort {effort!r} in routes.yaml")
            continue
        # Keep the higher effort when a model serves two lanes.
        cur = desired.get(model)
        if cur is not None and _EFFORT_ORDER.index(effort) < _EFFORT_ORDER.index(cur):
            continue
        desired[model] = effort

    cfg = load_config() or {}
    agent_cfg = cfg.get("agent") if isinstance(cfg.get("agent"), dict) else {}
    existing = agent_cfg.get("reasoning_overrides") or {}
    if not isinstance(existing, dict):
        print("  agent.reasoning_overrides is not a mapping; refusing to touch it")
        return 1

    merged = {**existing, **desired}
    if merged == existing:
        print("agent.reasoning_overrides already matches the route table; nothing to do.")
        print(f"  current: {json.dumps(existing, ensure_ascii=False)}")
        return 0

    diff_added = {k: v for k, v in desired.items() if existing.get(k) != v}
    diff_kept = {k: v for k, v in existing.items() if k not in desired}
    print(f"route table -> agent.reasoning_overrides ({len(diff_added)} set, "
          f"{len(diff_kept)} pre-existing kept)")
    for model, effort in sorted(diff_added.items()):
        mark = "+" if model not in existing else "~"
        print(f"  {mark} {model}: {existing.get(model, '-')} -> {effort}")
    for model, effort in sorted(diff_kept.items()):
        print(f"  = {model}: {effort} (kept; not in route table)")

    if dry_run:
        print("dry run: nothing written.")
        return 0

    set_config_value("agent.reasoning_overrides", json.dumps(merged))
    print("written. /model now carries each model's lane effort; "
          "/reasoning still overrides per session.")
    return 0


def setup_cli(subparser) -> None:
    """argparse wiring for `hermes evalroute` (register_cli_command setup_fn)."""
    subs = subparser.add_subparsers(dest="evalroute_action")
    route_p = subs.add_parser("route", help="Classify a task and print a route card")
    route_p.add_argument("task", nargs="*", help="The task description")
    route_p.add_argument("--lane", help="Pin a lane id instead of classifying")
    install_p = subs.add_parser("install-routes", help="Write the route table's effort "
                                   "column into agent.reasoning_overrides")
    install_p.add_argument("--dry-run", action="store_true", help="Show the diff, write nothing")
    subparser.set_defaults(func=evalroute_cli)


def evalroute_cli(args) -> int:
    """Handler for `hermes evalroute ...` (register_cli_command handler_fn)."""
    action = getattr(args, "evalroute_action", None)
    if action == "install-routes":
        return install_routes(dry_run=bool(getattr(args, "dry_run", False)))
    if action == "route":
        task = " ".join(getattr(args, "task", []) or [])
        lane = getattr(args, "lane", None)
        if not task and not lane:
            print("usage: hermes evalroute route [--lane <lane-id>] <task>")
            return 2
        try:
            if lane:
                print(_card_for_args(f"--lane {lane} {task}".strip()))
            else:
                print(_card_for_args(task))
        except Exception as exc:
            print(f"evalroute: {exc}")
            return 1
        return 0
    print("usage: hermes evalroute {route|install-routes}")
    return 2
