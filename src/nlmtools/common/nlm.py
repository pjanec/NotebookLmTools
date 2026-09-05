"""NotebookLM access, through `notebooklm_tools` as a library.

**Not through the `nlm` CLI.** The CLI's `--type` accepts only `doc|slides|sheets|pdf`
(`services/sources.py: DRIVE_MIME_TYPES`), so it cannot describe a plain `.txt` on Drive
-- it declares the file a Google Doc, and the source then lists but never resolves, its
title stuck as the raw Drive id. The library's `add_drive_source` takes a free-form mime
type, which is the single fact this whole pipeline rests on (see NOTES.md, 2026-09-05).

Measured with `text/plain` on a 1 MB file: ready in ~13s, ingested text exactly 100.0% of
the local file. The same file as a converted Google Doc failed outright.

Everything here maps failures onto this project's exit codes, so a NotebookLM problem is
never mistaken for a Drive one.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .exits import AMBIGUOUS, MISMATCH, NLM_AUTH, NLM_QUOTA, NOT_READY, ToolError

#: Drive files are uploaded and registered as plain text. Never a Google Docs mime type:
#: conversion reflows the content, cannot preserve a BOM, and caps near 1.02M characters.
TEXT_MIME = "text/plain"

# Source status, from NotebookLMClient's own constants.
STATUS_PROCESSING = 1
STATUS_READY = 2
STATUS_ERROR = 3
STATUS_PREPARING = 5

#: Statuses that mean "still working". A source sat at PREPARING for over a minute during
#: testing before becoming READY, so treating a transient non-ready as failure produces
#: false negatives.
PENDING = {STATUS_PROCESSING, STATUS_PREPARING, 0, None}

STATUS_NAMES = {
    STATUS_PROCESSING: "processing",
    STATUS_READY: "ready",
    STATUS_ERROR: "error",
    STATUS_PREPARING: "preparing",
}


@dataclass(frozen=True)
class Source:
    id: str
    title: str
    status: int | None = None
    type: str | None = None
    is_stale: bool = False

    @property
    def ready(self) -> bool:
        return self.status == STATUS_READY

    @property
    def failed(self) -> bool:
        return self.status == STATUS_ERROR

    @property
    def status_name(self) -> str:
        return STATUS_NAMES.get(self.status, f"unknown({self.status})")


def _wrap(error: Exception) -> ToolError:
    """Translate a library error into one of this project's failure classes."""
    text = str(error).lower()
    if any(word in text for word in ("auth", "unauthor", "401", "403", "cookie", "login")):
        return ToolError(
            NLM_AUTH,
            f"NotebookLM rejected the request: {error}",
            hint="run 'nlm login' on this host, then retry",
        )
    if any(word in text for word in ("quota", "rate limit", "429", "resource_exhausted")):
        return ToolError(
            NLM_QUOTA,
            f"NotebookLM rate or quota limit: {error}",
            hint="wait and retry; do not loop on this error",
        )
    return ToolError(
        MISMATCH,
        f"NotebookLM request failed: {type(error).__name__}: {error}",
        hint="re-run the command; a whole-run rerun is the recovery path",
    )


class NotebookLM:
    def __init__(self, client=None, *, profile: str | None = None) -> None:
        self._client = client
        self._profile = profile

    @property
    def client(self):
        if self._client is None:
            try:
                from notebooklm_tools.cli.utils import get_client

                self._client = get_client(self._profile)
            except Exception as error:  # noqa: BLE001
                raise ToolError(
                    NLM_AUTH,
                    f"could not open a NotebookLM session: {error}",
                    hint="run 'nlm login' on this host, then retry",
                ) from error
        return self._client

    # -- notebooks ----------------------------------------------------------------

    def find_notebook(self, title: str) -> str | None:
        """Resolve a notebook by exact title.

        Duplicate titles are a hard stop (design.md 3.5): NotebookLM allows them and the
        tool must not pick one. Both ids go in the error detail so the operator can tell
        them apart in the web UI -- the one place internal ids ever surface.
        """
        try:
            notebooks = self.client.list_notebooks() or []
        except Exception as error:  # noqa: BLE001
            raise _wrap(error) from error

        matches = [n for n in notebooks if getattr(n, "title", None) == title]
        if len(matches) > 1:
            raise ToolError(
                AMBIGUOUS,
                f"{len(matches)} notebooks are titled {title!r}",
                hint="rename or delete the duplicate in the NotebookLM web UI, then retry",
                detail={"notebook_ids": [getattr(n, "id", "?") for n in matches]},
            )
        return getattr(matches[0], "id", None) if matches else None

    def create_notebook(self, title: str) -> str:
        try:
            created = self.client.create_notebook(title)
        except Exception as error:  # noqa: BLE001
            raise _wrap(error) from error
        notebook_id = getattr(created, "id", None)
        if not notebook_id:
            raise ToolError(
                MISMATCH,
                f"creating notebook {title!r} returned no id",
                hint="check the NotebookLM web UI, then re-run",
            )
        return notebook_id

    def delete_notebook(self, notebook_id: str) -> bool:
        try:
            return bool(self.client.delete_notebook(notebook_id))
        except Exception as error:  # noqa: BLE001
            raise _wrap(error) from error

    # -- sources ------------------------------------------------------------------

    def list_sources(self, notebook_id: str) -> list[Source]:
        try:
            raw = self.client.get_notebook_sources_with_types(notebook_id) or []
        except Exception as error:  # noqa: BLE001
            raise _wrap(error) from error
        return [
            Source(
                id=str(item.get("id")),
                title=str(item.get("title", "")),
                status=item.get("status"),
                type=item.get("type"),
                is_stale=bool(item.get("is_stale", False)),
            )
            for item in raw
        ]

    def add_drive_text(self, notebook_id: str, drive_file_id: str, title: str) -> str:
        """Register a plain-text Drive file as a source.

        `mime_type=text/plain` is the whole point -- see the module docstring.
        """
        try:
            result = self.client.add_drive_source(
                notebook_id, drive_file_id, title, mime_type=TEXT_MIME, wait=False
            )
        except Exception as error:  # noqa: BLE001
            raise _wrap(error) from error
        source_id = (result or {}).get("id")
        if not source_id:
            raise ToolError(
                MISMATCH,
                f"adding {title!r} from Drive returned no source id",
                hint="re-run; if it persists, check the file is readable in Drive",
                detail={"drive_file_id": drive_file_id},
            )
        return str(source_id)

    def add_text(self, notebook_id: str, title: str, text: str) -> str:
        try:
            result = self.client.add_text_source(notebook_id, text, title=title, wait=False)
        except Exception as error:  # noqa: BLE001
            raise _wrap(error) from error
        source_id = (result or {}).get("id")
        if not source_id:
            raise ToolError(
                MISMATCH,
                f"adding text source {title!r} returned no source id",
                hint="re-run the command",
            )
        return str(source_id)

    def delete_source(self, source_id: str) -> bool:
        try:
            return bool(self.client.delete_source(source_id))
        except Exception as error:  # noqa: BLE001
            raise _wrap(error) from error

    def source_text(self, source_id: str) -> str:
        """The text as NotebookLM ingested it -- the evidence for design.md 6.5."""
        try:
            payload = self.client.get_source_fulltext(source_id) or {}
        except Exception as error:  # noqa: BLE001
            raise _wrap(error) from error
        if isinstance(payload, str):
            return payload
        for key in ("content", "text", "fulltext", "full_text"):
            value = payload.get(key)
            if isinstance(value, str):
                return value
        return ""

    # -- readiness ----------------------------------------------------------------

    def wait_until_ready(
        self,
        notebook_id: str,
        source_ids: list[str],
        *,
        timeout: float = 1200,
        poll_interval: float = 15,
        on_progress=None,
    ) -> list[Source]:
        """Block until every listed source is ready, or fail (design.md 7.1).

        Only STATUS_ERROR is terminal. PREPARING and PROCESSING are transient -- a 500 KB
        source stayed at PREPARING for over a minute in testing -- so a non-ready status
        must never be read as failure.
        """
        wanted = set(source_ids)
        deadline = time.monotonic() + timeout
        while True:
            sources = [s for s in self.list_sources(notebook_id) if s.id in wanted]

            failed = [s for s in sources if s.failed]
            if failed:
                raise ToolError(
                    MISMATCH,
                    f"{len(failed)} source(s) failed to process: "
                    + ", ".join(s.title for s in failed),
                    hint="check the file on Drive is plain text and readable, then re-run",
                    detail={"failed": [{"id": s.id, "title": s.title} for s in failed]},
                )

            pending = [s for s in sources if not s.ready]
            missing = wanted - {s.id for s in sources}
            if not pending and not missing:
                return sources

            if on_progress:
                on_progress(len(wanted) - len(pending) - len(missing), len(wanted))

            if time.monotonic() >= deadline:
                raise ToolError(
                    NOT_READY,
                    f"{len(pending) + len(missing)} of {len(wanted)} source(s) were still "
                    f"not ready after {timeout:.0f}s",
                    hint="wait, then run 'nlmt status'; raise --ready-timeout if this recurs",
                    detail={
                        "pending": [
                            {"title": s.title, "status": s.status_name} for s in pending
                        ],
                        "missing_ids": sorted(missing),
                    },
                )
            time.sleep(min(poll_interval, max(1.0, deadline - time.monotonic())))

    # -- querying -----------------------------------------------------------------

    def query(self, notebook_id: str, question: str, *, timeout: float = 300) -> dict:
        try:
            result = self.client.query(notebook_id, question, timeout=timeout)
        except Exception as error:  # noqa: BLE001
            raise _wrap(error) from error
        if isinstance(result, dict):
            return result
        return {"answer": str(result or ""), "citations": {}, "sources_used": []}
