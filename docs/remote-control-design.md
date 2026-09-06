# Remote NotebookLM control — design

How a Claude session on an ephemeral Linux VM gets work done on an always-on Windows
machine that holds credentials it must never be given.

Companions: [`remote-control-setup.md`](remote-control-setup.md) installs this once per
machine; [`onboarding-a-project.md`](onboarding-a-project.md) adds a project to a working
installation.

---

## 1. The problem

The pieces are fixed by circumstance, not by choice:

- **NotebookLM has no official API.** Access needs browser cookies, refreshed from a real
  Chrome profile, on a machine where someone once signed in.
- **Google Drive access needs an OAuth token** bound to that machine's Windows account
  through DPAPI, so it cannot be copied to a cloud VM even if that were wise.
- **The corpus is proprietary source**, tens of megabytes of it, which nothing but the
  operator's own machines should hold.
- **The cloud VM is ephemeral and untrusted-ish.** It is the operator's own session, but it
  is rebuilt constantly, cannot hold long-lived secrets, and is the least defensible
  component in the system.

So the credentials and the corpus stay on the Windows machine, and the cloud VM asks it to
act. The design question is entirely about **how it asks, and what it is allowed to ask
for**.

## 2. Why a git queue and not a service

The obvious answer is an HTTPS service behind a tunnel. The reason not to is that every
variant of it requires getting something exactly right *and keeping it right*: an
authentication check, a tunnel configuration, a certificate, an access policy. Each is a
place where a mistake is silent and the consequence is a machine holding Google credentials
exposed to the internet.

A private git repository as a job queue removes the category:

```
   Claude cloud VM                private ops repo              Windows always-on
   (Linux, ephemeral)              (GitHub, private)            (credentials, corpus)

     client.py  ──push job──►    jobs/<id>.json     ◄──poll──   agent
                                                                  │ sync / refresh / ask
     client.py  ◄──poll result── results/<id>.json  ──push──►   ──┘
```

Both machines make only **outbound** connections, to a host they already trust with the
source code. There is no port, no tunnel, no certificate, and nothing to find by scanning.
Authentication is GitHub's, which is maintained by people who do that for a living.

What it costs: latency of one poll interval (~20s), which is immaterial beside the one to
four minutes a real job takes, and job text living in a git history, which is why the ops
repo must be private and is worth pruning.

Alternatives were considered and rejected for this setting — a Cloudflare tunnel with an
Access token is a reasonable second choice if request/response latency ever matters, and
Tailscale is better still where the cloud VM can run it, which a sandboxed VM generally
cannot.

## 3. The trust boundary

The security question is not "can someone break in" — nothing listens — but **"what does an
attacker get if the ops-repo token leaks?"**

The answer is bounded by making the agent *not a shell*. It accepts four verbs, and the
caller chooses only which project and notebook, never what runs:

| Verb | Caller supplies | Operator's config fixes |
|---|---|---|
| `sync` | project, git ref | which repo, its origin, any ref allowlist |
| `refresh` | project, notebook | which filters produce which outputs, the masks |
| `ask` | project, notebook, question, attachments | which notebooks the project may reach |
| `status` | project, notebook | as above |

So a leaked token means someone can **ask your notebooks questions and read the answers** —
source disclosure, and serious — but cannot run commands on the machine holding your Google
credentials. That gap is the whole design.

Three consequences follow, and each is enforced rather than documented:

**Nothing that arrives with a branch is executed.** The obvious implementation of `refresh`
is to run the repository's `dump.bat`. That would be a backdoor by construction: sync an
untrusted ref, then run its script. Instead the agent calls the dump tool itself, once per
`{filter, output}` entry in its own configuration.

**A job cannot name a path, a command or a repository.** It names a *project*, which the
agent resolves to a repository. Refs are matched against a narrow pattern and resolved
against the configured origin, so a ref can never be a URL or read as a git option.

**A job cannot reach an arbitrary notebook.** Because the notebook is caller-supplied — a
project owns several, one per architectural area — it is the one input that selects *which
corpus is read*. `notebook_prefix` or `allowed_notebooks` bounds it, so one project's token
cannot query another's notebook.

Everything arriving in a job file is treated as untrusted input regardless of which
repository carried it. `ops/protocol.py` is that boundary and is tested as one.

### What remains exposed, stated plainly

Filter files are read from the repository. They are `.dumpignore`-style pattern lists, the
agent supplies the scan root, and the dump tool only walks that root — so a rewritten filter
can widen the dump *within* the repository but cannot reach outside it, and everything it
could add is content the same attacker already controls. `allowed_refs` closes even that,
at the cost of naming every branch you intend to use.

## 4. The pieces

| Component | Where | Owns |
|---|---|---|
| `ops/protocol.py` | shared | the job and result schema, and all validation |
| `ops/config.py` | Windows | the authorisation model: projects, notebooks, dump recipes |
| `ops/agent.py` | Windows | polling, executing, publishing, auditing |
| `ops/client.py` | cloud VM | submitting a job and waiting for its result |
| the ops repo | GitHub | the queue, and an audit trail nobody can edit silently |

`client.py` depends on nothing but the standard library and `git`, so a cloud VM can run it
with nothing installed.

### A job's life

1. The client pushes `jobs/<id>.json`. The **filename is the job's identity** — a caller
   cannot name the result file something else by setting a field.
2. The agent fetches, and skips everything that already has a result. Execution is therefore
   exactly-once as long as one agent runs.
3. It validates, resolves the project and notebook, and refuses with a result rather than
   with silence. Silence would leave the caller waiting out its whole timeout.
4. It executes, one job at a time, and the notebook lock serialises anything that would
   collide anyway.
5. It publishes `results/<id>.json` and pushes, rebasing if the other side pushed meanwhile.
6. Every job is appended to the audit log, **including refusals** — an attempt that was
   rejected is exactly what you want to find when reviewing later.

### State

The agent remembers the notebook last used per project, so a session names it once. That is
the only state, it is a convenience, and deleting it changes nothing except that the next
job must name its notebook. A remembered name is re-checked against the project's rules on
every use, so tightening them takes effect immediately.

## 5. Failure modes worth knowing

| Situation | Behaviour |
|---|---|
| agent stopped or `PAUSED` present | jobs queue up; the client times out and says which |
| NotebookLM session expired | renewed silently and headlessly; exit 11 only if that fails |
| a job is malformed or forbidden | a result explaining the refusal, and an audit entry |
| both sides push at once | whoever loses rebases and retries |
| **two agents on one ops repo** | both execute the same job; run only one |
| ingestion damaged the content | exit 13 from `load`, surfaced in the result |

The double-agent case is a convention rather than a guarantee. Enforcing it would mean
claiming a job with a marker commit before working; that is straightforward, and worth doing
if a second machine is ever added.

## 6. Deliberate non-goals

- **No streaming.** A job returns when it is done. Progress would need a channel this design
  does not have, and the work is minutes rather than hours.
- **No file transport.** Files move by git, between repositories that already exist. A
  second way to move bytes would be a second thing to secure.
- **No MCP exposure.** The MCP server has no authentication; anyone reaching it controls the
  Google account. It must never be published through any tunnel — see `design.md` §14.
- **No credentials on the cloud VM.** It holds one token, scoped to the queue, which cannot
  touch a source repository or Google.
