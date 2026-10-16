"""Tamper-evident audit log: each entry commits to the previous entry's hash (append-only JSONL)."""
import hashlib
import json
import time
from pathlib import Path

GENESIS = "0" * 64


def _h(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class AuditLog:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def entries(self):
        return [json.loads(l) for l in self.path.read_text().splitlines() if l.strip()]

    def head(self):
        e = self.entries()
        return e[-1]["hash"] if e else GENESIS

    def append(self, actor, action, subject, detail=None, ts=None):
        e = self.entries()
        entry = {"seq": len(e) + 1, "ts": ts if ts is not None else time.time(), "actor": actor, "action": action,
                 "subject": subject, "detail": detail or {}, "prev": e[-1]["hash"] if e else GENESIS}
        entry["hash"] = _h(entry)
        with open(self.path, "a") as f:
            f.write(json.dumps(entry) + "\n")
        return entry

    def verify(self):
        """Returns (ok, first_bad_seq). Detects edits, deletions, insertions and reordering."""
        prev = GENESIS
        for i, e in enumerate(self.entries(), start=1):
            if e["seq"] != i or e["prev"] != prev or e["hash"] != _h(e):
                return False, i
            prev = e["hash"]
        return True, None
