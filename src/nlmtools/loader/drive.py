"""Google Drive, via rclone (design.md 4.1, 6.1).

Files are stored as **plain `text/plain`, byte-for-byte identical to the local file, BOM
preserved**. Never converted to a native Google Doc: conversion reflows the content, loses
a BOM, caps near 1.02M characters, and -- measured -- fails to import at 1 MB, while the
unconverted file imports in seconds with its text exactly intact.

Verification is `rclone check --checksum`, so byte-identity is proven rather than assumed.
"""

from __future__ import annotations

import hashlib
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

    def upload_one(
        self, local: Path, project: str, bundle: str, remote_name: str
    ) -> DriveFile:
        """Upload a single file under a chosen remote name, and verify it by checksum.

        Used for attachments, which are stored as `<original-name>.txt` so NotebookLM
        accepts the type while the name the question refers to stays visible. Because the
        name differs from the local one, `rclone check` cannot pair them; the md5 is
        compared directly instead.
        """
        target = f"{self.bundle_path(project, bundle)}/{remote_name}"
        self._run(["copyto", str(local), target])

        listed = self._run(["lsjson", "--hash", self.bundle_path(project, bundle)])
        try:
            entries = json.loads(listed.stdout or "[]")
        except ValueError as error:
            raise ToolError(
                DRIVE_AUTH,
                f"could not read the Drive listing: {error}",
                hint="re-run; if it persists, check connectivity",
            ) from error

        match = next((e for e in entries if e.get("Name") == remote_name), None)
        if match is None:
            raise ToolError(
                FIDELITY,
                f"{remote_name!r} is not on Drive after upload",
                hint="re-run the command",
            )

        remote_md5 = (match.get("Hashes") or {}).get("md5")
        local_md5 = hashlib.md5(local.read_bytes()).hexdigest()  # noqa: S324
        if remote_md5 and remote_md5 != local_md5:
            raise ToolError(
                FIDELITY,
                f"{remote_name!r} does not match the local file after upload",
                hint="do not proceed; delete it from Drive and re-run",
                detail={"local_md5": local_md5, "remote_md5": remote_md5},
            )

        return DriveFile(
            id=match.get("ID", ""),
            name=match.get("Name", ""),
            size=int(match.get("Size", 0) or 0),
            mime=match.get("MimeType", ""),
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

    def remove_dir_if_empty(self, *parts: str) -> None:
        """Tidy away a container folder once its last child is gone.

        `rmdir` refuses a non-empty directory, so this is safe to call unconditionally --
        it removes the leftover `_ask` folder after the final ask is cleaned up, and does
        nothing while another ask is still in flight.
        """
        self._run(["rmdir", self._path(*parts)], check=False)
