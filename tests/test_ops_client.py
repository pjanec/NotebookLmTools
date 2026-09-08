"""The cloud client's own behaviour, without a queue behind it.

The round trip is covered elsewhere and costs minutes; these are the things that go wrong
in the last two lines of the program, after the expensive part has already succeeded.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CLIENT = Path(__file__).resolve().parents[1] / "src" / "nlmtools" / "ops" / "client.py"

# An emoji, an en dash and Czech diacritics: none of them survive cp1252, and all three
# turn up in architect answers about this codebase.
ANSWER = "Ready \U0001F680 — příliš žluťoučký kůň"

DRIVER = """
import sys
sys.path.insert(0, {client_dir!r})
import client

client.sync_repo = lambda ops: None
client.submit = lambda ops, job: "fake-job-id"
client.wait_for = lambda ops, job_id, timeout: {{"ok": True, "answer": {answer!r}}}

raise SystemExit(client.main(["--ops-repo", {ops!r}, "status"]))
"""


def _run_client(tmp_path: Path, encoding: str) -> subprocess.CompletedProcess:
    """Run the client in a subprocess whose stdout really is `encoding`."""
    (tmp_path / ".git").mkdir(exist_ok=True)   # the client insists on a clone; shape is enough
    driver = tmp_path / "driver.py"
    driver.write_text(
        DRIVER.format(client_dir=str(CLIENT.parent), answer=ANSWER, ops=str(tmp_path)),
        encoding="utf-8",
    )
    environment = dict(os.environ, PYTHONIOENCODING=encoding)
    return subprocess.run([sys.executable, str(driver)], capture_output=True,
                          env=environment, timeout=120)


def test_an_answer_prints_on_a_cp1252_console(tmp_path):
    """The ask has already succeeded and been paid for by the time we print it.

    A Windows session hit exactly this: the agent answered, the result file was fine, and
    the client died with a UnicodeEncodeError on an emoji -- so the answer had to be read
    out of results/ by hand. Crashing here throws away work that is already done.
    """
    done = _run_client(tmp_path, "cp1252")

    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    printed = done.stdout.decode("utf-8", "replace")
    assert "Ready" in printed and "žluťoučký" in printed


def test_an_answer_prints_on_a_utf8_console(tmp_path):
    """The fix must not break the case that already worked."""
    done = _run_client(tmp_path, "utf-8")

    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    assert ANSWER in done.stdout.decode("utf-8", "replace")
