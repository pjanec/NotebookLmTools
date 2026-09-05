# NotebookLmTools

Load a folder of `.txt` files into a NotebookLM notebook through Google Drive, and ask long
questions about them — from a shell, or from an AI agent.

```
local .txt bundle  ->  Google Drive (plain text)  ->  NotebookLM sources  ->  answers
```

## Why the Drive detour exists

**A `.txt` uploaded to NotebookLM as a local file loses its function bodies.** Headers and
signatures survive; the code inside does not. The damage is silent — the sources are
listed, the answers read fluently, and the content is gutted. Routing through Drive avoids
it, and that is the only reason Drive is in this pipeline.

Two supporting facts, both measured rather than assumed:

- Files are stored on Drive as **plain `text/plain`, byte-for-byte identical**, BOM
  included, and registered with that mime type. They are never converted to Google Docs: a
  converted 1 MB file failed to import at all, while the unconverted one imported in ~13
  seconds with its text exactly intact.
- Every load verifies the result twice — a checksum against Drive, and a comparison of the
  text NotebookLM actually ingested against the local file.

## Install

Windows, Python 3.11+. From the repository root:

```
powershell -f setup.ps1 -DriveClientId <id> -DriveClientSecret <secret>
```

The client ID comes from a one-time Google Cloud step — see
[`docs/google-oauth-setup.md`](docs/google-oauth-setup.md), which also explains why it is
unavoidable and what to ignore in the console. After the first run, `powershell -f
setup.ps1` alone repairs or refreshes the installation.

Setup is idempotent, is the only supported install path, and is how you move to another
machine. It creates its own virtual environment, so nothing depends on what is installed
system-wide.

## Use

```
nlmt doctor
nlmt load --notebook "Engine review" --local-folder D:\exports\engine
nlmt ask  --notebook "Engine review" --question-file question.md --attach metrics.json
```

`load` returns only when the sources are queryable, so there is nothing to poll. `ask`
uploads the question and any attachments as temporary sources, asks, saves the answer
under `answers/`, and removes them again.

| Command | Purpose |
|---|---|
| `nlmt load` | select by mask, upload, verify, replace the notebook's sources, wait for indexing |
| `nlmt ask` | long question, optionally with attached data files |
| `nlmt status` | what is in a notebook and whether it has finished indexing |
| `nlmt delete` | remove sources matching a mask — including one ask, `--mask Q7-` |
| `nlmt sweep` | remove every leftover ask source |
| `nlmt prune` | remove old Drive bundle folders |
| `nlmt doctor` | check both credentials |
| `nlmt gen-fixtures` | generate synthetic test bundles |

## Finding your way around

The tools are meant to be learnable without this file:

```
nlmt --ai             the complete reference, in one shot, written for an agent
nlmt help agent       how to drive these tools from an AI agent
nlmt help <topic>     workflow, masks, asks, readiness, output, secrets, exit-codes
nlmt <command> --help every argument, what comes back in --json, and a pasteable example
nlmt --help --json    the interface as machine-readable schema
```

Every failure carries an exit code and a `hint` naming the corrective action, so a caller
that has never read any documentation can still recover.

## Things worth knowing before you rely on it

- **The notebook is not a mirror.** Only sources matching the mask are ever touched.
  Anything you added by hand stays.
- **Masks scope everything**, and an empty selection is an error rather than a silent
  no-op — loading nothing would otherwise delete a notebook's sources and replace them
  with nothing.
- **A failed run is fixed by running it again.** There is no partial-resume logic, and
  nothing is deleted from a notebook until the replacement is on Drive and verified.
- **Old Drive bundles accumulate deliberately.** `nlmt prune` is the maintenance command.
- **Reloading breaks citations in older chat sessions** that pointed at the sources it
  replaced. Expected, not a bug.
- **Credentials never touch this repository.** The Drive token lives in an encrypted rclone
  config whose password is in Windows Credential Manager; the NotebookLM session lives in
  the profile `nlm` manages. Moving to another machine means authorizing once there.

## Documentation

| File | What it is |
|---|---|
| [`docs/bootstrap-new-machine.md`](docs/bootstrap-new-machine.md) | installing and verifying on another machine, end to end |
| [`docs/design.md`](docs/design.md) | the authoritative specification |
| [`docs/google-oauth-setup.md`](docs/google-oauth-setup.md) | the one-time Google Cloud step |
| [`docs/requirements-original-brief.md`](docs/requirements-original-brief.md) | the original brief, frozen; superseded where it differs |
| [`NOTES.md`](NOTES.md) | running log: what the undocumented API actually does, and why decisions were made |
| [`docs/ask-the-architect-guide.md`](docs/ask-the-architect-guide.md) | drop-in guidance for a consuming project's `CLAUDE.md` |
| [`docs/refreshing-the-bundle-guide.md`](docs/refreshing-the-bundle-guide.md) | when and how an agent should refresh the snapshot |
| [`CLAUDE.md`](CLAUDE.md) | rules for agents working in this repository |

## Development

```
.venv\Scripts\python.exe -m pytest tests -q      offline: no network, no credentials
.venv\Scripts\python.exe tests\live_check.py    end to end against the real account
```

Use the venv interpreter. The system Python does not have `notebooklm_tools`
installed, and tests that touch it will otherwise pass for the wrong reason.

The fixture generator writes files shaped like bundled source code with facts buried
**inside method bodies**, plus a manifest turning each into a question and its expected
answer. That placement is the point: a fact anywhere else would not detect the
body-stripping this project exists to prevent.
