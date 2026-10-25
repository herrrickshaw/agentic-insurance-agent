"""PII redaction labels and completeness for card numbers vs Aadhaar."""
from ai.guardrails import authorize_agent_action, redact_pii


def test_spaced_card_number_is_fully_redacted_as_card():
    out, found = redact_pii("card 4111 1111 1111 1111 thanks")
    assert out == "card [CARD] thanks"
    assert found == {"CARD": 1}


def test_unspaced_card_number_is_redacted_as_card():
    out, found = redact_pii("card 4111111111111111 thanks")
    assert out == "card [CARD] thanks" and found == {"CARD": 1}


def test_spaced_aadhaar_still_labelled_aadhaar():
    out, found = redact_pii("uid 2345 6789 0123 ok")
    assert out == "uid [AADHAAR] ok" and found == {"AADHAAR": 1}


def test_fast_track_limit_boundary():
    assert authorize_agent_action("fast_track", 100_000).ok
    assert not authorize_agent_action("fast_track", 100_001).ok
    assert authorize_agent_action("adjuster", 10**9).ok
