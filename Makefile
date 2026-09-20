.PHONY: install test demo api mcp lint

VENV ?= .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

install:
	python3 -m venv $(VENV)
	$(PIP) install -U pip
	$(PIP) install -e ".[dev]"

test:
	$(VENV)/bin/pytest -q

demo:
	$(PY) scripts/demo.py

api:
	$(VENV)/bin/uvicorn x_intel.api.main:app --host 127.0.0.1 --port 8080

mcp:
	$(PY) -m x_intel.mcp_server.server tools

ledger-status:
	$(PY) scripts/outcome_ledger.py status
