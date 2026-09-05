"""Per-notebook locks that survive a killed run (design.md 10.2).

A plain lockfile would break the recovery path: design principle 3.6 says a failed run is
fixed by running it again, and M8 kills a run mid-load and expects the rerun to converge.
So the lockfile records the owning PID and start time, and a lock whose PID is no longer
alive is stale and reclaimed automatically. A genuinely held lock is exit 17.

Locks are per notebook, so different notebooks can be loaded in parallel.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .exits import LOCKED, ToolError
from .naming import slugify


def _pid_alive(pid: int) -> bool:
    """Windows-first liveness check, with a POSIX fallback."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return False
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class NotebookLock:
    """Context manager. Raises ToolError(LOCKED) if another live run holds it."""

    def __init__(self, notebook: str, lock_dir: Path) -> None:
        self.notebook = notebook
        self.lock_dir = Path(lock_dir)
        self.path = self.lock_dir / f"{slugify(notebook)}.lock"
        self._held = False

    def __enter__(self) -> "NotebookLock":
        self.acquire()
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()

    def read_owner(self) -> dict | None:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def acquire(self) -> None:
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "pid": os.getpid(),
                "notebook": self.notebook,
                "started": time.time(),
                "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )
        for attempt in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                owner = self.read_owner()
                pid = int(owner.get("pid", -1)) if owner else -1
                if owner is None or not _pid_alive(pid):
                    # Stale: the owning run died. Reclaim it so a rerun can recover.
                    try:
                        self.path.unlink()
                    except OSError:
                        pass
                    if attempt == 0:
                        continue
                raise ToolError(
                    LOCKED,
                    f"notebook {self.notebook!r} is locked by a running process "
                    f"(pid {pid}, since {owner.get('started_utc', '?') if owner else '?'})",
                    detail={"lock_file": str(self.path), "pid": pid},
                )
            else:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                self._held = True
                return

    def release(self) -> None:
        if not self._held:
            return
        try:
            self.path.unlink()
        except OSError:
            pass
        self._held = False
