"""Repo-wide pytest hooks: traceability export (TRACE_OUT) and live-stack skipping for integration tests."""
import json
import os
import urllib.request

import pytest


def _engine_up():
    try:
        urllib.request.urlopen(os.environ.get("ENGINE_URL", "http://localhost:8080/engine-rest") + "/version", timeout=2)
        return True
    except Exception:
        return False


def pytest_collection_modifyitems(config, items):
    if os.environ.get("TRACE_OUT"):
        trace = {}
        for it in items:
            for m in it.iter_markers("story"):
                trace.setdefault(m.args[0], []).append(it.nodeid)
        with open(os.environ["TRACE_OUT"], "w") as f:
            json.dump(trace, f, indent=1)
    if any(i.get_closest_marker("integration") for i in items) and not _engine_up():
        skip = pytest.mark.skip(reason="live engine not reachable (start the compose stack)")
        for it in items:
            if it.get_closest_marker("integration"):
                it.add_marker(skip)


def pytest_bdd_apply_tag(tag, function):
    """Gherkin tags like @REC-5 become story markers, so scenarios count toward traceability."""
    import re
    if re.fullmatch(r"[A-Z]+-\d+", tag):
        pytest.mark.story(tag)(function)
        return True
    return None
