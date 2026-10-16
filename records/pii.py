"""PII detection for sensitivity labelling (detect and label; the matched value is never stored).

Generic patterns always run. National identifier formats are region packs you opt into (RECORDS_PII_REGIONS=US,IN),
because a format that is an ID in one country is just a number elsewhere.
"""
import os
import re

GENERIC = {
    "iban": r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b",
    "card": r"\b(?:\d[ -]?){13,16}\b",
    "email": r"\b[\w.+-]+@[\w-]+(\.[\w-]+)+\b",
    "phone_intl": r"(?<!\d)\+\d{1,3}[ -]?\d{3}[ -]?\d{4}(?!\d)",
}
REGIONS = {
    "US": {"national_id": r"\b\d{3}-\d{2}-\d{4}\b"},
    "IN": {"national_id": r"\b\d{4} \d{4} \d{4}\b", "tax_id": r"\b[A-Z]{5}\d{4}[A-Z]\b"},
    "IS": {"national_id": r"\b\d{6}-\d{4}\b"},
    "GB": {"national_id": r"\b[A-CEGHJ-PR-TW-Z]{2}\d{6}[A-D]\b"},
}
HIGH_SENSITIVITY = {"national_id", "tax_id", "iban", "card"}


def default_regions():
    return tuple(r for r in os.environ.get("RECORDS_PII_REGIONS", "").split(",") if r)


def detect(text: str, regions=None) -> dict:
    regions = default_regions() if regions is None else regions
    patterns = dict(GENERIC)
    for r in regions:
        patterns.update(REGIONS[r])
    return {k: len(re.findall(p, text)) for k, p in patterns.items() if re.search(p, text)}
