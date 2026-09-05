"""Running external tools (rclone, nlm) safely and legibly.

Two things this module is responsible for:

* **Secrets never leak into logs.** The command line and environment are logged for
  debugging, so both are scrubbed first. A password reaching a log file would defeat the
  whole point of keeping it in Credential Manager.
* **A Drive failure never looks like a NotebookLM failure** (design.md 4.4). Each caller
  supplies the exit code its failures map to, so the operator reading exit 10 from a phone
  knows it is rclone, not NotebookLM.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from .exits import ToolError

log = logging.getLogger("nlmtools.process")

#: Environment variables whose values must never be logged.
_SECRET_ENV = {"RCLONE_CONFIG_PASS", "NOTEBOOKLM_COOKIES", "RCLONE_CONFIG_PASSWORD"}


def scrub_env(env: dict[str, str] | None) -> dict[str, str]:
    """A copy safe to log: secret values replaced, not merely shortened."""
    if not env:
        return {}
    return {k: ("<redacted>" if k in _SECRET_ENV else v) for k, v in env.items()}


def _describe(command: list[str]) -> str:
    return " ".join(str(part) for part in command)


class Result:
    def __init__(self, code: int, stdout: str, stderr: str) -> None:
        self.code = code
        self.stdout = stdout
        self.stderr = stderr

    @property
    def ok(self) -> bool:
        return self.code == 0

    def json(self) -> object:
        try:
            return json.loads(self.stdout or "null")
        except ValueError as error:
            raise ToolError(
                1,
                f"expected JSON from the tool but could not parse it: {error}",
                hint="re-run with --log-level debug to see the raw output",
                detail={"stdout_head": self.stdout[:400]},
            ) from error


def run(
    command: list[str | Path],
    *,
    failure_code: int,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
    timeout: float | None = None,
    check: bool = True,
    failure_hint: str | None = None,
) -> Result:
    """Run a child process and return its result.

    `failure_code` is the exit code a failure maps to, so the caller decides whether this
    is a Drive problem or a NotebookLM one.
    """
    argv = [str(part) for part in command]
    log.debug("run: %s", _describe(argv))
    if env is not None:
        log.debug("env: %s", scrub_env({k: v for k, v in env.items() if k in _SECRET_ENV}))

    try:
        completed = subprocess.run(  # noqa: S603 - argv is built from our own code
            argv,
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
        )
    except FileNotFoundError as error:
        raise ToolError(
            failure_code,
            f"{argv[0]} was not found",
            hint="run setup.ps1 to install the tools this project depends on",
        ) from error
    except subprocess.TimeoutExpired as error:
        raise ToolError(
            failure_code,
            f"{argv[0]} timed out after {timeout}s",
            hint="re-run; if it recurs, raise the timeout or check your connection",
        ) from error

    result = Result(completed.returncode, completed.stdout or "", completed.stderr or "")
    if result.stderr.strip():
        log.debug("stderr: %s", result.stderr.strip()[:2000])

    if check and not result.ok:
        detail_text = (result.stderr.strip() or result.stdout.strip())[:400]
        raise ToolError(
            failure_code,
            f"{argv[0]} failed (exit {result.code}): {detail_text}",
            hint=failure_hint,
            detail={"command": _describe(argv[:3]), "exit_code": result.code},
        )
    return result
