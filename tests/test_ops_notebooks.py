"""Choosing a notebook: per job, remembered per project, and constrained by config.

A project holds many notebooks — typically one per architectural area — so the notebook is
a job parameter. That makes it the one caller-supplied value that selects *which corpus is
read*, so what a project may reach has to be decided by the operator's config rather than
by the caller.
"""

from __future__ import annotations

import json

import pytest

from nlmtools.ops.config import AgentConfig, ConfigError, DumpEntry, ProjectConfig
from nlmtools.ops.protocol import Job, JobRejected, validate


def project(**kwargs) -> ProjectConfig:
    base = dict(
        name="simhost", repo=None, drive_project="simhost", masks=["HROT."],
        dump=[DumpEntry(filter="f.dumpfilter", output="o.txt")],
    )
    base.update(kwargs)
    return ProjectConfig(**base)


def config(tmp_path, projects, **kwargs) -> AgentConfig:
    return AgentConfig(
        ops_repo=tmp_path / "ops", nlmt=tmp_path / "nlmt.exe",
        python=tmp_path / "py.exe", dump_tool=tmp_path / "dump.py",
        projects={p.name: p for p in projects},
        state_file=tmp_path / "state.json", **kwargs,
    )


# -- resolution order ----------------------------------------------------------------


def test_an_explicit_notebook_wins(tmp_path):
    agent = config(tmp_path, [project(default_notebook="Default")])
    agent.remember_notebook("simhost", "Remembered")
    assert agent.resolve_notebook(agent.projects["simhost"], "Explicit") == "Explicit"


def test_the_last_used_notebook_is_reused_when_none_is_given(tmp_path):
    agent = config(tmp_path, [project(default_notebook="Default")])
    agent.remember_notebook("simhost", "Remembered")
    assert agent.resolve_notebook(agent.projects["simhost"], None) == "Remembered"


def test_the_configured_default_is_the_last_resort(tmp_path):
    agent = config(tmp_path, [project(default_notebook="Default")])
    assert agent.resolve_notebook(agent.projects["simhost"], None) == "Default"


def test_with_nothing_to_go_on_the_job_is_refused_with_a_way_out(tmp_path):
    agent = config(tmp_path, [project()])
    with pytest.raises(ConfigError) as caught:
        agent.resolve_notebook(agent.projects["simhost"], None)
    message = str(caught.value)
    assert "--notebook" in message and "default_notebook" in message


def test_each_project_remembers_its_own(tmp_path):
    agent = config(tmp_path, [project(name="alpha"), project(name="beta")])
    agent.remember_notebook("alpha", "Alpha area")
    agent.remember_notebook("beta", "Beta area")
    assert agent.resolve_notebook(agent.projects["alpha"], None) == "Alpha area"
    assert agent.resolve_notebook(agent.projects["beta"], None) == "Beta area"


def test_the_memory_is_only_a_convenience(tmp_path):
    """Deleting the state file must not change behaviour beyond needing a name again."""
    agent = config(tmp_path, [project()])
    agent.remember_notebook("simhost", "Remembered")
    agent.state_file.unlink()
    with pytest.raises(ConfigError):
        agent.resolve_notebook(agent.projects["simhost"], None)
    assert agent.resolve_notebook(agent.projects["simhost"], "Named") == "Named"


def test_corrupt_state_does_not_break_the_agent(tmp_path):
    agent = config(tmp_path, [project(default_notebook="Default")])
    agent.state_file.write_text("{not json", encoding="utf-8")
    assert agent.resolve_notebook(agent.projects["simhost"], None) == "Default"


# -- what a project may reach --------------------------------------------------------


def test_a_prefix_keeps_one_project_out_of_anothers_notebooks(tmp_path):
    agent = config(tmp_path, [project(notebook_prefix="SimHost ")])
    assert agent.resolve_notebook(agent.projects["simhost"], "SimHost damage model")
    with pytest.raises(ConfigError) as caught:
        agent.resolve_notebook(agent.projects["simhost"], "Other project secrets")
    assert "not allowed" in str(caught.value)
    assert "SimHost " in str(caught.value)


def test_an_explicit_allowlist_is_exact(tmp_path):
    agent = config(tmp_path, [project(allowed_notebooks=["A", "B"])])
    assert agent.resolve_notebook(agent.projects["simhost"], "B") == "B"
    with pytest.raises(ConfigError):
        agent.resolve_notebook(agent.projects["simhost"], "C")


def test_without_rules_any_notebook_is_accepted(tmp_path):
    agent = config(tmp_path, [project()])
    assert agent.resolve_notebook(agent.projects["simhost"], "Anything at all")


def test_a_remembered_notebook_is_still_checked_against_the_rules(tmp_path):
    """Rules may tighten after a notebook was used; the memory must not grandfather it."""
    agent = config(tmp_path, [project(notebook_prefix="SimHost ")])
    agent.remember_notebook("simhost", "Legacy notebook")
    with pytest.raises(ConfigError):
        agent.resolve_notebook(agent.projects["simhost"], None)


def test_a_default_outside_the_rules_is_a_config_error(tmp_path):
    bad = project(notebook_prefix="SimHost ", default_notebook="Something else",
                  repo=tmp_path)
    (tmp_path / ".git").mkdir(exist_ok=True)
    with pytest.raises(ConfigError) as caught:
        bad.validate()
    assert "default_notebook" in str(caught.value)


# -- the job field -------------------------------------------------------------------


def test_a_human_notebook_title_is_accepted():
    validate(Job(verb="ask", question="q", notebook="SimHost — damage model (v2)"))


@pytest.mark.parametrize("title", ["", "   ", "with\nnewline", "with\ttab", "x" * 201])
def test_malformed_notebook_titles_are_refused(title):
    with pytest.raises(JobRejected):
        validate(Job(verb="ask", question="q", notebook=title))


def test_the_notebook_survives_the_json_round_trip():
    original = Job(verb="status", notebook="SimHost — networking")
    assert Job.from_json(original.to_json()).notebook == "SimHost — networking"


def test_state_file_shape_is_stable(tmp_path):
    """Written by the agent, read by a human when something looks wrong."""
    agent = config(tmp_path, [project()])
    agent.remember_notebook("simhost", "Area one")
    state = json.loads(agent.state_file.read_text(encoding="utf-8"))
    assert state == {"last_notebook": {"simhost": "Area one"}}
