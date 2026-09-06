"""The job protocol shared by the cloud client and the Windows agent.

A job is a file in a private git repository. There is no listening socket anywhere: the
cloud VM pushes a request, the Windows agent polls, executes, and pushes a result. Nothing
on the Windows machine is reachable from the internet, which is the whole point of choosing
git as the channel.

**The security model in one line:** the agent exposes three fixed verbs with no free-form
parameters, so a leaked ops-repo token lets someone ask questions and trigger refreshes --
not run commands on the machine holding the Google credentials.

Validation lives here, on the agent's side of the trust boundary. Anything arriving in a
job file is untrusted input, however it got there.
"""

from __future__ import annotations

import base64
import json
import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path

#: The only verbs the agent will execute. Anything else is refused unread.
VERBS = ("refresh", "ask", "status")

#: Verbs that once existed, kept only so the refusal can say what replaced them. A
#: caller working from older documentation gets an instruction rather than a puzzle.
RETIRED_VERBS = {
    "sync": "sync was folded into refresh, which now always syncs first; "
            "use `refresh --ref <branch>`",
}

#: A git ref. Deliberately narrow: no spaces, no options, nothing that could be read as a
#: flag by git, and no URL -- refs are resolved against the agent's configured origin, so a
#: caller can never point the machine at a repository of their choosing.
REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")

#: Attachment names are reduced to a bare filename with an allowed extension.
ATTACHMENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
ATTACHMENT_SUFFIXES = {".txt", ".json", ".csv", ".tsv", ".log", ".md", ".yaml", ".yml", ".xml"}

MAX_QUESTION_CHARS = 200_000
MAX_ATTACHMENT_BYTES = 8_000_000
MAX_ATTACHMENTS = 5
MAX_NOTEBOOK_CHARS = 200


class JobRejected(Exception):
    """The job is malformed or asks for something outside the allowlist."""


@dataclass
class Attachment:
    name: str
    content_b64: str

    def decoded(self) -> bytes:
        return base64.b64decode(self.content_b64)


@dataclass
class Job:
    verb: str
    id: str = field(default_factory=lambda: f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}")
    project: str | None = None
    notebook: str | None = None
    ref: str | None = None
    question: str | None = None
    name: str | None = None
    attachments: list[Attachment] = field(default_factory=list)
    created: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    requested_by: str = "cloud"

    def __post_init__(self) -> None:
        # Attachments arrive as plain dicts from JSON, and a caller constructing a Job by
        # hand will pass dicts too. Coerce once, here, so validation and execution never
        # have to care which they were given.
        self.attachments = [
            a if isinstance(a, Attachment) else Attachment(
                name=(a or {}).get("name", ""), content_b64=(a or {}).get("content_b64", "")
            )
            for a in self.attachments
        ]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "Job":
        try:
            data = json.loads(raw)
        except ValueError as error:
            raise JobRejected(f"not valid JSON: {error}") from error
        if not isinstance(data, dict):
            raise JobRejected("job must be a JSON object")
        attachments = [
            Attachment(name=a.get("name", ""), content_b64=a.get("content_b64", ""))
            for a in data.get("attachments") or []
        ]
        known = {f for f in cls.__dataclass_fields__ if f != "attachments"}
        return cls(attachments=attachments, **{k: v for k, v in data.items() if k in known})


@dataclass
class Result:
    id: str
    verb: str
    ok: bool
    exit_code: int = 0
    answer: str | None = None
    detail: dict = field(default_factory=dict)
    error: str | None = None
    hint: str | None = None
    started: str = ""
    finished: str = ""
    agent: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "Result":
        data = json.loads(raw)
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})


def validate(job: Job) -> Job:
    """Check a job before anything is executed. Raises JobRejected.

    Everything the agent will act on passes through here. The rules are deliberately
    stricter than they need to be: it is far easier to loosen one later than to discover
    that a field nobody thought about reached a subprocess.
    """
    if job.verb in RETIRED_VERBS:
        raise JobRejected(RETIRED_VERBS[job.verb])
    if job.verb not in VERBS:
        raise JobRejected(f"unknown verb {job.verb!r}; allowed: {', '.join(VERBS)}")

    if not job.id or not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$", job.id):
        raise JobRejected("job id is missing or malformed")

    # A ref is optional -- refresh without one syncs the project's default branch --
    # but whenever one is present it is checked before it can reach git.
    if job.ref is not None:
        if not REF_PATTERN.match(job.ref):
            raise JobRejected(
                f"ref {job.ref!r} is not a plain ref name. A ref is resolved against the "
                "agent's own origin; URLs and options are never accepted."
            )
        if job.ref.startswith("-") or ".." in job.ref:
            raise JobRejected("ref may not start with '-' or contain '..'")

    if job.verb == "ask":
        if not (job.question or "").strip():
            raise JobRejected("ask requires a question")
        if len(job.question or "") > MAX_QUESTION_CHARS:
            raise JobRejected(
                f"question is {len(job.question):,} characters; the limit is "
                f"{MAX_QUESTION_CHARS:,}"
            )
        if len(job.attachments) > MAX_ATTACHMENTS:
            raise JobRejected(f"at most {MAX_ATTACHMENTS} attachments")
        for attachment in job.attachments:
            _validate_attachment(attachment)

    if job.name is not None and not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", job.name):
        raise JobRejected("name must be a short alphanumeric slug")

    # The project selects which repository and notebook the verb acts on, so it is checked
    # here for shape and resolved against the agent's configuration later.
    if job.project is not None and not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", job.project):
        raise JobRejected("project must be a plain identifier")

    # A notebook title is human text -- spaces, dashes, accents are all fine -- so it is
    # checked for shape rather than matched against a pattern. Whether *this* project may
    # use *that* notebook is decided by the agent's configuration, not here.
    if job.notebook is not None:
        title = job.notebook
        if not title.strip():
            raise JobRejected("notebook, if given, must not be blank")
        if len(title) > MAX_NOTEBOOK_CHARS:
            raise JobRejected(f"notebook title is longer than {MAX_NOTEBOOK_CHARS} characters")
        if any(ch in title for ch in "\r\n\t") or any(ord(ch) < 32 for ch in title):
            raise JobRejected("notebook title may not contain control characters")

    return job


def _validate_attachment(attachment: Attachment) -> None:
    # Refuse a path rather than quietly reducing it to a basename. Silently rewriting
    # hostile input hides intent, and `Path(...).name` disagrees between Windows and Linux
    # on a string like "C:\dir\f.json" -- so the agent and the client would not even
    # reject the same things. The pattern below admits no separator, drive letter or colon.
    name = attachment.name or ""
    if not ATTACHMENT_NAME_PATTERN.match(name):
        raise JobRejected(
            f"attachment name {attachment.name!r} must be a plain filename: no directories, "
            "no drive letters, no separators"
        )
    if Path(name).suffix.lower() not in ATTACHMENT_SUFFIXES:
        raise JobRejected(
            f"attachment {name!r} must be text-like "
            f"({', '.join(sorted(ATTACHMENT_SUFFIXES))})"
        )
    try:
        raw = base64.b64decode(attachment.content_b64, validate=True)
    except Exception as error:  # noqa: BLE001
        raise JobRejected(f"attachment {name!r} is not valid base64: {error}") from error
    if len(raw) > MAX_ATTACHMENT_BYTES:
        raise JobRejected(
            f"attachment {name!r} is {len(raw):,} bytes; the limit is "
            f"{MAX_ATTACHMENT_BYTES:,}"
        )
    if b"\x00" in raw[:8192]:
        raise JobRejected(f"attachment {name!r} looks like a binary file")


def safe_attachment_name(name: str) -> str:
    """The name an attachment is written under, with any path stripped."""
    return Path(name).name
