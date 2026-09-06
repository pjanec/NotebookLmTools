# Onboarding a new project

You have the tools installed and the queue working. Now you want a **new** project — its own
repository, its own notebooks — reachable from a Claude cloud VM.

Steps are grouped by **where** you do them. Most of the work is on the Windows machine, and
the only genuinely new thinking is step 2: deciding how to slice the corpus.

- [`remote-control.md`](remote-control.md) — the design, the trust boundary, and installing the queue
- [`bootstrap-new-machine.md`](bootstrap-new-machine.md) — installing the tools, once per machine

---

## On GitHub

**Usually nothing.** One ops repo and one pair of tokens serve every project, because the
project is a job parameter rather than a deployment.

Two exceptions:

- **The source repository must be reachable from the Windows machine.** Private is expected;
  the machine clones it with your normal git credentials, not with the ops token.
- **A separate ops repo** is worth it only if a project has different collaborators, since
  anyone who can read the ops repo can read that project's questions and answers. If you do
  split, create a second pair of fine-grained PATs scoped to it and run a second agent with
  its own config.

**No new PAT is needed for a new project** in the normal case. If you are creating the ops
repo for the first time, that is in [`remote-control.md`](remote-control.md).

---

## On the always-on Windows machine

### 1. Clone the source repository

```
cd D:\WORK
git clone https://github.com/<you>/<project>.git
```

The agent syncs and dumps this clone. It must be a normal working clone the machine can
fetch from without prompting.

### 2. Decide how to slice the corpus — the only real design work

NotebookLM caps a source at 500,000 words, and answers get worse as a notebook becomes a
soup of unrelated material. So a project is dumped into **several files by subject**, not
one.

Write a `.dumpfilter` for each slice, in the repository, alongside the ones that already
exist for other projects. They are `.dumpignore`-style pattern lists — see
`C:\Utils\AITools\CodeDump\README.md`.

A slicing that works well:

| Slice | Contains | Typical output |
|---|---|---|
| engine / core | the subsystem you ask about most | `Core.Eng.txt` |
| subsystems | the layers around it | `Core.Sub.txt` |
| externals | third-party or generated code you still need context on | `Core.Ext.txt` |
| tests | behaviour as specified by tests | `Core.Tests.txt` |
| docs | design documents, ADRs, notes | `Docs.txt` |

**Slice for dumping, not for separate notebooks.** The slices exist because NotebookLM caps
a single source at 500,000 words, not because the corpus should be divided between notebooks.
Keep one project's slices in one notebook: **cross-module questions are the valuable ones**,
and they are exactly what a split corpus cannot answer. A coding agent already handles a
single small area well on its own — what it cannot do is reason across the whole system,
which is the entire reason to ask the architect.

Two rules that save trouble later:

- **Give every output of one project a common prefix**, or a small set of them. The prefixes
  become the load masks, and masks are what stop a load touching sources it did not upload.
- **Keep documents in their own slice.** Design intent and implementation age differently,
  and you will often want to refresh one without the other.

Check the sizes before wiring it up — the dump tool splits anything large into `.01`, `.02`
parts automatically, but a slice that needs ten parts is usually a slice that should have
been two.

### 3. Try it locally, before the queue

```
cd D:\WORK\<project>
python C:\Utils\AITools\CodeDump\dump.py --overwrite --filter-file dmp-Eng.dumpfilter . Core.Eng.txt
dir .dumps

D:\WORK\NotebookLM\.venv\Scripts\nlmt.exe load ^
  --notebook "<Project> core" --local-folder "D:\WORK\<project>\.dumps" ^
  --mask "Core." --mask "Docs." --project <project> --verify-ingest all
```

`--verify-ingest all` reads every source back from NotebookLM and compares it against the
local file. Worth the one-off cost on a corpus whose shape you have never loaded before: it
is the difference between knowing the content arrived intact and assuming it.

Then ask it something you already know the answer to. If that works, the queue will work,
because the queue only runs these same commands.

### 4. Add the project to the agent config

In `ops-agent.json`, add one entry under `projects`:

```json
"myproject": {
  "repo":     "D:\\WORK\\myproject",
  "origin":   "https://github.com/you/myproject.git",
  "drive_project": "myproject",
  "masks":    ["Core.", "Docs."],
  "notebook_prefix": "MyProject ",
  "default_notebook": "MyProject core",
  "dump": [
    {"filter": "dmp-Eng.dumpfilter",   "output": "Core.Eng.txt"},
    {"filter": "dmp-Sub.dumpfilter",   "output": "Core.Sub.txt"},
    {"filter": "dmp-Docs.dumpfilter",  "output": "Docs.txt"}
  ]
}
```

Getting these right matters more than it looks:

- **`masks` must cover every `dump` output prefix.** Masks gate what is *selected*, so a
  dump output whose prefix is missing is never uploaded at all: the slice simply does not
  exist in the notebook, and the architect answers as though that part of the codebase were
  not there. The load reports success, because from its point of view nothing went wrong.
  Check `counts.selected` against the number of files the dump produced — that is the one
  number that catches it.
- **`notebook_prefix`** decides which notebooks this project may touch. Name a project's
  notebooks with a common prefix and new architectural areas need no config change, while a
  leaked ops token still cannot reach another project's notebook.
- **`origin`** is checked against the clone's actual remote before any sync, and every
  refresh syncs.
- **`default_ref`** is what a refresh syncs when the caller names no ref. Leave it unset
  unless the project's work happens somewhere other than origin's default branch.
- **`drive_project`** names the folder under `/NotebookLmTools` on Drive. Keep it distinct so
  `nlmt prune` can retire one project's bundles without touching another's.

### 5. Restart the agent

It reads its configuration at startup, and refuses to start on a bad one rather than
half-working. A restart is the confirmation that the config is valid.

### 6. Prove it through the queue

From the Windows machine itself, submit one job the same way the cloud VM will:

```
D:\WORK\NotebookLM\.venv\Scripts\python.exe -m nlmtools.ops.client ^
  --ops-repo C:\Users\<you>\nlm-ops --project myproject status
```

A result within a poll interval or two means the project is wired correctly.

---

## On the Claude Linux VM

### 7. Clone the ops repo, once per VM

```
git clone https://github.com/<you>/nlm-ops.git ~/nlm-ops
```

Authenticate with the **cloud VM's** fine-grained token, not the agent's. That token can
touch the queue and nothing else.

### 8. Tell the agent how to use it

Add one entry to the project's `CLAUDE.md`, pointing at the canonical guide rather than
copying it — that document changes as the tools do, and a copy would quietly rot:

```markdown
## Consulting the NotebookLM architect

A NotebookLM notebook holds a snapshot of this project's sources and design documents and
can answer cross-module architectural questions. Reach it through the job queue in
`~/nlm-ops` (project `myproject`).

**Read this before using it:**
https://github.com/pjanec/NotebookLmTools/blob/main/docs/consulting-the-architect.md

It is **additional feedback, never a decision**: the user has the last word, never consult
it silently, and verify every load-bearing claim against source before acting on it. This
project's own convention for architectural question documents is unchanged.
```

A new session then learns the whole procedure from the repository, with no special prompt.

---

## What you now have

| | |
|---|---|
| On GitHub | usually nothing new; the source repo, reachable from Windows |
| On Windows | a clone, filter files, one config entry, an agent restart |
| On the cloud VM | a clone of the ops repo and a paragraph in `CLAUDE.md` |

Adding a **notebook** to an existing project needs none of this — create it in NotebookLM,
or let a load create it, and pass `--notebook`. Provided the name matches the project's
`notebook_prefix`, nothing needs reconfiguring.

## When it does not work

| Symptom | Cause |
|---|---|
| `unknown project` | the config entry is missing, or the agent was not restarted |
| `must name one` | several projects configured; pass `--project` |
| `notebook ... is not allowed` | it falls outside `notebook_prefix` / `allowed_notebooks` |
| `origin is ... expected ...` | the clone's remote does not match the configured origin |
| `filter ... is missing` | the filter file is not on the currently synced ref |
| `has uncommitted changes` | someone is working in the clone the agent syncs; commit, stash or discard on that machine |
| a slice is missing from the notebook | its prefix is missing from `masks`, so it was never uploaded |
| a source is stale while others update | its prefix was *removed* from `masks` after it was loaded, so it is neither replaced nor deleted |
| no result at all | the agent is stopped, or `PAUSED` is present in the ops repo |
