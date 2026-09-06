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

from nlmtools.ops.agent import Agent, AgentConfig, PAUSE_FILE
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


@pytest.fixture
def agent(ops, tmp_path):
    config = AgentConfig(
        ops_repo=ops["windows"],
        source_repo=tmp_path / "source",
        dump_command=["cmd", "/c", "dump.bat"],
        bundle_folder=tmp_path / "dumps",
        masks=["HROT.", "FDP."],
        project="test",
        notebook="Test notebook",
        nlmt=tmp_path / "nlmt.exe",
        audit_log=tmp_path / "audit.jsonl",
    )
    built = Agent(config)
    # Stub the verbs: the transport is what is under test here.
    built.do_refresh = lambda job: Result(id=job.id, verb=job.verb, ok=True,
                                          detail={"stub": "refreshed"})
    built.do_status = lambda job: Result(id=job.id, verb=job.verb, ok=True,
                                         detail={"stub": "status"})
    built.do_ask = lambda job: Result(id=job.id, verb=job.verb, ok=True,
                                      answer=f"answer to: {job.question}")
    built.do_sync = lambda job: Result(id=job.id, verb=job.verb, ok=True,
                                       detail={"ref": job.ref})
    return built


def submit(clone: Path, job: Job) -> str:
    jobs = clone / "jobs"
    jobs.mkdir(exist_ok=True)
    (jobs / f"{job.id}.json").write_text(job.to_json(), encoding="utf-8")
    git("add", "-A", cwd=clone)
    git("commit", "--quiet", "-m", f"job {job.id}", cwd=clone)
    git("push", "--quiet", "origin", "HEAD:main", cwd=clone)
    return job.id


def result_for(clone: Path, job_id: str) -> dict | None:
    git("fetch", "--quiet", "origin", cwd=clone)
    git("reset", "--quiet", "--hard", "origin/main", cwd=clone)
    path = clone / "results" / f"{job_id}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


# -- the round trip -----------------------------------------------------------------


def test_a_job_pushed_by_the_cloud_comes_back_answered(ops, agent):
    job_id = submit(ops["cloud"], Job(verb="ask", question="why is the sky blue?"))

    assert agent.poll_once() == 1

    result = result_for(ops["cloud"], job_id)
    assert result is not None, "the cloud side never saw a result"
    assert result["ok"] is True
    assert result["answer"] == "answer to: why is the sky blue?"
    assert result["started"] and result["finished"]


def test_a_job_is_executed_exactly_once(ops, agent):
    submit(ops["cloud"], Job(verb="refresh"))
    assert agent.poll_once() == 1
    assert agent.poll_once() == 0, "a completed job was picked up again"


def test_several_jobs_are_all_handled(ops, agent):
    ids = [submit(ops["cloud"], Job(verb="status")) for _ in range(3)]
    assert agent.poll_once() == 3
    for job_id in ids:
        assert result_for(ops["cloud"], job_id)["ok"] is True


def test_a_refused_job_still_produces_a_result(ops, agent):
    """Silence is the worst outcome: the caller would wait for its full timeout."""
    job_id = submit(ops["cloud"], Job(verb="sync", ref="--upload-pack=/bin/sh"))

    agent.poll_once()

    result = result_for(ops["cloud"], job_id)
    assert result["ok"] is False
    assert "refused" in result["error"]
    assert result["hint"]


def test_an_unknown_verb_is_refused_not_executed(ops, agent):
    job_id = submit(ops["cloud"], Job(verb="exec"))
    agent.poll_once()
    result = result_for(ops["cloud"], job_id)
    assert result["ok"] is False
    assert "unknown verb" in result["error"]


def test_the_pause_file_stops_the_agent(ops, agent):
    submit(ops["cloud"], Job(verb="refresh"))

    paused = ops["cloud"]
    (paused / PAUSE_FILE).write_text("maintenance\n", encoding="utf-8")
    git("add", "-A", cwd=paused)
    git("commit", "--quiet", "-m", "pause", cwd=paused)
    git("push", "--quiet", "origin", "HEAD:main", cwd=paused)

    assert agent.poll_once() == 0, "the agent ran a job while paused"


def test_the_job_id_comes_from_the_filename_not_the_payload(ops, agent):
    """A caller must not be able to name the result file something else."""
    job = Job(verb="refresh")
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
    submit(ops["cloud"], Job(verb="refresh"))
    submit(ops["cloud"], Job(verb="sync", ref="../../etc/passwd"))
    agent.poll_once()

    lines = [json.loads(l) for l in
             (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    outcomes = {entry["verb"]: entry["outcome"] for entry in lines}
    assert outcomes["refresh"] == "ok"
    assert outcomes["sync"] == "refused"
