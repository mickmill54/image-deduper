# Claude Code — Project Instructions

## Project Overview

`dedupe` — a local Python CLI tool that finds and quarantines duplicate image
files from a directory. Built for curating photo slideshows where safety and
auditability matter more than speed.

- `src/dedupe/` — Python package (CLI, scan, similar, restore, manifest, ui)
- `tests/` — pytest suite with programmatically-generated fixture images
- `Makefile` — single entrypoint for setup, test, lint, format
- `pyproject.toml` — editable install, ruff + pytest config

The tool is invoked as `dedupe <subcommand> ...` after `make setup`. Three
subcommands: `scan` (find + quarantine exact duplicates), `find-similar`
(perceptual-hash report only, never moves files), `restore` (replay manifest).

## Work Process

### Issue-First Development
- Create a GitHub issue before starting non-trivial work
- Post implementation plans as comments on the issue before coding
- Reference issue numbers in commit messages and PRs (`Closes #XX`)
- Use plan mode for any multi-file or architectural changes

### Commit Convention
Use [conventional commits](https://www.conventionalcommits.org/) so version
intent is unambiguous:
- `feat:` — new feature
- `fix:` — bug fix
- `docs:` — documentation only
- `chore:` — maintenance / tooling
- `test:` — test changes
- `refactor:` — refactoring without behavior change
- `feat!:` or `BREAKING CHANGE:` — breaking change

## Git Workflow

- Never commit or push directly to `main`
- Create a feature branch: `git checkout -b feat/<description>` or `fix/<description>`
- Open a PR; merge to `main` once tests pass
- Never force-push to `main`

## Documentation

Updating documentation is always allowed without extra confirmation
(`README.md`, `CHANGELOG.md`, anything under `docs/`).

**Rule: update docs in the same PR as code changes.** Do not defer doc
updates — this causes drift. If a code change affects documented behavior,
update the doc in the same PR.

## 12-Factor App (CLI subset)

This is a CLI, not a service, so only the relevant factors apply:

- **III. Config** — All configuration via CLI flags or environment variables
  (e.g. `NO_COLOR`). No hardcoded paths, no fallback defaults that point at
  real folders. Defaults are computed from arguments.
- **XI. Logs** — Use `logging.getLogger(__name__)` exclusively. No `print()`
  calls in library code. The `ui` module is the only place that writes to
  stdout/stderr; it respects `--quiet`, `--json`, `--no-color`, and `NO_COLOR`.

## Tooling

The dev loop is driven by `make`. Each of these must pass before a PR
can land — CI gates merges with the same checks:

| Command | What it runs |
|---|---|
| `make lint` | `ruff check src tests` |
| `make format` | `ruff format` + `ruff check --fix` |
| `make typecheck` | `pyright` (basic mode, py3.11 target) |
| `make test` | `pytest -v` |
| `make coverage` | pytest with HTML report at `htmlcov/` |
| `make build` | `python -m build` → `dist/*.whl` + `dist/*.tar.gz` |

Pre-commit hooks installed by `make setup` run `ruff check --fix` and
`ruff format` on every commit, plus a few standard hygiene hooks.
Bypass once with `git commit --no-verify`.

**Type-checking expectation:** the codebase passes pyright cleanly with
0 errors. New code should keep that property. If you hit a third-party
typing gap, prefer narrow `# type: ignore[<rule>]` comments with a
short reason over loosening the global config.

## Release process

Each `vX.Y.Z` tag pushed to `origin` triggers a CI release job that
builds the wheel + sdist and attaches them to the matching GitHub
release page. The job creates the release with auto-generated notes if
one doesn't yet exist, otherwise uploads to the manually-created
release. Standard flow:

1. Land code via PR (CI gates the merge).
2. Bump `__version__` (`src/dedupe/__init__.py`) and
   `version` (`pyproject.toml`) in the same PR or a follow-up.
3. Add a `[X.Y.Z]` section to `CHANGELOG.md` with a date and a link
   to the release page.
4. After merge, on `main`: `git tag -a vX.Y.Z -m "..."` →
   `git push origin vX.Y.Z`.
5. Create the release with custom notes:
   `gh release create vX.Y.Z --title "..." --notes "..."`.
6. The `release` job in CI (gated on `refs/tags/v*`) runs and uploads
   `dist/*.whl` + `dist/*.tar.gz` to the release page.

Conventional-commits decide the bump:
- `feat:` → minor (new behavior)
- `fix:` → patch
- `docs:` / `chore:` / `ci:` → patch (or no bump for pure docs)
- `feat!:` / `BREAKING CHANGE:` → major

## SOLID Principles

All Python code follows SOLID:

- **S — Single Responsibility**: Each module does one thing. `cli.py` parses
  args; `scan.py` finds duplicates; `manifest.py` reads/writes the manifest;
  `ui.py` is the only module that talks to the console.
- **O — Open/Closed**: Add a new subcommand by adding a module + wiring it in
  `cli.py`, not by modifying existing scan/restore logic.
- **L — Liskov Substitution**: The `ui` console wrapper exposes the same
  contract whether running in rich, quiet, or json mode — callers do not
  branch on mode.
- **I — Interface Segregation**: Keep function signatures focused. A function
  that only needs a path should not take the whole config object.
- **D — Dependency Inversion**: High-level commands depend on the `ui` and
  `manifest` abstractions, not on `rich` or `json` directly. Pillow / imagehash
  are imported only inside `similar.py`.

## Safety Invariants (project-specific)

These are non-negotiable for this tool:

- **Never delete files.** `scan` only ever *moves* duplicates to the dups
  folder. `restore` only ever *moves* them back. There is no `rm`, no
  `Path.unlink`, no `shutil.rmtree` on user data.
- **Manifest is the source of truth.** Every move is recorded before or
  immediately after it happens. A crash mid-scan must leave a usable manifest.
- **Refuse to overwrite.** `restore` skips and reports any file whose original
  location is now occupied. Never clobber.
- **Determinism.** "Shortest path wins, alphabetical tiebreak" — applied
  consistently, so re-running on the same input produces the same outcome.
- **`find-similar` is read-only.** It reports; it never moves or modifies
  files. This boundary is enforced in code and tests.
