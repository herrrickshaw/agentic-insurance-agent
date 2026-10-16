# Definition of Ready and Done

## Ready (a story may enter a sprint)
- Value statement and MoSCoW priority; WSJF inputs filled in `BACKLOG.yaml`
- Acceptance criteria in Given/When/Then, each testable without a human
- Dependencies named; fits in one sprint (split if size > 8)
- Data and PII implications noted; test approach known (unit, property, BDD, contract, eval)

## Done (a story may be marked `done`)
1. Every acceptance criterion has an automated test linked with `@pytest.mark.story("ID")` or a tagged Gherkin scenario
2. New behaviour was driven by a failing test first
3. Invariants have property-based tests where they exist (round trips, idempotence, monotonicity, conservation)
4. `make ci` is green: ruff, mypy, bandit, coverage at least 85%, AI evals, traceability
5. Changed API shape reviewed via the contract snapshot
6. No secrets or real personal data in code, fixtures or logs; PII-touching changes checked against the threat model
7. Changelog updated; ADR written if a hard-to-reverse decision changed
8. Demonstrable: a script, workflow run or report shows it working
