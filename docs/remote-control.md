# Remote control: driving the Windows machine from a Claude cloud VM

Design and setup for letting an ephemeral Linux session sync a branch, refresh a NotebookLM
bundle, or put a question to the architect — on an always-on Windows machine holding
credentials it must never be given.

For adding a project once this is running, see
[`onboarding-a-project.md`](onboarding-a-project.md).

```
   Claude cloud VM                private ops repo              Windows always-on
   (Linux, ephemeral)              (GitHub, private)            (credentials, corpus)

     client.py  ──push job──►    jobs/<id>.json     ◄──poll──   agent
                                                                  │ sync / refresh / ask
     client.py  ◄──poll result── results/<id>.json  ──push──►   ──┘
```

Both machines make only **outbound** connections. There is no port to open, no tunnel to
configure, no certificate to get wrong, and nothing to find by scanning.

---

## 1. Why it is shaped this way

The pieces are fixed by circumstance, not preference:

- **NotebookLM has no official API.** Access needs browser cookies, refreshed from a real
  Chrome profile, on a machine where someone once signed in.
- **The Google Drive token is bound to a Windows account** through DPAPI, so it cannot be
  copied to a cloud VM even if that were wise.
- **The corpus is proprietary source**, tens of megabytes of it.
- **The cloud VM is ephemeral.** It is your own session, but it is rebuilt constantly,
  cannot hold long-lived secrets, and is the least defensible component here.

So the credentials and the corpus stay put, and the cloud VM asks the Windows machine to
act. The only real decision is **how it asks, and what it may ask for.**

### Why a git queue rather than a service

The obvious design is an HTTPS service behind a tunnel. The reason against it is that every
variant requires getting something exactly right *and keeping it right* — an authentication
check, a tunnel config, a certificate, an access policy — and a mistake in any of them is
silent, on a machine holding Google credentials.

A private git repository as a queue removes the category. Authentication is GitHub's,
maintained by people who do that for a living, and the machines only ever make outbound
connections to a host they already trust with the source code.

What it costs: one poll interval of latency (~20s), immaterial beside the one to four
minutes a real job takes; and job text living in a git history, which is why the ops repo
must be private and is worth pruning.

Two alternatives, if the trade ever changes: a **Cloudflare tunnel** with an Access service
token, if request/response latency starts to matter; or **Tailscale**, better still where
the cloud VM can run it, which a sandboxed VM generally cannot.

---

## 2. The trust boundary

Nothing listens, so the question is not "can someone break in" but **"what does an attacker
get if the ops-repo token leaks?"**

The answer is bounded by making the agent **not a shell**. It accepts four verbs, and the
caller chooses only which project and notebook — never what runs:

| Verb | Caller supplies | The operator's config fixes |
|---|---|---|
| `sync` | project, git ref | which repo, its origin, any ref allowlist |
| `refresh` | project, notebook, optionally a ref | which filters produce which outputs, the masks |
| `ask` | project, notebook, question, attachments | which notebooks the project may reach |
| `status` | project, notebook | as above |

So a leaked token lets someone **ask your notebooks questions and read the answers** —
source disclosure, and serious — but not run commands on the machine holding your Google
credentials. That gap is the entire design.

Three consequences, each enforced rather than documented:

**Nothing that arrives with a branch is executed.** The obvious implementation of `refresh`
is to run the repository's `dump.bat`. That would be a backdoor by construction: sync an
untrusted ref, then run its script. Instead the agent calls the dump tool itself, once per
`{filter, output}` entry in its own configuration.

**A job cannot name a path, a command or a repository.** It names a *project*, which the
agent resolves. Refs are matched against a narrow pattern and resolved against the
configured origin, so a ref can never be a URL or read as a git option.

**A job cannot reach an arbitrary notebook.** The notebook is caller-supplied — a project
owns several, one per architectural area — which makes it the one input selecting *which
corpus is read*. `notebook_prefix` or `allowed_notebooks` bounds it.

Everything arriving in a job file is untrusted input regardless of which repository carried
it. `src/nlmtools/ops/protocol.py` is that boundary and is tested as one: refs must be plain
names, attachment names must be plain filenames with a text-like extension, sizes are
capped, binaries refused, and a job's identity comes from its **filename** rather than a
field the caller sets.

### What remains exposed, stated plainly

Filter files are read from the repository. They are `.dumpignore`-style pattern lists, the
agent supplies the scan root, and the dump tool only walks that root — so a rewritten filter
can widen the dump *within* the repository but cannot reach outside it, and everything it
could add is content the same attacker already controls. `allowed_refs` closes even that, at
the cost of naming every branch you intend to use.

---

## 3. The pieces

| Component | Where | Owns |
|---|---|---|
| `ops/protocol.py` | shared | the job and result schema, and all validation |
| `ops/config.py` | Windows | the authorisation model: projects, notebooks, dump recipes |
| `ops/agent.py` | Windows | polling, executing, publishing, auditing |
| `ops/client.py` | cloud VM | submitting a job and waiting for its result |
| the ops repo | GitHub | the queue, and an audit trail nobody can edit silently |

`client.py` depends on nothing but the standard library and `git`, so a cloud VM runs it
with nothing installed.

### A job's life

1. The client pushes `jobs/<id>.json`.
2. The agent fetches — **rebasing, never resetting** — and skips anything that already has
   a result, so execution is exactly-once as long as one agent runs. The distinction is not
   academic: an earlier version reset the clone to the remote at the start of each pass, and
   a result whose push had lost a race with an incoming job was discarded, so the job ran
   again. The first live run showed five executions for three jobs.
3. It validates, resolves the project and notebook, and refuses **with a result** rather
   than with silence — silence would leave the caller waiting out its whole timeout.
4. It executes, one job at a time.
5. It publishes `results/<id>.json`, retrying the push and rebasing between attempts —
   losing a race with an incoming job is normal, and a result that stays unpushed leaves the
   caller waiting for exactly that file.
6. Every job is appended to the audit log, **including refusals** — a rejected attempt is
   exactly what you want to find when reviewing later.

### State

The agent remembers the notebook last used per project, so a session names it once. That is
the only state, it is a convenience, and deleting it changes nothing except that the next
job must name its notebook. A remembered name is re-checked against the project's rules on
every use, so tightening them takes effect immediately.

---

## 4. Setup

### 4.1 A private ops repository

Create an **empty private** repository — `pjanec/nlm-ops` will do. It carries questions,
answers and job metadata, so it is private for the same reason the source repo is.

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

### 4.2 Two fine-grained tokens, one per machine

In GitHub, create **two** fine-grained personal access tokens, each scoped to **only**
`nlm-ops`, with `Contents: read and write` — one for the Windows agent, one for the cloud VM.

Separate tokens so they can be revoked independently and the audit trail shows which side
did what. Neither can touch your source repositories, so a compromise of either is contained
to the queue.

> Store them as you would any password. On Windows, put the agent's token in Credential
> Manager and let git use it; never in a file inside a repository. See `CLAUDE.md`.

### 4.3 Clone on both machines

```
git clone https://github.com/pjanec/nlm-ops.git      # on each side
```

### 4.4 Configure the agent

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
      "masks":    ["HROT.", "FDP.", "Docs."],
      "notebook_prefix": "SimHost ",
      "default_notebook": "SimHost FDP review",
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

**Everything a job may *not* choose lives here.** Widening this file is the only way to widen
what the cloud VM can reach.

### 4.5 Run the agent

```
D:\WORK\NotebookLM\.venv\Scripts\python.exe -m nlmtools.ops.agent --config ops-agent.json
```

Run it under your own Windows account: the Google credentials are in *your* Credential
Manager (DPAPI is per-user), so a service account cannot reach them. For an always-on
machine, a Scheduled Task set to *run whether the user is logged on or not*, with *restart
on failure*, is the simplest arrangement. The agent reads its config at startup and refuses
to start on a bad one rather than half-working.

### 4.6 Use it from the cloud VM

`client.py` needs nothing but Python and `git` — copy the single file across, or clone this
repository.

```
# the whole update in one job: check the branch out, re-dump, reload the notebook
python client.py --ops-repo ~/nlm-ops --project simhost \
    --notebook "SimHost damage model" refresh --ref feature/my-branch

# afterwards the notebook is remembered, so it can be omitted
python client.py --ops-repo ~/nlm-ops --project simhost ask --question-file q.md
python client.py --ops-repo ~/nlm-ops --project simhost status --json
```

Each call pushes a job and waits. Expect a poll interval (~20s) plus the work: a refresh is
several minutes, an ask one to four.

---

## 5. Configuration reference

**Top level**

| Field | Meaning |
|---|---|
| `ops_repo` | local clone of the private queue |
| `nlmt` | the `nlmt` executable in the project's venv |
| `python`, `dump_tool` | the interpreter and dump script used to build bundles |
| `poll_seconds` | how often to look for work (default 20) |
| `audit_log` | append-only record of every job, refusals included |
| `state_file` | remembers the last notebook per project; beside the config by default |

**Per project**

| Field | Meaning |
|---|---|
| `repo` | the working clone to sync and dump |
| `origin` | if set, must match the clone's actual remote before a sync proceeds — catches a repository repointed locally later |
| `allowed_refs` | if set, restricts `sync` to these branch names; omit to allow any ref on origin |
| `masks` | which files a load may select and which sources it may replace. **Must be non-empty** — an empty set would let a load touch sources it did not upload |
| `dump` | one entry per slice: a `filter` from the repository, and the `output` name. The agent applies `--overwrite`, which advances the ordinal and deletes the previous generation; the tool's word-splitting keeps parts under NotebookLM's 500,000-word cap. Output lands in `<repo>\.dumps` |
| `notebook_prefix` | which notebooks this project may touch, by prefix. Name a project's notebooks consistently and new areas need no config change |
| `allowed_notebooks` | an exact list instead, if you prefer |
| `default_notebook` | used only when a job names none and none has been used yet |
| `drive_project` | the folder under `/NotebookLmTools` on Drive; keep distinct so `prune` can retire one project's bundles |

With more than one project configured, **every job must name one**. The agent refuses to
guess, because guessing would eventually load one project's sources into another's notebook.

---

## 6. Operating it

**Pause it.** Commit a file called `PAUSED` to the ops repo and the agent stops picking work
up — from either machine, without touching the Windows box. Delete it to resume.

**See what it did.** The audit log records every job with timestamp, verb, project,
notebook, ref, question size, attachment names and outcome. The ops repo's git history is a
second, independent record.

**Rotate a token** by revoking it in GitHub and re-authenticating that side. Nothing else
changes.

**Prune the queue.** Jobs and results accumulate and contain proprietary text. Delete old
ones periodically; if that matters, rewrite the history rather than only deleting files,
since git keeps them otherwise.

---

## 7. When it misbehaves

| Symptom | Cause |
|---|---|
| no result at all | the agent is stopped, or `PAUSED` is present |
| every job refused | a validation rule; the `error` field names it exactly |
| `must name one` | several projects configured; pass `--project` |
| `unknown project` | missing config entry, or the agent was not restarted |
| `no notebook given and none remembered` | pass `--notebook` once, or set `default_notebook` |
| `notebook ... is not allowed` | outside `notebook_prefix` / `allowed_notebooks` |
| `origin is ... expected ...` | the clone's remote does not match the configured origin |
| `filter ... is missing` | the filter file is not on the currently synced ref |
| `ask` exits 11 | the NotebookLM session could not be renewed; a human must run `nlm login` on Windows |
| `refresh` exits 13 | content did not survive ingestion — do not trust the notebook until it is understood |
| results appear twice | **two agents against one ops repo.** Run only one |

That last one is a convention rather than a guarantee: two agents would both pick up the
same job and both execute it. Enforcing it would mean claiming a job with a marker commit
before working — straightforward, and worth doing if a second machine is ever added.

---

## 8. Deliberate non-goals

- **No arbitrary commands.** There is no verb for it, no parameter that becomes one, and
  nothing arriving with a synced branch is executed.
- **No arbitrary file reads.** `ask` returns an answer; `status` returns source titles.
- **No streaming.** A job returns when it is done. The work is minutes, not hours.
- **No second file transport.** Files move by git, between repositories that already exist.
- **No credentials on the cloud VM.** It holds one token, scoped to the queue, which can
  touch neither a source repository nor Google.
- **No MCP exposure.** The MCP server has no authentication; anyone reaching it controls the
  whole Google account. It must never be published through any tunnel — see `design.md` §14.
