"""Tier 2: first-turn sniff — advisory mismatch detection via pre_llm_call.

Fires once per turn; we only act on the FIRST turn of a session. Classifies
the user's opening message into a lane and compares the lane's route model
against the model actually running. On a high-confidence mismatch, injects a
single line asking the agent to mention `/route` at the top of its reply.

Design rules (from the phase-1 design conversation):
- Advisory ONLY. Never rewrites, never blocks, never switches models.
- Rules-first classifier, same one as the tool — no LLM call, no cost.
- Silent unless confident: fires only when (a) the classifier scored at
  least MIN_HITS keyword hits, and (b) the routed model differs from the
  active model.
- Never fires on subagents or gateway platforms where the user may have
  already routed deliberately; CLI/desktop only by default.
- Pending route notice: on the first turn, an unrated route in the labels
  file with no in-memory counterpart was armed by another process (a
  previous session, the CLI). One advisory line names the route id and says
  its session is unknown; a route this process issued (`/route` before turn
  1) is the normal workflow and gets no notice.
"""

from __future__ import annotations

import logging

from evalroute import routing as tools
from evalroute import flywheel as _fw

logger = logging.getLogger(__name__)

# Minimum keyword hits before the sniff will speak up. One hit = ambiguous
# ("test" is in almost anything); two+ = the lane is probably real.
MIN_HITS = 2

# Platforms where the sniff is active. The mismatch advice is for interactive
# sessions where the user can still act on it cheaply.
_PLATFORMS = {"cli", "tui", "desktop"}


def sniff(session_id: str, user_message, is_first_turn: bool, model: str,
          platform: str, **kwargs):
    """pre_llm_call callback: one advisory line on first-turn model mismatch."""
    if not is_first_turn or not model:
        return None
    if (platform or "").lower() not in _PLATFORMS:
        return None
    # Multimodal turns arrive as a list of parts; take the text.
    if isinstance(user_message, list):
        user_message = " ".join(
            part.get("text", "") for part in user_message
            if isinstance(part, dict) and part.get("type") == "text"
        )
    if not isinstance(user_message, str) or not user_message.strip():
        return None

    # A pending route with no in-memory counterpart was armed by another
    # process (previous session, CLI). The sniff only runs on single-user
    # platforms (cli/tui/desktop), so a route THIS process issued is the
    # user's own `/route` before turn 1 - the normal workflow, not a notice.
    # Ownership of a file-only route is still unknown; the text says so.
    try:
        pending = None
        if _fw._MEMORY.get("route") is None:
            pending = _fw.pending_route_from_file()
        if pending is not None:
            task = (pending.get("task") or "")[:60]
            return {"context": (
                f"[evalroute] An unrated route is pending in this profile "
                f"(session unknown): {pending.get('lane', '?')} - {task!r}. "
                f"Route ID: {pending.get('id', 'legacy')}. If you are "
                f"continuing that task, stay on {pending.get('model', '?')} "
                f"and `/rate pass|fail "
                f"{'--route-id ' + pending['id'] + ' ' if pending.get('id') else ''}"
                f"--note ...` when it completes; verify the selected row. Please do NOT "
                f"`/route` it again (a new route can hide this pending row as the latest). "
                f"Starting a different task? Rate or skip that ID first if it is yours."
            )}
    except Exception:
        pass  # advisory layer must never break a turn

    try:
        lane, conf, hits = tools.classify(user_message)
    except Exception:
        return None  # a broken route table must never break the turn
    if len(hits) < MIN_HITS:
        return None
    routed = lane.get("model") or ""
    if not routed or _same_model(routed, model):
        return None
    return {"context": (
        f"[evalroute] This looks like {lane['label']} work; the route table's "
        f"pick is {routed} @ {lane.get('effort', 'medium')} but this session is "
        f"running {model}. If the user has not chosen deliberately, mention "
        f"`/route {user_message.strip()[:120]}` at the top of your reply so they "
        f"can switch before work begins. Advisory only - do not block or delay "
        f"the task."
    )}


def _same_model(routed: str, active: str) -> bool:
    """Loose equality: bare slugs match prefixed ids (`glm-5.3` == `z-ai/glm-5.3`)."""
    r = routed.lower().strip().split("/")[-1]
    a = active.lower().strip().split("/")[-1]
    return r == a
