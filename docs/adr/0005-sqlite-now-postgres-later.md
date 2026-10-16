# 5. SQLite ledger and traces for the demo, Postgres for production
Status: accepted for the demo; superseded by DATA-1. **Decision**: SQLite for the ledger and trace store (zero setup), with the same SQL paths running on Postgres/pgvector where it matters (verified for traces and vector search). **Consequences**: single-writer limits and no HA until DATA-1 and a migration plan are done.
