# Driving the Windows machine from a Claude cloud VM

How a Linux cloud session asks the always-on Windows machine to sync a branch, refresh the
NotebookLM bundle, or put a question to the architect — **without anything on the Windows
machine listening to the internet.**

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
| `sync` | a git ref | which repository, and that refs resolve against *its* origin |
| `refresh` | nothing at all | the dump command, bundle folder, masks, project, notebook |
| `ask` | question text, attachments | which notebook, which project |
| `status` | nothing | which notebook |

A leaked token therefore lets someone ask your notebook questions and read the answers —
which is **source-code disclosure and should be taken seriously** — but not run commands on
the machine holding your Google credentials. That gap is the entire design.

Everything arriving in a job file is treated as untrusted, whatever repository it came
from. `src/nlmtools/ops/protocol.py` is the trust boundary and is tested directly: refs must
be plain names (never a URL, never something that reads as a git option), attachment names
must be plain filenames with a text-like extension, sizes are capped, binaries are refused,
and a job's identity comes from its filename rather than from a field the caller controls.

### The one real exposure, stated plainly

`refresh` runs `dump.bat` **from whatever ref was last synced**, so a `sync` followed by a
`refresh` executes code from that branch. That is unavoidable if the cloud VM is to work on
arbitrary branches — and it is acceptable for one reason: **the ref can only come from your
own private source repository.** Anyone able to push there already controls every machine
that pulls from it, so the agent adds no meaningful reach. What is forbidden is pointing the
machine at a *different* repository, which is why the verb takes a ref and never a URL.

If you would rather not accept even that, restrict `sync` to an allowlist of branch names in
the agent config.

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

Write `ops-agent.json` **outside any repository** — it names local paths, not secrets:

```json
{
  "ops_repo":      "C:\\Users\\pjane\\nlm-ops",
  "source_repo":   "D:\\WORK\\IOS-IG-SimHost-FDP",
  "dump_command":  ["cmd", "/c", "dump.bat"],
  "bundle_folder": "D:\\WORK\\IOS-IG-SimHost-FDP\\.dumps",
  "masks":         ["HROT.", "FDP.", "Docs."],
  "project":       "simhost",
  "notebook":      "SimHost FDP review",
  "nlmt":          "D:\\WORK\\NotebookLM\\.venv\\Scripts\\nlmt.exe",
  "poll_seconds":  20,
  "audit_log":     "C:\\Users\\pjane\\nlm-ops-audit.jsonl"
}
```

Everything a job may *not* choose lives here. Widening this file is the only way to widen
what the cloud VM can reach.

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
python client.py --ops-repo ~/nlm-ops sync --ref feature/my-branch
python client.py --ops-repo ~/nlm-ops refresh
python client.py --ops-repo ~/nlm-ops ask --question-file question.md --attach data.json
python client.py --ops-repo ~/nlm-ops status --json
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

- Running an arbitrary command. There is no verb for it and no parameter that becomes one.
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
