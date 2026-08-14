PY := ./.venv/Scripts/python.exe

.PHONY: setup up down logs verify-cypher test measure-extraction clean

setup:
	python -m venv .venv
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -e ".[dev]"

# One command to a running HydraDB. No Rust toolchain required.
up:
	@mkdir -p hydradb-data/store hydradb-data/cache
	@test -f hydradb-data/auth-token || printf '%s\n' 'local-development-token-32-bytes' > hydradb-data/auth-token
	docker compose up -d
	@echo "waiting for node..."
	@until curl -sf http://127.0.0.1:9090/metrics >/dev/null 2>&1; do sleep 2; done
	@echo "HydraDB ready: bolt 7687 / http 8443 / admin 9090"

down:
	docker compose down

logs:
	docker compose logs -f

# Every Cypher template the codebase can emit, executed against the live node.
# HydraDB rejects unsupported syntax at parse time and EXPLAIN is not reachable
# over Bolt, so this is the only place illegal statements get caught early.
verify-cypher:
	$(PY) -m pytest tests/test_statements.py -q

test:
	$(PY) -m pytest -q

# Slice 06 gate: extractor quality on a 20-instance slice. Blocks scaling
# ingest to the full corpus. Cached, so a rerun costs nothing.
measure-extraction:
	$(PY) scripts/measure_extraction.py

clean:
	docker compose down -v
	rm -rf hydradb-data
