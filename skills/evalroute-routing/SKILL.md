---
name: evalroute-routing
description: Use when picking a model or reasoning effort for a task, or when asked which model to use.
version: 0.2.0
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

## The workflow (route → arm → rate)

Every routed task follows the same four steps, in the session that runs the
work. The card prints this as its `next:` line so nobody has to remember it:

1. **`/route <task>`** (or `evalroute_route`, or `hermes evalroute route "<task>"`)
   — prints the card: lane, model, effort, provenance, facets.
2. **Check the lane before doing anything.** The card says which layer
   decided (`rules match` / `LLM fallback` / `defaulted`). If the lane is
   wrong, fix it NOW with `/route --lane <lane-id> <same task>` — the
   reroute re-logs the assignment and `/rate` attributes to the LAST route.
3. **Set the arm: `/model <model>`** from the card's `run:` line. If
   `install-routes` was run (per-model efforts in
   `agent.reasoning_overrides`), effort follows automatically; otherwise
   also `/reasoning <effort>`. Do it before turn 1 — mid-session switches
   re-read the whole context at full input price.
4. **After the work, `/rate pass|fail [--lane <id>] [--note why]`** — one
   word labels the outcome; the note is the diagnosis. `--lane` files a
   correction if the route was wrong. From the terminal:
   `hermes evalroute rate pass --note ...`.

Steps 1 and 4 are where the data comes from: routes that are never rated
teach the table nothing. The flywheel correlates /rate with the last route
in the session and the model that actually served the turns.

## Procedure notes

The workflow above is the spine; these are the details that bite:

- **The classifier is two-layer.** Strong keyword rules are trusted outright;
  weak signal escalates to one small LLM call, and the card says which layer
  decided (`rules match` / `LLM fallback` / `defaulted`). "Free, no API calls"
  only holds for the rules-strong path.
- **Run `/route` BEFORE the work starts.** The plugin cannot switch the model
  for you: say `/model <model>` out loud (or run it yourself if the user
  asks). Switching before turn 1 is free; mid-session switches re-read the
  whole context at full input price.
- **Read the provenance line.** Rows say `priors 2026-09-26` until replaced
  by the user's own measured data. Treat `verify`/`untested`/`unmeasured`
  rows as hypotheses, and say so when you quote the route.
- **Orchestration lane:** the route sets the ORCHESTRATOR's effort; the
  subagents' effort is separate — `delegation.reasoning_effort`, low-medium
  per the table. Set both, not just the model.
- **When the user's task is ambiguous** (no keyword hit), the card defaults
  to long-doc-reading with confidence 0.0 and says so. Ask for one more
  sentence about what the work involves, or pin with `--lane`.
- **If the user narrates a verdict** ("that worked", "that was garbage"),
  suggest `/rate` (or offer to run it for them). If they say the lane was
  wrong, reroute with `--lane` and rate in one flow.

## Updating the table

The table is `data/routes.yaml` inside the plugin. The harness is vendored at
`harness/evalroute.py` (its own venv, `openai`+`anthropic`): run k samples
per arm over a taskset, `report --csv`, then `routes_from_report.py` flips
the lane's row and stamps `provenance: measured ...`. Update the `provenance`
of anything still on `priors 2026-09-26`. Model ids must match `/model`
spelling exactly.
