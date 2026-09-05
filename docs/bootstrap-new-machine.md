# Bootstrapping on a new Windows machine

Written to be followed by **Claude Code running on that machine**, with a human present for
two browser sign-ins. Every stage states what to run, what proves it worked, and what to do
when it does not.

Budget about 20 minutes, most of it waiting on downloads and one live verification run.

---

## What you need before starting

| | |
|---|---|
| **Windows** | 10 or 11. The installer targets Windows PowerShell 5.1 (`powershell -f`), which is present by default; PowerShell 7 also works. |
| **Python** | 3.11 or newer, from python.org. The Microsoft Store build works but is preferred against — it redirects file writes. Setup finds an interpreter via the `py` launcher, PATH, the registry and the usual install directories, so it need not be on PATH. |
| **A Google account** | The same one that owns the NotebookLM notebooks. Both sign-ins below must use it. |
| **Google OAuth client id and secret** | From the Cloud project created once for this tool. **Reuse the existing one** — one client serves every machine. See [`google-oauth-setup.md`](google-oauth-setup.md) to create one from scratch. |
| **Chrome** | `nlm` drives it for authentication. |

### Getting the client id and secret onto the new machine

They live in Windows Credential Manager on the old machine, which is machine-bound, so they
must be carried across. On the **old** machine:

```
.venv\Scripts\python.exe -c "import keyring; print(keyring.get_password('NotebookLmTools','drive-oauth-client-id'))"
.venv\Scripts\python.exe -c "import keyring; print(keyring.get_password('NotebookLmTools','drive-oauth-client-secret'))"
```

> **Do not paste the secret into a chat, a file in this repository, or a commit message.**
> Carry it the way you would any password, and type it directly into the setup command on
> the new machine. See `CLAUDE.md`.

Alternatively, retrieve both from the Cloud console under *Google Auth Platform →
Clients*, where the client id is always visible.

---

## Stage 1 — Clone

```
git clone https://github.com/pjanec/NotebookLmTools.git
cd NotebookLmTools
```

**Proves it worked:** `docs\design.md` and `setup.ps1` exist.

---

## Stage 2 — Install

```
powershell -f setup.ps1 -DriveClientId <id> -DriveClientSecret <secret>
```

Idempotent — safe to re-run at any point, and the way to repair a half-finished install.
It locates Python, creates `.venv`, installs the pinned dependencies from
`requirements.lock`, downloads the pinned rclone and verifies it against Google's published
`SHA256SUMS`, then runs the two sign-ins.

**A human is needed twice, and only here:**

1. **Drive.** A browser opens for rclone. You will see an **"unverified app"** warning —
   expected for a Testing-status OAuth app. Click **Advanced → Go to ‹app name›**, then
   approve. If it fails with `access_denied`, the account is not listed under *Audience →
   Test users* in the Cloud console.
2. **NotebookLM.** Chrome opens for `nlm login`. **Use the same Google account.** It may
   ask for a full sign-in even though your everyday Chrome is signed in — `nlm` keeps its
   own browser profile, which starts empty. That is a one-time cost per machine.

**Proves it worked:** the script ends with `Setup complete.` and prints `nlmt 0.1.0`.

**If it does not:**

| Symptom | Cause |
|---|---|
| `no usable Python 3.11+ found` | It prints every candidate it tried and why each was rejected. If Python is installed, pass it explicitly: `-PythonExe "C:\Python313\python.exe"`. |
| checksum warning on the rclone download | Do not proceed. Re-run; if it recurs, the download is untrustworthy. |
| `access_denied` in the browser | Add the account under *Audience → Test users*. |

---

## Stage 3 — Offline tests

```
.venv\Scripts\python.exe -m pytest tests -q
```

**Use the venv interpreter, not the system one.** The system Python does not have
`notebooklm_tools` installed, and tests that touch it will pass for the wrong reason — this
has already happened once.

**Proves it worked:** every test passes. These need no network and no credentials; they
cover mask selection, locking, naming, the output contract, the help system, and the
contract we depend on from `notebooklm_tools`.

**If `test_library_contract.py` fails,** the installed `notebooklm-mcp-cli` has changed
something this project relies on — a renamed function or a changed signature. Read
`NOTES.md` under "We do not fork `notebooklm-mcp-cli`" before changing anything.

---

## Stage 4 — Live verification

```
.venv\Scripts\python.exe tests\live_check.py
```

This is the real proof. It exercises the whole pipeline against the account: Drive upload
and checksum verification, source registration, indexing, ingestion fidelity, querying,
the ask protocol with an attachment, and cleanup. It creates a notebook called
**`nlmt selftest`** and a Drive folder under **`/NotebookLmTools/selftest`**, and deletes
both when it finishes. Nothing else in the account is touched.

Allow several minutes: queries against a notebook take a minute or more, and that is the
API, not the tool.

**Proves it worked:** `Everything works: Drive, NotebookLM, fidelity, querying, ask,
cleanup.` and exit code 0.

Each stage means something specific, so a failure localises the problem:

| Stage | What a failure means |
|---|---|
| credentials | one of the two sign-ins did not take. Re-run `setup.ps1`. |
| fixtures | a local bug, not an environment problem. Should never fail. |
| drive | Drive upload or checksum verification. Exit 10 is authorization, exit 13 means bytes changed in transit — do not proceed. |
| load | the pipeline. Exit 13 here means NotebookLM did not ingest the content intact, which is the failure this whole project exists to prevent. |
| scoping | a reload deleted a source outside the mask. Serious: the notebook is meant never to be a mirror. |
| query | the corpus loaded but answers do not reflect it. |
| ask | the attachment never reached the answer, or ask left sources behind. A leftover `Q<n>-` source corrupts later answers. |
| cleanup | the notebook or Drive folder could not be removed; tidy up by hand. |

---

## Stage 5 — Load something real

```
.venv\Scripts\nlmt.exe load --notebook "<your notebook>" --local-folder "<your dump folder>" --mask "<prefix>" --project <name>
```

**Proves it worked:** `ready: true`, and `counts` showing `selected`, `uploaded`,
`verified` and `added` all equal.

On a first load against an unfamiliar corpus add `--verify-ingest all`, which reads every
source back and compares it against the local file rather than sampling one. It costs a
full download once.

---

## Afterwards

- `nlmt --ai` is the complete reference, written to be read by an agent in one shot.
- `nlmt help agent` covers driving the tools autonomously, including what each exit code
  means and which ones must never be retried in a loop.
- `nlmt doctor` re-checks both credentials and reports the signed-in account.

**Sessions last hours, not weeks.** The tool renews them silently in the background with no
browser window. Exit 11 means even that failed, and a human must run
`.venv\Scripts\nlm.exe login` — which normally completes by itself, opening and closing
Chrome without asking anything.

**Nothing sensitive travels with the repository.** Credential Manager is bound to the
Windows account on one machine, so the new machine holds its own copy of everything, and
the repository holds none of it.
