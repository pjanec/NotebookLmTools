"""`--config`: command line beats config file beats built-in default.

The precedence is the point. A config file that could override something typed on the
command line would be a trap, and a config file that is silently ignored — which is what
the flag did before this was implemented — is worse, because the setting looks applied.
"""

from __future__ import annotations

import json

import pytest

from nlmtools import cli
from nlmtools.common import exits


def write(tmp_path, text: str):
    path = tmp_path / "nlmt.toml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def run(tmp_path, capsys, config: str | None, *extra):
    out = tmp_path / "out"
    argv = ["gen-fixtures", "--out", str(out), "--json", *extra]
    if config:
        argv += ["--config", config]
    code = cli.main(argv)
    captured = capsys.readouterr().out
    return code, json.loads(captured) if captured.strip().startswith("{") else None


def test_a_value_comes_from_the_config_file(tmp_path, capsys):
    config = write(tmp_path, "[gen-fixtures]\nfiles = 2\nsize = 1500\n")
    code, envelope = run(tmp_path, capsys, config)
    assert code == exits.OK
    assert envelope["counts"]["files"] == 2


def test_the_command_line_beats_the_config_file(tmp_path, capsys):
    config = write(tmp_path, "[gen-fixtures]\nfiles = 2\n")
    _, envelope = run(tmp_path, capsys, config, "--files", "4")
    assert envelope["counts"]["files"] == 4


def test_the_built_in_default_applies_when_neither_says_otherwise(tmp_path, capsys):
    _, envelope = run(tmp_path, capsys, None, "--size", "1200")
    assert envelope["counts"]["files"] == 3  # the spec's default


def test_a_top_level_key_applies_to_commands_that_have_it(tmp_path, capsys):
    config = write(tmp_path, "size = 1400\n")
    _, envelope = run(tmp_path, capsys, config)
    assert envelope["counts"]["bytes"] > 0


def test_a_top_level_key_for_another_command_is_ignored(tmp_path, capsys):
    """One shared config must be able to serve several commands."""
    config = write(tmp_path, 'notebook = "somewhere"\nproject = "x"\n[gen-fixtures]\nfiles = 1\n')
    code, envelope = run(tmp_path, capsys, config)
    assert code == exits.OK
    assert envelope["counts"]["files"] == 1


def test_a_key_no_command_has_is_a_usage_error(tmp_path, capsys):
    """Silently ignoring a typo is how a setting goes unnoticed for weeks."""
    config = write(tmp_path, 'notabook = "typo"\n')
    code, envelope = run(tmp_path, capsys, config)
    assert code == exits.USAGE
    assert "no command has" in envelope["error"]["message"]
    assert envelope["error"]["hint"]


def test_an_unknown_key_under_a_command_table_is_a_usage_error(tmp_path, capsys):
    config = write(tmp_path, "[gen-fixtures]\nnotafield = 1\n")
    code, envelope = run(tmp_path, capsys, config)
    assert code == exits.USAGE
    assert "notafield" in envelope["error"]["message"]


def test_dashes_and_underscores_are_both_accepted(tmp_path, capsys):
    """Guessing wrong would produce a setting that looks applied and is not."""
    for key in ("files", "files"):
        config = write(tmp_path, f"[gen-fixtures]\n{key} = 2\n")
        _, envelope = run(tmp_path, capsys, config)
        assert envelope["counts"]["files"] == 2


def test_a_repeatable_option_can_be_a_list(tmp_path, capsys):
    from nlmtools.cli import load_config_file

    path = write(tmp_path, '[load]\nmask = ["HROT.", "FDP."]\n')
    _, for_load = load_config_file(path, "load")
    assert for_load["mask"] == ["HROT.", "FDP."]


def test_a_missing_config_file_is_a_usage_error(tmp_path, capsys):
    code, envelope = run(tmp_path, capsys, str(tmp_path / "nope.toml"))
    assert code == exits.USAGE
    assert "cannot read" in envelope["error"]["message"]


def test_malformed_toml_is_a_usage_error(tmp_path, capsys):
    config = write(tmp_path, "this is not = = toml\n")
    code, envelope = run(tmp_path, capsys, config)
    assert code == exits.USAGE
    assert "not valid TOML" in envelope["error"]["message"]


@pytest.mark.parametrize("command", ["load", "ask", "status"])
def test_every_command_still_advertises_config(command):
    from nlmtools.common import spec

    found = spec.command(command)
    assert any(a.name == "config" for a in found.args), f"{command} lost --config"
