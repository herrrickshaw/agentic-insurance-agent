# Test strategy

| Layer | Framework | What it proves | Where |
|---|---|---|---|
| Unit | pytest | Function-level behaviour, boundaries | `*/tests`, `tests/test_handlers*.py` |
| Property-based | hypothesis | Invariants for all inputs: path safety and idempotence, audit-chain tamper detection, Verhoeff error detection, actuarial identities, chain-ladder scaling | `tests/property` |
| BDD | pytest-bdd + Gherkin | Business rules in the stakeholders' language (records review, underwriting, payments) | `features/`, `tests/bdd` |
| Contract | pytest + OpenAPI snapshot | The AI service shape and the keys workers rely on cannot change unnoticed | `tests/contract`, `docs/api/openapi.json` |
| Integration | pytest, live compose stack | BPMN + DMN + workers + actuarial pricing work together (auto-skipped when the engine is down) | `tests/integration` |
| AI evals | labelled sets + thresholds | Classification, extraction, guardrails, ROT, PII labelling stay above thresholds | `ai/evals`, `records/evals.py` |
| Mutation | `tools/mutation_smoke.py` (AST operators) | The tests above fail when logic is deliberately broken | nightly |
| Traceability | `tools/traceability.py` | Every done story has tests | CI |

## Mutation testing results (sampled, seeded; higher is better)
Recorded when this strategy was written. Each survivor is either a missing test or an equivalent mutant.
| Module | Killed | Note |
|---|---|---|
| `records/audit.py` | 10/10 | after adding link-by-link tamper tests |
| `records/review.py` | 22/25 | was 6/15 before boundary tests |
| `records/rot.py` | 19/24 | was 7/15; survivors are mostly equivalent or threshold-edge mutants (open) |
| `insurance/actuarial/mortality.py` | 27/30 | was 11/15; added Fackler recursion, table-end, expense equation |
| `insurance/customers/resolve.py` | 20/30 | open: add scoring-weight and threshold boundary tests (backlog debt) |

**Overall: 98/119 = 82%** (sampled, seeded; nightly job tracks the trend).

## Known limits
- The mock provider is a keyword model tuned on the same synthetic vocabulary as the labelled sets, so AI eval scores prove plumbing and gating, not model quality.
- Synthetic data only; no production-like volume or latency testing; no load tests.
- Mutation operators are basic (comparison, arithmetic, boolean, numeric constants).
