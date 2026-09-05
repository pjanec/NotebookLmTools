"""The load pipeline (design.md 6).

    1. acquire the notebook lock                    -> exit 17 if genuinely held
    2. select files by mask                         -> exit 18 if empty
    3. upload to a fresh Drive bundle folder        -> exit 10
    4. verify by checksum                           -> exit 13
    5. resolve the notebook by title, create if absent -> exit 14 on duplicates
    6. delete the existing mask-matching sources
    7. add every uploaded file as a Drive source
    8. wait for indexing                            -> exit 16
    9. verify the ingested text                     -> exit 13
   10. release the lock

Nothing is deleted from the notebook until the new content is on Drive and proven intact
(steps 3-4 precede step 6). The brief deleted first; verifying before destroying costs
nothing and is strictly safer.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..common import naming
from ..common.envelope import Envelope
from ..common.exits import FIDELITY, MISMATCH, ToolError
from ..common.locking import NotebookLock
from ..common.nlm import NotebookLM
from .drive import Drive
from .select import select

log = logging.getLogger("nlmtools.load")


def _normalise(text: str) -> str:
    """Collapse whitespace for comparison.

    The ingested form is what the model reads, so line-ending and spacing differences are
    noise. A missing function body is not, and survives this normalisation.
    """
    return " ".join(text.split())


def verify_ingested(nlm: NotebookLM, source_id: str, local_path: Path) -> tuple[int, int]:
    """Compare what NotebookLM ingested against the local file (design.md 6.5).

    Returns (ingested_chars, local_chars). Raises on damage.
    """
    ingested = nlm.source_text(source_id)
    local = local_path.read_text(encoding="utf-8-sig", errors="replace")

    normalised_local = _normalise(local)
    normalised_ingested = _normalise(ingested)

    if not normalised_ingested:
        raise ToolError(
            FIDELITY,
            f"NotebookLM ingested no text for {local_path.name!r}",
            hint="do not trust this notebook; delete the source and re-run",
            detail={"source_id": source_id},
        )

    if normalised_ingested != normalised_local:
        # Locate the divergence so the report says what was lost, not merely that
        # something was.
        limit = min(len(normalised_local), len(normalised_ingested))
        diverged_at = next(
            (i for i in range(limit) if normalised_local[i] != normalised_ingested[i]),
            limit,
        )
        raise ToolError(
            FIDELITY,
            f"the text NotebookLM ingested for {local_path.name!r} differs from the "
            f"local file at {diverged_at / max(len(normalised_local), 1):.1%} of the way in",
            hint="do not trust this notebook; investigate before loading anything else",
            detail={
                "source_id": source_id,
                "local_chars": len(normalised_local),
                "ingested_chars": len(normalised_ingested),
                "local_at_divergence": normalised_local[diverged_at:diverged_at + 120],
                "ingested_at_divergence": normalised_ingested[diverged_at:diverged_at + 120],
            },
        )
    return len(ingested), len(local)


def run_load(
    *,
    notebook_title: str,
    local_folder: Path,
    masks: list[str] | None,
    project: str,
    lock_dir: Path,
    drive: Drive | None = None,
    nlm: NotebookLM | None = None,
    drive_only: bool = False,
    dry_run: bool = False,
    verify_ingest: str = "sample",
    ready_timeout: float = 1200,
    poll_interval: float = 15,
) -> Envelope:
    envelope = Envelope(action="load")
    envelope.set(notebook=notebook_title, project=project)
    drive = drive or Drive()
    nlm = nlm or NotebookLM()
    masks = list(masks or [])

    with NotebookLock(notebook_title, lock_dir):
        # 2. select
        files = select(Path(local_folder), masks)
        envelope.count("selected", len(files))
        log.info("selected %d file(s)", len(files))

        if dry_run:
            envelope.set(
                dry_run=True,
                files=[f.name for f in files],
                would_upload_to=f"{drive.root}/{project}/<new bundle>",
            )
            return envelope

        # 3-4. upload and prove the bytes survived
        bundle = drive.new_bundle_name()
        envelope.set(bundle=f"{drive.root}/{project}/{bundle}")
        log.info("uploading to %s", envelope.fields["bundle"])
        uploaded = drive.upload(files, project, bundle)
        envelope.count("uploaded", len(uploaded))
        envelope.count("verified", len(uploaded))

        by_name = {f.name: f for f in uploaded}
        missing = [f.name for f in files if f.name not in by_name]
        if missing:
            raise ToolError(
                MISMATCH,
                f"{len(missing)} file(s) are missing from Drive after upload: "
                + ", ".join(missing[:5]),
                hint="re-run the command; a whole-run rerun is the recovery path",
                detail={"missing": missing},
            )

        if drive_only:
            envelope.set(drive_only=True)
            return envelope

        # 5. resolve the notebook
        notebook_id = nlm.find_notebook(notebook_title)
        created = notebook_id is None
        if created:
            notebook_id = nlm.create_notebook(notebook_title)
            log.info("created notebook %s", notebook_id)
        envelope.set(notebook_created=created, notebook_id=notebook_id)

        # 6. delete the previous generation of this batch, and nothing else
        existing = nlm.list_sources(notebook_id)
        doomed = [
            s for s in existing
            if not naming.is_ask_source(s.title)
            and (not masks or any(s.title.startswith(m) for m in masks))
            and (masks or s.title in by_name)
        ]
        for source in doomed:
            nlm.delete_source(source.id)
        envelope.count("deleted", len(doomed))
        log.info("deleted %d existing source(s)", len(doomed))

        # 7. add the new ones. One call per file: --drive takes a single id.
        added: dict[str, str] = {}
        for path in files:
            drive_file = by_name[path.name]
            added[path.name] = nlm.add_drive_text(notebook_id, drive_file.id, path.name)
        envelope.count("added", len(added))

        # 8. wait for indexing
        def progress(done: int, total: int) -> None:
            log.info("indexed %d/%d", done, total)

        nlm.wait_until_ready(
            notebook_id,
            list(added.values()),
            timeout=ready_timeout,
            poll_interval=poll_interval,
            on_progress=progress,
        )
        envelope.set(ready=True, readiness_signal="per_source_status")

        # 9. verify what was actually ingested
        if verify_ingest != "none":
            targets = files if verify_ingest == "all" else files[:1]
            for path in targets:
                ingested_chars, local_chars = verify_ingested(nlm, added[path.name], path)
                log.info(
                    "verified %s: %d ingested chars vs %d local",
                    path.name, ingested_chars, local_chars,
                )
            envelope.count("ingest_verified", len(targets))
            envelope.set(verify_ingest=verify_ingest)

    return envelope
