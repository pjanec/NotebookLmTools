"""Locking, including the stale-lock reclaim that makes a rerun recover (design.md 10.2).

M8 kills a run mid-load and expects the rerun to converge. A naive lockfile would deadlock
there forever, so these tests pin the reclaim behaviour.
"""

from __future__ import annotations

import json
import os

import pytest

from nlmtools.common.exits import LOCKED, ToolError
from nlmtools.common.locking import NotebookLock


def test_acquire_and_release(tmp_path):
    lock = NotebookLock("Engine review", tmp_path)
    with lock:
        assert lock.path.exists()
    assert not lock.path.exists()


def test_second_live_holder_is_refused(tmp_path):
    first = NotebookLock("Engine review", tmp_path)
    first.acquire()
    try:
        with pytest.raises(ToolError) as caught:
            NotebookLock("Engine review", tmp_path).acquire()
        assert caught.value.code == LOCKED
        assert caught.value.hint
    finally:
        first.release()


def test_a_dead_owners_lock_is_reclaimed(tmp_path):
    """The M8 case: the previous run was killed, so its lock must not block the rerun."""
    lock_path = tmp_path / "engine-review.lock"
    lock_path.write_text(
        json.dumps({"pid": 999_999_999, "notebook": "Engine review", "started": 0}),
        encoding="utf-8",
    )
    with NotebookLock("Engine review", tmp_path):
        owner = json.loads(lock_path.read_text(encoding="utf-8"))
        assert owner["pid"] == os.getpid()


def test_a_corrupt_lockfile_is_reclaimed(tmp_path):
    (tmp_path / "engine-review.lock").write_text("{not json", encoding="utf-8")
    with NotebookLock("Engine review", tmp_path):
        pass


def test_different_notebooks_do_not_block_each_other(tmp_path):
    with NotebookLock("Engine review", tmp_path):
        with NotebookLock("Storage review", tmp_path):
            pass


def test_lock_name_is_slugified(tmp_path):
    lock = NotebookLock("Engine — locking review", tmp_path)
    assert lock.path.name == "engine-locking-review.lock"


def test_release_is_idempotent(tmp_path):
    lock = NotebookLock("Engine review", tmp_path)
    lock.acquire()
    lock.release()
    lock.release()
