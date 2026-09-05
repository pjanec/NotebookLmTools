"""Exit codes and the error type that carries them.

One distinct, greppable exit code per failure class (design.md 4.4). The operator often
reads these from a phone, so every error also carries a `hint` naming the corrective
action (design.md 5.5) -- that field is mandatory, not decorative.
"""

from __future__ import annotations

OK = 0
INTERNAL = 1
DRIVE_AUTH = 10
NLM_AUTH = 11
NLM_QUOTA = 12
FIDELITY = 13
AMBIGUOUS = 14
MISMATCH = 15
NOT_READY = 16
LOCKED = 17
EMPTY_SELECTION = 18
USAGE = 19

#: code -> (class name, one-line meaning, default corrective action)
TABLE: dict[int, tuple[str, str, str]] = {
    OK: ("OK", "success", ""),
    INTERNAL: (
        "INTERNAL",
        "unexpected exception",
        "re-run with --log-level debug and report the traceback",
    ),
    DRIVE_AUTH: (
        "DRIVE_AUTH",
        "rclone cannot authenticate to Google Drive",
        "re-run setup.ps1 to reauthorize rclone",
    ),
    NLM_AUTH: (
        "NLM_AUTH",
        "NotebookLM cookies are invalid or expired",
        "run 'nlm login' on this host, then retry",
    ),
    NLM_QUOTA: (
        "NLM_QUOTA",
        "NotebookLM rate or quota limit hit",
        "wait and retry; do not loop on this error",
    ),
    FIDELITY: (
        "FIDELITY",
        "uploaded file checksum does not match the local file",
        "do not proceed; inspect the file on Drive before loading anything",
    ),
    AMBIGUOUS: (
        "AMBIGUOUS",
        "more than one notebook has the target title",
        "rename or delete the duplicate in the NotebookLM web UI, then retry",
    ),
    MISMATCH: (
        "MISMATCH",
        "the notebook's live sources do not match what was uploaded",
        "re-run the same command; a whole-run rerun is the recovery path",
    ),
    NOT_READY: (
        "NOT_READY",
        "indexing did not finish within the ceiling",
        "wait, then run 'nlmt status'; raise --ready-timeout if this recurs",
    ),
    LOCKED: (
        "LOCKED",
        "another run holds this notebook's lock",
        "wait for it to finish; a dead run's lock is reclaimed automatically",
    ),
    EMPTY_SELECTION: (
        "EMPTY_SELECTION",
        "the mask matched no files",
        "check --local-folder and --mask; nothing was changed",
    ),
    USAGE: (
        "USAGE",
        "bad or missing arguments",
        "see the usage above, or run 'nlmt <command> --help'",
    ),
}


def class_of(code: int) -> str:
    return TABLE.get(code, ("UNKNOWN", "", ""))[0]


def default_hint(code: int) -> str:
    return TABLE.get(code, ("", "", ""))[2]


class ToolError(Exception):
    """A failure with a known exit code, a message, and a corrective action.

    `hint` defaults to the class's standard remedy, so no code path can accidentally
    produce an error that does not tell the caller what to do about it.
    """

    def __init__(
        self,
        code: int,
        message: str,
        *,
        hint: str | None = None,
        detail: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint or default_hint(code)
        self.detail = detail or {}

    @property
    def error_class(self) -> str:
        return class_of(self.code)

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "class": self.error_class,
            "message": self.message,
            "hint": self.hint,
            "detail": self.detail,
        }

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.error_class}: {self.message}"
