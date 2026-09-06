"""The Windows-side agent: poll a private git repo for jobs, execute, push results.

Nothing listens. The machine holding the Google credentials makes only outbound git
connections, so there is no port to attack, no tunnel to misconfigure and no certificate to
get wrong. The trade is latency -- a job waits up to one poll interval -- which is
irrelevant next to the couple of minutes an `ask` takes anyway.

One agent serves several projects. A job names a **project** and, optionally, a **ref**;
everything else -- repository, origin, notebook, masks, and which filters produce which
dump outputs -- comes from the agent's own configuration (`config.py`).

**The agent never runs a script that arrived with a branch.** `dump.bat` lives in the
repository, so syncing an untrusted ref and then running it would be a backdoor by
construction. That batch file only ever wrapped a handful of calls to the same dump tool,
so the agent makes those calls itself, from configuration it owns.

A job cannot say which folder, which command, which repository or which notebook. That is
what keeps a leaked ops-repo token from becoming a shell on an always-on machine.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path

from . import client
from .config import AgentConfig, ConfigError, ProjectConfig
from .protocol import Job, JobRejected, Result, safe_attachment_name, validate

log = logging.getLogger("nlmtools.ops.agent")

JOBS_DIR = "jobs"
RESULTS_DIR = "results"

#: Presence of this file in the ops repo stops the agent picking anything up. A kill switch
#: that works from either machine, needing no access to the Windows box.
PAUSE_FILE = "PAUSED"

#: How hard to try to publish a result. Losing a push race with an incoming job is normal;
#: giving up and leaving the caller waiting is not.
PUSH_ATTEMPTS = 4


def _run(args: list[str], cwd: Path, timeout: float = 1800) -> subprocess.CompletedProcess:
    """Run a child process. Never through a shell: every argument stays an argument."""
    return subprocess.run(  # noqa: S603 - argv built from config and validated input
        [str(a) for a in args],
        cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )


class Agent:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self._synced_ref: str | None = None   # what the last sync checked out, for the result
        self.jobs_dir = config.ops_repo / JOBS_DIR
        self.results_dir = config.ops_repo / RESULTS_DIR

    # -- audit --------------------------------------------------------------------

    def audit(self, job: Job, outcome: str, detail: str = "") -> None:
        """Append-only record of everything the agent was asked to do.

        Deliberately includes refused jobs: an attempt that was rejected is exactly what
        you want to find when reviewing later.
        """
        line = json.dumps({
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "id": job.id, "verb": job.verb, "ref": job.ref, "project": job.project,
            "notebook": job.notebook,
            "question_chars": len(job.question or ""),
            "attachments": [a.name for a in job.attachments],
            "outcome": outcome, "detail": detail[:400],
        })
        log.info("%s %s -> %s", job.verb, job.id, outcome)
        if self.config.audit_log:
            self.config.audit_log.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config.audit_log, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    # -- ops repo -----------------------------------------------------------------

    def refresh_ops_repo(self) -> bool:
        """Bring the local clone up to date **without discarding unpushed work**.

        This used to `reset --hard origin/main`, which was wrong in a way that only shows
        under contention: if a result had been committed but its push lost a race with an
        incoming job, the reset threw that result away, the job looked pending again, and it
        ran a second time. Harmless for `status`; an `ask` asked twice and a `refresh`
        re-dumped and reloaded.

        Rebasing preserves the local commit instead. If the rebase itself fails, we abort
        and skip the pass rather than force the tree into line — repeating work is bad, and
        silently destroying a result is worse.
        """
        repo = self.config.ops_repo
        _run(["git", "fetch", "--quiet", "origin"], repo)

        rebased = _run(["git", "rebase", "--quiet", "origin/main"], repo)
        if rebased.returncode != 0:
            _run(["git", "rebase", "--abort"], repo)
            log.warning(
                "could not rebase the ops repo onto origin/main; skipping this pass. "
                "Resolve by hand in %s", repo,
            )
            return False
        return True

    def publish(self, result: Result) -> None:
        self.results_dir.mkdir(parents=True, exist_ok=True)
        (self.results_dir / f"{result.id}.json").write_text(result.to_json(), encoding="utf-8")
        _run(["git", "add", f"{RESULTS_DIR}/{result.id}.json"], self.config.ops_repo)
        _run(["git", "commit", "--quiet", "-m", f"result {result.id} ({result.verb})"],
             self.config.ops_repo)
        # Push with retries: losing a race with an incoming job is expected, not
        # exceptional. A result that stays unpushed is the thing to avoid, because the
        # caller is waiting for exactly this file.
        for attempt in range(PUSH_ATTEMPTS):
            pushed = _run(["git", "push", "--quiet", "origin", "HEAD:main"],
                          self.config.ops_repo)
            if pushed.returncode == 0:
                return
            _run(["git", "fetch", "--quiet", "origin"], self.config.ops_repo)
            rebased = _run(["git", "rebase", "--quiet", "origin/main"], self.config.ops_repo)
            if rebased.returncode != 0:
                _run(["git", "rebase", "--abort"], self.config.ops_repo)
                break
            time.sleep(attempt + 1)

        log.error(
            "could not publish result %s after %d attempts; it is committed locally and "
            "will be pushed on the next successful pass", result.id, PUSH_ATTEMPTS,
        )

    def pending(self) -> list[Path]:
        if not self.jobs_dir.is_dir():
            return []
        done = {p.stem for p in self.results_dir.glob("*.json")} if self.results_dir.is_dir() else set()
        return sorted(p for p in self.jobs_dir.glob("*.json") if p.stem not in done)

    # -- verbs --------------------------------------------------------------------

    def sync(self, job: Job, project: ProjectConfig) -> Result | None:
        """Check out a ref of the project's repository. Returns a Result only on failure.

        The ref is resolved against the agent's own origin. A caller supplies a name, never
        a URL, so this cannot be pointed at someone else's repository -- and anyone able to
        push to the configured origin already controls every machine that pulls from it.

        A job that names no ref gets the project's default branch. There is deliberately
        no "leave the tree alone" mode: that was the one way to use a refresh wrongly,
        since it rebuilt whatever commit the clone happened to be sitting on and reported
        success either way.
        """
        repo = project.repo
        ref = job.ref or project.default_ref or self._default_branch(repo)

        if project.origin:
            # Refuse if the clone's origin is not the one configured. Cheap, and it catches
            # a repository that was repointed locally at some later date.
            actual = _run(["git", "remote", "get-url", "origin"], repo).stdout.strip()
            if actual != project.origin:
                return self._failed(
                    job,
                    f"{project.name}: origin is {actual!r}, expected {project.origin!r}",
                    hint="the agent refuses to sync a repository it does not recognise",
                )

        if not project.ref_allowed(ref):
            return self._failed(
                job,
                f"ref {ref!r} is not in this project's allowed list",
                hint="allowed: " + ", ".join(project.allowed_refs),
            )

        # The clone is often the operator's own working copy, and a sync hard-resets and
        # cleans it. Refuse rather than destroy: a refresh is not worth someone's afternoon.
        dirty = _run(["git", "status", "--porcelain"], repo).stdout.strip()
        if dirty:
            first = dirty.splitlines()[:5]
            return self._failed(
                job,
                f"{project.name}: the clone at {repo} has uncommitted changes, and a "
                f"refresh would discard them: " + "; ".join(s.strip() for s in first),
                hint="commit, stash or discard them on the always-on machine, then retry",
            )

        fetched = _run(["git", "fetch", "--quiet", "origin"], repo)
        if fetched.returncode != 0:
            return self._failed(job, f"git fetch failed: {fetched.stderr.strip()[-300:]}")

        resolved = _run(["git", "rev-parse", "--verify", "--quiet", f"origin/{ref}^{{commit}}"], repo)
        sha = resolved.stdout.strip()
        if not sha:
            # Allow a tag or a full sha that exists locally after the fetch.
            resolved = _run(["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], repo)
            sha = resolved.stdout.strip()
        if not sha:
            return self._failed(job, f"ref {ref!r} does not exist on origin")

        # -fd, never -fdx: ignored files include the dump bundle, which must survive.
        _run(["git", "checkout", "--quiet", "--detach", sha], repo)
        _run(["git", "reset", "--quiet", "--hard", sha], repo)
        _run(["git", "clean", "--quiet", "-fd"], repo)

        self._synced_ref = ref
        return None

    @staticmethod
    def _default_branch(repo: Path) -> str:
        """origin's own default branch, or `main` if the clone never recorded one."""
        head = _run(["git", "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], repo)
        name = head.stdout.strip()
        return name.split("/", 1)[1] if name.startswith("origin/") else (name or "main")

    def do_refresh(self, job: Job, project: ProjectConfig, notebook: str) -> Result:
        """Bring the notebook up to date: sync, then dump, then load.

        This is one operation and not three, because the parts are useless apart.
        Syncing without refreshing leaves the notebook on the old bundle, so the
        architect is entirely unaffected; refreshing without syncing rebuilds whatever
        commit the clone was left on, and reports success while the architect answers
        about the wrong tree. So a refresh always syncs, and always reports the commit
        it dumped -- staleness is then visible rather than silent.

        The dump is driven from our own configuration rather than by running the repo's
        `dump.bat`: each entry is one invocation of the dump tool with a filter from the
        repository and an output name we chose. `--overwrite` is what advances the ordinal
        and removes the previous generation, so only one batch is ever left on disk.
        """
        failed = self.sync(job, project)
        if failed is not None:
            return failed

        produced: list[str] = []
        for entry in project.dump:
            filter_path = project.repo / entry.filter
            if not filter_path.is_file():
                return self._failed(
                    job, f"{project.name}: filter {entry.filter!r} is missing from the "
                         f"repository at this ref",
                    hint="sync a ref that contains it, or correct the agent config",
                )
            dumped = _run(
                [self.config.python, self.config.dump_tool,
                 "--overwrite", "--filter-file", str(filter_path),
                 ".", entry.output],
                project.repo, timeout=3600,
            )
            if dumped.returncode != 0:
                return self._failed(
                    job,
                    f"{project.name}: dump of {entry.output!r} failed: "
                    f"{(dumped.stderr or dumped.stdout).strip()[-400:]}",
                )
            produced.append(entry.output)

        args = [self.config.nlmt, "load",
                "--notebook", notebook,
                "--local-folder", str(project.bundle_folder),
                "--project", project.drive_project, "--json"]
        for mask in project.masks:
            args += ["--mask", mask]

        loaded = _run(args, self.config.ops_repo, timeout=5400)
        envelope = _parse_envelope(loaded.stdout)
        # The ref and the commit both go in the result: the ref because it may have been
        # defaulted rather than asked for, the commit because that is what was dumped.
        built = {"ref": self._synced_ref, "commit": _head_of(project.repo)}
        if loaded.returncode != 0:
            return Result(id=job.id, verb=job.verb, ok=False, exit_code=loaded.returncode,
                          error=(envelope.get("error") or {}).get("message", "load failed"),
                          hint=(envelope.get("error") or {}).get("hint"),
                          detail={"envelope": envelope, "dumped": produced, **built})
        return Result(id=job.id, verb=job.verb, ok=True,
                      detail={"envelope": envelope, "dumped": produced, **built})

    def do_ask(self, job: Job, project: ProjectConfig, notebook: str) -> Result:
        """Ask the notebook. The caller chooses the question, never the notebook."""
        scratch = self.config.ops_repo / ".scratch" / job.id
        scratch.mkdir(parents=True, exist_ok=True)
        try:
            question_file = scratch / "question.md"
            question_file.write_text(job.question or "", encoding="utf-8")

            args = [self.config.nlmt, "ask",
                    "--notebook", notebook,
                    "--question-file", str(question_file),
                    "--project", project.drive_project, "--json"]
            if self.config.answers_dir:
                args += ["--answers-dir", str(self.config.answers_dir)]
            if job.name:
                args += ["--name", job.name]
            for attachment in job.attachments:
                path = scratch / safe_attachment_name(attachment.name)
                path.write_bytes(attachment.decoded())
                args += ["--attach", str(path)]

            asked = _run(args, self.config.ops_repo, timeout=3600)
            envelope = _parse_envelope(asked.stdout)
            if asked.returncode != 0:
                return Result(id=job.id, verb=job.verb, ok=False, exit_code=asked.returncode,
                              error=(envelope.get("error") or {}).get("message", "ask failed"),
                              hint=(envelope.get("error") or {}).get("hint"),
                              detail={"envelope": envelope})

            answer = ""
            transcript = (envelope.get("transcript") or "")
            if transcript and Path(transcript).is_file():
                text = Path(transcript).read_text(encoding="utf-8")
                answer = text.split("## Answer", 1)[-1].strip()
            return Result(id=job.id, verb=job.verb, ok=True, answer=answer,
                          detail={"envelope": envelope})
        finally:
            for leftover in scratch.glob("*"):
                leftover.unlink(missing_ok=True)
            scratch.rmdir()

    def do_status(self, job: Job, project: ProjectConfig, notebook: str) -> Result:
        args = [self.config.nlmt, "status", "--notebook", notebook, "--json"]
        checked = _run(args, self.config.ops_repo, timeout=600)
        envelope = _parse_envelope(checked.stdout)
        return Result(id=job.id, verb=job.verb, ok=checked.returncode == 0,
                      exit_code=checked.returncode, detail={"envelope": envelope})

    # -- loop ---------------------------------------------------------------------

    def _failed(self, job: Job, message: str, hint: str | None = None) -> Result:
        return Result(id=job.id, verb=job.verb, ok=False, exit_code=1,
                      error=message, hint=hint)

    def execute(self, job: Job) -> Result:
        started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            validate(job)
        except JobRejected as rejected:
            self.audit(job, "refused", str(rejected))
            result = self._failed(job, f"refused: {rejected}",
                                  hint="see the allowed verbs and argument rules in "
                                       "src/nlmtools/ops/protocol.py")
            result.started = result.finished = started
            return result

        try:
            project = self.config.project(job.project)
        except ConfigError as unknown:
            self.audit(job, "refused", str(unknown))
            result = self._failed(job, str(unknown),
                                  hint="name a project this agent serves")
            result.started = result.finished = started
            return result

        # Every verb acts on a notebook, which must be one this project may touch.
        notebook: str | None = None
        try:
            notebook = self.config.resolve_notebook(project, job.notebook)
        except ConfigError as refused:
            self.audit(job, "refused", str(refused))
            result = self._failed(job, str(refused),
                                  hint="pass --notebook, or widen the agent config")
            result.started = result.finished = started
            return result

        handler = {"refresh": self.do_refresh,
                   "ask": self.do_ask, "status": self.do_status}[job.verb]
        try:
            result = handler(job, project, notebook)
        except subprocess.TimeoutExpired as expired:
            result = self._failed(job, f"timed out after {expired.timeout}s")
        except Exception as error:  # noqa: BLE001 - the agent reports, never dies
            result = self._failed(job, f"{type(error).__name__}: {error}")

        result.started = started
        result.finished = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if notebook:
            result.detail.setdefault("notebook", notebook)
            if result.ok:
                # Remember it so the next job may omit the name.
                self.config.remember_notebook(project.name, notebook)
        self.audit(job, "ok" if result.ok else "failed", result.error or "")
        return result

    def publish_client(self) -> bool:
        """Keep a copy of `client.py` in the ops repo, matching this agent.

        The cloud side clones the ops repo and needs the client; fetching it separately is
        one more thing to get wrong, and a client older than the agent is exactly how a
        caller ends up using a verb that no longer exists. The agent already pushes here,
        so it publishes the client it is compatible with. Returns True if it changed.
        """
        source = Path(client.__file__).read_text(encoding="utf-8")
        target = self.config.ops_repo / "client.py"
        # Compared as text, never as bytes. With core.autocrlf on -- the default on a
        # Windows install -- git hands back CRLF in the working tree and stores LF in
        # the commit, so a byte comparison would differ forever and commit every pass.
        # The Linux VM gets LF from the blob either way.
        if target.is_file() and target.read_text(encoding="utf-8") == source:
            return False

        target.write_text(source, encoding="utf-8")
        _run(["git", "add", "client.py"], self.config.ops_repo)
        _run(["git", "commit", "--quiet", "-m",
              "client.py: publish the copy matching the agent"], self.config.ops_repo)
        pushed = _run(["git", "push", "--quiet", "origin", "HEAD:main"], self.config.ops_repo)
        if pushed.returncode != 0:
            log.warning("published client.py locally; the push will follow on a later pass")
        else:
            log.info("published client.py to the ops repo")
        return True

    def poll_once(self) -> int:
        """One pass: fetch, run everything pending, publish. Returns jobs handled."""
        if not self.refresh_ops_repo():
            return 0
        self.publish_client()
        if (self.config.ops_repo / PAUSE_FILE).exists():
            log.info("paused: %s is present", PAUSE_FILE)
            return 0

        handled = 0
        for path in self.pending():
            try:
                job = Job.from_json(path.read_text(encoding="utf-8"))
            except JobRejected as rejected:
                job = Job(verb="unknown", id=path.stem)
                self.audit(job, "refused", str(rejected))
                self.publish(self._failed(job, f"refused: {rejected}"))
                handled += 1
                continue

            job.id = path.stem  # the filename is the identity, not a field the caller sets
            self.publish(self.execute(job))
            handled += 1
        return handled

    def run_forever(self) -> None:
        log.info("agent watching %s every %ds", self.config.ops_repo, self.config.poll_seconds)
        while True:
            try:
                self.poll_once()
            except Exception as error:  # noqa: BLE001 - a poll failure must not end the agent
                log.warning("poll failed: %s: %s", type(error).__name__, error)
            time.sleep(self.config.poll_seconds)


def _head_of(repo: Path) -> str:
    """What commit the bundle was built from. Reported so staleness is visible."""
    described = _run(["git", "log", "-1", "--format=%h %s"], repo)
    return described.stdout.strip() if described.returncode == 0 else "unknown"


def _parse_envelope(stdout: str) -> dict:
    """The tools print one JSON envelope on stdout and nothing else."""
    try:
        return json.loads(stdout or "{}")
    except ValueError:
        return {"raw": (stdout or "")[:2000]}


def main(argv: list[str] | None = None) -> int:
    """Run the agent from the command line.

    Logging goes to stderr so that a Scheduled Task's captured output is a usable record on
    its own, alongside the audit log.
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="nlmtools.ops.agent",
        description=(
            "Poll a private git repository for jobs and execute them. Nothing listens: the "
            "machine makes only outbound git connections. What a job may ask for is fixed "
            "by the config file, not by the caller -- see docs/remote-control-setup.md."
        ),
    )
    parser.add_argument("--config", required=True,
                        help="agent config: which repo, which dump command, which notebook")
    parser.add_argument("--once", action="store_true",
                        help="handle whatever is pending and exit, instead of polling")
    parser.add_argument("--log-level", default="info",
                        choices=("debug", "info", "warning", "error"))
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    try:
        config = AgentConfig.from_file(Path(args.config))
    except (OSError, ValueError, KeyError) as error:
        print(f"could not read {args.config}: {error}", file=sys.stderr)
        print("see docs/remote-control-setup.md for the expected fields", file=sys.stderr)
        return 2

    agent = Agent(config)
    if args.once:
        handled = agent.poll_once()
        log.info("handled %d job(s)", handled)
        return 0

    try:
        agent.run_forever()
    except KeyboardInterrupt:
        log.info("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
