"""Entry point and help system (design.md 5.1, 5.5).

The parser is built from `common.spec`, and so are `--ai` and `--help --json`, so help
cannot drift from behaviour. Usage errors print what was wrong, the corrective action, a
working example, and then the full command help -- never a bare "invalid argument".
"""

from __future__ import annotations

import argparse
import json
import sys

from .common import exits, spec, topics
from .common.envelope import Envelope
from .common.exits import ToolError

PROGRAM = "nlmt"


class _HelpfulParser(argparse.ArgumentParser):
    """Argparse that teaches instead of scolding."""

    def __init__(self, *args, command: spec.Command | None = None, **kwargs) -> None:
        self.command = command
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> None:  # type: ignore[override]
        out = sys.stderr
        out.write(f"{PROGRAM}: {message}\n\n")
        hint = exits.default_hint(exits.USAGE)
        if self.command and self.command.examples:
            example, does = self.command.examples[0]
            out.write("A working example:\n")
            out.write(f"    {example}\n")
            out.write(f"    ({does})\n\n")
            hint = f"try the example above, or run '{PROGRAM} {self.command.name} --help'"
        out.write(f"-> {hint}\n\n")
        out.write(self.format_help())
        raise SystemExit(exits.USAGE)


def _add_arg(parser: argparse.ArgumentParser, arg: spec.Arg) -> None:
    kwargs: dict[str, object] = {"help": arg.help}
    if arg.action:
        kwargs["action"] = arg.action
    else:
        kwargs["metavar"] = arg.metavar or arg.name.upper()
        if arg.repeatable:
            kwargs["action"] = "append"
            kwargs["default"] = []
        else:
            kwargs["default"] = arg.default
        if arg.type == "integer":
            kwargs["type"] = int
        if arg.choices:
            kwargs["choices"] = list(arg.choices)
    if arg.required:
        kwargs["required"] = True
    parser.add_argument(*arg.flags, **kwargs)  # type: ignore[arg-type]


def _epilog(command: spec.Command) -> str:
    lines = ["examples:"]
    for example, does in command.examples:
        lines.append(f"  {example}")
        lines.append(f"      {does}")
    if command.planned_at:
        lines.append("")
        lines.append(
            f"note: not implemented yet; scheduled for milestone {command.planned_at}."
        )
    lines.append("")
    lines.append(f"see also: {PROGRAM} help  (concepts), {PROGRAM} --ai  (full reference)")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = _HelpfulParser(
        prog=PROGRAM,
        description=(
            "Load local .txt bundles into NotebookLM via Google Drive, and ask long "
            "questions about them."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            f"concepts:  {PROGRAM} help <topic>   ({', '.join(topics.names())})\n"
            f"reference: {PROGRAM} --ai           (everything, in one shot, for an agent)\n"
            f"schema:    {PROGRAM} --help --json  (the interface as structured data)"
        ),
    )
    parser.add_argument(
        "--ai",
        action="store_true",
        help="print the complete agent-oriented reference and exit",
    )
    parser.add_argument(
        "--version", action="store_true", help="print the version and exit"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="with --help, dump the interface as JSON schema",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    for command in spec.COMMANDS:
        summary = command.summary + (
            f"  [planned: {command.planned_at}]" if command.planned_at else ""
        )
        sub = subparsers.add_parser(
            command.name,
            help=summary,
            description=command.description,
            epilog=_epilog(command),
            formatter_class=argparse.RawDescriptionHelpFormatter,
            command=command,
        )
        for arg in command.args:
            _add_arg(sub, arg)

    help_cmd = subparsers.add_parser(
        "help",
        help="explain a concept: " + ", ".join(topics.names()),
        description="Concept help for the things no single argument owns.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    help_cmd.add_argument(
        "topic", nargs="?", metavar="TOPIC", help="one of: " + ", ".join(topics.names())
    )
    return parser


# -- generated references ---------------------------------------------------------


def ai_reference() -> str:
    """The whole interface in one shot, written for a model rather than a reader."""
    out: list[str] = []
    out.append("=" * 78)
    out.append(f"{PROGRAM} - complete reference")
    out.append("=" * 78)
    out.append("")
    out.append(
        "These tools load local .txt bundles into NotebookLM through Google Drive, and\n"
        "ask long questions about them. Every command accepts --json, which prints one\n"
        "envelope on stdout and nothing else; logs go to stderr. Exit codes are the\n"
        "coarse signal and every error carries a hint naming the corrective action."
    )
    out.append("")

    for name in ("workflow", "masks", "asks", "readiness", "output", "secrets"):
        out.append("-" * 78)
        out.append(topics.get(name) or "")
    out.append("-" * 78)
    out.append(topics.exit_codes_topic())

    out.append("=" * 78)
    out.append("COMMANDS")
    out.append("=" * 78)
    for command in spec.COMMANDS:
        out.append("")
        status = f"   [NOT IMPLEMENTED YET - milestone {command.planned_at}]" if command.planned_at else ""
        out.append(f"{PROGRAM} {command.name}{status}")
        out.append("-" * len(f"{PROGRAM} {command.name}"))
        out.append(command.summary)
        out.append("")
        out.append(command.description)
        out.append("")
        out.append("arguments:")
        for arg in command.args:
            flags = ", ".join(arg.flags)
            bits = []
            if arg.required:
                bits.append("required")
            if arg.repeatable:
                bits.append("repeatable")
            if arg.default not in (None, [], False):
                bits.append(f"default {arg.default!r}")
            if arg.choices:
                bits.append("one of " + "|".join(arg.choices))
            suffix = f"  [{'; '.join(bits)}]" if bits else ""
            out.append(f"  {flags:<22}{arg.help}{suffix}")
        out.append("")
        out.append("examples:")
        for example, does in command.examples:
            out.append(f"  {example}")
            out.append(f"      {does}")
    out.append("")
    return "\n".join(out) + "\n"


def schema() -> dict:
    return {
        "program": PROGRAM,
        "commands": [c.to_dict() for c in spec.COMMANDS],
        "exit_codes": [
            {"code": code, "class": name, "meaning": meaning, "action": action}
            for code, (name, meaning, action) in sorted(exits.TABLE.items())
        ],
        "topics": topics.names(),
    }


# -- dispatch ---------------------------------------------------------------------


def _run_gen_fixtures(args: argparse.Namespace) -> Envelope:
    from pathlib import Path

    from .fixtures.generate import generate_bundle

    envelope = Envelope(action="gen-fixtures")
    manifest = generate_bundle(
        Path(args.out),
        files=args.files,
        batch=args.batch,
        target_bytes=args.size,
        seed=args.seed,
        prefix=args.prefix,
    )
    total = sum(entry["bytes"] for entry in manifest["files"])
    envelope.set(
        out=str(Path(args.out).resolve()),
        manifest=str((Path(args.out) / "manifest.json").resolve()),
        smoke_question=manifest["smoke"]["question"],
        smoke_expect=manifest["smoke"]["expect"],
    )
    envelope.count("files", len(manifest["files"]))
    envelope.count("facts", len(manifest["facts"]))
    envelope.count("bytes", total)
    return envelope


def _default_cache_dir() -> "Path":
    from pathlib import Path

    return Path.home() / ".nlmtools"


def _run_load(args: argparse.Namespace) -> Envelope:
    from pathlib import Path

    from .loader.drive import Drive
    from .loader.load import run_load

    cache_dir = Path(args.cache_dir) if args.cache_dir else _default_cache_dir()
    return run_load(
        notebook_title=args.notebook,
        local_folder=Path(args.local_folder),
        masks=args.mask,
        project=args.project,
        lock_dir=cache_dir / "locks",
        drive=Drive(remote=args.drive_remote, root=args.drive_root),
        drive_only=args.drive_only,
        dry_run=args.dry_run,
        verify_ingest=args.verify_ingest,
        ready_timeout=args.ready_timeout,
        poll_interval=args.poll_interval,
    )


def _run_status(args: argparse.Namespace) -> Envelope:
    from .common import naming
    from .common.nlm import NotebookLM

    envelope = Envelope(action="status")
    nlm = NotebookLM()
    notebook_id = nlm.find_notebook(args.notebook)
    envelope.set(notebook=args.notebook, notebook_id=notebook_id, exists=bool(notebook_id))
    if not notebook_id:
        envelope.count("sources", 0)
        return envelope

    sources = nlm.list_sources(notebook_id)
    masks = list(args.mask or [])
    matched = [
        s for s in sources
        if not naming.is_ask_source(s.title)
        and (not masks or any(s.title.startswith(m) for m in masks))
    ]
    envelope.count("sources", len(sources))
    envelope.count("matched", len(matched))
    envelope.count("ask_sources", sum(1 for s in sources if naming.is_ask_source(s.title)))
    envelope.count("ready", sum(1 for s in sources if s.ready))
    envelope.count("failed", sum(1 for s in sources if s.failed))
    envelope.set(
        all_ready=all(s.ready for s in sources) if sources else True,
        detail=[{"title": s.title, "status": s.status_name} for s in sources],
    )
    return envelope


def _run_delete(args: argparse.Namespace) -> Envelope:
    from .common.nlm import NotebookLM

    envelope = Envelope(action="delete")
    masks = [m for m in (args.mask or []) if m]
    if not masks:
        raise ToolError(
            exits.USAGE,
            "at least one --mask is required",
            hint="pass --mask Q7- to delete one ask, or a corpus prefix; "
                 "an empty mask never means 'delete everything'",
        )

    nlm = NotebookLM()
    notebook_id = nlm.find_notebook(args.notebook)
    envelope.set(notebook=args.notebook, masks=masks)
    if not notebook_id:
        raise ToolError(
            exits.MISMATCH,
            f"no notebook titled {args.notebook!r}",
            hint="check the title, or run 'nlmt status' to list what is there",
        )

    doomed = [
        s for s in nlm.list_sources(notebook_id)
        if any(s.title.startswith(m) for m in masks)
    ]
    envelope.set(targets=[s.title for s in doomed])
    if args.dry_run:
        envelope.set(dry_run=True)
        envelope.count("would_delete", len(doomed))
        return envelope

    for source in doomed:
        nlm.delete_source(source.id)
    envelope.count("deleted", len(doomed))
    return envelope


def _not_implemented(command: spec.Command) -> ToolError:
    return ToolError(
        exits.INTERNAL,
        f"'{PROGRAM} {command.name}' is not implemented yet",
        hint=(
            f"scheduled for milestone {command.planned_at}; "
            f"see docs/design.md, and '{PROGRAM} {command.name} --help' for the "
            "interface it will have"
        ),
        detail={"milestone": command.planned_at},
    )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()

    # --help --json is schema introspection, handled before argparse consumes --help.
    if "--json" in argv and ("--help" in argv or "-h" in argv):
        json.dump(schema(), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return exits.OK

    args = parser.parse_args(argv)

    level = getattr(args, "log_level", None)
    if level:
        import logging

        logging.basicConfig(
            level=getattr(logging, level.upper(), logging.INFO),
            format="%(message)s",
            stream=sys.stderr,  # never stdout: that is the envelope's channel
        )
        # The HTTP libraries log every request at INFO, which buries our own progress
        # lines. Keep them for --log-level debug, where they are what you want.
        if level != "debug":
            for noisy in ("httpx", "httpcore", "urllib3", "googleapiclient"):
                logging.getLogger(noisy).setLevel(logging.WARNING)

    if args.ai:
        sys.stdout.write(ai_reference())
        return exits.OK
    if args.version:
        from importlib.metadata import PackageNotFoundError, version

        try:
            sys.stdout.write(f"{PROGRAM} {version('nlmtools')}\n")
        except PackageNotFoundError:  # running from a source tree
            sys.stdout.write(f"{PROGRAM} (source tree)\n")
        return exits.OK

    if not args.command:
        parser.print_help()
        return exits.OK

    if args.command == "help":
        if not args.topic:
            sys.stdout.write("Concept help. Pick a topic:\n\n")
            for name in topics.names():
                sys.stdout.write(f"    {PROGRAM} help {name}\n")
            sys.stdout.write(f"\nOr {PROGRAM} --ai for the complete reference.\n")
            return exits.OK
        text = topics.get(args.topic)
        if text is None:
            sys.stderr.write(f"{PROGRAM}: no such help topic: {args.topic!r}\n\n")
            sys.stderr.write("Available topics: " + ", ".join(topics.names()) + "\n")
            sys.stderr.write(f"\n-> run '{PROGRAM} help' to list them\n")
            return exits.USAGE
        sys.stdout.write(text)
        return exits.OK

    command = spec.command(args.command)
    assert command is not None
    envelope = Envelope(action=command.name)
    as_json = bool(getattr(args, "json", False))

    try:
        handlers = {
            "gen-fixtures": _run_gen_fixtures,
            "load": _run_load,
            "status": _run_status,
            "delete": _run_delete,
        }
        handler = handlers.get(command.name)
        if handler is None:
            raise _not_implemented(command)
        envelope = handler(args)
    except ToolError as error:
        envelope.fail(error)
    except KeyboardInterrupt:
        envelope.fail(
            ToolError(exits.INTERNAL, "interrupted", hint="re-run the command; a rerun is the recovery path")
        )
    except Exception as error:  # noqa: BLE001 - the envelope is the contract
        envelope.fail(
            ToolError(
                exits.INTERNAL,
                f"{type(error).__name__}: {error}",
                detail={"type": type(error).__name__},
            )
        )

    return envelope.emit(as_json)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
