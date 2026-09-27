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
- Session-cap continuations: on the first turn, an unrated route in the
  labels file with no in-memory counterpart means a previous session armed
  a row and never closed it (a hit session cap, a closed terminal, a CLI
  route). One advisory line tells the user to keep the arm and `/rate`
  rather than re-route — it supersedes the mismatch check, because a filed
  route is stronger evidence than a keyword classification.
"""

from __future__ import annotations

import logging

try:
    from . import tools
except ImportError:  # pragma: no cover - pytest imports the plugin root as a top-level module
    import tools  # type: ignore

try:
    from . import flywheel as _fw
except ImportError:  # pragma: no cover
    import flywheel as _fw  # type: ignore

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

    # Cross-session continuation: an unrated route in the file with no
    # in-memory counterpart was armed by another process (previous session
    # or the CLI). Session caps make this common — one advisory line, and
    # it supersedes the mismatch check below: a filed route is stronger
    # evidence than a first-turn classification.
    try:
        if _fw._MEMORY.get("route") is None:
            pending = _fw.pending_route_from_file()
            if pending is not None:
                task = (pending.get("task") or "")[:60]
                return {"context": (
                    f"[evalroute] An unrated route is pending from an earlier "
                    f"session: {pending.get('lane', '?')} - {task!r}. If you are "
                    f"continuing that task, stay on {pending.get('model', '?')} "
                    f"and `/rate pass|fail --note ...` when it completes - do NOT "
                    f"`/route` it again (a new route displaces the pending row). "
                    f"Starting a different task? `/rate skip` clears it first."
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
