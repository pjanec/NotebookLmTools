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
        if command.name == "gen-fixtures":
            envelope = _run_gen_fixtures(args)
        else:
            raise _not_implemented(command)
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
