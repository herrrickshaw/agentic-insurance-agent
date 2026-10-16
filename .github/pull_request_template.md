## Story
Closes: <!-- backlog id, e.g. REC-7 (docs/product/BACKLOG.yaml) -->

## What and why

## Definition of Done (docs/product/DEFINITION_OF_DONE.md)
- [ ] Acceptance criteria are covered by tests carrying `@pytest.mark.story("ID")` (or a tagged Gherkin scenario)
- [ ] Test written first for new behaviour (red then green); edge cases and a property test where an invariant exists
- [ ] `make ci` passes locally (ruff, mypy, bandit, coverage gate, AI evals, traceability)
- [ ] Generated files in sync (`make models`, `docs/product/TRACEABILITY.md`)
- [ ] Contract snapshot reviewed if the API shape changed (`UPDATE_SNAPSHOTS=1`)
- [ ] No secrets or real customer data; PII handling reviewed against docs/security/THREAT_MODEL.md
- [ ] CHANGELOG entry; ADR added if an architectural decision changed

## Risk and rollback
