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
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ..common import naming
from ..common.envelope import Envelope
from ..common.exits import FIDELITY, MISMATCH, ToolError
from ..common.locking import NotebookLock
from ..common.nlm import NotebookLM
from .drive import Drive
from .select import select

log = logging.getLogger("nlmtools.load")


#: Identifier-like tokens: what source code is actually made of. Comparing these rather
#: than raw characters is what makes the check survive real-world input.
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")

#: Fraction of local tokens that must appear in the ingested text. Not 100%: real dumps
#: contain bytes that are not text at all, and NotebookLM sanitises them.
DEFAULT_COVERAGE = 0.995


def _tokens(text: str) -> Counter:
    return Counter(_TOKEN.findall(text))


def verify_ingested(
    nlm: NotebookLM,
    source_id: str,
    local_path: Path,
    *,
    coverage: float = DEFAULT_COVERAGE,
) -> tuple[float, int]:
    """Check that NotebookLM ingested the file's content (design.md 6.5).

    Returns (coverage, missing_token_count). Raises FIDELITY when too much is missing.

    **Why tokens rather than an exact comparison.** The first version compared normalised
    text for equality, and on real input it reported damage twice for reasons that were
    not damage at all:

    * A dump contained an embedded TrueType font -- NUL bytes and all -- and NotebookLM
      stripped the unprintable bytes. 256k characters "vanished" and none of them were
      source code.
    * Another file was not valid UTF-8 in one spot; NotebookLM decoded those bytes as
      cp1252 and we decoded them as UTF-8, so identical content compared unequal.

    Comparing identifier-like tokens ignores both, while still catching the failure that
    matters: NotebookLM stripping function bodies loses the identifiers inside them, and
    that shows up immediately as missing tokens.
    """
    ingested = nlm.source_text(source_id)
    local = local_path.read_text(encoding="utf-8-sig", errors="replace")

    local_tokens = _tokens(local)
    if not local_tokens:
        return 1.0, 0

    ingested_tokens = _tokens(ingested)
    missing = local_tokens - ingested_tokens
    missing_count = sum(missing.values())
    total = sum(local_tokens.values())
    found = (total - missing_count) / total

    if found < coverage:
        raise ToolError(
            FIDELITY,
            f"NotebookLM ingested only {found:.1%} of the identifiers in "
            f"{local_path.name!r}; {missing_count:,} of {total:,} are missing",
            hint="do not trust this notebook; the source was not ingested intact",
            detail={
                "source_id": source_id,
                "coverage": round(found, 4),
                "missing_tokens": missing_count,
                "examples": [t for t, _ in missing.most_common(10)],
                "local_chars": len(local),
                "ingested_chars": len(ingested),
            },
        )
    return found, missing_count


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
    concurrency: int = 8,
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
        uploaded = drive.upload(files, project, bundle, transfers=concurrency)
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
        if doomed:
            with ThreadPoolExecutor(max_workers=max(1, min(concurrency, len(doomed)))) as pool:
                list(pool.map(lambda s: nlm.delete_source(s.id), doomed))
        envelope.count("deleted", len(doomed))
        log.info("deleted %d existing source(s)", len(doomed))

        # 7. Add the new ones. The API takes one Drive id per call, but the calls are
        # independent and each costs several seconds server-side, so they run
        # concurrently -- sequentially this dominated the whole run. NotebookLM then
        # indexes the batch in parallel regardless.
        added: dict[str, str] = {}
        failures: list[BaseException] = []

        def _add(path: Path) -> tuple[str, str]:
            return path.name, nlm.add_drive_text(notebook_id, by_name[path.name].id, path.name)

        workers = max(1, min(concurrency, len(files)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for future in as_completed([pool.submit(_add, path) for path in files]):
                try:
                    name, source_id = future.result()
                    added[name] = source_id
                    log.info("added %d/%d", len(added), len(files))
                except BaseException as error:  # noqa: BLE001 - re-raised below
                    failures.append(error)

        if failures:
            # Sources that did land are cleaned up by the next run's delete-by-mask, so
            # the recovery path stays "run it again".
            raise failures[0]
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

            def _verify(path: Path) -> tuple[str, float, int]:
                found, missing = verify_ingested(nlm, added[path.name], path)
                return path.name, found, missing

            with ThreadPoolExecutor(max_workers=max(1, min(concurrency, len(targets)))) as pool:
                for name, found, missing in pool.map(_verify, targets):
                    log.info("verified %s: %.2f%% of identifiers present%s",
                             name, found * 100,
                             f" ({missing:,} missing)" if missing else "")
            envelope.count("ingest_verified", len(targets))
            envelope.set(verify_ingest=verify_ingest)

    return envelope
