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
- Bundled skill `evalroute-routing` — when to route, and the gotchas
  (switch before turn 1; subagents get `delegation.reasoning_effort`).

Everything is local file math over `data/routes.yaml` — no network, no
credentials, nothing paid. The route table ships as the **2026-09-26 priors
snapshot** (public benchmarks, many vendor-run): replace rows with your own
`evalroute.py report --csv` output as soon as you have measured data, and
update each row's `provenance`.

## Install

```bash
hermes plugins install keppy/hermes-plugin-evalroute
hermes plugins enable evalroute
hermes evalroute install-routes --dry-run   # review, then drop --dry-run
```

Requires nothing beyond `pyyaml`. No `requires_env` — there are no
credentials to gate.

## The route table

`data/routes.yaml` — one row per lane: `id`, `keywords` (the classifier),
`model`, `effort`, `escalation`, `provenance`, `notes`. Lane taxonomy is the
union of the two source tables (9 lanes); where they disagreed (long-doc
merged into web-research in one, orchestration only in the other) both are
kept as distinct lanes. Model ids must match `/model` spelling exactly.

## Known limitations

- **The classifier is keyword rules, not an LLM.** Deterministic and free;
  misses paraphrases. No-hit defaults to long-doc-reading at confidence 0.0
  and says so in the card. Pin with `--lane`.
- **The plugin cannot switch the model for you.** `/route` prints the card;
  you run `/model <id>`. Run it before turn 1 — mid-session switches re-read
  the whole context at full input price.
- **One effort slot per model id** (`agent.reasoning_overrides`); when a model
  serves two lanes, `install-routes` keeps the higher effort, since
  under-routing effort is the costlier miss (coverage, not verbosity).
- **Provenance is priors until you measure.** Three rows are explicitly
  untested/unmeasured/contested; the card prints the provenance verbatim so
  nobody mistakes a hypothesis for a result.
- **No harness, no shim in this plugin.** Running the evalroute harness
  (paid API cells) stays in its own environment (`EVALROUTE_PYTHON`) via the
  terminal; the `cmd`-api Hermes shim is a second pass.

## Roadmap (second pass)

- Hermes shim for `api: "cmd"` arms — route cards backed by *measured*
  coverage/cost, not priors.
- First-turn sniff (`pre_llm_call` advisory) once the lane rules prove out.
- `routes.yaml` generation straight from `report --csv`.
