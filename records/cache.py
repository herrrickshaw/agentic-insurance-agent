"""Content-addressed classification cache: re-runs of an unchanged library cost zero model calls."""
import hashlib
import json
import sqlite3

from .classify import PLAN_FILE


def current_plan_version():
    return hashlib.sha256(PLAN_FILE.read_bytes()).hexdigest()[:12]


class ClassificationCache:
    def __init__(self, path=":memory:", plan_version=None):
        self.plan_version = plan_version or current_plan_version()
        self.con = sqlite3.connect(str(path), check_same_thread=False)
        self.con.execute("CREATE TABLE IF NOT EXISTS cls(sha TEXT, plan TEXT, value TEXT, PRIMARY KEY(sha, plan))")

    def get(self, sha):
        r = self.con.execute("SELECT value FROM cls WHERE sha=? AND plan=?", (sha, self.plan_version)).fetchone()
        return json.loads(r[0]) if r else None

    def put(self, sha, value):
        self.con.execute("INSERT OR REPLACE INTO cls VALUES(?,?,?)", (sha, self.plan_version, json.dumps(value)))
        self.con.commit()
