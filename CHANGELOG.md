# Changelog

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
