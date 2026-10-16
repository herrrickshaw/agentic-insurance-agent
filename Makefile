PY := .venv/bin/python
.PHONY: inventory setup lint type sec test cov bdd prop contract eval trace mutation e2e models ci
setup:        ## create venv and install dev tools
	python3 -m venv .venv && $(PY) -m pip install -q -r requirements-dev.txt
lint:         ## style + bug-prone patterns
	.venv/bin/ruff check .
type:         ## static types on the typed core
	.venv/bin/mypy
sec:          ## static security scan (bandit)
	.venv/bin/bandit -q -c pyproject.toml -r ai insurance records workers
test:         ## unit + property + bdd + contract (integration auto-skips without the stack)
	$(PY) -m pytest
cov:          ## tests with coverage gate (fail under 85%)
	$(PY) -m pytest --cov
bdd:          ## Gherkin scenarios only
	$(PY) -m pytest -m bdd
prop:         ## property-based tests only
	$(PY) -m pytest -m property
contract:     ## API / schema contracts (UPDATE_SNAPSHOTS=1 to accept a reviewed change)
	$(PY) -m pytest -m contract
eval:         ## AI regression gates (exit 1 below thresholds)
	AI_PROVIDER=mock $(PY) -m ai.evals.run_evals && AI_PROVIDER=mock $(PY) -m records.evals
trace:        ## backlog <-> test traceability gate
	$(PY) tools/traceability.py
mutation:     ## do the tests notice broken logic? (slow, sampled)
	$(PY) tools/mutation_smoke.py --per-module 30
e2e:          ## needs `docker compose up`; runs the integration tests against the live engine
	$(PY) -m pytest -m integration
inventory:    ## regenerate docs/governance/AI_INVENTORY.md
	$(PY) tools/ai_inventory.py
models:       ## regenerate BPMN/DMN (CI fails if the committed files drift)
	python3 tools/build_models.py
ci: lint type sec cov eval trace   ## what the pipeline runs on every push
