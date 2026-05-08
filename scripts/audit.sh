#!/usr/bin/env bash
#
# Code-quality audit suite.
#
# Runs lint, type-check, tests-with-coverage, pre-commit drift check,
# project-specific safety check, security analysis, CVE scan, complexity
# grading, maintainability grading, and dead-code detection. Prints a
# PASS/FAIL summary table at the end.
#
# Usage:
#   scripts/audit.sh           # full suite (~60s, hits network for CVE scan)
#   scripts/audit.sh --fast    # local-dev subset (~5s, no CVE scans)
#
# Exit codes:
#   0 — every hard-gate step passed
#   1 — at least one hard-gate step failed
#
# The first 5 steps are HARD GATES (failure -> exit 1). The last 5 are
# REPORT-ONLY (printed but don't fail the audit). This split is a
# deliberate choice from the audit plan: tighten the gate over time as
# findings drive to zero.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

VENV_BIN="$REPO_ROOT/.venv/bin"

# Pick the venv tools if available, otherwise fall back to PATH (CI uses PATH).
ruff="${VENV_BIN}/ruff"
[[ -x "$ruff" ]] || ruff="ruff"
pyright="${VENV_BIN}/pyright"
[[ -x "$pyright" ]] || pyright="pyright"
pytest="${VENV_BIN}/pytest"
[[ -x "$pytest" ]] || pytest="pytest"
pre_commit="${VENV_BIN}/pre-commit"
[[ -x "$pre_commit" ]] || pre_commit="pre-commit"
bandit="${VENV_BIN}/bandit"
[[ -x "$bandit" ]] || bandit="bandit"
pip_audit="${VENV_BIN}/pip-audit"
[[ -x "$pip_audit" ]] || pip_audit="pip-audit"
radon="${VENV_BIN}/radon"
[[ -x "$radon" ]] || radon="radon"
vulture="${VENV_BIN}/vulture"
[[ -x "$vulture" ]] || vulture="vulture"

FAST_MODE=0
if [[ "${1:-}" == "--fast" ]]; then
  FAST_MODE=1
fi

# Tracking: each step writes "STEP_NAME RESULT" to a tempfile so we can
# print a clean summary at the end regardless of which steps passed.
RESULTS_FILE="$(mktemp -t dedupe-audit-XXXXXX)"
trap 'rm -f "$RESULTS_FILE"' EXIT

hard_gate_failures=0

record() {
  # record <step-name> <result>
  printf '%s\t%s\n' "$1" "$2" >> "$RESULTS_FILE"
}

run_hard() {
  # run_hard <step-name> <command...>
  # Command's stdout/stderr is shown; failure increments hard_gate_failures.
  local name="$1"
  shift
  echo ""
  echo "── $name ──"
  if "$@"; then
    record "$name" "PASS"
  else
    record "$name" "FAIL"
    hard_gate_failures=$((hard_gate_failures + 1))
  fi
}

run_report() {
  # run_report <step-name> <command...>
  # Command's stdout/stderr is shown; result recorded but never fails the
  # audit. These are observational checks meant to surface drift.
  local name="$1"
  shift
  echo ""
  echo "── $name (report only) ──"
  if "$@"; then
    record "$name" "PASS"
  else
    record "$name" "FINDINGS"
  fi
}

echo "═══════════════════════════════════════════════"
if [[ $FAST_MODE -eq 1 ]]; then
  echo "  Code Quality Audit — fast mode"
else
  echo "  Code Quality Audit — full suite"
fi
echo "═══════════════════════════════════════════════"

# --- HARD GATES ---------------------------------------------------------

run_hard "1. Ruff (lint)" \
  "$ruff" check src tests

run_hard "2. Pyright (type check)" \
  "$pyright"

run_hard "3. Pytest + coverage (>=80%)" \
  "$pytest" -q --cov=dedupe --cov-report=term --cov-fail-under=80

if [[ $FAST_MODE -eq 0 ]]; then
  # Skip pre-commit in fast mode — it's helpful but slow on first run.
  run_hard "4. Pre-commit (drift check)" \
    "$pre_commit" run --all-files
fi

run_hard "5. Destructive-call safety check" \
  bash "$REPO_ROOT/scripts/check_no_destructive_calls.sh"

# --- REPORT ONLY --------------------------------------------------------

run_report "6. Bandit (Python SAST)" \
  "$bandit" -r src -ll -q

if [[ $FAST_MODE -eq 0 ]]; then
  # CVE scan hits the network; skip in fast mode. Scan declared runtime
  # deps via requirements.txt rather than the active venv — the venv
  # contains our own (unpublished) `dedupe` package which would otherwise
  # surface as "not on PyPI" noise.
  run_report "7. pip-audit (CVEs in requirements.txt)" \
    "$pip_audit" -r "$REPO_ROOT/requirements.txt" --strict
fi

run_report "8. Radon CC (complexity, grade C+)" \
  bash -c "
    output=\"\$(\"$radon\" cc src -s -n C --no-assert 2>/dev/null)\"
    if [[ -n \"\$output\" ]]; then
      echo \"\$output\"
      exit 1
    fi
  "

run_report "9. Radon MI (maintainability, grade C+)" \
  bash -c "
    output=\"\$(\"$radon\" mi src -s -n C 2>/dev/null)\"
    if [[ -n \"\$output\" ]]; then
      echo \"\$output\"
      exit 1
    fi
  "

run_report "10. Vulture (dead code, confidence>=80)" \
  "$vulture" src tests --min-confidence 80

# --- SUMMARY -----------------------------------------------------------

echo ""
echo "═══════════════════════════════════════════════"
echo "  Audit Summary"
echo "═══════════════════════════════════════════════"
echo ""
printf "  %-44s %s\n" "Check" "Result"
printf "  %-44s %s\n" "--------------------------------------------" "--------"
while IFS=$'\t' read -r name result; do
  printf "  %-44s %s\n" "$name" "$result"
done < "$RESULTS_FILE"
echo ""

if [[ $hard_gate_failures -eq 0 ]]; then
  echo "  Result: PASS (hard gates clean; review report-only findings)"
  exit 0
else
  echo "  Result: FAIL ($hard_gate_failures hard-gate step(s) failed)"
  exit 1
fi
