---
name: evalroute-routing
description: Use when picking a model or reasoning effort for a task, or when asked which model to use.
version: 0.1.0
author: James Dominguez (keppy)
license: MIT
---

# Routing a task to the right (model, effort) arm

The route table encodes a two-step procedure: **lane → model** ("best open
weight tier per lane"), then **lane → effort** ("starting settings to test").
Never pick a model without also picking the effort — the README's own numbers
show effort moves results about as much as model choice does (GPT-6 Luna at
max beats GPT-6 Sol at high on DeepSWE; V4.1 Flash drops 39 → 25 with
reasoning off).

## Procedure

1. **Run `evalroute_route` (or `/route`) BEFORE starting the work.** It is
   free, deterministic keyword rules — no API calls. The card gives the lane,
   the model, the effort, the escalation option, and the provenance.
2. **Say the command out loud.** The plugin cannot switch the model for you:
   tell the user to run `/model <model>` before the first turn. Switching
   before turn 1 is free; switching mid-session re-reads the whole context at
   full input price.
3. **Effort usually follows the model automatically** if the user ran
   `hermes evalroute install-routes`: `agent.reasoning_overrides` makes
   `/model` carry the lane's effort. `/reasoning` per session still wins over
   it. Check with `/reasoning show` if a lane needs a different effort than
   the table (e.g. max where the default is high).
4. **Read the provenance line.** Rows say `priors 2026-09-26` until replaced
   by the user's own measured data. Treat `verify`/`untested`/`unmeasured`
   rows as hypotheses, and say so when you quote the route.
5. **Orchestration lane:** the route sets the ORCHESTRATOR's effort; the
   subagents' effort is separate — `delegation.reasoning_effort`, low-medium
   per the table. Set both, not just the model.
6. **When the user's task is ambiguous** (no keyword hit), the card defaults
   to long-doc-reading with confidence 0.0 and says so. Ask for one more
   sentence about what the work involves, or pin with `--lane`.
7. **Label outcomes with `/rate`.** After a routed task finishes, run
   `/rate pass` or `/rate fail` (add `--lane <id>` if the route's lane was
   wrong; `--note` for context). One word, optional correction. This feeds
   the observed-provenance table — the flywheel that decides which controlled
   batch to buy. If the user narrates a verdict ("that worked", "that was
   garbage"), suggest they run /rate (or offer to run it for them).

## Updating the table

The table is `data/routes.yaml` inside the plugin. Replace rows with output
from the evalroute harness (`evalroute.py report --csv`) once the user has
measured data, and update each row's `provenance` from `priors 2026-09-26`
to the measured source. Model ids must match `/model` spelling exactly.
