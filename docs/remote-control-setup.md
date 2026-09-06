# Driving the Windows machine from a Claude cloud VM

How a Linux cloud session asks the always-on Windows machine to sync a branch, refresh the
NotebookLM bundle, or put a question to the architect — **without anything on the Windows
machine listening to the internet.**

This is the one-time installation. See [`remote-control-design.md`](remote-control-design.md)
for why it is built this way, and [`onboarding-a-project.md`](onboarding-a-project.md) for
adding a project once this is running.

```
   Claude cloud VM                private git repo               Windows always-on
   (Linux, ephemeral)              (github, private)             (holds the credentials)

     client.py  ──push job──►    jobs/<id>.json     ◄──poll──   agent
                                                                  │ sync / refresh / ask
     client.py  ◄──poll result── results/<id>.json  ──push──►   ──┘
```

Both machines only make **outbound** git connections. There is no port to open, no tunnel
to configure, no certificate to get wrong, and nothing to find by scanning.

---

## The security model

The Windows machine holds a Google Drive token, NotebookLM cookies and the whole
proprietary source tree. So the question that matters is: **what does an attacker get if
the ops-repo token leaks?**

**The agent is not a shell.** It accepts four verbs, and the caller cannot choose what they
operate on:

| Verb | The caller chooses | The operator fixes, at startup |
|---|---|---|
| `sync` | a project, a git ref | which repository each project means, its origin, and any ref allowlist |
| `refresh` | a project, a notebook | which filters produce which outputs, the bundle folder, the masks |
| `ask` | a project, a notebook, question text, attachments | which notebooks that project may reach |
| `status` | a project, a notebook | as above |

A project usually owns **several notebooks**, one per architectural area, so the notebook is
chosen per job. That makes it the one caller-supplied value that decides *which corpus is
read*, so `notebook_prefix` or `allowed_notebooks` is what stops a leaked token querying a
different project's notebook. Omit the notebook and the agent reuses the one last used for
that project.

A leaked token therefore lets someone ask your notebook questions and read the answers —
which is **source-code disclosure and should be taken seriously** — but not run commands on
the machine holding your Google credentials. That gap is the entire design.

Everything arriving in a job file is treated as untrusted, whatever repository it came
from. `src/nlmtools/ops/protocol.py` is the trust boundary and is tested directly: refs must
be plain names (never a URL, never something that reads as a git option), attachment names
must be plain filenames with a text-like extension, sizes are capped, binaries are refused,
and a job's identity comes from its filename rather than from a field the caller controls.

### The agent never runs code that arrived with a branch

`dump.bat` lives *in the repository*, so syncing an untrusted ref and then running it would
be a backdoor by construction — a compromised branch would own the machine holding your
Google credentials. The agent therefore does not use it. That script only ever wrapped a
handful of calls to the same dump tool, so the agent makes those calls itself, from
configuration the operator owns.

What *is* still read from the repository is the filter files, and that is a bounded choice
rather than an oversight. They are `.dumpignore`-style pattern lists, the agent supplies the
scan root, and the dump tool only walks that root. A rewritten filter can therefore widen
the dump *within the repository* but cannot reach outside it — and everything it could add
is content the same attacker already controls. The path is validated as a plain relative
path in any case.

For the tightest setting, combine `allowed_refs` with a `dump` list naming only filters you
have reviewed.

---

## Setup

### 1. A private ops repository

Create an **empty private** repository — `pjanec/nlm-ops` will do. It carries questions,
answers and job metadata, so it must be private for the same reason the source repo is.

```
mkdir nlm-ops && cd nlm-ops
git init -b main
mkdir jobs results
echo ops queue > README.md
touch jobs/.gitkeep results/.gitkeep
git add -A && git commit -m "seed"
git remote add origin https://github.com/pjanec/nlm-ops.git
git push -u origin main
```

### 2. Two fine-grained tokens, one per machine

In GitHub, create **two** fine-grained personal access tokens, each scoped to **only**
`nlm-ops`, with `Contents: read and write`. One for the Windows agent, one for the cloud VM.

Separate tokens because they can then be revoked independently, and the audit trail shows
which side did what. Neither token can touch your source repositories, so a compromise of
either is contained to the queue.

> Store them the way you store any password. On Windows, put the agent's token in Credential
> Manager and let git use it; never in a file inside a repository. See `CLAUDE.md`.

### 3. Clone on both machines

```
git clone https://github.com/pjanec/nlm-ops.git      # on each side
```

### 4. Configure the agent (Windows)

Write `ops-agent.json` **outside any repository** — it names local paths, not secrets. One
agent serves as many projects as you list:

```json
{
  "ops_repo":   "C:\\Users\\pjane\\nlm-ops",
  "nlmt":       "D:\\WORK\\NotebookLM\\.venv\\Scripts\\nlmt.exe",
  "python":     "C:\\Python313\\python.exe",
  "dump_tool":  "C:\\Utils\\AITools\\CodeDump\\dump.py",
  "poll_seconds": 20,
  "audit_log":  "C:\\Users\\pjane\\nlm-ops-audit.jsonl",

  "projects": {
    "simhost": {
      "repo":     "D:\\WORK\\IOS-IG-SimHost-FDP",
      "origin":   "https://github.com/pjanec/IOS-IG-SimHost-FDP.git",
      "drive_project": "simhost",
      "notebook_prefix": "SimHost ",
      "default_notebook": "SimHost FDP review",
      "masks":    ["HROT.", "FDP.", "Docs."],
      "dump": [
        {"filter": "dmp-EXT.dumpfilter",                  "output": "FDP.Ext.txt"},
        {"filter": "dmp-FDP.dumpfilter",                  "output": "FDP.Eng.txt"},
        {"filter": "dmp-HROT.Eng.dumpfilter",             "output": "HROT.Eng.txt"},
        {"filter": "dmp-HROT.Subsys.dumpfilter",          "output": "HROT.Sub.txt"},
        {"filter": "dmp-HROT.Blueprint.Tests.dumpfilter", "output": "HROT.Blueprint.Tests.txt"}
      ]
    }
  }
}
```

**Everything a job may *not* choose lives here**, and widening this file is the only way to
widen what the cloud VM can reach.

Four fields earn their place:

- **`dump`** replaces running the repository's `dump.bat`. Each entry is one call to the
  dump tool with a filter from the repository and an output name the agent chose. The agent
  applies `--overwrite`, which advances the ordinal and deletes the previous generation, and
  the tool's default word-splitting keeps every part under NotebookLM's 500,000-word cap.
  Output lands in `<repo>\.dumps`.
- **`origin`**, when set, must match the clone's actual origin before a sync proceeds.
  Cheap, and it catches a repository repointed locally at some later date.
- **`allowed_refs`**, when set, restricts `sync` to a fixed list of branch names. Omit it to
  allow any ref on the origin.
- **`masks`** must be non-empty: an empty mask set would let a load touch sources it did not
  upload.
- **`notebook_prefix`** (or `allowed_notebooks`, for an exact list) limits which notebooks
  this project may load into or query. A prefix is usually the practical choice: name a
  project's notebooks consistently and new ones need no config change, while a leaked token
  still cannot reach another project's. Leave both unset to allow any name.
- **`default_notebook`** is used only when a job names none and none has been used yet.

The agent remembers the notebook last used **per project** in `state_file` (beside the
config by default), so a session can name it once and omit it thereafter. That memory is a
convenience and nothing more: delete the file and the only consequence is that the next job
must name its notebook again. A remembered name is still checked against the rules above, so
tightening `notebook_prefix` takes effect immediately rather than being grandfathered.

With more than one project configured, **every job must name one**. The agent refuses to
guess, because guessing would eventually load one project's sources into another's notebook.

### 5. Run the agent

```
D:\WORK\NotebookLM\.venv\Scripts\python.exe -m nlmtools.ops.agent --config ops-agent.json
```

Run it under your own Windows account: the Google credentials are in *your* Credential
Manager (DPAPI is per-user), so a service account cannot reach them. For an always-on
machine, a Scheduled Task set to *run whether the user is logged on or not*, with *restart
on failure*, is the simplest arrangement.

### 6. Use it from the cloud VM

`client.py` needs nothing but Python and `git` — copy the single file across, or clone this
repository.

```
python client.py --ops-repo ~/nlm-ops --project simhost sync --ref feature/my-branch

# name the notebook once ...
python client.py --ops-repo ~/nlm-ops --project simhost \
    --notebook "SimHost damage model" refresh

# ... then omit it: the agent reuses the last one for this project
python client.py --ops-repo ~/nlm-ops --project simhost ask --question-file q.md
python client.py --ops-repo ~/nlm-ops --project simhost status --json
```

Each call pushes a job and waits for the result. Expect a poll interval (~20s) plus the work
itself: a refresh is several minutes, an ask is one to four.

---

## Operating it

**Pause it.** Commit a file called `PAUSED` to the ops repo and the agent stops picking
work up, from either machine, without touching the Windows box. Delete it to resume.

**See what it did.** The audit log records every job — including refused ones, which are
what you want to find when reviewing — with timestamp, verb, ref, question size, attachment
names and outcome. The git history of the ops repo is a second, independent record.

**Rotate a token** by revoking it in GitHub and re-authenticating that side. Nothing else
changes.

**Prune the queue.** Jobs and results accumulate, and they contain proprietary text. Delete
old ones periodically; if that matters, rewrite the history rather than only deleting the
files, since git keeps them otherwise.

---

## What is deliberately impossible

- Running an arbitrary command. There is no verb for it, no parameter that becomes one,
  and nothing arriving with a synced branch is executed.
- Reading an arbitrary file. `ask` returns an answer; `status` returns source titles.
- Pointing the machine at a different repository. `sync` takes a ref, never a URL.
- Writing outside the scratch directory. Attachment names may not contain a path.
- **Exposing the MCP server.** It has no authentication, and anyone reaching it controls the
  whole Google account. It must never be published through any tunnel — see `design.md` §14.

---

## If the queue misbehaves

| Symptom | Likely cause |
|---|---|
| a job never gets a result | the agent is stopped, or `PAUSED` is present |
| every job comes back refused | a validation rule; the `error` names it exactly |
| `ask` fails with exit 11 | the NotebookLM session could not be renewed; a human must run `nlm login` on Windows |
| `refresh` fails with exit 13 | content did not survive ingestion — do not trust the notebook until it is understood |
| results appear twice | two agents are running against one ops repo; run only one |
| `must name one` | the agent serves several projects; pass `--project` |
| `no notebook given and none remembered` | pass `--notebook` once, or set `default_notebook` |
| `notebook ... is not allowed` | it falls outside `notebook_prefix` / `allowed_notebooks` |
| `origin is ... expected ...` | the clone's remote does not match the configured origin |
