.DEFAULT_GOAL := help
SHELL := /bin/bash
VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: help setup test coverage lint format typecheck build run clean dedupe heic-convert convert hooks

# Most action targets accept a FOLDER variable. Override per-call:
#   make heic-convert FOLDER=~/Desktop/naomi-slide-show
FOLDER ?=
QUALITY ?= 92
TO ?= jpeg

help: ## List available commands
	@echo "dedupe — find and quarantine duplicate image files"
	@echo ""
	@echo "Usage: make <command> [VAR=value]"
	@echo ""
	@echo "Commands:"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""
	@echo "Action targets accept FOLDER=, e.g.:"
	@echo "  make dedupe FOLDER=~/Desktop/naomi-slide-show"
	@echo "  make heic-convert FOLDER=~/Desktop/naomi-slide-show"
	@echo "  make convert FOLDER=~/Pictures/foo TO=webp QUALITY=85"
	@echo ""
	@echo "After 'make setup', activate with: source $(VENV)/bin/activate"

setup: ## Create venv, install package + dev deps, install pre-commit hooks
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"
	$(VENV)/bin/pre-commit install
	@echo ""
	@echo "Done. Run: source $(VENV)/bin/activate && dedupe --help"

hooks: ## (Re)install pre-commit hooks into .git/hooks
	$(VENV)/bin/pre-commit install

test: ## Run pytest
	$(VENV)/bin/pytest -v

coverage: ## Run pytest with coverage; HTML report at htmlcov/index.html
	$(VENV)/bin/pytest --cov=dedupe --cov-report=term-missing --cov-report=html
	@echo ""
	@echo "HTML coverage report: htmlcov/index.html"

lint: ## Lint with ruff
	$(VENV)/bin/ruff check src tests

format: ## Auto-format with ruff
	$(VENV)/bin/ruff format src tests
	$(VENV)/bin/ruff check --fix src tests

typecheck: ## Static type-check with pyright
	$(VENV)/bin/pyright

build: ## Build wheel + sdist into dist/
	$(VENV)/bin/python -m build
	@echo ""
	@echo "Artifacts in dist/:"
	@ls -1 dist/

run: ## Show usage hint
	@echo "dedupe needs a folder argument. Try:"
	@echo "  dedupe scan <folder>"
	@echo "  dedupe find-similar <folder>"
	@echo "  dedupe restore <dups-folder>"
	@echo "  dedupe convert <folder>"
	@echo "  dedupe --help"

# --- action targets ---------------------------------------------------------
# Each action target requires FOLDER=... and forwards extra flags via $(ARGS).

# define check_folder = $(if $(FOLDER),,$(error FOLDER is required, e.g. make $@ FOLDER=~/Desktop/photos))
check_folder = @if [ -z "$(FOLDER)" ]; then \
	echo "error: FOLDER is required, e.g. make $(1) FOLDER=~/Desktop/photos"; \
	exit 2; \
fi

dedupe: ## Scan FOLDER for duplicates; pass --dry-run via ARGS=--dry-run
	$(call check_folder,dedupe)
	$(VENV)/bin/dedupe scan "$(FOLDER)" $(ARGS)

heic-convert: ## Convert HEIC/HEIF in FOLDER to JPEG (writes to <FOLDER>-converted)
	$(call check_folder,heic-convert)
	$(VENV)/bin/dedupe convert "$(FOLDER)" --to jpeg --quality $(QUALITY) $(ARGS)

convert: ## Generic convert; honors TO=jpeg|png|webp, QUALITY=N, ARGS=...
	$(call check_folder,convert)
	$(VENV)/bin/dedupe convert "$(FOLDER)" --to $(TO) --quality $(QUALITY) $(ARGS)

clean: ## Remove venv, caches, build artifacts, coverage output
	rm -rf $(VENV) build dist *.egg-info src/*.egg-info htmlcov .coverage
	rm -rf .pyright
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
