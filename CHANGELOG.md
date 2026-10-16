# Changelog
Format: [Keep a Changelog](https://keepachangelog.com), versioning: [SemVer](https://semver.org). Commits follow Conventional Commits.

## [Unreleased]
### Added
- AI use-case inventory and proportionate tiering, group-fairness metrics, proxy-free pricing by marginalisation, and a literature review with evidence grades (GOV-1, GOV-2, PRC-1).
### Changed
- Records module is domain-neutral: configurable file plan, target-platform profiles, opt-in PII region packs, neutral synthetic data; capability doc no longer tied to a job description.
### Fixed
- Near-duplicate flagging depended on filesystem order; it is now deterministic and flags the draft/copy rather than the primary file.

## [0.3.0] - 2026-10-16
### Added
- Records migration: AI classification against a file plan, ROT analysis, PII sensitivity labels, target-safe paths (profile-based), audited records-manager decisions (hash-chained log), readiness report, `records_migration` BPMN (REC-1..REC-9).
- Classification cache by content hash and a per-batch model-call budget guard (REC-7, REC-8).
- Engineering system: backlog-as-code with traceability gate, BDD (Gherkin), property-based, contract, integration and mutation tests; coverage, lint, type and security gates; CI, nightly and release workflows; ADRs and threat model.
### Fixed
- Path sanitiser did not rename reserved names (`con`, `Forms`, `_vti_*`): found by a property test.
- Smoker loading was applied twice (mortality multiple and a DMN loading).
- Entity-resolution scoring could never reach the auto-merge threshold without an email.

## [0.2.0] - customer ingestion, entity resolution, profiles, portfolio risk, actuarial library
## [0.1.0] - BPMN/DMN insurance workflows on Operaton, GenAI service
