"""Input/output guardrails: PII redaction, prompt-injection screening, grounding and business-rule checks."""
import re
from dataclasses import dataclass, field
from datetime import date

PII = {
    "AADHAAR": re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"),
    "PAN": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
    "CARD": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
    "EMAIL": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"),
    "PHONE": re.compile(r"(?<!\d)(?:\+91[- ]?)?[6-9]\d{9}(?!\d)"),
}
INJECTION = [r"ignore (all |any )?(previous|prior|above) instructions", r"disregard (the )?(system|above)",
             r"you are now", r"reveal (your|the) (system )?prompt", r"approve (this|the) claim (immediately|without)",
             r"set (the )?(fraud|risk) score to"]
POLICY_NO = re.compile(r"^POL-\d{4}-\d{6}$")


def redact_pii(text: str):
    found = {}
    for label, rx in PII.items():
        def sub(m, label=label):
            found[label] = found.get(label, 0) + 1
            return f"[{label}]"
        text = rx.sub(sub, text)
    return text, found


def screen_injection(text: str) -> list[str]:
    return [p for p in INJECTION if re.search(p, text, re.I)]


@dataclass
class Verdict:
    ok: bool = True
    issues: list[str] = field(default_factory=list)


def check_extraction(source: str, fields: dict, doc_type: str) -> Verdict:
    """Fields must be grounded (evidence quoted verbatim from the source) and pass business-rule sanity checks."""
    v = Verdict()
    norm = re.sub(r"\s+", " ", source.lower())
    for name, f in fields.items():
        ev = re.sub(r"\s+", " ", str(f.get("evidence", "")).lower())
        if not ev or ev not in norm:
            v.issues.append(f"{name}: evidence not found in source (possible hallucination)")
    if "claim_amount" in fields or "total_amount" in fields:
        raw = (fields.get("claim_amount") or fields.get("total_amount"))["value"]
        try:
            if float(str(raw).replace(",", "")) <= 0:
                v.issues.append("amount must be positive")
        except ValueError:
            v.issues.append(f"amount not numeric: {raw!r}")
    if "policy_number" in fields and not POLICY_NO.match(str(fields["policy_number"]["value"])):
        v.issues.append("policy_number format invalid")
    if "loss_date" in fields:
        try:
            if date.fromisoformat(str(fields["loss_date"]["value"])) > date.today():
                v.issues.append("loss_date is in the future")
        except ValueError:
            v.issues.append("loss_date not ISO-8601")
    v.ok = not v.issues
    return v


AUTO_PAYOUT_LIMIT = 100_000


def authorize_agent_action(route: str, claim_amount: int) -> Verdict:
    """The agent only recommends. Anything it can't safely say is forced to a human lane."""
    if route == "fast_track" and claim_amount > AUTO_PAYOUT_LIMIT:
        return Verdict(False, [f"fast_track not allowed above {AUTO_PAYOUT_LIMIT}"])
    return Verdict()
