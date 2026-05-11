# Makefile — local CI gate replica for medrec-superpower
# Every target is idempotent and offline-safe.

.PHONY: help install lint format typecheck test cov audit ci run clean

UV := uv

help:  ## show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install:  ## sync deps (incl. dev)
	$(UV) sync --extra dev

lint:  ## ruff check + format check
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format:  ## auto-format with ruff
	$(UV) run ruff format .
	$(UV) run ruff check . --fix

typecheck:  ## mypy strict
	$(UV) run mypy medrec_superpower

test:  ## run all tests with coverage gate
	$(UV) run pytest

cov:  ## test + html coverage report
	$(UV) run pytest --cov-report=html
	@echo "open htmlcov/index.html"

audit:  ## dependency + source security scans
	$(UV) run pip-audit
	$(UV) run bandit -q -r medrec_superpower

ci:  ## full local CI replica (run before pushing)
	$(MAKE) lint
	$(MAKE) typecheck
	$(MAKE) test
	$(MAKE) audit
	@echo ""
	@echo "ALL GATES GREEN"

run:  ## start the MCP server on :8765 (streamable-http)
	$(UV) run python -m medrec_superpower

clean:  ## remove caches and build artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov coverage.xml .coverage*
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
