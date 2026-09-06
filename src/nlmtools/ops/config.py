"""What the agent is allowed to do, and for which projects.

This file is the whole authorisation model. A job names a **project** and, for `sync`, a
**ref**; everything else — which repository, which origin, which filters produce which
outputs, which notebook receives them — is fixed here by the operator and cannot be
influenced by a caller.

**Why the agent does not run `dump.bat`.** That script lives *in the repository*, so a
`sync` to an untrusted branch followed by a `refresh` would execute whatever that branch
contained. It is a backdoor by construction. The batch file only ever wrapped a few calls to
the same dump tool, so the agent makes those calls itself from this configuration, and
nothing that arrives with a branch is ever executed.

Filter files are still read from the repository, and that is a deliberate, bounded choice:
they are `.dumpignore`-style pattern lists, the agent supplies the scan root, and the dump
tool only walks that root — so a rewritten filter can widen the dump *within the repository*
but cannot reach outside it. The path is validated as a plain relative path all the same.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

#: Project names appear in job files, so keep them boring.
PROJECT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ConfigError(Exception):
    """The agent configuration is unusable. Refuse to start rather than half-work."""


@dataclass
class DumpEntry:
    """One invocation of the dump tool: a filter from the repo, and what it produces."""

    filter: str   # relative path inside the repository
    output: str   # output filename; the tool adds the ordinal and any split suffix

    def validate(self, project: str) -> None:
        for label, value in (("filter", self.filter), ("output", self.output)):
            if not value or value.strip() != value:
                raise ConfigError(f"{project}: dump {label} is empty or padded")
            if Path(value).is_absolute() or ".." in Path(value).parts:
                raise ConfigError(
                    f"{project}: dump {label} {value!r} must be a plain relative path "
                    "inside the repository"
                )


@dataclass
class ProjectConfig:
    name: str
    repo: Path
    notebook: str
    drive_project: str
    masks: list[str]
    dump: list[DumpEntry]
    origin: str | None = None       # if set, the repo's origin must match before syncing
    allowed_refs: list[str] = field(default_factory=list)  # empty means any ref on origin

    @property
    def bundle_folder(self) -> Path:
        """Where the dump tool writes: `.dumps` beneath the repository root."""
        return self.repo / ".dumps"

    def validate(self) -> None:
        if not PROJECT_NAME.match(self.name):
            raise ConfigError(f"project name {self.name!r} is not a plain identifier")
        if not self.repo.is_dir():
            raise ConfigError(f"{self.name}: repo {self.repo} does not exist")
        if not (self.repo / ".git").exists():
            raise ConfigError(f"{self.name}: repo {self.repo} is not a git clone")
        if not self.notebook.strip():
            raise ConfigError(f"{self.name}: notebook is required")
        if not self.masks:
            raise ConfigError(
                f"{self.name}: at least one mask is required — an empty mask set would let "
                "a load touch sources it did not upload"
            )
        if not self.dump:
            raise ConfigError(f"{self.name}: at least one dump entry is required")
        for entry in self.dump:
            entry.validate(self.name)

    def ref_allowed(self, ref: str) -> bool:
        return not self.allowed_refs or ref in self.allowed_refs


@dataclass
class AgentConfig:
    ops_repo: Path
    nlmt: Path
    python: Path
    dump_tool: Path
    projects: dict[str, ProjectConfig]
    poll_seconds: int = 20
    audit_log: Path | None = None

    @classmethod
    def from_file(cls, path: Path) -> "AgentConfig":
        path = Path(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except OSError as error:
            raise ConfigError(f"cannot read {path}: {error}") from error
        except ValueError as error:
            raise ConfigError(f"{path} is not valid JSON: {error}") from error

        try:
            projects = {}
            for name, raw in (data.get("projects") or {}).items():
                projects[name] = ProjectConfig(
                    name=name,
                    repo=Path(raw["repo"]),
                    notebook=raw["notebook"],
                    drive_project=raw.get("drive_project", name),
                    masks=list(raw["masks"]),
                    dump=[DumpEntry(filter=d["filter"], output=d["output"])
                          for d in raw["dump"]],
                    origin=raw.get("origin"),
                    allowed_refs=list(raw.get("allowed_refs") or []),
                )
            config = cls(
                ops_repo=Path(data["ops_repo"]),
                nlmt=Path(data["nlmt"]),
                python=Path(data.get("python") or "python"),
                dump_tool=Path(data["dump_tool"]),
                projects=projects,
                poll_seconds=int(data.get("poll_seconds", 20)),
                audit_log=Path(data["audit_log"]) if data.get("audit_log") else None,
            )
        except KeyError as error:
            raise ConfigError(f"{path} is missing required field {error}") from error

        config.validate()
        return config

    def validate(self) -> None:
        if not self.projects:
            raise ConfigError("no projects configured; the agent would accept no work")
        if not self.dump_tool.is_file():
            raise ConfigError(f"dump tool {self.dump_tool} does not exist")
        if not (self.ops_repo / ".git").is_dir():
            raise ConfigError(f"ops repo {self.ops_repo} is not a git clone")
        for project in self.projects.values():
            project.validate()

    def project(self, name: str | None) -> ProjectConfig:
        """Resolve the project a job asked for.

        With exactly one project configured, a job may omit the name. With several, it must
        choose — guessing would eventually load one project's sources into another's
        notebook.
        """
        if name is None:
            if len(self.projects) == 1:
                return next(iter(self.projects.values()))
            raise ConfigError(
                "this agent serves several projects, so a job must name one: "
                + ", ".join(sorted(self.projects))
            )
        if name not in self.projects:
            raise ConfigError(
                f"unknown project {name!r}; this agent serves: "
                + ", ".join(sorted(self.projects))
            )
        return self.projects[name]
