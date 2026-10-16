"""ROT analysis: Redundant (exact/near duplicates), Obsolete (past retention), Trivial (temp/empty)."""
import re
from collections import defaultdict


def shingles(text, k=5):
    w = re.findall(r"\w+", text.lower())
    return {" ".join(w[i:i + k]) for i in range(max(len(w) - k + 1, 0))}


def jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


JUNK = re.compile(r"(copy of|archive|backup|drafts?|old|_v\d|\(\d\)|~)", re.I)


def _junk(path):
    """Prefer the copy that lives in the primary tree: fewer 'Copy of / Archive / Drafts / backup' markers wins."""
    return len(JUNK.findall(path))


def find_exact_duplicates(items):
    """sha256 groups -> {path_of_duplicate: path_kept}. Keep the shortest, then alphabetically first path."""
    groups = defaultdict(list)
    for it in items:
        if it.size > 0:
            groups[it.sha256].append(it.rel_path)
    out = {}
    for paths in groups.values():
        if len(paths) > 1:
            keep = sorted(paths, key=lambda p: (_junk(p), len(p), p))[0]
            out.update({p: keep for p in paths if p != keep})
    return out


def find_near_duplicates(items, threshold=0.9, min_chars=60):
    """Pairwise Jaccard on 5-word shingles within the same top-level folder (cheap blocking). Returns {path: other}."""
    by_top = defaultdict(list)
    for it in items:
        if len(it.text) > min_chars:
            by_top[it.rel_path.split("/")[0]].append((it.rel_path, shingles(it.text), it.sha256))
    out = {}
    for group in by_top.values():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                if group[i][2] != group[j][2] and jaccard(group[i][1], group[j][1]) >= threshold:
                    out.setdefault(group[j][0], group[i][0])
    return out


def is_trivial(item, min_chars=40):
    return item.is_temp or item.size == 0 or (item.ext in {".txt", ".md", ".eml"} and len(item.text.strip()) < min_chars)
