# Changelog

## [0.6.4] - 2026-10-05

### Changed
- Pin `evalroute>=0.9.2,<0.10`. Plugin code unchanged. 0.9.2 makes the harness part of the
  arm (`harness` on outcome rows and in `contribute`), adds the verified `claude-code` named
  runner for `hermes evalroute dispatch --runner claude-code`, and `--model/--effort`
  overrides on dispatch. Floor bump because the plugin environment never re-resolves an
  unchanged requirement string.

## [0.6.3] - 2026-10-05

### Changed
- Pin `evalroute>=0.9.1,<0.10`. Plugin code unchanged. 0.9.1 ships flywheel schema 2
  (lane corrections leave as lane pairs, never text; `max_turns` recorded as part of the
  arm — `hermes evalroute dispatch` now passes `--max-turns` from `agent.max_turns`) and
  the per-lane "real labeled rows" table in `report`. The lower bound moves because the
  Hermes plugin environment is keyed on the requirement string: an unchanged pin never
  re-resolves, so a floor bump is how a library fix reaches an installed plugin.

## [0.6.2] - 2026-10-04

### Changed

- Pin `evalroute>=0.9,<0.10`: brings `hermes evalroute export-cases` (your pinned routes
  as encoder training rows — local file, counts only on stdout) and `hermes evalroute
  install-encoder <dir|owner/name>` (opt-in local classifier between the keyword rules
  and the LLM fallback; needs `evalroute[encoder]` in the plugin environment — the
  plugin does not install torch). Nothing changes until you run `install-encoder`.
  Plugin code unchanged.

## [0.6.1] - 2026-10-04

### Changed

- Pin `evalroute>=0.8,<0.9`: brings `hermes evalroute contribute` (opt-in, gated on
  `evalroute.contribute: true` in config.yaml, redacted outcome rows to your own HF
  path), `--version`, and `evalroute_version` in `route --json`. Catalog description
  gains the matching Disclosure clause. Plugin code unchanged.

## [0.6.0] - 2026-10-04

### Changed
- Depends on `evalroute>=0.7,<0.8` (was `>=0.6,<0.7`). The plugin sets the library's command surface per call path — `hermes-chat` for `/route`, `/rate` and the tool, `hermes-cli` for `hermes evalroute …` — so card hints match where you typed. Contract version unchanged (the library added `set_surface` as an additive name).
- The plugin is now a thin adapter over the `evalroute` library
  (PyPI `evalroute`, source keppy/evalroute): `__init__.py`
  imports the nine contract names from `evalroute.*` and wires them into
  Hermes unchanged; `sniff.py` classifies via `evalroute.routing`. A
  contract guard (`evalroute.contract.CONTRACT_VERSION == 1`) runs before
  any registration and refuses to register against a library that speaks a
  different contract.
- `pyproject.toml` dependencies: `evalroute>=0.6,<0.7` (drops the direct
  `pyyaml` pin; the library brings it). No version bump here — the
  dispatcher releases.

### Removed

- `tools.py`, `flywheel.py`, `dispatch.py`, `dataset.py`, `schemas.py`,
  `adjudicate.py`, `routes_from_report.py`, `routes_from_labels.py`,
  `harness/`, `runners/`, `data/`, `examples/`, `scripts/`, and every test
  that moved with them — all now live in the evalroute library
  (keppy/evalroute). The plugin keeps `__init__.py`, `sniff.py`,
  `plugin.yaml`, `catalog/`, `skills/`, and adapter-only tests
  (`test_registration.py`, `test_sniff.py`, `test_contract.py`).

## [0.5.1] - 2026-10-03

### Fixed

- `hermes evalroute dispatch` crashed on the installed plugin with `ModuleNotFoundError: flywheel`
  (0.5.0, and 0.4.0 before it). `dispatch.py` is loaded by file path, so it has no package context and
  its bare sibling imports only resolve under pytest. `tools.evalroute_cli` now hands it `fw`/`tools`
  before exec, and `dispatch.py` only imports when they were not injected. The 0.4.1 fix for this
  was copied into the live install but never committed, which is how 0.5.0 shipped without it; a
  test now makes the bare import fail from inside the path-loaded module.

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
