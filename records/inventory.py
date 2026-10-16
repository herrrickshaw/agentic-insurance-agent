"""Scan an exported legacy library: file facts, content hash, text excerpt, and target-platform path problems."""
import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .targets import DEFAULT_TARGET, TargetProfile

TEMP = re.compile(r"^(~\$.*|.*\.tmp|thumbs\.db|desktop\.ini|\.ds_store)$", re.I)
TEXT_EXT = {".txt", ".md", ".csv", ".eml", ".log", ".json", ".xml", ".htm", ".html"}


@dataclass
class Item:
    rel_path: str
    size: int
    ext: str
    sha256: str
    text: str
    path_issues: list = field(default_factory=list)
    is_temp: bool = False


def path_issues(rel_path: str, profile: TargetProfile = DEFAULT_TARGET) -> list[str]:
    issues, parts = [], Path(rel_path).parts
    if len(rel_path) > profile.max_path:
        issues.append(f"path length {len(rel_path)} > {profile.max_path}")
    for p in parts:
        low = p.lower()
        if profile.illegal.search(p):
            issues.append(f"illegal character in '{p}'")
        if p != p.strip() or p.endswith("."):
            issues.append(f"leading/trailing space or dot in '{p}'")
        if low.split(".")[0] in profile.reserved_names or low in profile.reserved_names or any(s in low for s in profile.reserved_substrings):
            issues.append(f"reserved name '{p}'")
    return issues


def scan(root, max_text=6000, profile: TargetProfile = DEFAULT_TARGET):
    root = Path(root)
    for dp, dirs, files in os.walk(root):
        dirs.sort()                                   # deterministic order across filesystems
        for f in sorted(files):
            p = Path(dp) / f
            rel = str(p.relative_to(root))
            data = p.read_bytes()
            text = data[:max_text * 2].decode("utf-8", "ignore") if p.suffix.lower() in TEXT_EXT else ""
            yield Item(rel, len(data), p.suffix.lower(), hashlib.sha256(data).hexdigest(), text[:max_text],
                       path_issues(rel, profile), bool(TEMP.match(f)))
