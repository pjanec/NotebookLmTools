"""The job round trip, end to end, against a local bare repository.

No GitHub, no credentials, no network: two clones of a bare repo standing in for the cloud
VM and the Windows machine. What this proves is the part that is easy to get wrong and hard
to debug remotely -- that a job pushed by one side is picked up exactly once by the other,
that a result comes back, that a refused job still produces a result rather than silence,
and that the pause switch works.

The verbs themselves are stubbed. Their real behaviour is covered by the live check; what
matters here is the transport and the trust boundary.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from nlmtools.ops.agent import Agent, PAUSE_FILE
from nlmtools.ops.config import AgentConfig, DumpEntry, ProjectConfig
from nlmtools.ops.protocol import Job, Result


# Each test builds three git clones, so the file costs about a minute and a half. That is
# worth paying in CI and not during quick iteration: skip with -m 'not slow'.
pytestmark = pytest.mark.slow


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False,
    )


@pytest.fixture
def ops(tmp_path):
    """A bare 'remote' plus two clones: one for the cloud side, one for the agent."""
    remote = tmp_path / "ops.git"
    git("init", "--quiet", "--bare", "-b", "main", str(remote), cwd=tmp_path)

    seed = tmp_path / "seed"
    git("clone", "--quiet", str(remote), str(seed), cwd=tmp_path)
    (seed / "README.md").write_text("ops queue\n", encoding="utf-8")
    for name in ("jobs", "results"):
        (seed / name).mkdir()
        (seed / name / ".gitkeep").write_text("", encoding="utf-8")
    git("add", "-A", cwd=seed)
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "--quiet", "-m", "seed", cwd=seed)
    git("push", "--quiet", "origin", "HEAD:main", cwd=seed)

    cloud = tmp_path / "cloud"
    windows = tmp_path / "windows"
    git("clone", "--quiet", str(remote), str(cloud), cwd=tmp_path)
    git("clone", "--quiet", str(remote), str(windows), cwd=tmp_path)
    for clone in (cloud, windows):
        git("config", "user.email", "t@t", cwd=clone)
        git("config", "user.name", "t", cwd=clone)
    return {"remote": remote, "cloud": cloud, "windows": windows}


def make_project(name: str, root: Path) -> ProjectConfig:
    repo = root / name
    (repo / ".git").mkdir(parents=True, exist_ok=True)
    return ProjectConfig(
        name=name, repo=repo, drive_project=name, masks=["HROT.", "FDP."],
        dump=[DumpEntry(filter="dmp-a.dumpfilter", output="A.txt")],
        default_notebook=f"{name} notebook",
    )


@pytest.fixture
def agent(ops, tmp_path):
    """An agent serving two projects, so routing is exercised rather than assumed."""
    config = AgentConfig(
        ops_repo=ops["windows"],
        nlmt=tmp_path / "nlmt.exe",
        python=tmp_path / "python.exe",
        dump_tool=tmp_path / "dump.py",
        projects={name: make_project(name, tmp_path) for name in ("alpha", "beta")},
        audit_log=tmp_path / "audit.jsonl",
        state_file=tmp_path / "state.json",
    )
    built = Agent(config)
    # Stub the verbs: the transport is what is under test here.
    built.do_refresh = lambda job, project, notebook: Result(
        id=job.id, verb=job.verb, ok=True, detail={"notebook": notebook})
    built.do_status = lambda job, project, notebook: Result(
        id=job.id, verb=job.verb, ok=True, detail={"notebook": notebook})
    built.do_ask = lambda job, project, notebook: Result(
        id=job.id, verb=job.verb, ok=True,
        answer=f"answer to: {job.question}", detail={"notebook": notebook})
    built.sync = lambda job, project: None      # returns a Result only on failure
    return built


def submit(clone: Path, job: Job) -> str:
    """Push a job, mirroring what the real client does.

    Including the rebase: both sides push to the same branch, so a submit that follows a
    published result starts out behind. The client handles that; a test helper that did not
    failed silently and left a test looking for a result that was never requested.
    """
    git("fetch", "--quiet", "origin", cwd=clone)
    git("rebase", "--quiet", "origin/main", cwd=clone)

    jobs = clone / "jobs"
    jobs.mkdir(exist_ok=True)
    (jobs / f"{job.id}.json").write_text(job.to_json(), encoding="utf-8")
    git("add", "-A", cwd=clone)
    git("commit", "--quiet", "-m", f"job {job.id}", cwd=clone)
    pushed = git("push", "--quiet", "origin", "HEAD:main", cwd=clone)
    assert pushed.returncode == 0, f"job {job.id} was never pushed: {pushed.stderr}"
    return job.id


def result_for(clone: Path, job_id: str) -> dict | None:
    git("fetch", "--quiet", "origin", cwd=clone)
    git("reset", "--quiet", "--hard", "origin/main", cwd=clone)
    path = clone / "results" / f"{job_id}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


# -- the round trip -----------------------------------------------------------------


def test_a_job_pushed_by_the_cloud_comes_back_answered(ops, agent):
    job_id = submit(ops["cloud"], Job(verb="ask", question="why is the sky blue?", project="alpha"))

    assert agent.poll_once() == 1

    result = result_for(ops["cloud"], job_id)
    assert result is not None, "the cloud side never saw a result"
    assert result["ok"] is True
    assert result["answer"] == "answer to: why is the sky blue?"
    assert result["started"] and result["finished"]


def test_a_job_is_executed_exactly_once(ops, agent):
    submit(ops["cloud"], Job(verb="refresh", project="alpha"))
    assert agent.poll_once() == 1
    assert agent.poll_once() == 0, "a completed job was picked up again"


def test_several_jobs_are_all_handled(ops, agent):
    ids = [submit(ops["cloud"], Job(verb="status", project="alpha")) for _ in range(3)]
    assert agent.poll_once() == 3
    for job_id in ids:
        assert result_for(ops["cloud"], job_id)["ok"] is True


def test_a_refused_job_still_produces_a_result(ops, agent):
    """Silence is the worst outcome: the caller would wait for its full timeout."""
    job_id = submit(ops["cloud"], Job(verb="refresh", ref="--upload-pack=/bin/sh",
                                      project="alpha"))

    agent.poll_once()

    result = result_for(ops["cloud"], job_id)
    assert result["ok"] is False
    assert "refused" in result["error"]
    assert result["hint"]


def test_an_unknown_verb_is_refused_not_executed(ops, agent):
    job_id = submit(ops["cloud"], Job(verb="exec", project="alpha"))
    agent.poll_once()
    result = result_for(ops["cloud"], job_id)
    assert result["ok"] is False
    assert "unknown verb" in result["error"]


def test_the_pause_file_stops_the_agent(ops, agent):
    submit(ops["cloud"], Job(verb="refresh", project="alpha"))

    paused = ops["cloud"]
    (paused / PAUSE_FILE).write_text("maintenance\n", encoding="utf-8")
    git("add", "-A", cwd=paused)
    git("commit", "--quiet", "-m", "pause", cwd=paused)
    git("push", "--quiet", "origin", "HEAD:main", cwd=paused)

    assert agent.poll_once() == 0, "the agent ran a job while paused"


def test_the_job_id_comes_from_the_filename_not_the_payload(ops, agent):
    """A caller must not be able to name the result file something else."""
    job = Job(verb="refresh", project="alpha")
    real_id = job.id
    payload = json.loads(job.to_json())
    payload["id"] = "../../escape"
    (ops["cloud"] / "jobs" / f"{real_id}.json").write_text(
        json.dumps(payload), encoding="utf-8")
    git("add", "-A", cwd=ops["cloud"])
    git("commit", "--quiet", "-m", "job", cwd=ops["cloud"])
    git("push", "--quiet", "origin", "HEAD:main", cwd=ops["cloud"])

    agent.poll_once()

    assert result_for(ops["cloud"], real_id) is not None
    assert not (ops["windows"] / "results" / "..").joinpath("escape.json").exists()


def test_every_job_is_audited_including_refusals(ops, agent, tmp_path):
    submit(ops["cloud"], Job(verb="refresh", project="alpha"))
    submit(ops["cloud"], Job(verb="ask", project="alpha"))      # no question: refused
    agent.poll_once()

    lines = [json.loads(l) for l in
             (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    outcomes = {entry["verb"]: entry["outcome"] for entry in lines}
    assert outcomes["refresh"] == "ok"
    assert outcomes["ask"] == "refused"


def test_a_job_must_name_one_of_several_projects(ops, agent):
    """Guessing would eventually load one project's sources into another's notebook."""
    job_id = submit(ops["cloud"], Job(verb="status"))
    agent.poll_once()
    result = result_for(ops["cloud"], job_id)
    assert result["ok"] is False
    assert "must name one" in result["error"]
    assert "alpha" in result["error"] and "beta" in result["error"]


def test_an_unknown_project_is_refused(ops, agent):
    job_id = submit(ops["cloud"], Job(verb="status", project="gamma"))
    agent.poll_once()
    result = result_for(ops["cloud"], job_id)
    assert result["ok"] is False
    assert "unknown project" in result["error"]


def test_the_project_selects_the_notebook(ops, agent):
    alpha = submit(ops["cloud"], Job(verb="status", project="alpha"))
    beta = submit(ops["cloud"], Job(verb="status", project="beta"))
    agent.poll_once()
    assert result_for(ops["cloud"], alpha)["detail"]["notebook"] == "alpha notebook"
    assert result_for(ops["cloud"], beta)["detail"]["notebook"] == "beta notebook"


def test_a_notebook_named_in_a_job_is_used_and_then_remembered(ops, agent):
    first = submit(ops["cloud"], Job(verb="status", project="alpha",
                                     notebook="alpha area two"))
    agent.poll_once()
    assert result_for(ops["cloud"], first)["detail"]["notebook"] == "alpha area two"

    # The next job omits it and should land on the same notebook.
    second = submit(ops["cloud"], Job(verb="status", project="alpha"))
    agent.poll_once()
    assert result_for(ops["cloud"], second)["detail"]["notebook"] == "alpha area two"


def test_remembering_is_per_project(ops, agent):
    submit(ops["cloud"], Job(verb="status", project="alpha", notebook="alpha area two"))
    agent.poll_once()
    beta = submit(ops["cloud"], Job(verb="status", project="beta"))
    agent.poll_once()
    assert result_for(ops["cloud"], beta)["detail"]["notebook"] == "beta notebook"


def test_an_unpushed_result_survives_the_next_poll(ops, agent):
    """The exactly-once bug, reproduced.

    The agent used to `reset --hard origin/main` at the start of every pass. If a result
    had been committed but its push lost a race with an incoming job, the reset discarded
    it, the job looked pending again, and it ran a second time — an ask asked twice, a
    refresh reloaded twice. Observed in the first live run: five executions for three jobs.
    """
    job_id = submit(ops["cloud"], Job(verb="status", project="alpha"))
    agent.poll_once()

    # Commit a result locally and deliberately do not push it, while the other side
    # advances the branch — exactly the losing-race state.
    stranded = Job(verb="status", project="alpha")
    (agent.results_dir / f"{stranded.id}.json").write_text(
        Result(id=stranded.id, verb="status", ok=True).to_json(), encoding="utf-8")
    git("add", "-A", cwd=ops["windows"])
    git("commit", "--quiet", "-m", f"result {stranded.id}", cwd=ops["windows"])
    submit(ops["cloud"], Job(verb="refresh", project="alpha"))  # remote moves on

    agent.poll_once()

    assert (agent.results_dir / f"{stranded.id}.json").is_file(), \
        "the unpushed result was discarded, so its job would run again"
    assert result_for(ops["cloud"], job_id)["ok"] is True


def test_each_job_is_executed_once_even_when_the_queue_moves(ops, agent):
    """Several jobs arriving between passes must still each run exactly once."""
    executions = []
    original = agent.do_status
    agent.do_status = lambda job, project, notebook: (
        executions.append(job.id) or original(job, project, notebook))

    ids = [submit(ops["cloud"], Job(verb="status", project="alpha")) for _ in range(3)]
    agent.poll_once()
    submit(ops["cloud"], Job(verb="status", project="alpha"))
    agent.poll_once()
    agent.poll_once()

    for job_id in ids:
        assert executions.count(job_id) == 1, f"{job_id} ran {executions.count(job_id)} times"


def test_the_answer_travels_back_in_the_result(ops, agent):
    """A cloud session must be able to read the answer, not just learn that one exists.

    The agent extracts the answer from the transcript it just wrote. When that path was
    relative it resolved against the agent's working directory, `is_file()` failed, and the
    result came back with `answer: ""` -- so the caller could see the ask had succeeded and
    still could not read it. Reported from a live cloud session before it was noticed here.
    """
    transcript = agent.config.ops_repo.parent / "answers-test" / "t.md"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text("# Q1\n\n## Answer\n\nThe answer text.\n", encoding="utf-8")

    def fake_ask(job, project, notebook):
        text = transcript.read_text(encoding="utf-8")
        return Result(id=job.id, verb=job.verb, ok=True,
                      answer=text.split("## Answer", 1)[-1].strip(),
                      detail={"envelope": {"transcript": str(transcript)}})

    agent.do_ask = fake_ask
    job_id = submit(ops["cloud"], Job(verb="ask", question="why?", project="alpha"))
    agent.poll_once()

    result = result_for(ops["cloud"], job_id)
    assert result["answer"] == "The answer text.", "the caller cannot read the answer"


def test_the_transcript_itself_stays_out_of_the_ops_repo(ops, agent, tmp_path):
    """The answer travels in the result; the file that quotes the sources does not."""
    from nlmtools.ops.config import ConfigError

    agent.config.answers_dir = agent.config.ops_repo / "answers"
    with pytest.raises(ConfigError) as caught:
        agent.config.validate()
    assert "inside the ops repo" in str(caught.value)


def _refresh_that_only_syncs(agent, recorder):
    """The real do_refresh, with the sync stubbed to record and then stop the job.

    Stopping at the sync keeps the test off the dump tool and NotebookLM while still
    exercising the thing under test: that the sync happens at all, and first.
    """
    agent.sync = lambda job, project: (
        recorder.append(job.ref)
        or Result(id=job.id, verb=job.verb, ok=False, error="stopped after the sync"))
    agent.do_refresh = Agent.do_refresh.__get__(agent)


def test_a_refresh_always_syncs_first(ops, agent):
    """One verb does the whole update, so the notebook cannot lag the branch."""
    refs = []
    _refresh_that_only_syncs(agent, refs)

    job_id = submit(ops["cloud"], Job(verb="refresh", project="alpha", ref="feature/x"))
    agent.poll_once()

    assert refs == ["feature/x"]
    # A sync that fails aborts the refresh: rebuilding from the wrong tree is worse
    # than not rebuilding, because it succeeds quietly.
    assert result_for(ops["cloud"], job_id)["ok"] is False


def test_a_refresh_without_a_ref_syncs_the_default_branch(ops, agent):
    """There is no unsynced refresh -- that was the one way to use it wrongly."""
    refs = []
    _refresh_that_only_syncs(agent, refs)

    submit(ops["cloud"], Job(verb="refresh", project="alpha"))
    agent.poll_once()

    assert refs == [None]        # sync() resolves the default, so a sync still happens


def test_a_bad_ref_is_refused_before_it_reaches_git(ops, agent):
    """The ref is validated on the agent's side of the trust boundary."""
    job_id = submit(ops["cloud"], Job(verb="refresh", project="alpha",
                                      ref="--upload-pack=/bin/sh"))
    agent.poll_once()
    result = result_for(ops["cloud"], job_id)
    assert result["ok"] is False
    assert "refused" in result["error"]


def test_the_retired_sync_verb_is_refused_with_a_pointer(ops, agent):
    """An old client, or old documentation, gets told what to call instead."""
    job_id = submit(ops["cloud"], Job(verb="sync", project="alpha", ref="main"))
    agent.poll_once()
    result = result_for(ops["cloud"], job_id)
    assert result["ok"] is False
    assert "refresh --ref" in result["error"]


def test_a_refresh_refuses_to_clobber_uncommitted_work(ops, agent, tmp_path):
    """The synced clone is often someone's working copy. Losing it is not acceptable."""
    source = tmp_path / "source-repo"
    source.mkdir()
    for command in (["git", "init", "--quiet"],
                    ["git", "config", "user.email", "t@example.invalid"],
                    ["git", "config", "user.name", "t"]):
        subprocess.run(command, cwd=source, check=True)
    (source / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=source, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "seed"], cwd=source, check=True)
    (source / "seed.txt").write_text("work in progress\n", encoding="utf-8")

    project = agent.config.projects["alpha"]
    project.repo = source
    project.origin = None
    agent.sync = Agent.sync.__get__(agent)              # the real one, not the stub
    agent.do_refresh = Agent.do_refresh.__get__(agent)

    job_id = submit(ops["cloud"], Job(verb="refresh", project="alpha", ref="main"))
    agent.poll_once()

    result = result_for(ops["cloud"], job_id)
    assert result["ok"] is False
    assert "uncommitted" in result["error"]
    assert (source / "seed.txt").read_text(encoding="utf-8") == "work in progress\n"


def test_the_agent_publishes_a_matching_client(ops, agent):
    """The cloud side clones the ops repo; an older client is how a caller uses a dead verb."""
    from nlmtools.ops import client as client_module

    assert agent.publish_client() is True
    published = ops["windows"] / "client.py"
    assert published.read_text(encoding="utf-8") == \
        Path(client_module.__file__).read_text(encoding="utf-8")

    # Idempotent: an unchanged client makes no commit, so the log stays readable.
    assert agent.publish_client() is False

    subprocess.run(["git", "pull", "--quiet", "--rebase"], cwd=ops["cloud"], check=True)
    assert (ops["cloud"] / "client.py").is_file()
