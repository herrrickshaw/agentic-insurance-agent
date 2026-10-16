"""Policy-clause retrieval: pgvector in Postgres (production) or in-memory cosine (tests/offline)."""
import hashlib
import math
import re

DIM = 256


def hash_embed(text: str, dim=DIM) -> list[float]:
    """Offline feature-hashing embedding. Swap for Azure OpenAI embeddings in production (set dim to match)."""
    v = [0.0] * dim
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        v[h % dim] += 1.0 if (h >> 8) % 2 else -1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def load_clauses(path):
    """Markdown with '## <id> <title>' headings -> [(id, text)]."""
    out, cur = [], None
    for line in open(path, encoding="utf-8"):
        if line.startswith("## "):
            cur = [line[3:].split()[0], line[3:].strip() + "\n"]
            out.append(cur)
        elif cur:
            cur[1] += line
    return [(i, t.strip()) for i, t in out]


class MemoryStore:
    def __init__(self, embed=hash_embed):
        self.embed, self.items = embed, []

    def add(self, doc_id, text):
        self.items.append((doc_id, text, self.embed(text)))

    def search(self, query, k=3):
        q = self.embed(query)
        scored = sorted(((sum(a * b for a, b in zip(q, e)), i, t) for i, t, e in self.items), reverse=True)
        return [{"id": i, "text": t, "score": round(s, 3)} for s, i, t in scored[:k]]


class PgVectorStore:
    def __init__(self, dsn, embed=hash_embed, dim=DIM):
        import psycopg
        self.embed, self.dim = embed, dim
        self.con = psycopg.connect(dsn, autocommit=True)
        self.con.execute("CREATE EXTENSION IF NOT EXISTS vector")
        self.con.execute(f"CREATE TABLE IF NOT EXISTS policy_clauses(id TEXT PRIMARY KEY, body TEXT, "
                         f"embedding vector({dim}))")

    def add(self, doc_id, text):
        vec = "[" + ",".join(f"{x:.6f}" for x in self.embed(text)) + "]"
        self.con.execute("INSERT INTO policy_clauses VALUES(%s,%s,%s::vector) ON CONFLICT(id) DO UPDATE "
                         "SET body=EXCLUDED.body, embedding=EXCLUDED.embedding", (doc_id, text, vec))

    def search(self, query, k=3):
        vec = "[" + ",".join(f"{x:.6f}" for x in self.embed(query)) + "]"
        rows = self.con.execute("SELECT id, body, 1-(embedding <=> %s::vector) FROM policy_clauses "
                                "ORDER BY embedding <=> %s::vector LIMIT %s", (vec, vec, k)).fetchall()
        return [{"id": r[0], "text": r[1], "score": round(float(r[2]), 3)} for r in rows]
