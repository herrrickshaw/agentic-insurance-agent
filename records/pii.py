"""PII detection for sensitivity labelling (detect + label; never store the matched value)."""
import re

PATTERNS = {
    "national_id_IS": re.compile(r"\b\d{6}-\d{4}\b"),                   # Icelandic kennitala, hyphenated form
    "iban": re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),
    "card": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+(\.[\w-]+)+\b"),
    "phone_intl": re.compile(r"(?<!\d)\+\d{1,3}[ -]?\d{3}[ -]?\d{4}(?!\d)"),
}


def detect(text: str) -> dict:
    return {k: len(rx.findall(text)) for k, rx in PATTERNS.items() if rx.search(text)}
