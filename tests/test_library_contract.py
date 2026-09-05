"""What we rely on from `notebooklm_tools`, asserted.

We do **not** fork or patch that package -- `requirements.lock` pins the published release
and the installed files are untouched. What we do is call its library API directly instead
of going through its `nlm` CLI, because the CLI cannot express two things this project
needs: a `text/plain` mime type for a Drive source, and a headless session renewal.

That is a legitimate use of the package, but some of it is not a documented stable
interface. A version bump could rename or remove any of it. These tests fail immediately
and legibly when that happens, rather than letting it surface part-way through a live run
against the operator's account.

They are offline: imports and signatures only, no network, no credentials.
"""

from __future__ import annotations

import inspect

import pytest


def test_client_class_is_importable():
    from notebooklm_tools.core.client import NotebookLMClient  # noqa: F401


def test_authenticated_client_factory_is_importable():
    """How we obtain a client from the saved profile."""
    from notebooklm_tools.cli.utils import get_client

    assert callable(get_client)


def test_headless_auth_is_importable():
    """Session renewal with no browser window (design.md 4.2)."""
    from notebooklm_tools.utils.auth_browser import run_headless_auth

    parameters = inspect.signature(run_headless_auth).parameters
    assert "profile_name" in parameters
    assert "timeout" in parameters


def test_config_accessor_is_importable():
    from notebooklm_tools.utils.config import get_config

    assert callable(get_config)


@pytest.mark.parametrize(
    "method",
    [
        "list_notebooks", "create_notebook", "delete_notebook",
        "get_notebook_sources_with_types",
        "add_drive_source", "add_text_source", "delete_source",
        "get_source_fulltext", "query",
    ],
)
def test_client_method_exists(method):
    from notebooklm_tools.core.client import NotebookLMClient

    assert hasattr(NotebookLMClient, method), f"NotebookLMClient.{method} is gone"


def test_drive_sources_accept_a_free_form_mime_type():
    """The single fact the whole pipeline rests on.

    Without this parameter a plain .txt on Drive cannot be registered as what it is, the
    source never resolves, and there is no route to the proven manual workflow at all.
    """
    from notebooklm_tools.core.client import NotebookLMClient

    parameters = inspect.signature(NotebookLMClient.add_drive_source).parameters
    assert "mime_type" in parameters


def test_query_exposes_conversation_control():
    """Continuing a thread is wanted; starting a clean one must stay possible."""
    from notebooklm_tools.core.client import NotebookLMClient

    parameters = inspect.signature(NotebookLMClient.query).parameters
    assert "conversation_id" in parameters
    assert "new_conversation" in parameters


def test_source_status_constants_match_what_we_assume():
    """We branch on these; only the error state is terminal (design.md 7.1)."""
    from notebooklm_tools.core.client import NotebookLMClient

    from nlmtools.common import nlm

    assert NotebookLMClient.SOURCE_STATUS_READY == nlm.STATUS_READY
    assert NotebookLMClient.SOURCE_STATUS_ERROR == nlm.STATUS_ERROR
    assert NotebookLMClient.SOURCE_STATUS_PROCESSING == nlm.STATUS_PROCESSING
    assert NotebookLMClient.SOURCE_STATUS_PREPARING == nlm.STATUS_PREPARING


def test_the_cli_still_cannot_express_plain_text():
    """The reason we bypass the CLI, asserted so the workaround can be retired.

    If a future release adds a plain-text entry here, driving the CLI becomes viable again
    and this test should be revisited -- not deleted quietly.
    """
    from notebooklm_tools.services.sources import DRIVE_MIME_TYPES

    assert "text/plain" not in DRIVE_MIME_TYPES.values()
