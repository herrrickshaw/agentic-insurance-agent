"""workers/runner.py: the external-task long-poll loop, with the engine and the handlers faked."""
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "workers"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import runner  # noqa: E402


class Stop(BaseException):
    """Raised by fakes to break out of `while True` (BaseException: the loop's `except Exception` must not eat it)."""


class Engine:
    """Scripted stand-in for runner.call: batches of tasks for fetchAndLock, records everything else."""
    def __init__(self, *fetches):
        self.fetches, self.calls = list(fetches), []

    def __call__(self, path, body):
        self.calls.append((path, body))
        if path.endswith("fetchAndLock"):
            if not self.fetches:
                raise Stop
            f = self.fetches.pop(0)
            if isinstance(f, Exception):
                raise f
            return f
        return None


@pytest.fixture
def loop(monkeypatch):
    monkeypatch.setattr(runner, "connect", lambda: "CON")
    sleeps = []
    monkeypatch.setattr(runner.time, "sleep", sleeps.append)

    def start(engine, handlers):
        monkeypatch.setattr(runner, "call", engine)
        monkeypatch.setattr(runner, "HANDLERS", handlers)
        with pytest.raises(Stop):
            runner.main()
        return sleeps
    return start


def task(topic="t1", tid="id1", variables=None, retries=None, pid="0123456789abcdef"):
    d = {"id": tid, "topicName": topic, "processInstanceId": pid, "variables": variables if variables is not None else {}}
    if retries is not None:
        d["retries"] = retries
    return d


def test_call_rejects_non_http_engine_url(monkeypatch):
    monkeypatch.setattr(runner, "ENGINE", "file:///etc/passwd")
    with pytest.raises(ValueError, match="ENGINE_URL must be http"):
        runner.call("/x", {})
    monkeypatch.setattr(runner, "ENGINE", "ftp://h")
    with pytest.raises(ValueError):
        runner.call("/x", {})


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_call_posts_json_and_parses_reply(monkeypatch, scheme):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen.update(url=req.full_url, data=req.data, headers=dict(req.header_items()), timeout=timeout, method=req.get_method())
        return Resp(b'{"ok": true}')
    monkeypatch.setattr(runner, "ENGINE", f"{scheme}://engine/rest")
    monkeypatch.setattr(runner.urllib.request, "urlopen", fake_urlopen)
    assert runner.call("/p", {"a": 1}) == {"ok": True}
    assert seen["url"] == f"{scheme}://engine/rest/p" and json.loads(seen["data"]) == {"a": 1}
    assert seen["timeout"] == 60 and seen["method"] == "POST"
    assert seen["headers"]["Content-type"] == "application/json"


def test_call_empty_body_returns_none(monkeypatch):
    monkeypatch.setattr(runner, "ENGINE", "http://e")
    monkeypatch.setattr(runner.urllib.request, "urlopen", lambda req, timeout=None: Resp(b""))
    assert runner.call("/complete", {}) is None


def test_typed_bool_before_int_and_defaults():
    assert runner.typed(True) == {"value": True, "type": "Boolean"}
    assert runner.typed(False)["type"] == "Boolean"                   # bool is an int subclass: must not become Long
    assert runner.typed(0) == {"value": 0, "type": "Long"}
    assert runner.typed(1.0) == {"value": 1.0, "type": "Double"}
    assert runner.typed("s") == {"value": "s", "type": "String"}
    assert runner.typed(None) == {"value": None, "type": "String"}
    assert runner.typed([1]) == {"value": [1], "type": "String"}


def test_main_subscribes_to_every_topic_and_completes_tasks(loop):
    seen = {}

    def h1(con, v):
        seen["args"] = (con, v)
        return {"n": 3, "flag": True, "s": "x", "f": 0.5}
    eng = Engine([task("t1", "A", {"x": {"value": 7, "type": "Long"}, "y": {"value": "s"}, "z": {}})])
    loop(eng, {"t1": h1, "t2": lambda c, v: {}})
    assert seen["args"] == ("CON", {"x": 7, "y": "s", "z": None})
    path, body = eng.calls[0]
    assert path == "/external-task/fetchAndLock"
    assert body["workerId"] == runner.WORKER_ID and body["maxTasks"] == 10 and body["asyncResponseTimeout"] == 20000
    assert body["topics"] == [{"topicName": t, "lockDuration": 30000, "deserializeValues": False} for t in ("t1", "t2")]
    path, body = eng.calls[1]
    assert path == "/external-task/A/complete" and body["workerId"] == runner.WORKER_ID
    assert body["variables"] == {"n": {"value": 3, "type": "Long"}, "flag": {"value": True, "type": "Boolean"},
                                 "s": {"value": "x", "type": "String"}, "f": {"value": 0.5, "type": "Double"}}


def test_task_without_variables_key_gets_empty_vars(loop):
    seen = []
    t = task("t1", "A")
    del t["variables"]
    loop(Engine([t]), {"t1": lambda c, v: seen.append(v) or {}})
    assert seen == [{}]


def test_engine_unavailable_sleeps_three_seconds_then_retries(loop):
    eng = Engine(ConnectionError("down"), OSError("still"), [task("t1", "B")])
    done = []
    sleeps = loop(eng, {"t1": lambda c, v: done.append(1) or {}})
    assert sleeps == [3, 3] and done == [1]
    assert [c[0] for c in eng.calls].count("/external-task/fetchAndLock") == 4      # 2 failed, 1 ok, 1 Stop


def test_handler_failure_reports_decremented_retries_and_continues(loop):
    def boom(con, v):
        raise RuntimeError("x" * 600)
    ok = []
    eng = Engine([task("bad", "F1", retries=2), task("good", "G1")])
    loop(eng, {"bad": boom, "good": lambda c, v: ok.append(1) or {}})
    fail = next(c for c in eng.calls if c[0] == "/external-task/F1/failure")
    assert fail[1] == {"workerId": runner.WORKER_ID, "errorMessage": "x" * 500, "retries": 1, "retryTimeout": 5000}
    assert ok == [1]                                   # next task in the batch still ran
    assert any(c[0] == "/external-task/G1/complete" for c in eng.calls)


@pytest.mark.parametrize("given,expected", [(None, 2), (0, -1), (5, 4)])
def test_failure_retries_default_to_three_and_zero_is_respected(loop, given, expected):
    eng = Engine([task("bad", "F", retries=given)])
    loop(eng, {"bad": lambda c, v: 1 / 0})
    fail = next(c for c in eng.calls if c[0] == "/external-task/F/failure")
    assert fail[1]["retries"] == expected


def test_unknown_topic_is_reported_as_failure(loop):
    eng = Engine([task("mystery", "U")])
    loop(eng, {"t1": lambda c, v: {}})
    fail = next(c for c in eng.calls if c[0] == "/external-task/U/failure")
    assert "mystery" in fail[1]["errorMessage"]


def test_complete_call_failure_triggers_failure_report(loop):
    class E(Engine):
        def __call__(self, path, body):
            if path.endswith("/complete"):
                self.calls.append((path, body))
                raise OSError("complete failed")
            return super().__call__(path, body)
    eng = E([task("t1", "C")])
    loop(eng, {"t1": lambda c, v: {}})
    assert any(c[0] == "/external-task/C/failure" for c in eng.calls)


def test_empty_batch_loops_without_sleeping(loop):
    eng = Engine([], [], [])
    sleeps = loop(eng, {"t1": lambda c, v: {}})
    assert sleeps == [] and len(eng.calls) == 4
