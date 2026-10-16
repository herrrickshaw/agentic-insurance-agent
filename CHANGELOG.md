# Changelog
Format: [Keep a Changelog](https://keepachangelog.com), versioning: [SemVer](https://semver.org). Commits follow Conventional Commits.

## [0.3.0] - 2026-10-16
### Added
- Records migration: AI classification against a file plan, ROT analysis, PII sensitivity labels, SharePoint-safe paths, audited records-manager decisions (hash-chained log), readiness report, `records_migration` BPMN (REC-1..REC-9).
- Classification cache by content hash and a per-batch model-call budget guard (REC-7, REC-8).
- Engineering system: backlog-as-code with traceability gate, BDD (Gherkin), property-based, contract, integration and mutation tests; coverage, lint, type and security gates; CI, nightly and release workflows; ADRs and threat model.
### Fixed
- Path sanitiser did not rename SharePoint-reserved names (`con`, `Forms`, `_vti_*`): found by a property test.
- Smoker loading was applied twice (mortality multiple and a DMN loading).
- Entity-resolution scoring could never reach the auto-merge threshold without an email.

## [0.2.0] - customer ingestion, entity resolution, profiles, portfolio risk, actuarial library
## [0.1.0] - BPMN/DMN insurance workflows on Operaton, GenAI service
