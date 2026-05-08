#!/usr/bin/env bash
#
# Project-specific safety check.
#
# The "never delete files" invariant from CLAUDE.md is a bright line:
# `scan`, `restore`, `convert`, `find-similar`, and `info` may not call
# any destructive filesystem function. The ONE narrow exception is
# `Path.unlink()` inside `src/dedupe/sweep.py`, used only for the
# JUNK_FILES allowlist (Thumbs.db, .DS_Store, etc.).
#
# This script greps `src/dedupe/` for forbidden patterns and fails if
# anything is found outside the allowlist. It's a hard gate in the
# audit suite — production code that violates the invariant should
# never land.
#
# Forbidden anywhere in src/dedupe/:
#   - os.remove
#   - os.unlink
#   - shutil.rmtree
#   - Path.rmdir
#
# Conditionally allowed:
#   - Path.unlink (only in src/dedupe/sweep.py)
#
# Exit codes:
#   0 — no violations
#   1 — violations found (the script prints them)
#
# Usage:
#   scripts/check_no_destructive_calls.sh
#
# Note: this script doesn't try to be a fully-correct AST analysis —
# string matching is good enough for a project-specific bright line.
# False positives can be silenced with a # noqa: destructive-ok=<reason>
# comment on the same line, but use sparingly and document why.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DIR="$REPO_ROOT/src/dedupe"

if [[ ! -d "$SRC_DIR" ]]; then
  echo "error: $SRC_DIR not found (script must run from repo root or scripts/)" >&2
  exit 2
fi

# Patterns that are forbidden everywhere in src/dedupe/.
# Each pattern is a Python identifier or method call string we'd never want
# to invoke on user data. The grep matches the literal text.
FORBIDDEN_PATTERNS=(
  'os\.remove'
  'os\.unlink'
  'shutil\.rmtree'
  '\.rmdir\('
)

# Path.unlink is allowed ONLY in sweep.py. We grep separately.
SWEEP_ONLY_PATTERN='\.unlink\('

violations=0

# Search for the always-forbidden patterns across all of src/dedupe/.
for pattern in "${FORBIDDEN_PATTERNS[@]}"; do
  matches="$(
    grep -rnE "$pattern" "$SRC_DIR" \
      --include='*.py' \
      | grep -v 'noqa: destructive-ok' \
      || true
  )"
  if [[ -n "$matches" ]]; then
    echo "FORBIDDEN: '$pattern' is not permitted in src/dedupe/" >&2
    echo "$matches" >&2
    echo "" >&2
    violations=$((violations + 1))
  fi
done

# Path.unlink is allowed in sweep.py only.
unlink_matches="$(
  grep -rnE "$SWEEP_ONLY_PATTERN" "$SRC_DIR" \
    --include='*.py' \
    | grep -v 'noqa: destructive-ok' \
    || true
)"
if [[ -n "$unlink_matches" ]]; then
  while IFS= read -r line; do
    file="$(echo "$line" | cut -d: -f1)"
    rel_file="${file#"$REPO_ROOT/"}"
    if [[ "$rel_file" != "src/dedupe/sweep.py" ]]; then
      echo "FORBIDDEN: '.unlink(' is allowed only in src/dedupe/sweep.py" >&2
      echo "$line" >&2
      echo "" >&2
      violations=$((violations + 1))
    fi
  done <<< "$unlink_matches"
fi

if [[ $violations -gt 0 ]]; then
  echo "FAIL: $violations destructive-call violation(s) found in src/dedupe/" >&2
  echo "      See CLAUDE.md \"Safety Invariants\" for the rationale." >&2
  exit 1
fi

echo "PASS: no forbidden destructive calls in src/dedupe/"
exit 0
