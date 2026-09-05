"""End-to-end verification against a real Google account.

Run this after `setup.ps1` on a new machine. It exercises every stage of the pipeline for
real -- Drive upload, checksum verification, source registration, indexing, ingestion
fidelity, querying, the ask protocol with an attachment, and cleanup -- and reports
pass/fail per stage.

    .venv\\Scripts\\python.exe tests\\live_check.py

It creates a notebook called "nlmt selftest", a Drive folder under
/NotebookLmTools/selftest, and deletes both at the end. Nothing else in the account is
touched.

Exit code 0 means every stage passed. Anything else means the installation is not
trustworthy yet -- the failing stage names what to fix.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nlmtools.common import naming  # noqa: E402
from nlmtools.common.exits import ToolError  # noqa: E402
from nlmtools.common.nlm import NotebookLM  # noqa: E402
from nlmtools.fixtures.generate import generate_bundle  # noqa: E402
from nlmtools.loader.drive import Drive  # noqa: E402
from nlmtools.loader.load import run_load  # noqa: E402
from nlmtools.ask.ask import run_ask  # noqa: E402

NOTEBOOK = "nlmt selftest"
PROJECT = "selftest"
WORK = ROOT / "tests" / "fixtures" / "selftest"
CACHE = Path.home() / ".nlmtools" / "selftest"

results: list[tuple[str, bool, str]] = []
started = time.time()


def stage(name: str):
    """Run a stage, record the outcome, and keep going where it is safe to."""
    def decorator(fn):
        print(f"\n--- {name} ", "-" * max(0, 60 - len(name)))
        t = time.time()
        try:
            detail = fn() or ""
            results.append((name, True, detail))
            print(f"    PASS  ({time.time() - t:.0f}s)  {detail}")
            return True
        except Exception as error:  # noqa: BLE001 - a self-test reports, never crashes
            message = f"{type(error).__name__}: {error}"
            if isinstance(error, ToolError):
                message = f"exit {error.code} {error.error_class}: {error.message}"
            results.append((name, False, message))
            print(f"    FAIL  ({time.time() - t:.0f}s)  {message}")
            return False
    return decorator


nlm = NotebookLM()
drive = Drive()
manifest: dict = {}

# -- 1. credentials ------------------------------------------------------------------

@stage("credentials: Drive reachable and NotebookLM signed in")
def _credentials():
    listing = drive._run(["lsd", f"{drive.remote}:", "--max-depth", "1"], check=False)
    if not listing.ok:
        raise RuntimeError(f"Drive unreachable: {listing.stderr.strip()[-200:]}")
    nlm.find_notebook("a title that matches nothing at all")
    return "both credentials work"


# -- 2. fixtures ---------------------------------------------------------------------

@stage("fixtures: generate a bundle with known buried facts")
def _fixtures():
    global manifest
    if WORK.exists():
        shutil.rmtree(WORK)
    manifest = generate_bundle(WORK, files=3, target_bytes=60_000, prefix="Selftest")
    facts = len(manifest["facts"])
    if facts < 3:
        raise RuntimeError("generator produced too few facts to be diagnostic")
    return f"{len(manifest['files'])} files, {facts} facts buried inside method bodies"


# -- 3. Drive round trip -------------------------------------------------------------

@stage("drive: upload and checksum-verify, without touching a notebook")
def _drive_only():
    envelope = run_load(
        notebook_title=NOTEBOOK, local_folder=WORK, masks=["Selftest"],
        project=PROJECT, lock_dir=CACHE / "locks", drive=drive, nlm=nlm,
        drive_only=True,
    )
    if envelope.counts.get("verified") != envelope.counts.get("selected"):
        raise RuntimeError(f"checksum verification incomplete: {envelope.counts}")
    return f"{envelope.counts['verified']} file(s) verified byte-identical on Drive"


# -- 4. full load --------------------------------------------------------------------

@stage("load: full pipeline into a notebook, with ingestion verified")
def _load():
    envelope = run_load(
        notebook_title=NOTEBOOK, local_folder=WORK, masks=["Selftest"],
        project=PROJECT, lock_dir=CACHE / "locks", drive=drive, nlm=nlm,
        verify_ingest="all",
    )
    if not envelope.fields.get("ready"):
        raise RuntimeError("sources did not become ready")
    if envelope.counts.get("ingest_verified") != envelope.counts.get("added"):
        raise RuntimeError(f"not every source was verified: {envelope.counts}")
    return (f"{envelope.counts['added']} sources, all ingested intact "
            f"({envelope.fields.get('elapsed', '')}{round(time.time() - started)}s in)")


# -- 5. the scoping guarantee --------------------------------------------------------

@stage("scoping: a reload leaves sources outside the mask alone")
def _scoping():
    notebook_id = nlm.find_notebook(NOTEBOOK)
    keeper = nlm.add_text(notebook_id, "SELFTEST-keep-me", "A source outside the mask.")
    run_load(
        notebook_title=NOTEBOOK, local_folder=WORK, masks=["Selftest"],
        project=PROJECT, lock_dir=CACHE / "locks", drive=drive, nlm=nlm,
        verify_ingest="none",
    )
    titles = [s.title for s in nlm.list_sources(notebook_id)]
    if "SELFTEST-keep-me" not in titles:
        raise RuntimeError("a reload deleted a source outside the mask")
    nlm.delete_source(keeper)
    return "the notebook is not a mirror, as designed"


# -- 6. querying ---------------------------------------------------------------------

@stage("query: an answer from inside a method body, with a citation")
def _query():
    notebook_id = nlm.find_notebook(NOTEBOOK)
    smoke = manifest["smoke"]
    result = nlm.query(notebook_id, smoke["question"], fresh=True)
    answer = result.get("answer") or ""
    if smoke["expect"] not in answer:
        raise RuntimeError(f"expected {smoke['expect']!r}; got {answer[:160]!r}")
    if not (result.get("citations") or {}):
        raise RuntimeError("answer carried no citation")
    return f"answered {smoke['expect']!r} with {len(result['citations'])} citation(s)"


# -- 7. ask with an attachment -------------------------------------------------------

@stage("ask: question plus attachment, then cleanup")
def _ask():
    attachment = WORK / "selftest-data.json"
    attachment.write_text(
        json.dumps({"note": "these values appear in no source file",
                    "quorum_size": 8123, "election_timeout_ms": 4471}, indent=2),
        encoding="utf-8",
    )
    envelope = run_ask(
        notebook_title=NOTEBOOK,
        question=("In the attached selftest-data.json, what are the values of "
                  "quorum_size and election_timeout_ms? Answer with both numbers."),
        attachments=[attachment], project=PROJECT, lock_dir=CACHE / "locks",
        answers_dir=WORK / "answers", name="selftest", nlm=nlm, drive=drive,
        fresh=True,
    )
    transcript = Path(envelope.fields["transcript"]).read_text(encoding="utf-8")
    for value in ("8123", "4471"):
        if value not in transcript:
            raise RuntimeError(f"the answer never used the attachment value {value}")

    leftovers = [s.title for s in nlm.list_sources(nlm.find_notebook(NOTEBOOK))
                 if naming.is_ask_source(s.title)]
    if leftovers:
        raise RuntimeError(f"ask left sources behind: {leftovers}")
    return "attachment values used in the answer; nothing left behind"


# -- 8. teardown ---------------------------------------------------------------------

@stage("cleanup: remove the notebook and the Drive bundles")
def _cleanup():
    notebook_id = nlm.find_notebook(NOTEBOOK)
    if notebook_id:
        nlm.delete_notebook(notebook_id)
    for bundle in drive.list_bundles(PROJECT):
        drive.delete_bundle(PROJECT, bundle)
    drive.delete_path(PROJECT)
    if WORK.exists():
        shutil.rmtree(WORK)
    return "account left as it was found"


# -- report --------------------------------------------------------------------------

print("\n" + "=" * 70)
passed = sum(1 for _, ok, _ in results if ok)
for name, ok, detail in results:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f"        {detail}")
print(f"\n{passed}/{len(results)} stages passed in {time.time() - started:.0f}s")

if passed != len(results):
    print("\nThe installation is NOT verified. Fix the failing stage above; each error "
          "names the exit class and what to do about it.")
    raise SystemExit(1)
print("\nEverything works: Drive, NotebookLM, fidelity, querying, ask, cleanup.")
