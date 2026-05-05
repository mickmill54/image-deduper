.DEFAULT_GOAL := help
SHELL := /bin/bash
VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: help setup test lint format run clean

help: ## List available commands
	@echo "dedupe — find and quarantine duplicate image files"
	@echo ""
	@echo "Usage: make <command>"
	@echo ""
	@echo "Commands:"
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""
	@echo "After 'make setup', activate with: source $(VENV)/bin/activate"

setup: ## Create venv and install package + deps in editable mode
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"
	@echo ""
	@echo "Done. Run: source $(VENV)/bin/activate && dedupe --help"

test: ## Run pytest
	$(VENV)/bin/pytest -v

lint: ## Lint with ruff
	$(VENV)/bin/ruff check src tests

format: ## Auto-format with ruff
	$(VENV)/bin/ruff format src tests
	$(VENV)/bin/ruff check --fix src tests

run: ## Show usage hint
	@echo "dedupe needs a folder argument. Try:"
	@echo "  dedupe scan <folder>"
	@echo "  dedupe find-similar <folder>"
	@echo "  dedupe restore <dups-folder>"
	@echo "  dedupe --help"

clean: ## Remove venv, caches, build artifacts
	rm -rf $(VENV) build dist *.egg-info src/*.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
