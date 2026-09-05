"""The output contract (design.md 5.3).

Rules that must not be broken:

* With --json, the envelope is written to stdout and *nothing else is*. All logs and
  progress go to stderr. A caller pipes stdout straight into a JSON parser.
* A partial failure still emits a complete envelope with accurate counts, so a caller can
  see how far the run got.
* Field names never change meaning between versions -- add fields, never repurpose them.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field

from . import exits
from .exits import ToolError


@dataclass
class Envelope:
    action: str
    started: float = field(default_factory=time.monotonic)
    ok: bool = True
    counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    fields: dict[str, object] = field(default_factory=dict)
    error: ToolError | None = None

    def set(self, **kwargs: object) -> None:
        """Record top-level envelope fields (notebook, bundle, ready, ask, ...)."""
        self.fields.update(kwargs)

    def count(self, name: str, value: int) -> None:
        self.counts[name] = value

    def bump(self, name: str, delta: int = 1) -> None:
        self.counts[name] = self.counts.get(name, 0) + delta

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def fail(self, error: ToolError) -> None:
        self.ok = False
        self.error = error

    @property
    def exit_code(self) -> int:
        return exits.OK if self.ok else self.error.code  # type: ignore[union-attr]

    def to_dict(self) -> dict:
        out: dict[str, object] = {"ok": self.ok, "action": self.action}
        out.update(self.fields)
        out["counts"] = self.counts
        out["elapsed_s"] = round(time.monotonic() - self.started, 1)
        out["warnings"] = self.warnings
        out["error"] = self.error.as_dict() if self.error else None
        return out

    # -- rendering ---------------------------------------------------------------

    def emit(self, as_json: bool, stream=None) -> int:
        """Write the result and return the process exit code."""
        stream = stream or sys.stdout
        if as_json:
            json.dump(self.to_dict(), stream, indent=2)
            stream.write("\n")
        else:
            stream.write(self.render_text())
        stream.flush()
        return self.exit_code

    def render_text(self) -> str:
        lines: list[str] = []
        if self.ok:
            lines.append(f"OK  {self.action}")
        else:
            err = self.error
            assert err is not None
            lines.append(f"FAILED  {self.action}  ({err.error_class}, exit {err.code})")
            lines.append(f"  {err.message}")
            if err.hint:
                lines.append(f"  -> {err.hint}")
            for key, value in err.detail.items():
                lines.append(f"     {key}: {value}")
        for key, value in self.fields.items():
            lines.append(f"  {key}: {value}")
        if self.counts:
            pairs = ", ".join(f"{k}={v}" for k, v in self.counts.items())
            lines.append(f"  counts: {pairs}")
        for warning in self.warnings:
            lines.append(f"  warning: {warning}")
        lines.append(f"  elapsed: {round(time.monotonic() - self.started, 1)}s")
        return "\n".join(lines) + "\n"
