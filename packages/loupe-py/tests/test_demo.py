"""`loupe demo` seed tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from loupe.annotation import AnnotationStore
from loupe.demo import seed


@pytest.fixture()
def loupe_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "loupe-home"
    home.mkdir()
    monkeypatch.setenv("LOUPE_HOME", str(home))
    from loupe import store as store_mod

    store_mod._default = None
    return home


def test_demo_seeds_three_traces(loupe_home: Path) -> None:
    ids = seed()
    assert len(ids) == 3

    files = sorted((loupe_home / "traces").glob("*.jsonl"))
    assert len(files) == 3


def test_demo_tags_the_failure_by_default(loupe_home: Path) -> None:
    ids = seed()
    assert len(ids) >= 2
    # The middle trace is the failure
    fail_id = ids[1]
    annotations = AnnotationStore().load(fail_id)
    assert len(annotations) == 1
    ann = annotations[0]
    assert ann.failure_category == "unguarded-delete"
    assert ann.severity == "critical"
    assert "rm -rf" in ann.notes
    assert ann.annotator == "loupe-demo"
    assert "coding" in ann.tags


def test_demo_no_tag_option_skips_annotation(loupe_home: Path) -> None:
    ids = seed(tag_failure=False)
    fail_id = ids[1]
    assert AnnotationStore().load(fail_id) == []
