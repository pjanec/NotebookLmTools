"""Concept help: the things no single argument owns (design.md 5.5).

Reachable as `nlmt help <topic>`, and included whole in `nlmt --ai`.
"""

from __future__ import annotations

from . import exits

TOPICS: dict[str, str] = {
    "workflow": """\
What these tools automate

    local .txt bundle  ->  Google Drive (plain text)  ->  NotebookLM sources

A .txt is NEVER uploaded to NotebookLM as a local file. NotebookLM's own text ingestion
strips function bodies out of bundled source code, keeping only headers and signatures.
The failure is silent: the sources are there, the answers read fluently, and the code is
gutted. Routing through Drive avoids it, and is the entire reason Drive is in the
pipeline. Sources are always added from Drive, never as a file upload.

Files on Drive stay plain UTF-8 text, byte-identical, BOM preserved. They are never
converted to Google Docs: a Doc cannot preserve a BOM and caps near 1.02M characters,
against roughly 1 MB chunks.

Sources are import-time copies. There is no sync back to Drive, so refreshing content
means deleting the old sources and adding new ones -- which is what `load` does.

Typical sequence:

    nlmt doctor
    nlmt load   --notebook "Engine review" --local-folder D:\\exports\\engine
    nlmt ask    --notebook "Engine review" --question-file question.md --attach data.json
""",
    "masks": """\
Masks: how a run is scoped

A mask is a starts-with prefix on a filename or a source title. Masks are the only thing
that decides what a run may touch.

  * Files: `.txt` only, one folder, not recursive.
  * Sources: a source is in scope only if its title starts with one of the masks.
  * The notebook is NOT a mirror. Sources outside the mask are never deleted, including
    ones added by hand in the web UI.
  * No --mask at all means every .txt in the folder.
  * An empty selection is an error (exit 18), never a silent no-op: loading nothing would
    delete the notebook's sources and replace them with nothing.

Ask sources (see 'nlmt help asks') are excluded from load's automatic delete-by-mask
whatever the mask says, so a question in flight is never collateral damage.
""",
    "asks": """\
Ask sources and the Q<n>- prefix

A long question becomes temporary sources in the notebook: the question body, plus one
source per --attach'ed data file. They all share a short ordinal prefix:

    Q7-q-locking-review        the question body
    Q7-a1-config.json.txt      first attachment
    Q7-a2-metrics.csv.txt      second attachment

Because they share the prefix, one ask is one mask. That is the point:

    nlmt delete --notebook "Engine review" --mask Q7-

The ordinal is one greater than the highest already in the notebook, so nothing is
persisted between runs. Ordinals are reused after an ask is deleted; the durable record
is the transcript under answers/, whose filename carries the full timestamp.

Titles matching ^Q\\d+- are reserved. Do not name corpus files that way.

The question body goes in as text, directly. Attachments go through Drive, like the
corpus, because an attached JSON or CSV is the data the answer depends on -- a dropped
element or a truncated field produces a confidently wrong answer.

`ask` deletes everything it created, in a finally block. --keep skips that, for
debugging; `nlmt sweep` clears strays.
""",
    "readiness": """\
Readiness: why a query has to wait

Indexing takes minutes. The NotebookLM web UI shows a spinner per source while it works,
and a notebook queried before indexing finishes answers from an incomplete corpus without
saying so.

So every load, and every ask, waits until all of its sources are ready before querying:

  * Preferred signal: the per-source processing status, polled every --poll-interval.
  * Fallback, if no status field is exposed: run --smoke-question and check the answer
    contains --smoke-expect, with backoff.

Give up after --ready-timeout and exit 16. A good smoke question is answerable only from
inside a function body, so that every load re-verifies that bodies survived ingestion.
""",
    "secrets": """\
Where credentials live

Nothing sensitive is stored in plaintext on disk, and nothing sensitive is ever written
inside the repository.

  * Google Drive is reached through rclone. Its config file is encrypted.
  * The encryption password lives in Windows Credential Manager (DPAPI, bound to your
    Windows account), and is handed to rclone only through an environment variable on the
    child process. It is never logged and never appears in the JSON envelope.
  * NotebookLM cookies live in the profile `nlm` manages, outside the repository.

Because Credential Manager is machine-bound, moving to another Windows machine means one
interactive login there -- run setup.ps1 and it will walk you through it.

If a credential is ever committed, treat it as leaked: rotate it, do not merely delete
the file in a later commit.
""",
    "output": """\
Output contract

Human-readable text is the default. With --json, one envelope is written to stdout and
nothing else is; all logs and progress go to stderr, so stdout pipes straight into a JSON
parser.

    {
      "ok": true,
      "action": "load",
      "notebook": "Engine review",
      "counts": {"selected": 20, "uploaded": 20, "verified": 20, "added": 20},
      "ready": true,
      "elapsed_s": 412,
      "warnings": [],
      "error": null
    }

On failure, `ok` is false and `error` carries a code, a class, a message, a `hint` naming
the corrective action, and a `detail` object -- the one place internal IDs ever appear.

A partial failure still emits a complete envelope with accurate counts, so a caller can
see how far the run got. Field names never change meaning between versions.
""",
}


def exit_codes_topic() -> str:
    lines = [
        "Exit codes",
        "",
        "Every failure class has its own code, so a caller can branch on it without",
        "parsing text. Every error also carries a hint naming the corrective action.",
        "",
        f"  {'code':<6}{'class':<18}{'meaning':<48}action",
    ]
    for code in sorted(exits.TABLE):
        name, meaning, action = exits.TABLE[code]
        lines.append(f"  {code:<6}{name:<18}{meaning:<48}{action}")
    return "\n".join(lines) + "\n"


def get(topic: str) -> str | None:
    if topic in ("exit-codes", "exit_codes", "exits"):
        return exit_codes_topic()
    return TOPICS.get(topic)


def names() -> list[str]:
    return sorted(list(TOPICS) + ["exit-codes"])
