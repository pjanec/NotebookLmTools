"""The single definition of the command-line interface.

Design.md 5.5 requires that help never lies: argument help is generated from the same
definitions the parser uses, so the two cannot drift. This module is those definitions.
`cli.py` builds argparse from it, `--ai` renders it as an agent reference, and
`--help --json` dumps it as schema. Change the interface here and all three follow.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Arg:
    flags: tuple[str, ...]
    help: str
    metavar: str | None = None
    default: object = None
    action: str | None = None
    repeatable: bool = False
    required: bool = False
    type: str = "string"
    choices: tuple[str, ...] | None = None

    @property
    def name(self) -> str:
        return self.flags[0].lstrip("-").replace("-", "_")

    def to_dict(self) -> dict:
        out: dict[str, object] = {
            "flags": list(self.flags),
            "help": self.help,
            "type": "flag" if self.action else self.type,
            "required": self.required,
            "repeatable": self.repeatable,
        }
        if self.default is not None:
            out["default"] = self.default
        if self.choices:
            out["choices"] = list(self.choices)
        return out


@dataclass
class Command:
    name: str
    summary: str
    description: str
    args: list[Arg] = field(default_factory=list)
    examples: list[tuple[str, str]] = field(default_factory=list)
    planned_at: str | None = None  # milestone that implements it, if not yet built

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "summary": self.summary,
            "description": self.description,
            "implemented": self.planned_at is None,
            "planned_at": self.planned_at,
            "arguments": [a.to_dict() for a in self.args],
            "examples": [{"command": c, "does": d} for c, d in self.examples],
        }


# -- arguments shared by several commands ----------------------------------------

COMMON = [
    Arg(("--json",), "emit the machine-readable envelope on stdout and nothing else",
        action="store_true"),
    Arg(("--log-level",), "how much progress detail goes to stderr; stdout is unaffected",
        metavar="LEVEL", default="info",
        choices=("debug", "info", "warning", "error")),
    Arg(("--config",), "optional TOML config file; lowest priority, overridden by every "
        "command-line argument", metavar="PATH"),
]

NOTEBOOK = Arg(("--notebook",), "target notebook, addressed by its exact title; created "
               "if it does not exist", metavar="TITLE", required=True)
PROJECT = Arg(("--project",), "names the Drive subtree and the answers/cache namespace",
              metavar="NAME", default="default")
MASK = Arg(("--mask",), "starts-with prefix selecting which files and sources this run "
           "may touch; repeatable; omit to select every .txt",
           metavar="PREFIX", repeatable=True)
CACHE_DIR = Arg(("--cache-dir",), "where the deletable cache lives; safe to delete at any "
                "time", metavar="PATH")
DRIVE_REMOTE = Arg(("--drive-remote",), "name of the rclone remote to upload through; "
                   "created by setup.ps1", metavar="NAME", default="nlmtools")
DRIVE_ROOT = Arg(("--drive-root",), "folder in your Google Drive that this tool creates "
                 "and owns; bundles are written under it", metavar="PATH",
                 default="/NotebookLmTools")
DRY_RUN = Arg(("--dry-run",), "report what would happen; change nothing",
              action="store_true")

READINESS = [
    Arg(("--ready-timeout",), "seconds to wait for indexing before giving up (exit 16)",
        metavar="S", default=1200, type="integer"),
    Arg(("--poll-interval",), "seconds between readiness polls", metavar="S", default=15,
        type="integer"),
    Arg(("--smoke-question",), "fallback readiness probe: a question answerable only from "
        "inside a function body", metavar="Q"),
    Arg(("--smoke-expect",), "substring the smoke answer must contain", metavar="S"),
]


COMMANDS: list[Command] = [
    Command(
        name="load",
        summary="upload a local .txt bundle to Drive and load it into a notebook",
        description=(
            "Selects local .txt files by mask, uploads them to a fresh Drive bundle "
            "folder as plain text, verifies every upload by checksum, then replaces the "
            "notebook's mask-matching sources with the new ones and waits for indexing.\n"
            "\n"
            "Sources outside the mask are never touched, and neither are ask sources "
            "(Q<n>-). Nothing is deleted from the notebook until the new content is on "
            "Drive and proven intact. A failed run is fixed by running it again."
        ),
        args=[NOTEBOOK, Arg(("--local-folder",), "folder holding the .txt bundle",
                            metavar="PATH", required=True),
              MASK, PROJECT, DRIVE_REMOTE, DRIVE_ROOT, CACHE_DIR,
              Arg(("--drive-only",), "stop after upload and checksum verification; do "
                  "not touch the notebook", action="store_true"),
              Arg(("--verify-ingest",), "how much of the corpus to read back from "
                  "NotebookLM and compare against the local files: one file, every file, "
                  "or nothing. Body-stripping is systemic, so one file usually proves it",
                  metavar="MODE", default="sample",
                  choices=("sample", "all", "none")),
              DRY_RUN, *READINESS, *COMMON],
        examples=[
            ('nlmt load --notebook "Engine review" --local-folder D:\\exports\\engine',
             "load every .txt in the folder, replacing what was there before"),
            ('nlmt load --notebook "Engine review" --local-folder D:\\exports\\engine '
             '--mask Engine_ --mask Core_',
             "load only files starting with Engine_ or Core_, leaving other sources alone"),
            ('nlmt load --notebook "Engine review" --local-folder D:\\exports\\engine '
             '--drive-only',
             "upload and verify against Drive without touching the notebook"),
        ],
        planned_at="M3",
    ),
    Command(
        name="status",
        summary="report a notebook's sources and whether they have finished indexing",
        description=(
            "Resolves the notebook by title, enumerates its sources, and reports which "
            "ones match the mask, which are ask sources, and whether indexing is "
            "complete. Read-only."
        ),
        args=[NOTEBOOK, MASK, CACHE_DIR, *COMMON],
        examples=[
            ('nlmt status --notebook "Engine review"',
             "list every source and its readiness"),
            ('nlmt status --notebook "Engine review" --json',
             "the same, as a parseable envelope"),
        ],
        planned_at="M4",
    ),
    Command(
        name="ask",
        summary="ask a long question, optionally with attached data files",
        description=(
            "Uploads the question as a temporary source, uploads any --attach"
            "ed data files to Drive as <name>.txt and adds them as sources, waits for "
            "them to index, queries the notebook with a generated prompt that maps each "
            "attachment back to the filename the question refers to, saves the answer, "
            "and deletes everything it created.\n"
            "\n"
            "All of an ask's sources share one ordinal prefix, so --keep plus "
            "'nlmt delete --mask Q7-' is the manual cleanup path."
        ),
        args=[NOTEBOOK,
              Arg(("--question",), "the question body", metavar="TEXT"),
              Arg(("--question-file",), "read the question body from a file; stdin is "
                  "used if neither --question nor --question-file is given",
                  metavar="PATH"),
              Arg(("--attach",), "a text-like data file the question refers to (JSON, "
                  "CSV, log, plain text); repeatable", metavar="FILE", repeatable=True),
              Arg(("--name",), "slug for the question source title and transcript "
                  "filename", metavar="SLUG"),
              Arg(("--keep",), "do not delete the question and attachment sources",
                  action="store_true"),
              PROJECT, DRIVE_REMOTE, DRIVE_ROOT, CACHE_DIR, *READINESS, *COMMON],
        examples=[
            ('nlmt ask --notebook "Engine review" --question-file question.md',
             "ask a long question held in a file"),
            ('nlmt ask --notebook "Engine review" --question-file question.md '
             '--attach config.json --attach metrics.csv',
             "ask about two attached data files, referenced by name from the question"),
            ('type question.md | nlmt ask --notebook "Engine review" --json',
             "pipe the question in and parse the envelope"),
        ],
        planned_at="M7",
    ),
    Command(
        name="delete",
        summary="delete a notebook's sources matching a mask",
        description=(
            "The same delete-by-mask primitive load uses, exposed directly. Its main use "
            "is removing one ask that was kept with --keep: every source of ask 7 starts "
            "with Q7-.\n"
            "\n"
            "At least one --mask is required; an empty mask is a usage error, never "
            "'delete everything'. Corpus sources are always restorable by rerunning load."
        ),
        args=[NOTEBOOK,
              Arg(("--mask",), "starts-with prefix; repeatable; at least one required",
                  metavar="PREFIX", repeatable=True, required=True),
              DRY_RUN, CACHE_DIR, *COMMON],
        examples=[
            ('nlmt delete --notebook "Engine review" --mask Q7-',
             "delete ask 7: its question source and every attachment"),
            ('nlmt delete --notebook "Engine review" --mask Engine_ --dry-run',
             "list what would be deleted without deleting it"),
        ],
        planned_at="M4",
    ),
    Command(
        name="sweep",
        summary="delete every ask source and any leftover ask folder on Drive",
        description=(
            "The blunt cleanup: removes every source titled Q<n>-* from the notebook and "
            "every stray _ask folder from Drive. Use it when asks were abandoned during "
            "development; use 'delete --mask Q<n>-' to remove just one."
        ),
        args=[NOTEBOOK, PROJECT, DRIVE_REMOTE, DRIVE_ROOT, DRY_RUN, CACHE_DIR, *COMMON],
        examples=[
            ('nlmt sweep --notebook "Engine review"',
             "remove every stranded question and attachment"),
        ],
        planned_at="M7",
    ),
    Command(
        name="prune",
        summary="list and delete old Drive bundle folders",
        description=(
            "Every load creates a new dated bundle folder on Drive and old ones are kept "
            "deliberately. This is the maintenance command that removes them. It never "
            "touches a notebook."
        ),
        args=[PROJECT, DRIVE_REMOTE, DRIVE_ROOT,
              Arg(("--keep-last",), "how many recent bundles to keep", metavar="N",
                  default=3, type="integer"),
              DRY_RUN, *COMMON],
        examples=[
            ("nlmt prune --project engine --dry-run",
             "show which bundle folders would be removed"),
            ("nlmt prune --project engine --keep-last 5",
             "delete all but the five most recent bundles"),
        ],
        planned_at="M3",
    ),
    Command(
        name="doctor",
        summary="check both logins and confirm they are the same Google account",
        description=(
            "Verifies that rclone can reach Drive and that NotebookLM cookies are valid, "
            "then prints both Google identities and confirms they match. A mismatch is "
            "the cause of permission errors that otherwise surface much later and make "
            "no sense."
        ),
        args=[DRIVE_REMOTE, *COMMON],
        examples=[("nlmt doctor", "check the environment before a long run")],
        planned_at="M0",
    ),
    Command(
        name="gen-fixtures",
        summary="generate a synthetic test bundle with a ground-truth manifest",
        description=(
            "Writes .txt files shaped like bundled source code, with facts buried inside "
            "method bodies, plus a manifest.json turning each buried fact into a "
            "question/expected pair.\n"
            "\n"
            "The buried facts are the point: NotebookLM's local-file ingestion strips "
            "function bodies, so a fact that lives only inside a body detects that "
            "failure. Files alternate between BOM and non-BOM UTF-8, since both occur in "
            "the real data and both must survive byte-identically."
        ),
        args=[Arg(("--out",), "directory to write the bundle into", metavar="PATH",
                  required=True),
              Arg(("--files",), "how many .txt files to generate", metavar="N",
                  default=3, type="integer"),
              Arg(("--size",), "approximate bytes per file", metavar="BYTES",
                  default=8000, type="integer"),
              Arg(("--batch",), "batch number used in filenames", metavar="N",
                  default=17, type="integer"),
              Arg(("--prefix",), "filename prefix; defaults to the module name",
                  metavar="NAME"),
              Arg(("--seed",), "PRNG seed, for reproducible bundles", metavar="N",
                  default=20260905, type="integer"),
              *COMMON],
        examples=[
            ("nlmt gen-fixtures --out tests\\fixtures\\small",
             "three small files plus a manifest, for fast iteration"),
            ("nlmt gen-fixtures --out tests\\fixtures\\full --files 20 --size 1000000",
             "a full-scale bundle: 20 files of about 1 MB, as in M9"),
        ],
    ),
]


def command(name: str) -> Command | None:
    return next((c for c in COMMANDS if c.name == name), None)
