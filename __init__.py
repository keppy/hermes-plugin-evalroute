"""evalroute plugin — route a task to the right (model, effort) arm.

Thin adapter over the ``evalroute`` library (PyPI; keppy/evalroute): this
module only wires the library into Hermes — the classifier, the flywheel
ledger, dispatch, the dataset sync and the route table all live in the
library. It registers one model-facing tool (``evalroute_route``), two
slash commands (``/route``, ``/rate``), one CLI subcommand (``hermes
evalroute``), three hooks, and a bundled skill.
"""

from __future__ import annotations

import logging
from pathlib import Path

import evalroute
from evalroute import cli as _cli
from evalroute import contract as _contract
from evalroute import flywheel as _flywheel
from evalroute import routing as _tools
from evalroute import schemas

try:
    from . import sniff as _sniff
except ImportError:  # pragma: no cover - pytest imports the plugin root as a top-level module
    import sniff as _sniff  # type: ignore

PLUGIN_CONTRACT = 1


def _contract_mismatch() -> str | None:
    """Guard the lib↔plugin contract before registering anything (mirrors thomas).

    Reads ``evalroute.contract.CONTRACT_VERSION`` live through the module so
    an in-process change (or a test monkeypatch) is seen, not just import time.
    """
    lib_contract = _contract.CONTRACT_VERSION
    if lib_contract != PLUGIN_CONTRACT:
        return (f"evalroute library contract {lib_contract} != plugin contract "
                f"{PLUGIN_CONTRACT}; update hermes-plugin-evalroute "
                f"(installed evalroute {evalroute.__version__})")
    return None


_mismatch = _contract_mismatch()
if _mismatch:  # pragma: no cover - the installed lib is within the pin; a future lib trips this
    raise RuntimeError(_mismatch)

logger = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).parent / "skills"


def _chat_route(args: str) -> str:
    """/route in chat: card speaks slash commands."""
    _tools.set_surface("hermes-chat")
    return _tools.handle_route_command(args)


def _hermes_cli(args):
    """`hermes evalroute ...`: card speaks `hermes evalroute ...` commands."""
    _tools.set_surface("hermes-cli")
    return _cli.evalroute_cli(args)


def register(ctx):
    """Wire the tool, the sniff hook, the slash command, the CLI command, and the skill."""
    # Re-read live: an in-process lib upgrade must not silently register a
    # mismatched contract (the module-level guard only sees import time).
    live = _contract_mismatch()
    if live:
        raise RuntimeError(live)

    # The library speaks one of three command surfaces (evalroute >= 0.7):
    # "hermes-chat" for /route, /rate and the tool; "hermes-cli" for
    # `hermes evalroute ...`. One register() serves both, so set it per call.
    _tools.set_surface("hermes-chat")

    ctx.register_tool(
        name="evalroute_route",
        toolset="evalroute",
        schema=schemas.EVALROUTE_ROUTE,
        handler=_tools.evalroute_route,
    )

    ctx.register_hook("pre_llm_call", _sniff.sniff)

    # Flywheel: label routes/outcomes as part of daily workflow. pre_command
    # (/model, /reasoning after a route) and post_llm_call (last-seen model)
    # are observers; /rate is the explicit outcome label.
    ctx.register_hook("pre_command", _flywheel.on_pre_command)
    ctx.register_hook("post_llm_call", _flywheel.on_post_llm_call)

    ctx.register_command(
        "rate",
        handler=_flywheel.handle_rate,
        description="Label the last routed task pass|fail (feeds the observed route table)",
        args_hint="pass|fail [--lane <lane-id>] [--note <text>]",
    )

    ctx.register_command(
        "route",
        handler=_chat_route,
        description="Classify a task and print a (model, effort) route card",
        args_hint="[--lane <lane-id>] <task>",
    )

    ctx.register_cli_command(
        name="evalroute",
        help="Model routing: classify a task, install per-model effort defaults",
        setup_fn=_cli.setup_cli,
        handler_fn=_hermes_cli,
        description="Route tasks to the right (model, reasoning effort) arm",
    )

    if _SKILLS_DIR.is_dir():
        for child in sorted(_SKILLS_DIR.iterdir()):
            skill_md = child / "SKILL.md"
            if child.is_dir() and skill_md.exists():
                ctx.register_skill(child.name, skill_md)

    # Host-owned LLM facade for the weak-signal classifier fallback
    # (ctx.llm.complete_structured). Works under the default trust policy:
    # no provider/model/task overrides. Stash; None disables the fallback.
    _tools.set_llm_facade(getattr(ctx, "llm", None))
