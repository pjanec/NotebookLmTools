"""Google Drive, via rclone (design.md 4.1, 6.1).

Files are stored as **plain `text/plain`, byte-for-byte identical to the local file, BOM
preserved**. Never converted to a native Google Doc: conversion reflows the content, loses
a BOM, caps near 1.02M characters, and -- measured -- fails to import at 1 MB, while the
unconverted file imports in seconds with its text exactly intact.

Verification is `rclone check --checksum`, so byte-identity is proven rather than assumed.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from ..common import secrets
from ..common.exits import DRIVE_AUTH, FIDELITY, ToolError
from ..common.process import run


def rclone_path(repo_root: Path | None = None) -> Path:
    root = repo_root or Path(__file__).resolve().parents[3]
    exe = root / "tools" / "rclone.exe"
    if not exe.exists():
        raise ToolError(
            DRIVE_AUTH,
            f"rclone is not installed at {exe}",
            hint="run setup.ps1 to install it",
        )
    return exe


@dataclass(frozen=True)
class DriveFile:
    id: str
    name: str
    size: int
    mime: str


class Drive:
    """Only what load, ask, sweep and prune need, so failures map to our exit codes."""

    def __init__(
        self,
        *,
        remote: str = "nlmtools",
        root: str = "/NotebookLmTools",
        exe: Path | None = None,
    ) -> None:
        self.remote = remote
        self.root = "/" + root.strip("/")
        self.exe = exe or rclone_path()

    # -- plumbing -----------------------------------------------------------------

    def _run(self, args: list[str], *, hint: str | None = None, check: bool = True):
        # The config file is encrypted; its password reaches rclone only through the
        # environment of this one child process, and is never logged (common/process.py).
        return run(
            [self.exe, *args],
            failure_code=DRIVE_AUTH,
            env=secrets.rclone_env(),
            check=check,
            failure_hint=hint or "run setup.ps1 to reauthorize Google Drive",
        )

    def _path(self, *parts: str) -> str:
        joined = "/".join(p.strip("/") for p in parts if p)
        return f"{self.remote}:{self.root}/{joined}" if joined else f"{self.remote}:{self.root}"

    # -- bundles ------------------------------------------------------------------

    @staticmethod
    def new_bundle_name() -> str:
        """One folder per bundle, named for when it was made (design.md 6.1)."""
        return time.strftime("%Y-%m-%dT%H-%M-%SZ", time.gmtime())

    def bundle_path(self, project: str, bundle: str) -> str:
        return self._path(project, bundle)

    def list_bundles(self, project: str) -> list[str]:
        result = self._run(["lsf", "--dirs-only", self._path(project)], check=False)
        if not result.ok:
            return []
        return sorted(line.strip("/") for line in result.stdout.splitlines() if line.strip())

    def delete_bundle(self, project: str, bundle: str) -> None:
        self._run(["purge", self.bundle_path(project, bundle)])

    # -- upload -------------------------------------------------------------------

    def upload(self, files: list[Path], project: str, bundle: str) -> list[DriveFile]:
        """Copy files into a fresh bundle folder and prove they arrived intact."""
        target = self.bundle_path(project, bundle)
        for path in files:
            self._run(["copyto", str(path), f"{target}/{path.name}"])

        self.verify(files, project, bundle)
        return self.list_bundle_files(project, bundle)

    def verify(self, files: list[Path], project: str, bundle: str) -> None:
        """`rclone check --checksum`: a hash comparison, not a guess. Mismatch is exit 13."""
        local_dir = files[0].parent
        include = [arg for path in files for arg in ("--include", path.name)]
        result = self._run(
            ["check", str(local_dir), self.bundle_path(project, bundle), "--checksum", *include],
            check=False,
        )
        if not result.ok:
            raise ToolError(
                FIDELITY,
                "uploaded files do not match the local originals",
                hint="do not proceed; inspect the files on Drive and re-run",
                detail={"rclone": result.stderr.strip()[-600:]},
            )

    def list_bundle_files(self, project: str, bundle: str) -> list[DriveFile]:
        result = self._run(["lsjson", self.bundle_path(project, bundle)])
        try:
            entries = json.loads(result.stdout or "[]")
        except ValueError as error:
            raise ToolError(
                DRIVE_AUTH,
                f"could not read the Drive listing: {error}",
                hint="re-run; if it persists, check connectivity",
            ) from error
        return [
            DriveFile(
                id=entry.get("ID", ""),
                name=entry.get("Name", ""),
                size=int(entry.get("Size", 0) or 0),
                mime=entry.get("MimeType", ""),
            )
            for entry in entries
            if not entry.get("IsDir")
        ]

    def delete_path(self, *parts: str) -> None:
        self._run(["purge", self._path(*parts)], check=False)
