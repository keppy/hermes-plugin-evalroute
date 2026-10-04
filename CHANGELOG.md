# Changelog

## [0.5.0] - 2026-10-03

### Added

- `hermes evalroute sync [--revision R] [--status] [--clear]`: pin the
  published route-table dataset `keppy/evalroute-flywheel` under
  `<hermes home>/evalroute/dataset/<sha>/`. The dataset is fetched only on
  the user's explicit `sync`, pinned to a resolved commit sha, and routing
  works fully offline without it: without (or after `--clear`) every code
  path uses the bundled `data/routes.yaml` exactly as before. `sync`
  downloads only the `routes/` config (not the measured evidence),
  validates the table through the same lane validation as the bundled
  loader plus the MANIFEST's `routes_sha256`, refuses a bad download
  without touching the active table, and needs `huggingface_hub`
  (`pip install huggingface_hub`) — an optional extra, so the plugin keeps
  importing it lazily and lists no new dependency.
- The route card gains a `table:` provenance line (also in the
  `_tool_result` JSON envelope / `route --json`): `table: bundled (plugin
  0.4.1)` or `table: keppy/evalroute-flywheel @ <sha12> (generated <date>,
  plugin <version>)`.
- `scripts/publish_dataset.py` (run by hand, stdlib + huggingface_hub
  only): stages `measured/<lane>/{runs.jsonl,report.csv,tasks.jsonl}`,
  `measured/models/`, a byte copy of `data/routes.yaml` with a
  `routes/MANIFEST.json` (version, commit, sha256s, date) and a dataset
  card, and uploads it; `--dry-run` prints the tree and uploads nothing.

### Changed

- Route-table resolution is now: pinned dataset table if
  `<hermes home>/evalroute/dataset/current` names a dir whose
  `routes/routes.yaml` parses and validates, else bundled. A corrupt
  dataset table falls back to bundled with one warning on stderr —
  routing must always work. `_load_routes` shares `_validate_lanes` with
  `sync`; no lane, keyword or number changed.

## [0.4.1] - 2026-10-03

### Fixed

- `hermes evalroute dispatch` crashed with `ModuleNotFoundError: flywheel` when
  the plugin is installed as a package: `dispatch.py` is loaded by file path
  (to dodge a pytest package-shadowing hazard), so its bare sibling imports had
  no package context. `tools.py` now injects the already-imported `flywheel`
  and `tools` modules before executing it. 0.4.0's suite was green because
  pytest imports the plugin root as top-level, where the bare import works —
  the first live run on PATH caught it.

## [0.4.0] - 2026-10-03

### Added

- `hermes evalroute dispatch <brief.md>`: one verb that routes the brief,
  spawns `hermes chat` on exactly that arm (model, effort, provider) as a
  subprocess worker, and prints the `rate it:` line for step 4. Options:
  `--lane`, `--in`, `--task`, `--out`, `--timeout` (exit 124, whole process
  tree killed), `--rate-on-exit fail`, `--dry-run`. It never auto-rates
  `pass` — a worker's "done" is a claim, the human verifies and rates.
- `hermes evalroute route --json`: machine-readable envelope
  (`model`/`effort`/`provider`/`route_id`).
- `hermes evalroute route --lane <lane> --replace-route-id <id>`: replace a
  pending route instead of smuggling the flags inside the task string.
- `hermes evalroute rate --model <id> --effort <level>`: confirm the actual
  arm from the CLI (previously chat-only).

### Changed

- Outcome rows from dispatched work carry `arm_attribution: explicit_user`,
  recorded from the spawn arguments instead of leaving the arm unknown. This
  is a stronger row than an observational switch — but it is still a
  caller-stated arm, not a paired experiment.
- `tests/test_artifacts.py` skips the vendored-adjudication comparison when
  `gonogo` is not installed, like `tests/test_adjudicate.py` already did.

### Fixed

- `dispatch` fails fast (exit 1, nothing spawned) when the route could not be
  recorded, instead of spawning an unattributable worker.
- `dispatch --timeout` kills the whole process tree (Windows: taskkill /T /F
  in a new process group; POSIX: killpg), not just the `hermes` parent.
- `_effort_auto` honors `$HERMES_HOME/config.yaml` when `hermes_constants`
  is unimportable, matching `labels_path`'s fallback.
