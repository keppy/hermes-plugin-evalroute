# hermes-plugin-evalroute

Route a task to the right **(model, reasoning effort) arm** before you start.

This plugin is a **thin adapter**: all behaviour lives in the
[`evalroute`](https://github.com/keppy/evalroute) library (PyPI) — the
classifier, the flywheel ledger, dispatch, the dataset sync, and the route
table. The plugin changes only when the Hermes manifest/hooks wiring or the
lib↔plugin contract changes; classifier, ledger, dispatch, dataset and
route-table work ship as library releases with no catalog PR.

## What it registers

| registration | name | provider |
| --- | --- | --- |
| tool | `evalroute_route` | `evalroute.routing.evalroute_route` |
| slash command | `/route` | `evalroute.routing.handle_route_command` |
| slash command | `/rate` | `evalroute.flywheel.handle_rate` |
| CLI command | `hermes evalroute …` | `evalroute.cli.setup_cli` / `evalroute_cli` |
| hook | `pre_llm_call` | first-turn sniff (plugin-local `sniff.py`) |
| hooks | `pre_command`, `post_llm_call` | `evalroute.flywheel.on_pre_command` / `on_post_llm_call` |
| skill | `evalroute-routing` | bundled in this repo |

A contract guard (`evalroute.contract.CONTRACT_VERSION == 1`) refuses to
register when the installed library speaks a different contract than this
adapter was written for — update this plugin instead of half-working.

## Commands

- `/route <task>` (or `hermes evalroute route "<task>"`) — classify a task
  into a lane, print a route card: model, effort, provenance, route id.
  `--lane <id>` pins; `--json` emits the machine envelope.
- `/rate pass|fail|skip [--route-id <id>] [--lane <id>] [--model <id>
  --effort <level>] [--note <text>]` (or `hermes evalroute rate …`) — label
  the outcome; this is the flywheel's only data source.
- `hermes evalroute …` — also `dispatch <brief.md>` (route a brief, spawn a
  worker on that arm, print the rate line), `install-routes` (effort
  defaults into `agent.reasoning_overrides`), `sync` (pin the published
  route table), `report` (a static HTML page over the ledger, sessions,
  trains, drift), `contribute` (opt-in: upload whitelist-redacted outcome
  rows to your own path in the shared dataset — off until you set
  `evalroute.contribute: true` with `hermes config set`; `--dry-run` shows
  the exact rows first), and more; `hermes evalroute` with no verb prints the
  workflow epilog, `--version` the resolved library.

## Install

```bash
hermes plugins install keppy/hermes-plugin-evalroute
hermes plugins enable evalroute
hermes evalroute install-routes --dry-run   # review, then drop --dry-run
```

Installs from the plugin catalog; `pyproject.toml` pulls
`evalroute>=0.8,<0.9` from PyPI into Hermes's managed environment. No
credentials, no `requires_env`.

## Why the split

Before the split, the routing core shipped inside the plugin: every
classifier tweak, ledger field, or route-table regen was a plugin release,
and a plugin release means a catalog pin, an install-probe-tag cycle, and a
review of unrelated routing changes under Hermes' manifest rules. After the
split the plugin is manifest + hooks + a contract guard — the pieces that
are actually about Hermes — and the library carries everything else. A
library release is `uv publish`; a plugin release is now rare by design.

## Details

Classifier design (rules vs LLM fallback), facets, the ledger row kinds,
the route-table provenance states, the harness, the shim, and the dataset
sync are documented in the [library README](https://github.com/keppy/evalroute#readme).
