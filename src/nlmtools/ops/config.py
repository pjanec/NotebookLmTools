"""What the agent is allowed to do, and for which projects.

This file is the whole authorisation model. A job names a **project** and, optionally, a
**ref**; everything else — which repository, which origin, which filters produce which
outputs, which notebook receives them — is fixed here by the operator and cannot be
influenced by a caller.

**Why the agent does not run `dump.bat`.** That script lives *in the repository*, and a
refresh checks a branch out before it dumps, so running it would execute whatever that
branch contained. It is a backdoor by construction. The batch file only ever wrapped a few calls to
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
import sys
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
    """One repository, its dump recipe, and which notebooks it may feed.

    A project has **many** notebooks -- typically one per architectural area -- so the
    notebook is chosen per job rather than fixed here. What is fixed here is which names
    are acceptable, so a job cannot reach a notebook belonging to another project.
    """

    name: str
    repo: Path
    drive_project: str
    masks: list[str]
    dump: list[DumpEntry]
    origin: str | None = None       # if set, the repo's origin must match before syncing
    allowed_refs: list[str] = field(default_factory=list)  # empty means any ref on origin

    #: Synced when a refresh names no ref. Unset means origin's own default branch,
    #: which is what a project wants unless its work happens somewhere else.
    default_ref: str | None = None

    #: Notebooks this project may touch. A prefix is the practical guard: name notebooks
    #: for a project consistently and new ones need no config change, while a leaked ops
    #: token still cannot query another project's notebook. Leave both unset to allow any
    #: name -- convenient, and fine when the account holds only one project's notebooks.
    notebook_prefix: str | None = None
    allowed_notebooks: list[str] = field(default_factory=list)

    #: Used when a job names no notebook and none has been used yet.
    default_notebook: str | None = None

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
        if self.default_notebook is not None and not self.notebook_allowed(self.default_notebook):
            raise ConfigError(
                f"{self.name}: default_notebook {self.default_notebook!r} is not allowed "
                "by this project's own notebook rules"
            )
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

    def notebook_allowed(self, notebook: str) -> bool:
        if self.allowed_notebooks:
            return notebook in self.allowed_notebooks
        if self.notebook_prefix:
            return notebook.startswith(self.notebook_prefix)
        return True

    def notebook_rule(self) -> str:
        """How to explain a refusal, in the operator's own terms."""
        if self.allowed_notebooks:
            return "one of: " + ", ".join(repr(n) for n in self.allowed_notebooks)
        if self.notebook_prefix:
            return f"a name starting with {self.notebook_prefix!r}"
        return "any name"


@dataclass
class AgentConfig:
    ops_repo: Path
    nlmt: Path
    python: Path
    dump_tool: Path
    projects: dict[str, ProjectConfig]
    poll_seconds: int = 20
    audit_log: Path | None = None

    #: Where `ask` transcripts are saved. **Never inside the ops repo**: the agent runs
    #: commands with that repo as the working directory, so a relative default would drop
    #: proprietary answers into a git working tree, one `git add -A` from being published.
    answers_dir: Path | None = None

    #: Remembers the notebook last used per project, so a job may omit it. Purely a
    #: convenience: delete the file and the only consequence is that the next job must
    #: name its notebook again.
    state_file: Path | None = None

    def last_notebook(self, project: str) -> str | None:
        if not self.state_file or not self.state_file.is_file():
            return None
        try:
            return (json.loads(self.state_file.read_text(encoding="utf-8"))
                    .get("last_notebook", {}).get(project))
        except (OSError, ValueError):
            return None

    def remember_notebook(self, project: str, notebook: str) -> None:
        if not self.state_file:
            return
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        state.setdefault("last_notebook", {})[project] = notebook
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(state, indent=2, sort_keys=True),
                                   encoding="utf-8")

    def resolve_notebook(self, project: ProjectConfig, asked: str | None) -> str:
        """Which notebook a job means.

        Explicit beats remembered beats configured default. A refusal names every way out,
        because the caller cannot see the agent's configuration.
        """
        chosen = asked or self.last_notebook(project.name) or project.default_notebook
        if not chosen:
            raise ConfigError(
                f"{project.name}: no notebook given and none remembered yet. Name one with "
                "--notebook, or set default_notebook in the agent config."
            )
        if not project.notebook_allowed(chosen):
            raise ConfigError(
                f"{project.name}: notebook {chosen!r} is not allowed; this project accepts "
                f"{project.notebook_rule()}"
            )
        return chosen

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
                    drive_project=raw.get("drive_project", name),
                    masks=list(raw["masks"]),
                    dump=[DumpEntry(filter=d["filter"], output=d["output"])
                          for d in raw["dump"]],
                    origin=raw.get("origin"),
                    allowed_refs=list(raw.get("allowed_refs") or []),
                    default_ref=raw.get("default_ref"),
                    notebook_prefix=raw.get("notebook_prefix"),
                    allowed_notebooks=list(raw.get("allowed_notebooks") or []),
                    default_notebook=raw.get("default_notebook"),
                )
            config = cls(
                ops_repo=Path(data["ops_repo"]),
                nlmt=Path(data["nlmt"]),
                # Default to the interpreter running the agent: it is this project's venv,
                # where the dump tool's dependencies are pinned. A bare "python" would pick
                # up whatever is on PATH, whose site-packages are nobody's responsibility.
                python=Path(data.get("python") or sys.executable),
                dump_tool=Path(data["dump_tool"]),
                projects=projects,
                poll_seconds=int(data.get("poll_seconds", 20)),
                audit_log=Path(data["audit_log"]) if data.get("audit_log") else None,
                state_file=Path(data["state_file"]) if data.get("state_file")
                else Path(path).with_name("ops-agent-state.json"),
                answers_dir=Path(data["answers_dir"]) if data.get("answers_dir")
                else Path(path).with_name("nlm-answers"),
            )
        except KeyError as error:
            raise ConfigError(f"{path} is missing required field {error}") from error

        config.validate()
        return config

    def validate(self) -> None:
        if self.answers_dir and self.ops_repo in self.answers_dir.parents:
            raise ConfigError(
                f"answers_dir {self.answers_dir} is inside the ops repo. Transcripts quote "
                "proprietary sources; keep them out of any git working tree."
            )
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
