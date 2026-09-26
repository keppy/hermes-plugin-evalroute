"""evalroute plugin — route a task to the right (model, effort) arm.

Registers one model-facing tool (``evalroute_route``), one slash command
(``/route``), one CLI subcommand (``hermes evalroute``), and a bundled skill.
Everything is local file math over ``data/routes.yaml``: no network calls, no
credentials, nothing paid. The two-step procedure the route table encodes:

1. lane -> model          ("best open weight tier per lane")
2. lane -> effort         ("starting settings to test"; ``install-routes``
                           writes this half into ``agent.reasoning_overrides``
                           so ``/model`` carries it)
"""

from __future__ import annotations

import logging
from pathlib import Path

try:
    from . import schemas, tools
    from . import sniff as _sniff
    from . import flywheel as _flywheel
except ImportError:  # pragma: no cover - pytest imports the plugin root as a top-level module
    import schemas  # type: ignore
    import tools  # type: ignore
    import sniff as _sniff  # type: ignore
    import flywheel as _flywheel  # type: ignore

logger = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).parent / "skills"


def register(ctx):
    """Wire the tool, the sniff hook, the slash command, the CLI command, and the skill."""
    ctx.register_tool(
        name="evalroute_route",
        toolset="evalroute",
        schema=schemas.EVALROUTE_ROUTE,
        handler=tools.evalroute_route,
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
        handler=tools.handle_route_command,
        description="Classify a task and print a (model, effort) route card",
        args_hint="[--lane <lane-id>] <task>",
    )

    ctx.register_cli_command(
        name="evalroute",
        help="Model routing: classify a task, install per-model effort defaults",
        setup_fn=tools.setup_cli,
        handler_fn=tools.evalroute_cli,
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
    tools.set_llm_facade(getattr(ctx, "llm", None))
