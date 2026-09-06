# Configuration files

Everything a fresh machine needs, in one place. There are only two files you write by hand;
the rest are generated and are listed here so you know what they are and what not to commit.

---

## 1. `ops-agent.json` — the agent's authorisation model

**Written by hand. Lives outside any repository.** Suggested location:
`C:\Users\<you>\ops-agent.json`, passed to the agent as `--config`.

This is the only place a project exists. It defines what a job may *not* choose — which
repository, which notebooks, which filters produce which outputs — so widening this file is
the only way to widen what a cloud VM can reach.

```json
{
  "ops_repo":     "C:\\Users\\pjane\\nlm-ops",
  "nlmt":         "D:\\WORK\\NotebookLM\\.venv\\Scripts\\nlmt.exe",
  "python":       "C:\\Python313\\python.exe",
  "dump_tool":    "C:\\Utils\\AITools\\CodeDump\\dump.py",
  "poll_seconds": 20,
  "audit_log":    "C:\\Users\\pjane\\nlm-ops-audit.jsonl",
  "state_file":   "C:\\Users\\pjane\\ops-agent-state.json",

  "projects": {
    "simhost": {
      "repo":             "D:\\WORK\\IOS-IG-SimHost-FDP",
      "origin":           "https://github.com/pjanec/IOS-IG-SimHost-FDP.git",
      "drive_project":    "simhost",
      "masks":            ["HROT.", "FDP.", "Docs."],
      "notebook_prefix":  "SimHost ",
      "default_notebook": "SimHost FDP review",
      "dump": [
        {"filter": "dmp-EXT.dumpfilter",                  "output": "FDP.Ext.txt"},
        {"filter": "dmp-FDP.dumpfilter",                  "output": "FDP.Eng.txt"},
        {"filter": "dmp-HROT.Eng.dumpfilter",             "output": "HROT.Eng.txt"},
        {"filter": "dmp-HROT.Subsys.dumpfilter",          "output": "HROT.Sub.txt"},
        {"filter": "dmp-HROT.Blueprint.Tests.dumpfilter", "output": "HROT.Blueprint.Tests.txt"},
        {"filter": "dmp-Docs.dumpfilter",                 "output": "Docs.All.txt"}
      ]
    }
  }
}
```

### Top level

| Field | Required | Meaning |
|---|---|---|
| `ops_repo` | yes | local clone of the private job queue |
| `nlmt` | yes | the `nlmt` executable in this project's venv |
| `python` | no | interpreter used to run the dump tool. **Defaults to the one running the agent**, i.e. this project's venv, where the dump tool's dependency (`pathspec`) is pinned. Override only if you must, and see the warning below |
| `dump_tool` | yes | path to `dump.py` |
| `poll_seconds` | no | how often to look for work (default 20) |
| `audit_log` | no | append-only record of every job, refusals included |
| `state_file` | no | remembers the last notebook per project; defaults beside this file |

### Per project

| Field | Required | Meaning |
|---|---|---|
| `repo` | yes | the working clone to sync and dump |
| `origin` | no | if set, must match the clone's actual remote before a sync — catches a repository repointed locally later |
| `allowed_refs` | no | restricts `sync` to these branch names; omit to allow any ref on origin |
| `masks` | yes | which files a load may select and which sources it may replace |
| `dump` | yes | one entry per slice: a `filter` in the repository, and the `output` name |
| `notebook_prefix` | no | which notebooks this project may touch, by prefix |
| `allowed_notebooks` | no | an exact list instead of a prefix |
| `default_notebook` | no | used when a job names none and none has been used yet |
| `drive_project` | no | folder under `/NotebookLmTools` on Drive (default: the project name) |

### The two mistakes worth checking for

**Every `dump` output prefix must be covered by a `mask`.** Masks gate *selection*, so an
output whose prefix is missing is never uploaded at all — the slice simply does not exist in
the notebook, and the load still reports success. Compare `counts.selected` against the
number of files the dump produced.

Note that the dump tool inserts an ordinal: `Docs.All.txt` becomes `Docs.All_276.01.txt`, so
the mask `Docs.` still matches. A mask of `Docs.All.` would **not** match, because the
ordinal lands before the extension.

**Do not point `python` at a system interpreter.** The dump tool needs `pathspec`, which
this project declares and `setup.ps1` installs into the venv. Naming a system Python instead
works only if `pathspec` happens to be installed there — and on Windows it can fail even
when it looks installed: a `pip install --user` package lives in
`AppData\Roaming\Python\...`, but that same interpreter, when spawned as a child of the
venv, resolves user-site to the Microsoft Store location and cannot see it. The failure is
`ModuleNotFoundError: No module named 'pathspec'` from an interpreter you can prove has it.

**Filter files must be committed.** `sync` runs `git clean -fd`, which deletes untracked
files — an uncommitted `.dumpfilter` disappears the first time a branch is synced, and
`refresh` then fails with "filter is missing".

---

## 2. `nlmt.toml` — optional defaults for the command line

**Optional, written by hand, and lowest priority.** Passed with `--config`. Every setting is
a command-line argument first; this file only supplies defaults for the ones you would
otherwise retype.

```toml
# Top-level keys apply to any command that has that option.
project  = "simhost"
notebook = "SimHost FDP review"

[load]
local-folder  = "D:/WORK/IOS-IG-SimHost-FDP/.dumps"
mask          = ["HROT.", "FDP.", "Docs."]
verify-ingest = "sample"
concurrency   = 8

[ask]
ready-timeout = 1800
```

**Precedence is command line → config file → built-in default.** Nothing in this file can
override something you typed.

- Keys may be written as on the command line (`local-folder`) or as identifiers
  (`local_folder`).
- A repeatable option such as `mask` takes a list.
- A top-level key that belongs to *some* command but not the one being run is ignored, so
  one file can serve several commands.
- A key that belongs to **no** command is a usage error — silently ignoring a typo is how a
  setting goes unnoticed for weeks.

---

## 3. Files you do not write

| File | Created by | Commit it? |
|---|---|---|
| `requirements.lock` | `setup.ps1`, from the first resolve | **yes** — it is what makes another machine match |
| `tools/rclone.lock.json` | `setup.ps1` | **yes** — pins the rclone version and its checksum |
| `tools/rclone.exe` | `setup.ps1` | no, gitignored |
| `.venv/` | `setup.ps1` | no, gitignored |
| `%APPDATA%\rclone\rclone.conf` | rclone, encrypted | **never** — it holds the Drive token |
| `~\.notebooklm-mcp-cli\` | `nlm login` | **never** — it holds the session cookies |
| `ops-agent-state.json` | the agent | no — it only remembers the last notebook per project |
| `answers/` | `nlmt ask` | no, gitignored — transcripts contain source excerpts |

### Credentials, and where they are not

Nothing sensitive belongs in any file you write. The Drive OAuth client id and secret and
the rclone config password live in **Windows Credential Manager**; the NotebookLM session
lives in the `nlm` profile; the GitHub token lives in Credential Manager via the git
credential helper. None of them can be committed because none of them are in a file this
repository knows about. See `CLAUDE.md`.

---

## 4. Installing on a new machine from these documents alone

1. [`bootstrap-new-machine.md`](bootstrap-new-machine.md) — clone, `setup.ps1`, the two
   browser logins, then `tests\live_check.py` to prove it works.
2. [`google-oauth-setup.md`](google-oauth-setup.md) — only if you do not already have a
   Google OAuth client to reuse.
3. [`remote-control.md`](remote-control.md) — the ops repo, the two tokens, and the agent, if
   this machine is to be driven from a cloud VM.
4. This file — write `ops-agent.json`, and `nlmt.toml` if you want it.
5. [`onboarding-a-project.md`](onboarding-a-project.md) — per project thereafter.
