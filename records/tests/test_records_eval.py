import tempfile
from pathlib import Path

import pytest

from ai.client import LLMClient
from ai.observe import TraceStore
from records import evals, pipeline, synth


@pytest.fixture(scope="module")
def scored():
    d = Path(tempfile.mkdtemp()); truth = synth.build(d / "lib")
    rows = pipeline.analyse(d / "lib", LLMClient(store=TraceStore(":memory:")), **evals.ANALYSE_KW)
    return rows, evals.score(rows, truth)


@pytest.mark.story("REC-1")
def test_classification_meets_threshold(scored):
    assert scored[1]["class_accuracy"] >= evals.THRESH["class_accuracy"]
    assert scored[1]["review_recall"] >= evals.THRESH["review_recall"]


@pytest.mark.story("REC-2")
def test_rot_detection(scored):
    assert scored[1]["rot_precision"] >= evals.THRESH["rot_precision"] and scored[1]["rot_recall"] >= evals.THRESH["rot_recall"]


@pytest.mark.story("REC-3")
def test_paths_are_made_safe_for_the_target_and_preflight_passes(scored):
    rows, s = scored
    assert s["path_recall"] == 1.0 and pipeline.preflight(rows, "sharepoint_online") == []


@pytest.mark.story("REC-4")
def test_pii_raises_sensitivity(scored):
    assert scored[1]["pii_recall"] >= evals.THRESH["pii_recall"]
