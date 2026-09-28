# hermes-plugin-evalroute

Route a task to the right **(model, reasoning effort) arm** before you start.
Built around the evalroute procedure — a one-file harness that measures
**cost per verified success** per task lane (vendored at `harness/evalroute.py`
so this repo is self-contained: measure, then serve the results) — this
plugin turns its output into a route table with provenance on every row:
pick the lane, pick the model, pick the effort, as data.

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

## The loop, end to end

![evalroute demo](docs/demo.gif)

One command classifies the task and prints the arm (`route`), the card
tells you what to run and how to check the lane (`next:`), and one command
closes the loop (`rate`). The GIF shows the real plugin (v0.3.2, installed
at the release pin) on a DL/ML lane task: the `basis: measured` line and the
gonogo stamp are live output — 10 shared tasks, both arms at 100% coverage,
$p = 1.000$, a cost decision between ties — and the `logged:` line confirms
which row the rating closed. Commands and output are verbatim captures;
only the typing animation is authored. The demo pair was then pruned from
the labels file — demo records never live in the ledger.

## What this changes for Hermes

Hermes ships one default (model, effort) per user; every task pays the same
arm regardless of shape. This plugin makes that a per-task decision, backed
by three things a default can't have:

- **Measured routes.** Three lanes measured with the evalroute harness
  (10 tasks x 3 samples x 4 arms each, ~$5 total spend): routine coding,
  DL/ML research engineering, alignment reasoning. Every winner was
  statistically indistinguishable from its runner-up at n=10 (McNemar, via
  gonogo) — the routes are cost decisions on tied arms, and the card says
  so instead of implying a quality gap. Routine coding overturned the
  priors' vendor pick: glm-5.3-flash at medium effort covered every task
  at $0.00005/success, 2.4–3.9x cheaper than the V4.1 Flash arms at equal
  coverage.
- **Provenance states.** Every row is `priors`, `observed`, or `measured`.
  Nothing hypothesis-shaped masquerades as a result — the card prints the
  row's provenance verbatim, statistical stamp included.
- **A flywheel.** Daily use labels itself: `/route` logs the assignment,
  `/model` and `/reasoning` switches log accept/reject, `/rate pass|fail`
  logs the outcome — with facets, so an audit of a long research plan is
  `long-doc + domain-dlml + tier-hard`, not one collapsed label.

The platform adoption path needs no core changes: a route table is data plus
config writes (`agent.reasoning_overrides`), and the effort half of routing
already ships in Hermes. A community-maintained measured table is this same
plugin with better data in it.

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

## Facets: labels with dimensions

A lane is the routing decision; facets are the label. Every route also
captures the task's shape along three axes, defined in `data/facets.yaml`:

- **input-shape**: `long-doc` | `interactive`
- **domain**: `domain-dlml` | `domain-alignment` | `domain-math` | `domain-prose` | `domain-research`
- **demand-tier**: `tier-routine` | `tier-hard` | `tier-orchestration`

So "audit my RL training plan files" is recorded as `long-doc +
domain-dlml + tier-hard` — three facts about one task — instead of one
collapsed lane. The rules layer derives facets from keyword evidence
(conservative: only lanes that drew hits claim facets); the LLM fallback
names them semantically in the same structured call.

When a task claims facets on multiple axes, the card states the
conjunctive rule and the arm follows the most demanding facet — domain
beats input-shape (judgment is the scarcer resource; bulk input is what
flash-class models are for), tier beats both:

```
facets: long-doc + domain-dlml (conjunctive — domain drives the arm; input-shape rides along)
```

Facet conjunctions aggregate in `routes_from_labels.py`, so the
high-dimensional nodes — "how do long-doc x dl-ml tasks fare on arm X?" —
fill in from daily use without controlled-batch spend.

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
# in the harness venv (openai + anthropic; it stays out of the Hermes venv)
python harness/evalroute.py run -m models.json -t tasks.jsonl -k 3
python harness/evalroute.py report -o runs.jsonl --csv report.csv
python routes_from_report.py --csv report.csv --runs runs.jsonl
```

The harness is vendored so the loop is complete inside one repo: write
tasksets (deterministic `python` checkers where possible — validate every
checker against a reference solution before paid runs), run k samples per
arm, report, then flip the lane's row. `routes_from_report.py` applies the
report's own routing rule (coverage-gated lowest all-in $/success), stamps
`provenance: measured ...` with a gonogo McNemar stamp when the winner and
runner-up shared cases, preserves each lane's `keywords`/`match_hint`/
`escalation`/`notes`, and carries unmeasured lanes over verbatim —
regeneration never silently deletes a route. The tier-a tasksets and runs
that produced the current measured lanes are under `examples/artifacts/`
(sets: `tasks.jsonl`; raw run records: `runs.jsonl`).

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
- **The evalroute harness stays out of the Hermes venv.** It's vendored at
  `harness/evalroute.py` (run it in its own venv with `openai`/`anthropic`;
  `EVALROUTE_PYTHON` points the plugin's runners at that interpreter).
  This plugin itself requires nothing beyond `pyyaml`.
- **The sniff hook is advisory only.** It speaks when the classifier is
  confident and the session's model disagrees with the lane's route; it
  never rewrites, blocks, or switches.

## Roadmap

- **In progress:** flip the remaining 6 priors lanes to measured. Three done
  (routine-coding, dl-ml, alignment — 2026-09-27, ~$5); each needs a
  10-task set with checkers that discriminate (pre-spend validation
  against a reference solution catches broken fixtures before paid runs).
- **Theory stretch — classifier fine-tune.** Train a lane classifier with
  the thomas training harness, fed by flywheel labels (task text → lane,
  plus the `--lane` corrections). LLM fallback becomes the last resort;
  different kinds of users could run different locally-finetuned
  classifiers. Needs a hand-checked experiment first (label volume,
  class balance, per-user vs. global training) before any training spend.
  Not scheduled until the flywheel has enough labels to train on.
- **Live:** the flywheel (`/route` + `/rate` in daily use, plus implicit
  verdicts from `/model` and `/reasoning` switches). First human-rated
  outcome recorded 2026-09-27. Labels snapshots publish with this repo
  (see below); a month of them decides which controlled batch to buy next —
  observational data prioritizes, the harness measures.
- Gonogo is wired (McNemar stamps on measured rows, decide() verdicts on
  observed rows). Remaining: thomas integration — lane as a field on the
  Case shape, so intake → lane → route → (model, effort) arm closes the
  loop.

## Flywheel: labels from daily workflow

The controlled harness is not the only source of data. As you use `/route`
in daily sessions, the plugin quietly builds an observational dataset:

- **`/route`** logs the assignment (lane, recommended arm, method,
  confidence, facets) to `<hermes home>/evalroute/labels.jsonl` — the
  task text you typed is the label.
- **`/model` or `/reasoning` after a route** logs an implicit verdict
  (switching to a different model than the card recommended is recorded as a
  route rejection — no effort required from you).
- **`/rate pass|fail [--lane <id>] [--note ...]`** labels the outcome when you
  finish a routed task. `--lane` files a lane correction (the classifier's
  favorite food); `skip` discards the pending route. One word is the whole cost.
- Nothing else is recorded: no response bodies, no conversation content, no turn
  telemetry. The last-seen model is kept in memory only, for `/rate` correlation.

**Continuing across sessions (turn caps).** A task that outlives its session —
the turn limit hits, the terminal closes mid-task — is a continuation, not a
new route. The row being labeled is (task, arm), not (task, session):

- Prefer staying in the session: `continue: <what remains>` gets a fresh
  iteration budget, and the arm is session-scoped and persists.
- Otherwise `hermes -c` continues the same conversation, or paste the capped
  session's final turn as the new session's opener.
- Never `/route` the continuation — a new route displaces the pending row. If
  the new session is actually a different task, `/rate skip` clears the
  pending one first.
- Steer as much as you like. The observed layer is defined as
  daily-workflow-with-a-human-in-the-loop; a directive continuation is normal
  operation, and it matches a detailed original prompt better than a bare
  "continue" — which quietly tests prompt-luck instead of the arm. Measured
  rows are untouched: they come from fixed-prompt, fresh-context harness cells.
- Put the methodology in the note: `--note "completed across two sessions
  (turn cap), directive continuation"`. The label records neither cost nor
  session boundaries, and a session-spanning pass re-reads the accumulated
  context at full input price — the note is where that lives.

**Turning labels into route data:** `python routes_from_labels.py` prints
per-lane, per-arm pass rates, lane corrections, and facet conjunction
outcomes; `--apply` writes `data/routes.observed.yaml`. Observed rows carry
honest, weaker provenance:

```
observed 23 tasks, single-arm, pass 78%, 2026-10-30
```

and only where the lane has no `measured` row — observational data can contest
a priors row, never overwrite a measured one. When gonogo is installed, each
observed row also carries its decide() verdict, so a row with 3 outcomes reads
as INSUFFICIENT_EVIDENCE rather than a pass rate someone will trust. The one
strong signal that flips a recommended arm: the routed arm failing 2+ times
while an escalation-tier model passed. Everything else informs; only the
harness's k-sample batches make rows gold.

**Publishing the labels.** The live ledger stays local and append-only;
this repo carries snapshots under `data/flywheel/labels.jsonl`, committed
alongside the code that reads the schema. Records contain routed task
texts and no response bodies; early snapshots include unrated route calls
from install verification. When the dataset grows past a few hundred
outcomes — or multiple users contribute ledgers — it moves to a Hugging
Face dataset with a loader script; the repo snapshot is the v1 home.
