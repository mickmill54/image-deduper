# Contributing to dedupe

Thanks for your interest. This is a small, opinionated CLI tool, so the
contribution loop is light. The high-level shape:

1. **[Open an issue](https://github.com/mickmill54/image-deduper/issues/new/choose)** before non-trivial work. Sketch the problem and the proposed shape; we'll discuss the approach there before code lands.
2. **Fork** the repo and create a branch from `main`. Direct pushes to
   `main` are blocked by branch protection; all changes land via PR.
3. **Make your change** — see "Dev loop" below for what must pass.
4. **Open a pull request** against `main`. CI runs lint + type check +
   pytest across Python 3.11/3.12/3.13.
5. **Iterate on review comments.** PR conversations must be resolved
   before merge.
6. **Squash-merge** is the norm; the maintainer handles the merge.

## Platform note

This project is **macOS Apple Silicon only** in its shipped form. The
test suite runs on Linux in CI (Ubuntu runners, headless), so contributing
on Linux works fine for code changes — you just can't exercise the
PyInstaller binary build, which is gated to a `macos-latest` job. If
your contribution touches the binary build path, ideally have a Mac to
test it locally first.

## Dev loop

```bash
git clone https://github.com/<your-fork>/image-deduper.git
cd image-deduper
make setup              # creates .venv, installs deps, installs pre-commit hooks
source .venv/bin/activate
```

Before you commit, all of these must pass — CI gates the merge on the
same checks:

```bash
make lint        # ruff check
make typecheck   # pyright in basic mode
make test        # pytest -v
```

Before opening a PR, an extra sanity check via the audit suite is a
good idea:

```bash
make audit-fast  # ~5s — lint + types + tests + coverage + safety check + complexity + dead code
make audit       # ~60s — same plus CVE scan + pre-commit drift check (full suite, also runs nightly in CI)
```

`make audit-fast` skips the network-bound CVE scan and the pre-commit
drift check, so it's quick enough to run before every PR. The full
`make audit` runs nightly on `main` via the `Audit` GitHub Actions
workflow and uploads its report as an artifact.

Other useful targets:

```bash
make format      # ruff format + ruff check --fix
make coverage    # pytest with coverage HTML at htmlcov/index.html
make build       # python -m build → wheel + sdist in dist/
make binary      # PyInstaller single-file binary at dist/dedupe (macOS arm64 only)
```

`make setup` also installs **pre-commit hooks** that run `ruff check
--fix`, `ruff format`, and standard hygiene hooks (trailing whitespace,
EOF newline, YAML/TOML syntax, merge-conflict markers, large-file
guard). To bypass once: `git commit --no-verify`.

## Conventions

### Branches

- Naming: `feat/<short-description>` for features, `fix/<short-description>`
  for bug fixes, `docs/<short-description>` for documentation, etc.
- Branch from `main`. Don't branch from another feature branch unless
  you're stacking changes intentionally.

### Commits

We use [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` — new feature (minor version bump on release)
- `fix:` — bug fix (patch bump)
- `docs:` — documentation only
- `chore:` — tooling, build, deps, repo scaffolding
- `test:` — test changes
- `refactor:` — refactoring without behavior change
- `ci:` — CI workflow changes
- `feat!:` or `BREAKING CHANGE:` in the body — breaking change (major bump)

Commit messages should explain *why*, not just *what*. The subject line
is the *what*; the body is the *why*. Reference issues and PRs by number
(e.g. `Closes #42`).

### Pull requests

- One logical change per PR. If you find yourself writing "and also" in
  the description, consider splitting.
- Reference the issue you're closing in the description (`Closes #N`)
  so it auto-closes on merge.
- Fill out the PR template's **Test plan** checklist. Don't merge if
  it's incomplete.
- CI must be green: lint + pyright + pytest on Python 3.11/3.12/3.13.
- Conversations on review comments must be resolved before merge.
- The maintainer (mickmill54) does the actual merge.

### Code style

- **Python ≥ 3.11.** Use modern type hints (`list[str]`, `Path | None`,
  `match`/`case` where it reads better).
- **`ruff check` and `ruff format`** are the source of truth for style.
  Don't argue with the formatter; if you disagree, raise the question
  on the issue.
- **`pyright` in basic mode** must be clean. For genuine third-party
  typing gaps, use a narrow `# type: ignore[<rule>]` with a short
  reason comment. Don't loosen the global config.
- **No `print()` in library code.** The `ui` module is the only thing
  that talks to stdout/stderr; everything else goes through it. Use
  `logging.getLogger(__name__)` for diagnostic output.
- **Filesystem mutations are confined** to a small set of clearly-named
  helpers (`_move_one` in `scan.py`, `shutil.move` in `restore.py`,
  `Image.save` in `convert.py`). If your change adds a new mutation
  call, please discuss in the issue first — the safety invariants in
  `CLAUDE.md` are non-negotiable.

### Tests

- New behavior gets a test. Bug fixes should include a regression test
  that fails on the bug and passes on the fix.
- Fixtures are programmatically generated in `tests/conftest.py` —
  don't check in binary image files.
- Use `pytest.fixture` and existing fixtures (`fixture_tree`,
  `convert_tree`, `similar_tree`, `heic_tree`) when they fit.
- Test naming: `test_<thing>_<expected_behavior>` (e.g.
  `test_scan_skips_hidden_by_default`).

### Documentation

- Update docs in the **same PR** as code changes. Don't defer doc
  updates — that causes drift.
- `README.md` is for users. `docs/architecture.md` is for contributors
  / future-maintainer-you. `CHANGELOG.md` gets a section per release.
- For non-trivial design decisions, add a short section to
  `docs/architecture.md` explaining the *why*.

## Reporting bugs

Open an issue with the **Bug report** template. The form will ask for:

- Your macOS version and which Apple Silicon chip you have
  (the specific chip generation rarely matters for reproduction,
  but it helps narrow things down)
- The `dedupe --version` you're running
- The exact command you ran
- Expected vs actual behavior
- Any error output

If your bug involves filesystem state, please use a small synthetic
folder rather than your real photo library when describing the
reproduction. Saves you from sharing private data and saves us from
needing it.

## Suggesting features

Open an issue with the **Feature request** template. The form will ask
for:

- The problem you're trying to solve (what's painful today?)
- A proposed shape for the solution
- Alternatives you considered

Features land best when the design discussion happens on the issue
*before* code is written. Saves wasted PR work.

## Security issues

For now, please file public issues. The project doesn't currently
handle secrets, network requests, or credentials, so the typical
"please don't disclose publicly" model isn't necessary. If that
changes, this section will too.

## License

By contributing, you agree that your contributions will be licensed
under the project's [MIT License](LICENSE).
