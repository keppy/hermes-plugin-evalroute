# hermes-plugin-evalroute

Route a task to the right **(model, reasoning effort) arm** before you start.
A Hermes plugin wrapping the [evalroute](https://github.com/keppy/evalroute)
procedure: pick the lane, pick the model, pick the effort — as data, with
provenance on every row.

- `/route <task>` — classify a task into a lane, get a route card (model,
  effort, escalation, provenance) before the first turn. Works in CLI,
  gateway, and desktop.
- `evalroute_route` tool — the same card as a model-facing tool, so the agent
  consults it when planning (including for subagent delegation).
- `hermes evalroute install-routes` — write the route table's effort column
  into `agent.reasoning_overrides`, so `/model` carries the lane's effort
  automatically. `--dry-run` shows the diff.
- First-turn sniff (`pre_llm_call` hook) — on the first turn of a session,
  a high-confidence lane/model mismatch gets one advisory line suggesting
  `/route`. Silent when you're already on the right model. CLI/TUI/desktop
  only; never blocks, never switches anything.
- `runners/hermes-shim.py` — the `api: "cmd"` adapter for running Hermes
  itself as an evalroute arm (see below).
- `routes_from_report.py` — regenerate `data/routes.yaml` from measured
  `evalroute.py report --csv` output, preserving classifier fields and
  unmeasured rows.
- Bundled skill `evalroute-routing` — when to route, and the gotchas
  (switch before turn 1; subagents get `delegation.reasoning_effort`).

The route table ships as the **2026-09-26 priors snapshot** (public
benchmarks, many vendor-run): replace rows with your own measured data as
soon as you have it, and update each row's `provenance`.

## Classification: rules first, LLM when weak

The classifier's first layer is deterministic keyword rules over
`data/routes.yaml` — free, no API calls — but rules alone misroute
paraphrase ("manage life, writing, and researchy tasks" has zero keyword
signal) and negation ("not usually hard math though" used to count as a
math hit; the rules now guard negated keywords). So routing is two-layer:

1. **Strong rules** (2+ distinct keyword hits on the winning lane) — trusted
   outright, no LLM call.
2. **Weak signal** (0-1 hits) — one small structured call via `ctx.llm`
   (the host's own model and auth; works under the default trust policy,
   no new credentials). The LLM judges what the work IS — a description of
   an assistant's duties routes to `orchestration`, not to whatever nouns
   appear.

The card always prints which layer decided: `rules match`, `LLM fallback`,
or `no keyword hit - defaulted`. If the LLM call fails (offline, trust
denied), the weak rules result stands and the card says so. Pin manually
with `/route --lane <id>` when you know better.

## Install

```bash
hermes plugins install keppy/hermes-plugin-evalroute
hermes plugins enable evalroute
hermes evalroute install-routes --dry-run   # review, then drop --dry-run
```

Requires nothing beyond `pyyaml`. No `requires_env` — there are no
credentials to gate.

## The route table

`data/routes.yaml` — one row per lane: `id`, `keywords` (the rule layer),
`model`, `effort`, `escalation`, `provenance`, `notes`. Lane taxonomy is the
union of the two source tables (9 lanes); where they disagreed (long-doc
merged into web-research in one, orchestration only in the other) both are
kept as distinct lanes. Model ids must match `/model` spelling exactly.

### Regenerating from measured data

```bash
python routes_from_report.py --csv report.csv   # output of evalroute.py report --csv
```

Within each lane the generator applies the report's own routing rule
(coverage-gated lowest all-in $/success), stamps `provenance: measured ...`,
preserves each lane's `keywords`/`match_hint`/`escalation`/`notes` from the
existing table, and carries unmeasured lanes over verbatim — regeneration
never silently deletes a route.

## The Hermes shim (`api: "cmd"` arms)

To run Hermes itself as an evalroute arm (agentic cells):

```json
{"name": "hermes-glm@high", "api": "cmd", "effort": "high",
 "cmd": "python runners/hermes-shim.py --model {model} --effort {effort} --prompt {prompt_file}",
 "model": "z-ai/glm-5.3", "in": 0.91, "out": 2.86, "timeout": 3600}
```

The shim wraps `hermes -z` (one-shot; tools, memory, AGENTS.md loaded as
normal; approvals auto-bypassed), reads the usage report (`--usage-file`),
and emits the contract evalroute expects: the answer on stdout, then one
JSON last line `{"text": ..., "usage": {"inp", "out", "cache_read"}}`. A
non-zero hermes exit becomes an `error` field in that line (the harness
records an error row and retries on the next `run`); the shim itself always
exits 0. `--system` is prepended to the prompt. `--hermes PATH` overrides
the executable (tests use this to point at a fake — no real runs).

## Known limitations

- **The rule layer is keywords.** Deterministic, free, and misses
  paraphrase — which is what the LLM fallback is for. The fallback's
  quality tracks whatever model the session is on; it costs one small
  structured call (temp 0, 256 tokens) only when rules are weak.
- **The plugin cannot switch the model for you.** `/route` prints the card;
  you run `/model <id>`. Run it before turn 1 — mid-session switches re-read
  the whole context at full input price.
- **One effort slot per model id** (`agent.reasoning_overrides`); when a model
  serves two lanes, `install-routes` keeps the higher effort, since
  under-routing effort is the costlier miss (coverage, not verbosity).
- **Provenance is priors until you measure.** Several rows are explicitly
  untested/unmeasured/contested; the card prints the provenance verbatim so
  nobody mistakes a hypothesis for a result.
- **The evalroute harness stays out of the Hermes venv.** Paid API cells run
  in the harness's own environment (`EVALROUTE_PYTHON`) via the terminal;
  this plugin only ships the shim and the route tooling.
- **The sniff hook is advisory only.** It speaks when the classifier is
  confident and the session's model disagrees with the lane's route; it
  never rewrites, blocks, or switches.

## Roadmap

- Real evalroute runs feeding `routes_from_report` — replace priors with
  measured provenance, lane by lane.
- Gonogo/thomas integration: lane as a field on the Case shape, so
  intake → lane → route → (model, effort) arm closes the loop.
