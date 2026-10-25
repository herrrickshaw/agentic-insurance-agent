"""Regression tests for the 2026-10-25 static sweep (bandit/semgrep S306). Offline; writes only to tmp_path."""
import importlib.util
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_traceability():
    spec = importlib.util.spec_from_file_location("traceability_tool", ROOT / "tools" / "traceability.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_traceability_does_not_use_insecure_mktemp(tmp_path, monkeypatch):
    """tempfile.mktemp() hands back a name without creating the file (race / symlink
    attack, deprecated); the gate must create its scratch file atomically instead."""
    mod = _load_traceability()
    (tmp_path / "docs" / "product").mkdir(parents=True)
    (tmp_path / "docs" / "product" / "BACKLOG.yaml").write_text(
        "stories:\n"
        "  - id: S1\n    title: demo\n    status: done\n    priority: Must\n"
        "    value: 5\n    tc: 3\n    rr: 2\n    size: 2\n    ac: [a, b]\n"
    )
    monkeypatch.setattr(mod, "ROOT", tmp_path)

    def _no_mktemp(*a, **k):
        raise AssertionError("tempfile.mktemp must not be used")

    monkeypatch.setattr(tempfile, "mktemp", _no_mktemp)

    def _fake_pytest(cmd, cwd, env, **k):
        Path(env["TRACE_OUT"]).write_text('{"S1": ["tests/test_x.py::test_a"]}')
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", _fake_pytest)

    rc = mod.main()
    assert rc == 0
    md = (tmp_path / "docs" / "product" / "TRACEABILITY.md").read_text()
    assert "| S1 demo | Must | done |" in md
