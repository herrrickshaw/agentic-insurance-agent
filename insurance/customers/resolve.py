"""Entity resolution: blocking -> pairwise scoring -> auto-merge / steward review / new customer; golden record."""
import itertools
import uuid
from collections import defaultdict

AUTO, REVIEW = 0.92, 0.75


def jaro_winkler(a, b, p=0.1):
    a, b = a.lower(), b.lower()
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    md = max(la, lb) // 2 - 1
    ma, mb = [False] * la, [False] * lb
    m = 0
    for i in range(la):
        for j in range(max(0, i - md), min(lb, i + md + 1)):
            if not mb[j] and a[i] == b[j]:
                ma[i] = mb[j] = True
                m += 1
                break
    if m == 0:
        return 0.0
    t, k = 0, 0
    for i in range(la):
        if ma[i]:
            while not mb[k]:
                k += 1
            t += a[i] != b[k]
            k += 1
    jaro = (m / la + m / lb + (m - t / 2) / m) / 3
    l = 0
    for x, y in zip(a[:4], b[:4]):
        if x != y:
            break
        l += 1
    return jaro + l * p * (1 - jaro)


def score(a, b):
    """Weighted evidence. Government-ID hash equality is decisive; otherwise name+dob+contact must agree."""
    if a["pan_hash"] and a["pan_hash"] == b["pan_hash"]:
        return 1.0
    if a["aadhaar_hash"] and a["aadhaar_hash"] == b["aadhaar_hash"]:
        return 1.0
    if a["dob"] != b["dob"]:                       # different birth date: never the same person (typos go to review via name+contact)
        return min(0.6, 0.6 * jaro_winkler(a["full_name"], b["full_name"]))
    parts = [(0.4, jaro_winkler(a["full_name"], b["full_name"])), (0.2, 1.0)]       # name, dob (equal here)
    corroborated = False
    for key, w in (("phone", 0.2), ("email", 0.1), ("pincode", 0.1)):
        if a[key] and b[key]:                                                   # only fields present on both sides count
            eq = a[key] == b[key]
            parts.append((w, 1.0 if eq else 0.0))
            corroborated |= eq
    s = sum(w * x for w, x in parts) / sum(w for w, _ in parts)
    if not corroborated:                                                        # name+dob alone is never enough to auto-merge
        s = min(s, AUTO - 0.07)
    return round(s, 4)


def blocks(rows):
    b = defaultdict(set)
    for r in rows:
        for key in ("pan_hash", "aadhaar_hash", "phone", "email"):
            if r[key]:
                b[(key, r[key])].add(r["raw_id"])
        b[("dob_initial", r["dob"], r["full_name"][:1])].add(r["raw_id"])
    return b


class UnionFind:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def resolve_batch(con, batch_id):
    """Match this batch against ALL staged records. Auto-merge >= AUTO; queue REVIEW..AUTO for a data steward."""
    rows = [dict(r) for r in con.execute("SELECT * FROM customers_raw")]
    by_id = {r["raw_id"]: r for r in rows}
    batch_ids = {r["raw_id"] for r in rows if r["batch_id"] == batch_id}
    uf = UnionFind()
    seen, auto, queued = set(), 0, 0
    for r in rows:
        uf.find(r["raw_id"])
    # preserve earlier decisions: already-linked records stay together
    anchor = {}
    for r in rows:
        if r["golden_id"]:
            uf.union(r["raw_id"], anchor.setdefault(r["golden_id"], r["raw_id"]))
    for members in blocks(rows).values():
        for a, b in itertools.combinations(sorted(members), 2):
            if (a, b) in seen or (a not in batch_ids and b not in batch_ids):
                continue
            seen.add((a, b))
            if uf.find(a) == uf.find(b):
                continue
            s = score(by_id[a], by_id[b])
            if s >= AUTO:
                uf.union(a, b)
                auto += 1
            elif s >= REVIEW:
                con.execute("INSERT INTO match_queue(batch_id,raw_a,raw_b,score) VALUES(?,?,?,?)", (batch_id, a, b, s))
                queued += 1
    assign_golden(con, rows, uf)
    con.commit()
    return {"auto_merged": auto, "queued_for_review": queued,
            "golden_customers": con.execute("SELECT COUNT(*) FROM customers").fetchone()[0]}


def assign_golden(con, rows, uf):
    clusters = defaultdict(list)
    for r in rows:
        clusters[uf.find(r["raw_id"])].append(r)
    for members in clusters.values():
        gid = next((m["golden_id"] for m in members if m["golden_id"]), None) or "C" + uuid.uuid4().hex[:10]
        for m in members:
            con.execute("UPDATE customers_raw SET golden_id=? WHERE raw_id=?", (gid, m["raw_id"]))
        write_golden(con, gid, members)


def write_golden(con, gid, members):
    """Survivorship: latest record wins per field if non-empty (higher raw_id = more recent)."""
    members = sorted(members, key=lambda m: m["raw_id"], reverse=True)
    pick = lambda f: next((m[f] for m in members if m[f] not in (None, "")), None)
    con.execute("INSERT OR REPLACE INTO customers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (gid, pick("full_name"), pick("dob"), pick("gender"), pick("phone"), pick("email"), pick("pan_last4"),
                 pick("aadhaar_last4"), pick("aadhaar_valid"), pick("pincode"), pick("state"), pick("occupation"),
                 max(m["annual_income"] or 0 for m in members), int(any(m["smoker"] for m in members)),
                 ",".join(sorted({m["channel"] for m in members if m["channel"]})), len(members)))


def apply_steward_decision(con, queue_id, merge: bool):
    q = con.execute("SELECT * FROM match_queue WHERE id=?", (queue_id,)).fetchone()
    con.execute("UPDATE match_queue SET status=? WHERE id=?", ("merged" if merge else "distinct", queue_id))
    if merge:
        a = con.execute("SELECT golden_id FROM customers_raw WHERE raw_id=?", (q["raw_a"],)).fetchone()[0]
        b = con.execute("SELECT golden_id FROM customers_raw WHERE raw_id=?", (q["raw_b"],)).fetchone()[0]
        if a != b:
            con.execute("UPDATE customers_raw SET golden_id=? WHERE golden_id=?", (a, b))
            con.execute("DELETE FROM customers WHERE golden_id=?", (b,))
            members = [dict(r) for r in con.execute("SELECT * FROM customers_raw WHERE golden_id=?", (a,))]
            write_golden(con, a, members)
    con.commit()
