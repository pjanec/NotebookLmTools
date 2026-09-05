"""The output contract and the self-teaching help (design.md 5.3, 5.5).

These are M8's agent-drivability checks, run as unit tests so they cannot rot.
"""

from __future__ import annotations

import json

import pytest

from nlmtools import cli
from nlmtools.common import exits, spec, topics


# -- output contract --------------------------------------------------------------


def test_json_output_is_the_only_thing_on_stdout(tmp_path, capsys):
    code = cli.main(["gen-fixtures", "--out", str(tmp_path), "--files", "2",
                     "--size", "2000", "--json"])
    captured = capsys.readouterr()
    assert code == exits.OK
    parsed = json.loads(captured.out)  # must parse with nothing else in the way
    assert parsed["ok"] is True
    assert parsed["action"] == "gen-fixtures"
    assert parsed["counts"]["files"] == 2


def test_failure_still_emits_a_complete_envelope(capsys):
    code = cli.main(["status", "--notebook", "X", "--json"])
    captured = capsys.readouterr()
    assert code != exits.OK
    parsed = json.loads(captured.out)
    assert parsed["ok"] is False
    for field in ("action", "counts", "elapsed_s", "warnings", "error"):
        assert field in parsed
    for field in ("code", "class", "message", "hint", "detail"):
        assert field in parsed["error"]


def test_every_error_carries_a_hint(capsys):
    """A caller who never read the docs must still learn what to do."""
    cli.main(["status", "--notebook", "X", "--json"])
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["error"]["hint"].strip()


def test_text_output_is_the_default(tmp_path, capsys):
    cli.main(["gen-fixtures", "--out", str(tmp_path), "--files", "1", "--size", "1000"])
    out = capsys.readouterr().out
    assert out.startswith("OK  gen-fixtures")
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


# -- self-teaching help -----------------------------------------------------------


def test_usage_error_exits_19_and_teaches(capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["load"])
    assert caught.value.code == exits.USAGE
    err = capsys.readouterr().err
    assert "required" in err            # what was wrong
    assert "A working example:" in err  # how to get it right
    assert "->" in err                  # the corrective action
    assert "usage: nlmt load" in err    # the full help, not a one-liner


def test_every_command_has_a_pasteable_example():
    for command in spec.COMMANDS:
        assert command.examples, f"{command.name} has no example"
        for example, does in command.examples:
            assert example.startswith("nlmt ") or "| nlmt " in example
            assert does


def test_no_argument_is_documented_by_restating_its_own_name():
    for command in spec.COMMANDS:
        for arg in command.args:
            bare = arg.flags[0].lstrip("-").replace("-", " ")
            assert arg.help.strip().lower() != bare, (command.name, arg.flags)
            assert len(arg.help) > len(bare) + 8, (command.name, arg.flags)


def test_ai_reference_covers_every_command_and_exit_code(capsys):
    assert cli.main(["--ai"]) == exits.OK
    text = capsys.readouterr().out
    for command in spec.COMMANDS:
        assert f"nlmt {command.name}" in text
        for arg in command.args:
            assert arg.flags[0] in text
    for code, (name, _, _) in exits.TABLE.items():
        assert name in text
        assert str(code) in text


def test_ai_reference_states_the_load_bearing_rule(capsys):
    """An agent reading only --ai must not improvise a local file upload."""
    cli.main(["--ai"])
    text = capsys.readouterr().out.lower()
    assert "never uploaded to notebooklm as a local file" in text
    assert "byte-identical" in text


def test_help_json_is_schema(capsys):
    assert cli.main(["--help", "--json"]) == exits.OK
    parsed = json.loads(capsys.readouterr().out)
    assert {c["name"] for c in parsed["commands"]} == {c.name for c in spec.COMMANDS}
    assert parsed["exit_codes"]
    assert parsed["topics"]


def test_schema_matches_the_parser():
    """Help is generated from the parser's own definitions, so it cannot drift."""
    parser = cli.build_parser()
    actions = {a.dest for a in parser._subparsers._group_actions}  # noqa: SLF001
    assert actions  # the subparser exists
    for command in spec.COMMANDS:
        sub = next(
            p for name, p in
            parser._subparsers._group_actions[0].choices.items()  # noqa: SLF001
            if name == command.name
        )
        flags = {opt for action in sub._actions for opt in action.option_strings}  # noqa: SLF001
        for arg in command.args:
            assert arg.flags[0] in flags, (command.name, arg.flags)


@pytest.mark.parametrize("topic", topics.names())
def test_every_topic_renders(topic, capsys):
    assert cli.main(["help", topic]) == exits.OK
    assert capsys.readouterr().out.strip()


def test_unknown_topic_lists_the_real_ones(capsys):
    assert cli.main(["help", "nonsense"]) == exits.USAGE
    err = capsys.readouterr().err
    assert "Available topics" in err
    for topic in topics.names():
        assert topic in err


def test_bare_help_lists_topics(capsys):
    assert cli.main(["help"]) == exits.OK
    out = capsys.readouterr().out
    for topic in topics.names():
        assert topic in out


def test_unimplemented_commands_say_which_milestone(capsys):
    """A command that is not built yet must name the milestone, not fail obscurely."""
    planned = [c for c in spec.COMMANDS if c.planned_at]
    if not planned:
        pytest.skip("every command is implemented")
    command = planned[0]
    required = [f for a in command.args if a.required for f in (a.flags[0], "X")]
    cli.main([command.name, *required, "--json"])
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["error"]["detail"]["milestone"] == command.planned_at
    assert "not implemented" in parsed["error"]["message"]
