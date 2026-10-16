"""Target-platform path rules. Add a profile to support another destination (object store, file share, DMS)."""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TargetProfile:
    name: str
    max_path: int
    illegal: re.Pattern
    reserved_names: frozenset
    reserved_substrings: tuple = ()


SHAREPOINT_ONLINE = TargetProfile(
    "sharepoint_online", 400, re.compile(r'["*:<>?/\\|#%]'),
    frozenset({"con", "prn", "aux", "nul", "forms", "desktop.ini", "thumbs.db"}), ("_vti_",))
GENERIC_FS = TargetProfile(
    "generic_fs", 1000, re.compile(r'[<>:"|?*\\\x00-\x1f]'), frozenset({"con", "prn", "aux", "nul"}))

TARGETS = {p.name: p for p in (SHAREPOINT_ONLINE, GENERIC_FS)}
DEFAULT_TARGET = GENERIC_FS


def resolve(target):
    """Accepts a TargetProfile, a profile name, or None (-> RECORDS_TARGET env, else the generic default)."""
    import os
    if isinstance(target, TargetProfile):
        return target
    name = target or os.environ.get("RECORDS_TARGET", DEFAULT_TARGET.name)
    if name not in TARGETS:
        raise ValueError(f"unknown target {name!r}; choose from {sorted(TARGETS)}")
    return TARGETS[name]
