"""The NotebookLM wrapper's shape, checked without a network.

These exist because an edit once deleted the `client` property outright and the whole
suite still passed -- the mistake only surfaced part-way through a live run against the
operator's account. Anything the rest of the code calls should be guarded here, where it
costs milliseconds instead of minutes.
"""

from __future__ import annotations

import pytest

from nlmtools.common import nlm as nlm_module
from nlmtools.common.exits import NLM_AUTH, ToolError
from nlmtools.common.nlm import NotebookLM, Source


REQUIRED = [
    "client", "find_notebook", "create_notebook", "delete_notebook", "list_sources",
    "add_drive_text", "add_text", "delete_source", "source_text", "wait_until_ready",
    "query",
]


@pytest.mark.parametrize("name", REQUIRED)
def test_public_surface_exists(name):
    assert hasattr(NotebookLM, name), f"NotebookLM.{name} is missing"


def test_drive_sources_are_registered_as_plain_text():
    """The single fact the whole pipeline rests on (design.md 1.2)."""
    assert nlm_module.TEXT_MIME == "text/plain"


def test_only_the_error_status_is_terminal():
    """A transient status read as failure produced a false negative once."""
    assert nlm_module.STATUS_READY == 2
    assert nlm_module.STATUS_ERROR == 3
    assert nlm_module.STATUS_PROCESSING in nlm_module.PENDING
    assert nlm_module.STATUS_PREPARING in nlm_module.PENDING
    assert nlm_module.STATUS_READY not in nlm_module.PENDING
    assert nlm_module.STATUS_ERROR not in nlm_module.PENDING


def test_source_reports_its_state():
    assert Source("i", "t", status=2).ready
    assert not Source("i", "t", status=2).failed
    assert Source("i", "t", status=3).failed
    assert Source("i", "t", status=5).status_name == "preparing"


def test_auth_errors_are_classified_as_auth():
    error = nlm_module._wrap(RuntimeError("Authentication expired. Run 'nlm login'"))
    assert error.code == NLM_AUTH
    assert error.hint


def test_supplied_client_disables_automatic_renewal():
    """A caller passing its own client owns the session; do not re-authenticate under it."""
    assert NotebookLM(client=object())._auto_login is False
    assert NotebookLM()._auto_login is True


def test_renewal_is_attempted_once_then_the_error_stands():
    """A second failure will not be fixed by a third attempt."""
    calls = {"work": 0, "relogin": 0}

    class Flaky(NotebookLM):
        def _relogin(self):
            calls["relogin"] += 1
            return True  # claims success, but the work keeps failing

    instance = Flaky()

    @nlm_module._reauthing
    def always_expired(self):
        calls["work"] += 1
        raise ToolError(NLM_AUTH, "Authentication expired")

    with pytest.raises(ToolError) as caught:
        always_expired(instance)

    assert caught.value.code == NLM_AUTH
    assert calls["relogin"] == nlm_module.MAX_RELOGINS
    assert calls["work"] == nlm_module.MAX_RELOGINS + 1


def test_a_non_auth_failure_never_triggers_renewal():
    calls = {"relogin": 0}

    class Watched(NotebookLM):
        def _relogin(self):
            calls["relogin"] += 1
            return True

    @nlm_module._reauthing
    def fails_otherwise(self):
        raise ToolError(nlm_module.MISMATCH, "something else went wrong")

    with pytest.raises(ToolError):
        fails_otherwise(Watched())
    assert calls["relogin"] == 0
