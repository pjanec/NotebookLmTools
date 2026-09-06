"""The cloud side: submit a job to the ops repo and wait for its result.

Deliberately dependency-free -- standard library and `git` only -- so a Claude cloud VM can
run it with nothing installed and no network access beyond the git remote it already uses.

    python client.py --ops-repo ~/nlm-ops sync --ref feature/my-branch
    python client.py --ops-repo ~/nlm-ops refresh
    python client.py --ops-repo ~/nlm-ops ask --question-file q.md --attach data.json

Each call pushes one job file, then polls for the matching result file. The Windows agent
never connects here; both sides only talk to the git remote.
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

JOBS_DIR = "jobs"
RESULTS_DIR = "results"


def run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603
        [str(a) for a in args], cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def sync_repo(ops: Path) -> None:
    run(["git", "fetch", "--quiet", "origin"], ops)
    run(["git", "reset", "--quiet", "--hard", "origin/main"], ops)


def submit(ops: Path, job: dict) -> str:
    jobs = ops / JOBS_DIR
    jobs.mkdir(parents=True, exist_ok=True)
    path = jobs / f"{job['id']}.json"
    path.write_text(json.dumps(job, indent=2, sort_keys=True), encoding="utf-8")

    run(["git", "add", f"{JOBS_DIR}/{job['id']}.json"], ops)
    run(["git", "commit", "--quiet", "-m", f"job {job['id']} ({job['verb']})"], ops)
    pushed = run(["git", "push", "--quiet", "origin", "HEAD:main"], ops)
    if pushed.returncode != 0:
        run(["git", "fetch", "--quiet", "origin"], ops)
        run(["git", "rebase", "--quiet", "origin/main"], ops)
        pushed = run(["git", "push", "--quiet", "origin", "HEAD:main"], ops)
        if pushed.returncode != 0:
            raise SystemExit(f"could not push the job: {pushed.stderr.strip()[-300:]}")
    return job["id"]


def wait_for(ops: Path, job_id: str, timeout: float, poll: float = 15) -> dict:
    """Poll for the result. The agent may take minutes; an ask alone takes about two."""
    deadline = time.time() + timeout
    result_path = ops / RESULTS_DIR / f"{job_id}.json"
    while True:
        sync_repo(ops)
        if result_path.is_file():
            return json.loads(result_path.read_text(encoding="utf-8"))
        if time.time() > deadline:
            raise SystemExit(
                f"no result for {job_id} after {timeout:.0f}s. The agent may be stopped, "
                f"paused (a PAUSED file in the ops repo), or still working."
            )
        time.sleep(poll)


def build_job(args: argparse.Namespace) -> dict:
    job = {
        "id": f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}",
        "verb": args.verb,
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "requested_by": "cloud",
        "attachments": [],
    }
    if args.project:
        job["project"] = args.project
    if args.notebook:
        job["notebook"] = args.notebook
    if args.verb == "sync":
        job["ref"] = args.ref
    elif args.verb == "ask":
        if args.question_file:
            job["question"] = Path(args.question_file).read_text(encoding="utf-8")
        else:
            job["question"] = args.question or sys.stdin.read()
        if args.name:
            job["name"] = args.name
        for attachment in args.attach or []:
            path = Path(attachment)
            job["attachments"].append({
                "name": path.name,
                "content_b64": base64.b64encode(path.read_bytes()).decode("ascii"),
            })
    return job


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nlm-ops",
        description="Ask the Windows agent to do something, through a private git repo. "
                    "Nothing listens on either side.",
    )
    parser.add_argument("--ops-repo", required=True,
                        help="local clone of the private ops repository")
    parser.add_argument("--timeout", type=float, default=3600,
                        help="how long to wait for a result (default 3600s)")
    parser.add_argument("--json", action="store_true", help="print the raw result")
    parser.add_argument("--project",
                        help="which configured project to act on; required when the agent "
                             "serves more than one")
    parser.add_argument("--notebook",
                        help="which notebook to load into or ask; omit to reuse the one "
                             "last used for this project")

    verbs = parser.add_subparsers(dest="verb", required=True)

    sync = verbs.add_parser("sync", help="check out a ref of the source repo on Windows")
    sync.add_argument("--ref", required=True, help="branch, tag or commit on the origin")

    verbs.add_parser("refresh", help="regenerate the bundle and load it into NotebookLM")
    verbs.add_parser("status", help="what is in the notebook and whether it is ready")

    ask = verbs.add_parser("ask", help="ask the architect")
    ask.add_argument("--question", help="the question text")
    ask.add_argument("--question-file", help="read the question from a file")
    ask.add_argument("--attach", action="append", help="a text-like data file; repeatable")
    ask.add_argument("--name", help="short slug for the transcript")

    args = parser.parse_args(argv)
    ops = Path(args.ops_repo).expanduser()
    if not (ops / ".git").is_dir():
        raise SystemExit(f"{ops} is not a git clone of the ops repository")

    sync_repo(ops)
    job = build_job(args)
    job_id = submit(ops, job)
    print(f"submitted {job_id} ({job['verb']}); waiting", file=sys.stderr)

    result = wait_for(ops, job_id, timeout=args.timeout)

    if args.json:
        print(json.dumps(result, indent=2))
    elif result.get("answer"):
        print(result["answer"])
    else:
        print(json.dumps(result.get("detail") or {}, indent=2))

    if not result.get("ok"):
        print(f"\nFAILED: {result.get('error')}", file=sys.stderr)
        if result.get("hint"):
            print(f"-> {result['hint']}", file=sys.stderr)
        return int(result.get("exit_code") or 1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
