"""Scan an exported legacy library: file facts, content hash, text excerpt, and SharePoint Online constraints."""
import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

ILLEGAL = re.compile(r'["*:<>?/\\|#%]')            # SPO also rejects # and % in older tooling; treat as unsafe
FORBIDDEN_NAMES = {"con", "prn", "aux", "nul", "forms", "_vti_", "desktop.ini", "thumbs.db"}
TEMP = re.compile(r"^(~\$.*|.*\.tmp|thumbs\.db|desktop\.ini|\.ds_store)$", re.I)
MAX_PATH = 400                                       # SharePoint Online decoded URL path limit
TEXT_EXT = {".txt", ".md", ".csv", ".eml", ".log", ".json", ".xml", ".htm", ".html"}


@dataclass
class Item:
    rel_path: str
    size: int
    ext: str
    sha256: str
    text: str
    spo_issues: list = field(default_factory=list)
    is_temp: bool = False


def spo_issues(rel_path: str) -> list[str]:
    issues, parts = [], Path(rel_path).parts
    if len(rel_path) > MAX_PATH:
        issues.append(f"path length {len(rel_path)} > {MAX_PATH}")
    for p in parts:
        if ILLEGAL.search(p):
            issues.append(f"illegal character in '{p}'")
        if p != p.strip() or p.endswith("."):
            issues.append(f"leading/trailing space or dot in '{p}'")
        if p.lower().split(".")[0] in FORBIDDEN_NAMES or p.lower() in FORBIDDEN_NAMES or "_vti_" in p.lower():
            issues.append(f"reserved name '{p}'")
    return issues


def scan(root, max_text=6000):
    root = Path(root)
    for dp, _, files in os.walk(root):
        for f in sorted(files):
            p = Path(dp) / f
            rel = str(p.relative_to(root))
            data = p.read_bytes()
            text = data[:max_text * 2].decode("utf-8", "ignore") if p.suffix.lower() in TEXT_EXT else ""
            yield Item(rel, len(data), p.suffix.lower(), hashlib.sha256(data).hexdigest(), text[:max_text],
                       spo_issues(rel), bool(TEMP.match(f)))
