"""The long-question protocol (design.md 8).

A long question is often more than text: it references attached data files -- a JSON
export, a CSV, a log extract -- that the question asks about. Both the question and its
attachments are temporary sources, created for one query and removed afterwards.

Two rules that are easy to get wrong and expensive to get wrong:

* **The question text goes in directly; attachments go through Drive.** A question is
  prose with no code bodies to lose. An attached JSON *is* the data the answer depends on,
  so it takes the proven, checksum-verified path.
* **Cleanup happens in `finally`.** An abandoned question or attachment source pollutes
  retrieval for every later answer in that notebook.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from ..common import naming
from ..common.envelope import Envelope
from ..common.exits import USAGE, ToolError
from ..common.locking import NotebookLock
from ..common.nlm import NotebookLM
from ..loader.drive import Drive

log = logging.getLogger("nlmtools.ask")

#: Attachments must be text-like. Embedding binaries is out of scope, and a binary would
#: arrive as mojibake rather than failing loudly.
TEXT_SUFFIXES = {
    ".txt", ".json", ".csv", ".tsv", ".log", ".md", ".yaml", ".yml",
    ".xml", ".ini", ".cfg", ".toml", ".sql", ".html",
}


def _check_attachment(path: Path) -> None:
    if not path.is_file():
        raise ToolError(
            USAGE,
            f"attachment not found: {path}",
            hint="check the path passed to --attach",
        )
    if path.suffix.lower() not in TEXT_SUFFIXES:
        raise ToolError(
            USAGE,
            f"{path.name!r} is not a text-like file",
            hint="attachments must be text (" + ", ".join(sorted(TEXT_SUFFIXES)) + ")",
        )
    head = path.read_bytes()[:8192]
    if b"\x00" in head:
        raise ToolError(
            USAGE,
            f"{path.name!r} looks like a binary file",
            hint="attachments must be text; embedding binaries is out of scope",
        )


def build_prompt(question_title: str, attachments: list[tuple[str, str]]) -> str:
    """Generate the query prompt, bridging the naming gap (design.md 8.3).

    The question body refers to `config.json`; the notebook knows a source called
    `Q7-a1-config.json.txt`. Without an explicit mapping the model cannot connect them.
    """
    lines = [f'Answer the question in source "{question_title}".']
    if attachments:
        lines.append("The data it refers to is attached as these sources:")
        width = max(len(original) for original, _ in attachments)
        for original, title in attachments:
            lines.append(f'  {original:<{width}} -> source "{title}"')
    lines.append("Use the other sources in this notebook as evidence.")
    return "\n".join(lines)


def write_transcript(
    answers_dir: Path,
    notebook: str,
    ordinal: int,
    slug: str,
    question: str,
    attachments: list[tuple[str, str]],
    answer: str,
    sources: list[str],
) -> Path:
    """Save the answer. The timestamp is what makes it durable: ordinals get reused."""
    stamp = time.strftime("%Y-%m-%dT%H-%M-%SZ", time.gmtime())
    folder = Path(answers_dir) / naming.slugify(notebook)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{stamp}-Q{ordinal}-{slug}.md"

    body = [
        f"# Q{ordinal} — {slug}",
        "",
        f"- notebook: {notebook}",
        f"- asked: {stamp}",
    ]
    if attachments:
        body.append("- attachments: " + ", ".join(o for o, _ in attachments))
    body += ["", "## Question", "", question.strip(), "", "## Answer", "", answer.strip()]
    body += ["", "## Sources at time of asking", ""]
    body += [f"- {title}" for title in sources]
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    return path


def run_ask(
    *,
    notebook_title: str,
    question: str,
    attachments: list[Path],
    project: str,
    lock_dir: Path,
    answers_dir: Path,
    name: str | None = None,
    keep: bool = False,
    drive: Drive | None = None,
    nlm: NotebookLM | None = None,
    ready_timeout: float = 1200,
    poll_interval: float = 15,
    query_timeout: float = 300,
) -> Envelope:
    envelope = Envelope(action="ask")
    envelope.set(notebook=notebook_title)

    question = (question or "").strip()
    if not question:
        raise ToolError(
            USAGE,
            "the question body is empty",
            hint="pass --question TEXT, --question-file PATH, or pipe it on stdin",
        )
    for path in attachments:
        _check_attachment(path)

    drive = drive or Drive()
    nlm = nlm or NotebookLM()
    slug = naming.slugify(name or question[:60])

    # The lock is taken, not merely checked: otherwise a concurrent load could delete the
    # corpus out from under a running question.
    with NotebookLock(notebook_title, lock_dir):
        notebook_id = nlm.find_notebook(notebook_title)
        if not notebook_id:
            raise ToolError(
                USAGE,
                f"no notebook titled {notebook_title!r}",
                hint="load a bundle into it first, or check the title",
            )

        existing = nlm.list_sources(notebook_id)
        ordinal = naming.next_ask_ordinal([s.title for s in existing])
        envelope.set(ask=f"Q{ordinal}", notebook_id=notebook_id)
        log.info("ask Q%d in %s", ordinal, notebook_title)

        created: list[str] = []
        drive_bundle = f"_ask/Q{ordinal}"
        mapping: list[tuple[str, str]] = []

        try:
            # Attachments first: they must be present before the question refers to them.
            for index, path in enumerate(attachments, start=1):
                # The source title must carry the Q<n>- prefix, and for a Drive source
                # the title is NOT ours to choose: NotebookLM names the source after the
                # Drive file, ignoring the title argument. So the prefix has to go on the
                # Drive filename itself, or the attachment ends up unprefixed, invisible
                # to sweep and to `delete --mask Q<n>-`, and stranded in the corpus.
                # The `.txt` suffix stays because NotebookLM rejects other types, and the
                # original name stays visible because 8.3's prompt maps it back.
                title = naming.attachment_title(ordinal, index, path.name)
                log.info("attaching %s as %s", path.name, title)
                uploaded = drive.upload_one(path, project, drive_bundle, title)
                created.append(nlm.add_drive_text(notebook_id, uploaded.id, title))
                mapping.append((path.name, title))
                envelope.bump("attachments")

            question_title = naming.question_title(ordinal, slug)
            created.append(nlm.add_text(notebook_id, question_title, question))

            nlm.wait_until_ready(
                notebook_id, created,
                timeout=ready_timeout, poll_interval=poll_interval,
            )

            prompt = build_prompt(question_title, mapping)
            log.info("querying")
            result = nlm.query(notebook_id, prompt, timeout=query_timeout)
            answer = result.get("answer", "") or ""

            live = [s.title for s in nlm.list_sources(notebook_id)
                    if not naming.is_ask_source(s.title)]
            transcript = write_transcript(
                answers_dir, notebook_title, ordinal, slug,
                question, mapping, answer, live,
            )
            envelope.set(
                transcript=str(transcript),
                answer_chars=len(answer),
                citations=len(result.get("citations") or {}),
                corpus_sources=len(live),
            )
            envelope.count("created", len(created))
        finally:
            # Always, even on failure: a stranded question source pollutes every later
            # answer in this notebook.
            if keep:
                envelope.set(kept=True, cleanup_mask=naming.ask_mask(ordinal))
                log.info("keeping ask sources; remove later with --mask %s",
                         naming.ask_mask(ordinal))
            else:
                # Delete by mask rather than by the ids we happen to hold. A source can
                # exist server-side while the call that created it reported failure, and
                # such a source would otherwise be stranded -- which is exactly what
                # happened the first time this ran. The ordinal prefix is what makes the
                # sweep exact.
                mask = naming.ask_mask(ordinal)
                doomed = {source_id: None for source_id in created}
                try:
                    for source in nlm.list_sources(notebook_id):
                        if source.title.startswith(mask):
                            doomed[source.id] = None
                except ToolError as error:
                    envelope.warn(f"could not enumerate sources for cleanup: {error}")

                for source_id in doomed:
                    try:
                        nlm.delete_source(source_id)
                    except ToolError as error:
                        envelope.warn(f"could not delete a question source: {error}")
                if attachments:
                    drive.delete_path(project, drive_bundle)
                    # And the container, once the last ask under it is gone.
                    drive.remove_dir_if_empty(project, "_ask")
                envelope.count("cleaned_up", len(doomed))

    return envelope
