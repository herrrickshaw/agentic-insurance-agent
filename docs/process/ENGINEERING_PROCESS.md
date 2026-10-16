# Engineering process (SDLC, Agile, testing) as applied in this repo

Every practice below is wired into something executable. If a rule has no gate, it is marked *convention*.

## 1. SDLC phases and where they live
| Phase | Practice | Artifact / gate |
|---|---|---|
| Requirements | User stories with Given/When/Then criteria, MoSCoW + WSJF prioritisation | `docs/product/BACKLOG.yaml` (validated by `tools/traceability.py`) |
| Design | Lightweight ADRs for decisions that are costly to reverse; threat model before handling PII | `docs/adr/`, `docs/security/THREAT_MODEL.md` |
| Implementation | Trunk-based development, short-lived branches, Conventional Commits, TDD (test first, see REC-7/REC-8 history) | `CONTRIBUTING.md`, PR template |
| Verification | Test pyramid + property + mutation + contract + AI evals | `docs/TEST_STRATEGY.md`, `make ci` |
| Release | SemVer, changelog, tag-must-match-version check, images built only after gates pass | `.github/workflows/release.yml`, `CHANGELOG.md` |
| Operate | Health endpoint, per-call LLM traces and root-cause queries, readiness reports, audit chain | `/v1/ai/metrics`, `records/report.py` |
| Maintain | Nightly mutation run, backlog groomed from survivors/gaps | `.github/workflows/nightly.yml` |

## 2. Agile cadence (convention, 2-week sprints)
- **Planning**: pull from the top of `BACKLOG.yaml` by WSJF where `status: ready`; capacity stated in `docs/product/SPRINT_PLAN.md`.
- **Daily**: work visible as small PRs, each closing one story id.
- **Review/demo**: the demo scripts (`scripts/demo_*.py`) and the narrated videos are the increments' evidence.
- **Retrospective**: improvements become backlog items (e.g. mutation survivors became new tests this cycle).
- **Definition of Ready / Done**: `docs/product/DEFINITION_OF_DONE.md`, enforced by the PR template and CI.

## 3. Quality gates (all in CI, run locally with `make ci`)
| Gate | Tool | Threshold |
|---|---|---|
| Lint | ruff | zero findings |
| Types | mypy on the typed core (actuarial, audit, review, rot, inventory) | zero errors |
| Security | bandit, gitleaks (pre-commit) | zero medium/high without a justified `nosec` |
| Tests | pytest (unit, property, BDD, contract; integration auto-skips without the stack) | all pass |
| Coverage | pytest-cov over ai, insurance, records, workers | at least 85% |
| AI quality | `ai.evals`, `records.evals` | per-metric thresholds in code/JSON |
| Traceability | `tools/traceability.py` | every `done` story has tests; no test references an unknown story |
| Generated files | `build_models.py`, `TRACEABILITY.md` | no drift |

## 4. Branching and releases
Trunk-based: `main` is always releasable; branches live less than two days; feature flags or dark code for larger work. Release = bump version in `pyproject.toml`, move changelog entries, tag `vX.Y.Z`; the release workflow re-runs `make ci` and rejects a tag that does not match the version.

## 5. Risks tracked
| Risk | Mitigation here | Residual |
|---|---|---|
| AI quality regresses silently | eval gates, schema repair, grounding checks, human lane | gates pass on a mock provider; real-model gate is AIP-4 (blocked on credentials) |
| PII leaks to a model or log | redaction before calls, hashed identifiers, threat model | no network egress controls, no DLP |
| Test suite gives false comfort | mutation testing found real gaps (see TEST_STRATEGY) | sampled mutants, simple operators |
| Illustrative actuarial inputs mistaken for real | stated in docs, video and code | needs a real table and experience study |
