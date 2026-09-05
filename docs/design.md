# NotebookLM Tools — Design

**Status:** living specification. Authoritative. Supersedes
`requirements-original-brief.md` wherever the two differ (see §11).

**Target host:** Windows 11, always-on, single operator, single Google account.
Must be reproducible on a different Windows machine by running the setup script.

---

## 1. What this automates

A proven manual workflow, unchanged in substance:

```
local .txt bundle  ->  Google Drive (plain text, byte-identical)  ->  NotebookLM sources
```

The operator does this by hand today and it works. The tools reproduce it mechanically.

### 1.1 The three facts that constrain everything

1. **A `.txt` is never uploaded to NotebookLM as a local file.** NotebookLM's own text
   ingestion strips function bodies out of bundled source code, keeping only headers and
   signatures. The damage is silent: sources present, answers fluent, code gutted. Routing
   through Drive avoids it. Sources are therefore **always** added with
   `source_type="drive"` and a Drive file ID, never `source_type="file"`.

2. **Sources are import-time copies. There is no sync.** A source does not stay linked to
   its Drive file. Refreshing content means deleting the old sources and adding new ones.
   Drive freshness and any `source_sync_drive` facility are irrelevant.

3. **Indexing takes minutes.** The web UI shows a per-source spinner. The notebook must
   not be queried until every source in the batch has finished (§7).

### 1.2 No format conversion, ever

Files are stored on Drive as **plain `text/plain`**, byte-for-byte identical to the local
file, BOM preserved where present. They are **not** converted to native Google Docs.

This overturns §4.2 of the original brief, which specified conversion to
`application/vnd.google-apps.document`. That was wrong for this workflow: a Doc conversion
cannot preserve a BOM, caps at ~1.02M characters (the real chunks are ~1 MB each, right on
that ceiling), and would reintroduce exactly the class of silent corruption that fact 1
exists to prevent. The operator's manual process never converted; neither does this.

Consequence: fidelity verification is a **checksum comparison**, not a semantic diff. If
the MD5 of the remote file matches the local file, the content is provably intact.

---

## 2. Scope

**In:** upload a local `.txt` bundle to Drive; load it into a named notebook as Drive
sources; wait for indexing; ask long questions via a temporary question source; prune old
Drive bundles; generate test fixtures.

**Out:** web UI, daemon or watcher, Studio artifacts, multi-user support, any network
listener, chunk generation from real source code (an upstream concern).

---

## 3. Design principles

1. **Names are the only interface.** Inputs are a notebook *title*, a local folder path,
   and a project name. Notebook IDs, Drive file IDs and source IDs are never accepted as
   input and never required from the operator. They may be cached, and they appear in
   output only in the exit-14 ambiguity message, where they are the only way to tell two
   same-named things apart.
2. **Dumb tool, smart caller.** The tools decide nothing about *what* to load or *what* to
   ask. They run from a shell by hand and are equally drivable by an AI agent, so output is
   a contract, not prose (§5.3).
3. **No persisted state beyond a cache.** Everything is derivable from three listings: the
   local folder, the Drive bundle folder, and the notebook's sources. Any cache is a
   latency optimisation, deletable at any moment without changing behaviour. Tested (M8).
4. **Masks scope every operation.** A batch is a set of `starts-with` prefixes plus `.txt`.
   Only matching files and sources are ever touched. **The notebook is not a mirror** —
   sources outside the mask set are left alone, always.
5. **Ambiguity is a hard stop.** Two notebooks with the target title means the tool cannot
   know what was meant. Exit 14 with both IDs; never guess.
6. **Whole-run rerun is the recovery path.** No partial-resume logic. A failed run is fixed
   by running it again, and doing so must converge.
7. **`Q<n>-` is reserved** for ephemeral ask sources (§8). Any source title matching
   `^Q\d+-` is excluded from every mask operation in `load`, always, even if a configured
   mask would otherwise match it. Do not name corpus files that way.
8. **Secrets never touch a tracked file, and never sit in plaintext on disk** (§4, and the
   repo-wide rule in `CLAUDE.md`).

---

## 4. Credentials and secret storage

### 4.1 Google Drive — rclone

Drive access is via **rclone**, not a hand-rolled Google API client.

Rationale: rclone ships a pre-registered, Google-verified OAuth client, so there is **no
Cloud console setup at all** — the operator authorizes rclone against their normal personal
Google account in a browser, once. Because rclone's client is published rather than in
"Testing" status, the refresh token does not expire after 7 days as an unverified
self-registered client's would. `rclone check --checksum` also provides the MD5
verification §1.2 requires, correctly, for free.

Files land in the operator's ordinary personal Drive and are fully visible and deletable in
the Drive web UI.

Scope: `drive.file` — rclone may only touch files it created. Sufficient because the tool
creates and owns its folders (§6.1), and it means the token cannot read the rest of the
operator's Drive.

### 4.2 NotebookLM — nlm

`notebooklm-mcp-cli` (the `nlm` CLI and `notebooklm-mcp` MCP server) drives Google's
**internal**, undocumented NotebookLM endpoints using browser cookies.

```
nlm login            # browser login; extracts cookies, saves a persistent profile
nlm login --check
nlm doctor
```

Cookies last roughly 2-4 weeks and auto-refresh from the saved profile; a fully expired
Google session needs an interactive `nlm login`. That profile is managed by `nlm` itself,
lives in its own directory outside the repo, and is gitignored.

### 4.3 Secret storage

**Nothing sensitive is stored in plaintext on disk, and nothing sensitive is ever written
inside the repository.**

- `rclone.conf` is **encrypted** with a random password generated at setup time.
- That password lives in **Windows Credential Manager** (DPAPI-backed, bound to the
  operator's Windows account) under service `NotebookLmTools`, accessed via `keyring`.
- At runtime the password is read from Credential Manager and passed to the rclone child
  process via the `RCLONE_CONFIG_PASS` environment variable. It is never written to disk,
  never logged, and never appears in the JSON envelope.
- The `nlm` cookie profile is managed by that tool; it stays outside the repo and is
  gitignored. Its on-disk form is not under our control.

Because DPAPI is machine- and account-bound, moving to another Windows machine requires one
interactive re-login there. That is inherent to not keeping portable secrets on disk, and
is the intended tradeoff.

### 4.4 Failure must be legible

One distinct, greppable exit code per class. The operator often reads this from a phone.

| Exit | Class | Meaning | Operator action |
|---|---|---|---|
| 0 | — | success | — |
| 1 | `INTERNAL` | unexpected exception | file a bug with the envelope |
| 10 | `DRIVE_AUTH` | rclone cannot authenticate to Drive | re-run `setup.ps1` |
| 11 | `NLM_AUTH` | NotebookLM cookies invalid or expired | `nlm login` on the host |
| 12 | `NLM_QUOTA` | NotebookLM rate/quota limit | wait |
| 13 | `FIDELITY` | remote checksum != local checksum | do not proceed; investigate |
| 14 | `AMBIGUOUS` | duplicate notebook title | resolve by hand in the UI |
| 15 | `MISMATCH` | live source count != expected after load | rerun |
| 16 | `NOT_READY` | indexing did not finish within the ceiling | wait, then `nlmt status` |
| 17 | `LOCKED` | another run holds this notebook's lock | wait for it to finish |
| 18 | `EMPTY_SELECTION` | mask matched no files | fix the mask or the folder |
| 19 | `USAGE` | bad or missing arguments | fix the command line |

Codes 17, 18 and 19 are additions; the brief reused 14 for a held lock, which is a
different condition with a different remedy and violated its own "one exit per class" rule.

A Drive failure must never surface as a traceback that looks like a NotebookLM failure.

---

## 5. Interface

### 5.1 Command surface

A single entry point, `nlmt`, dispatching subcommands. One tool per subpackage; shared
machinery in `common` (§10).

| Command | Purpose |
|---|---|
| `nlmt load` | the full pipeline: select -> upload -> load -> wait (§6) |
| `nlmt status` | resolve a notebook, enumerate its sources, report mask-matching set and readiness |
| `nlmt ask` | long-question protocol (§8) |
| `nlmt delete` | delete sources matching a mask — the same primitive `load` uses (§5.6) |
| `nlmt sweep` | delete **every** ask source (`Q<n>-*`) and any stray `_ask` Drive folder |
| `nlmt prune` | list and delete old Drive bundle folders (§6.1) |
| `nlmt doctor` | verify both auths and assert the two Google identities match (M0) |
| `nlmt gen-fixtures` | generate synthetic test bundles (§9) |
| `nlmt help [topic]` | concept help: `exit-codes`, `masks`, `secrets`, `readiness`, `workflow` |
| `nlmt --ai` | the complete agent-oriented reference, on stdout, in one shot (§5.5) |

`status` and `doctor` were named but never specified in the brief; `prune`,
`gen-fixtures`, `help` and `--ai` are new.

### 5.2 Arguments over configuration

Every input is a command-line argument. A config file is optional and is itself named by an
argument. **Precedence: command line > config file > built-in default.**

| Argument | Meaning |
|---|---|
| `--notebook TITLE` | target notebook, by name; created if absent |
| `--local-folder PATH` | folder holding the `.txt` bundle |
| `--mask PREFIX` | repeatable `starts-with` prefix; default: every `.txt` except `Q-` |
| `--project NAME` | names the Drive subtree and the answers/cache namespace |
| `--drive-remote NAME` | rclone remote name (default `nlmtools`) |
| `--drive-root PATH` | Drive root for this tool (default `/NotebookLmTools`) |
| `--cache-dir PATH` | where the deletable cache lives |
| `--config PATH` | optional TOML; lowest priority |
| `--json` | emit the machine envelope on stdout |
| `--drive-only` | stop after upload and verification; do not touch the notebook |
| `--dry-run` | report the plan; change nothing |
| `--ready-timeout S` | readiness ceiling (default 1200) |
| `--poll-interval S` | readiness poll period (default 15) |
| `--smoke-question Q` / `--smoke-expect S` | fallback readiness probe (§7.2) |
| `--question TEXT` / `--question-file PATH` | (`ask`) the question body; stdin if neither is given |
| `--attach FILE` | (`ask`) repeatable; a text-like data file the question refers to (§8.2) |
| `--keep` | (`ask`) do not delete the question and attachment sources |
| `--log-level LEVEL` | stderr verbosity |

### 5.3 Output contract

Human-readable text is the default. `--json` emits one envelope, and **nothing else, on
stdout**; all logs and progress go to stderr. Exit codes are the coarse signal, the
envelope carries detail.

```json
{
  "ok": true,
  "action": "load",
  "notebook": "Engine - locking review",
  "notebook_created": false,
  "bundle": "/NotebookLmTools/engine/2026-09-05T14-30-02Z",
  "counts": {"selected": 20, "uploaded": 20, "verified": 20, "deleted": 20, "added": 20},
  "ready": true,
  "readiness_signal": "per_source_status",
  "elapsed_s": 412,
  "warnings": [],
  "error": null
}
```

On failure:

```json
{
  "ok": false,
  "error": {
    "code": 14,
    "class": "AMBIGUOUS",
    "message": "two notebooks are titled 'Engine - locking review'",
    "hint": "rename or delete one in the NotebookLM web UI, then rerun",
    "detail": {"notebook_ids": ["...", "..."]}
  }
}
```

`hint` is mandatory on every error and states the **corrective action**, so a caller that
never read the documentation can still recover (§5.5). The `detail` object is the one place
internal IDs surface (§3.1).

Field names never change meaning between versions — add fields, do not repurpose them. A
partial failure still emits a complete envelope with accurate counts, so a caller can see
how far it got.

### 5.4 Division of labour

The tools own Drive and source management and know nothing about the content of questions
or answers. An AI agent owns querying, via the MCP server, and calls these tools as a
subprocess when files need moving.

The long-question protocol (§8) straddles that line: it is a query concern that manipulates
sources. It lives here as `nlmt ask`, implemented once and correctly, so an agent calls it
instead of improvising `source_add` + `source_delete` through MCP. An agent doing it by
hand is exactly how question sources get stranded in a notebook.

### 5.5 Help is a feature, not documentation

These tools are driven by an AI agent as often as by a human, and an agent has no manual
and no memory of yesterday's session. It learns the tool from two places: the help text,
and the error it just got back. Both are therefore part of the product, held to the same
standard as the code, and tested (M8).

**Every level responds to `--help`.** `nlmt --help` lists the subcommands with a
one-line purpose each. `nlmt <cmd> --help` documents every argument with its type,
default, whether it is required, how it interacts with the config file, and **at least one
complete worked example** that could be pasted into a shell unchanged. No argument is
documented by restating its own name.

**`nlmt --ai` prints the whole reference at once** — every subcommand, every argument, the
exit-code table, the output envelope schema, the reserved `Q-` prefix, the mask semantics,
and the workflow the tools automate. One dense document on stdout, written for a model
rather than for a terminal reader, so an agent can orient itself in a single call instead
of probing subcommand by subcommand. The `nlm` CLI has the same convention; this mirrors it
deliberately.

**Failure teaches.** A usage error (exit 19) prints what was wrong, what was expected, and
a correct example — then the relevant subcommand help in full, not a one-line "invalid
argument". An unknown or misspelled argument or subcommand suggests the nearest valid one.
Every error, in text and in JSON alike, carries the `hint` field from §5.3 naming the
corrective action. The rule of thumb: **a caller who has never read the docs should be able
to reach a correct command from any error message the tools can produce.**

**`nlmt help <topic>`** covers the concepts that no single argument owns — the exit-code
table, mask semantics and the `Q-` reservation, where secrets live, what readiness means
and why queries must wait for it, and the end-to-end workflow.

**Machine-readable introspection.** `nlmt --help --json` emits the command and argument
schema as structured data, so an agent can enumerate the interface without parsing prose.

**Help never lies.** Argument help is generated from the same definitions the parser uses,
so the two cannot drift. Any behaviour change that is not reflected in help is an
incomplete change.

### 5.6 `nlmt delete`

`nlmt delete --notebook X --mask PREFIX` deletes the notebook's sources whose title starts
with any given mask. It is the same delete-by-mask primitive `load` uses in step 9, exposed
directly, so an ask's sources can be removed by hand:

```
nlmt delete --notebook "Engine - locking review" --mask Q7-
```

- At least one `--mask` is required. An empty or missing mask is exit 19, never "delete
  everything".
- `--dry-run` lists what would go without touching anything.
- Unlike `load`, this command *may* target `Q<n>-` sources — that is its main use. The §3.7
  exclusion protects them from `load`'s automatic delete-by-mask, not from a deliberate one.

Deletion is low-stakes by construction: corpus sources are always restorable by rerunning
`load` from the local files, and ask sources are ephemeral by design.

---

## 6. `load`

```
 1. parse and validate arguments                       -> exit 19 on bad usage
 2. acquire the notebook lock                          -> exit 17 if genuinely held
 3. select files: mask, *.txt, non-recursive, no Q-    -> exit 18 if empty
 4. create a fresh Drive bundle folder                 (§6.1)
 5. rclone copy the selected files into it             -> exit 10 on auth failure
 6. rclone check --checksum against the local folder   -> exit 13 on any mismatch
 7. resolve the uploaded files' Drive IDs
 8. resolve the notebook by title; create if absent    -> exit 14 on duplicates (§6.2)
 9. delete existing sources matching the masks         (§6.3)
10. add every uploaded file as a Drive source          (§6.4)
11. enumerate; assert one source per file              -> exit 15 on any difference
12. wait for indexing                                  -> exit 16 on timeout (§7)
13. release the lock; emit the envelope
```

Steps 4-6 complete before step 9 touches the notebook: nothing is deleted until the new
content is on Drive and provably intact. This differs from the brief, which deleted first;
verifying before destroying is strictly safer and costs nothing.

`--drive-only` stops after step 7.

### 6.1 Drive layout

```
/NotebookLmTools/<project>/<UTC-timestamp>/<file>.txt
```

One folder per bundle, created fresh on every load. Old bundles accumulate deliberately and
are removed by `nlmt prune` or by hand in the Drive web UI.

Because every bundle folder is new, filenames never collide, so the brief's
overwrite-by-name logic and its duplicate-Drive-name exit-14 case do not exist. This also
retires the brief's M5 assertion that a file ID is preserved across reloads — with a folder
per bundle, a reloaded file is a new file with a new ID, by design.

The notebook still holds exactly **one** version of the bundle: step 9 deletes the previous
sources before step 10 adds the new ones. Drive accumulates; the notebook does not.

### 6.2 Notebook by name

List notebooks, match on exact title. Zero matches -> create, and report
`notebook_created: true`. **Two or more matches -> exit 14**, with both IDs in
`error.detail`; NotebookLM permits duplicate titles and the tool must not pick one.

### 6.3 Delete by mask

Enumerate the notebook's sources and delete those whose title starts with any active mask.
Never delete anything else — not unmatched sources, not `Q-` sources, not sources the
operator added by hand. This is principle 3.4 and is tested directly (M5).

Source titles are expected to derive from the Drive filename. Whether the `.txt` extension
survives into the title is a **recon item** (§12); the matcher must be confirmed against
real titles in M4 before this step is trusted.

### 6.4 Add sources

```
source_add(notebook_id, source_type="drive", document_id=<drive file id>, ...)
```

Never `source_type="file"` (§1.1). If `source_add` accepts a list of document IDs, load the
batch in one call, mirroring the web picker; otherwise loop. Which of those applies is a
recon item (§12).

---

## 7. Readiness

Two possible signals, in order of preference.

### 7.1 Per-source status (preferred)

If enumeration exposes a processing state — the data behind the web UI's spinner — poll
until every source in the batch reports ready. Poll every `--poll-interval` (default 15s),
ceiling `--ready-timeout` (default 20 min), then exit 16.

### 7.2 Smoke query (fallback)

If no status field exists, run `--smoke-question` and check the answer contains
`--smoke-expect`. The question must be answerable only from **inside a function body**, so
that it re-verifies §1.1's core property on every load. Back off 30s / 60s / 120s / 240s,
then exit 16.

Either way, log the observed indexing time per run in `NOTES.md`. `ask` refuses to run
while the notebook's lock is held.

---

## 8. Long questions — `nlmt ask`

A long question is often more than text: it references **attached data files** — a JSON
export, a CSV, a log extract — that the question asks about. Both the question and its
attachments are temporary sources, created for one query and removed afterwards.

```
 1. acquire the notebook lock (not merely check it)
 2. n = next free ask ordinal for this notebook                 (§8.2)
 3. for each --attach FILE, i = 1..k:                           (§8.2)
      upload to /NotebookLmTools/<project>/_ask/Q<n>/<name>.txt
      verify by checksum                                        -> exit 13 on mismatch
      source_add(nb, source_type="drive", document_id=<id>,
                 title="Q<n>-a<i>-<name>.txt")                  -> attachment source
 4. source_add(nb, source_type="text", title="Q<n>-q-<slug>",
               text=<long question body>)                       -> question source
 5. wait for every source created in 3 and 4 to finish indexing (§7)  -> exit 16
 6. notebook_query(nb, <generated prompt>)                       (§8.3)
 7. save the answer
 8. delete every source created in 3 and 4, and the _ask/Q<n>/
    Drive folder                                     # in a finally block
 9. release the lock
```

### 8.1 The question text bypasses Drive; attachments do not

The **question body** is added directly as `source_type="text"`. That is correct and *not*
a violation of §1.1: the rule there forbids `source_type="file"`, a **local file upload**,
because NotebookLM's file ingestion strips function bodies out of source code. A question
is prose instructions, has no bodies to lose, and is deleted minutes later. Routing it
through Drive would buy a round-trip and a stray Drive file for nothing.

**Attachments are different and go through Drive, exactly like the corpus.** An attached
JSON or CSV is *data whose exact content is the point of the question* — a dropped array
element or a truncated field produces a confidently wrong answer, which is the same silent
failure mode §1.1 exists to prevent. Attachments therefore take the proven path: Drive,
plain text, checksum-verified, added as `source_type="drive"`. The cost is one upload and
one delete; the alternative is trusting an unverified ingestion path with the data the
answer depends on.

### 8.2 Extension and naming

NotebookLM will not accept arbitrary file types, so an attachment is uploaded with `.txt`
appended to its **full original filename** — `config.json` becomes `config.json.txt`. The
bytes are unchanged; only the name gains a suffix. Keeping the original name visible
matters, because the question body refers to the file by the name the operator or agent
knows it by.

Every source belonging to one ask shares a short **ordinal prefix** `Q<n>-` (§3.7):

```
Q7-q-locking-review        the question body
Q7-a1-config.json.txt      first attachment
Q7-a2-metrics.csv.txt      second attachment
```

The point of the ordinal is that **`Q7-` is a mask**, so the whole ask is removable with the
same delete-by-mask primitive everything else uses — `nlmt delete --mask Q7-` (§5.6) —
without the operator having to look anything up or type a timestamp from a phone.

The `q-` and `a<i>-` markers say at a glance what each source is, and keep an attachment
that happens to be named `question.txt` from colliding with the question body. The
attachment keeps its original filename after that marker, because §8.3 has to map it back
to the name the question body uses.

**Allocating the ordinal.** `n` is one greater than the highest ordinal already present
among the notebook's `^Q\d+-` sources, or 1 if there are none. It is derived from a
listing, so it keeps principle 3.3 — no persisted state — and the notebook lock makes the
read-then-allocate safe. Ordinals are reused after an ask is deleted; that is harmless,
because the durable record of an ask is its transcript in `answers/`, whose filename
carries the full timestamp.

Attachments must be text-like (JSON, CSV, log, plain text). A binary file is refused with
exit 19 and a hint; embedding binaries is out of scope.

### 8.3 The generated prompt

The query prompt is generated, not written by the caller, and must **bridge the naming
gap**: the question body refers to `config.json`, while the notebook knows a source titled
`Q7-a1-config.json.txt`. Without an explicit mapping the model cannot reliably
connect the two.

```
Answer the question in source "Q7-q-locking-review".
The data it refers to is attached as these sources:
  config.json   -> source "Q7-a1-config.json.txt"
  metrics.csv   -> source "Q7-a2-metrics.csv.txt"
Use the other sources in this notebook as evidence.
```

### 8.4 Notes

- Step 1 **takes** the lock; the brief only had `ask` refuse when one was held, which would
  let a concurrent `load` delete the corpus out from under a running question.
- Step 5 is a full readiness wait (§7), not merely `wait=True` on the add. Attachments are
  real sources and must finish indexing before the query, for the same reason a batch must.
- The question body comes from `--question TEXT`, `--question-file PATH`, or stdin.
  `--attach FILE` is repeatable.
- Attachments count against the notebook's source limit while the ask is in flight.
- Step 8 is in `finally`, and cleans up **both** the notebook sources and the `_ask` Drive
  folder. An abandoned question or attachment source pollutes retrieval for every later
  answer in that notebook. `--keep` skips it when debugging — and because the ask is a
  single mask, cleaning up afterwards is just `nlmt delete --mask Q7-`. `nlmt sweep` is the
  blunt version: every `Q<n>-` source and every leftover `_ask/*` Drive folder.
- The envelope reports the allocated ordinal as `ask: "Q7"`, so a caller that used `--keep`
  knows the mask to delete later without having to enumerate sources.
- Default query budget 120s wall-clock. For heavy questions over a 20-source corpus, use
  `notebook_query_start` + poll `notebook_query_status` rather than raising the sync
  timeout.
- Answers are written to `answers/<notebook-slug>/<timestamp>-Q<n>-<slug>.md` with the
  question body, the list of attachments, the answer, and the live source list at the time
  of asking. The timestamp is what makes the record durable, since ordinals get reused.
  `answers/` is gitignored — transcripts contain source excerpts.
- Question and attachment sources live in the same notebook as the corpus; a separate
  notebook cannot see the corpus, so the question could not be answered there.
- **Chat history caveat:** reloading a bundle breaks citations in older chat sessions that
  pointed at now-deleted sources. Expected, not a bug. Documented in `README.md`.

---

## 9. Test fixtures — `nlmt gen-fixtures`

No real source data is used for development. The generator produces synthetic bundles that
are structurally realistic:

- N files of configurable size, named to a bundle convention with a batch number.
- Content shaped like bundled source: file headers, class and function declarations, and
  **function bodies containing facts that appear nowhere else** — specific constants,
  thresholds, retry limits.
- A `manifest.json` per bundle listing every buried fact as a question/expected pair, so
  the smoke probe (§7.2) is derived from ground truth rather than hand-picked, and a
  gutted-body failure is detected automatically.
- A mix of BOM and non-BOM UTF-8 files, since both occur in the real data and both must
  round-trip byte-identically.
- Size knob: small by default for fast iteration, scalable to 20 x 1 MB for M9.

---

## 10. Repository layout

```
NotebookLmTools/
  CLAUDE.md               # agent rules: secrets, interaction style
  README.md               # how to install and run; written last
  NOTES.md                # running log: API surprises, timings, decisions
  setup.ps1               # idempotent installer (10.1)
  pyproject.toml
  requirements.lock       # pinned, for reproducibility
  docs/
    README.md             # which document means what
    requirements-original-brief.md   # frozen
    design.md             # this file
  src/nlmtools/
    common/               # args, config merge, envelope, exit codes, locking, secrets, logging
    loader/               # select, drive (rclone wrapper), notebook, readiness
    ask/                  # long-question protocol
    fixtures/             # synthetic bundle generator
    cli.py                # dispatcher
  tools/                  # pinned rclone.exe, fetched by setup
  tests/
  answers/                # gitignored
  .venv/                  # gitignored
```

Subpackage per tool, shared machinery in `common`, as the operator asked.

### 10.1 `setup.ps1`

Idempotent, re-runnable at any time, and the sole install path — nothing is ever installed
ad hoc. It is also the transfer mechanism to a new Windows machine.

```
1. locate a Python >= 3.11 (do not assume a location; validate the version)
2. create .venv if absent; never reuse a system site-packages install
3. install pinned dependencies from requirements.lock into .venv
4. install notebooklm-mcp-cli into .venv
5. fetch the pinned rclone.exe into tools/ if absent; verify its checksum
6. if no rclone remote is configured: generate a random config password, store it in
   Windows Credential Manager, and run the interactive rclone OAuth flow
7. if nlm is not logged in: run the interactive nlm login
8. run `nlmt doctor` and report
```

Every step is a no-op when already satisfied. Steps 6 and 7 are the only interactive ones,
and only on first run or after credentials expire.

### 10.2 Locking

`locks/<notebook-slug>.lock`, written with the owning PID and start time. A lock whose PID
is no longer alive is stale and is reclaimed automatically, so the recovery path in
principle 3.6 works after a killed run (M8). A genuinely held lock is exit 17.

Locks are per notebook, so different notebooks can be loaded in parallel.

---

## 11. Deviations from the original brief

| Brief | This design | Why |
|---|---|---|
| §4.2 convert to Google Docs | plain `text/plain`, no conversion | operator's proven manual process never converted; a Doc cannot preserve a BOM and caps at ~1.02M chars against ~1 MB chunks |
| §4.2 export round-trip diff, exit 13 on semantic damage | MD5 checksum comparison | with no conversion, byte-identity is provable rather than judged |
| §3.1 Google Drive API, own OAuth client, full `drive` scope | rclone, `drive.file` scope | no Cloud console setup; rclone's client is verified so refresh tokens do not expire after 7 days as an unverified "Testing" app's would |
| §3.1 `token.json` / `credentials.json` on disk | encrypted `rclone.conf`, password in Windows Credential Manager | operator requirement: no plaintext secrets on disk |
| §2 `config.toml` is the interface | CLI arguments are the interface; config optional and lowest priority | operator requirement |
| §1 masks carry a batch number; batches accumulate | masks are batch-agnostic; the notebook holds one bundle | operator requirement |
| §4.2 flat Drive folder, overwrite by name | folder per bundle, `prune` to remove old ones | operator wants old bundles removable as maintenance; also eliminates Drive name collisions |
| §4 delete sources, then upload | upload and verify, then delete sources | never destroy before the replacement is proven intact |
| §4 lock conflict is exit 14 | exit 17 `LOCKED` | 14 is `AMBIGUOUS`; different condition, different remedy |
| §4 empty selection has no code | exit 18 `EMPTY_SELECTION` | the brief mandates the refusal and tests it, but never assigned a code |
| §6 `ask` refuses if the lock is held | `ask` takes the lock | otherwise a concurrent `load` can delete the corpus mid-question |
| §6 a question is text only | a question may carry `--attach`ed data files, uploaded via Drive as `<name>.txt` (§8) | operator requirement; attachment data is what the answer depends on, so it takes the verified path, not an unproven one |
| §0.3.8 reserves the literal prefix `Q-` | reserves `Q<n>-`, an ordinal per ask, plus a `nlmt delete --mask` command | one ask is then a single short mask, deletable by hand with the same primitive `load` uses, without typing a timestamp |
| M5 asserts a preserved Drive file ID | dropped | meaningless under folder-per-bundle |
| M1 uses three real chunks | synthetic generator with a ground-truth manifest | no real data available yet |
| §2 `uv tool install` | install into the project venv | self-contained and transferable |

---

## 12. Open reconnaissance items

To be answered against the **installed** `nlm` before §6 and §7 are finalised, and recorded
in `NOTES.md`. Where the installed tool contradicts this document, the tool wins.

1. Does `source_add` accept **multiple** document IDs in one call (§6.4)?
2. Does source enumeration expose an **index/processing status** (§7.1)? If not, §7.2 is
   the readiness signal.
3. Does `source_add` with `source_type="drive"` accept a **plain `.txt`** Drive file, or
   only native Google Docs? This is the load-bearing assumption of §1.2. The operator's
   manual workflow says yes; confirm it in M1 before building on it.
4. What exactly is a source's **title** derived from — does the `.txt` extension survive?
   (§6.3's matcher depends on it.)
5. Is enumeration **paginated**? If so, page it (§6, step 11).
6. Which `nlm` version is this all verified against?

---

## 13. Milestones

Work in order; do not start one until the previous passes. Record results with timestamps
in `tests/test_integration.md`.

- **M0 — Environment.** `setup.ps1` completes from clean. `nlm login --check` passes,
  `nlm doctor` is clean, rclone reaches Drive. Print both identities — the Google account
  rclone is authorized against and the account `nlm` is logged into — and **confirm they
  match.** A mismatch causes baffling permission errors much later.
- **M1 — Fixtures and recon.** Generate a small bundle with a manifest. Answer all six
  §12 questions in `NOTES.md`.
- **M2 — Selection.** Unit tests for mask matching: right files, `.txt` only, `Q-` never
  matched, non-recursive, empty result is an error not a no-op. No network.
- **M3 — Upload and fidelity.** `nlmt load --drive-only`. Accept only if the files appear
  in a fresh bundle folder as plain text, `rclone check --checksum` reports zero
  differences, and a BOM file and a non-BOM file both round-trip byte-identically.
- **M4 — Load into a new notebook.** Accept only if: the notebook is created and a rerun
  targets the same one rather than making a second; every file appears as a source; the
  titles match what §6.3's matcher expects; enumeration is stable across two calls; and
  readiness completes via whichever signal M1 established, with the manifest's smoke
  question returning its expected value **with a citation**.
- **M5 — Reload and the scoping guarantee.** Add a source by hand in the web UI, named
  outside the masks. Edit one local file. Rerun `load`. Accept only if: the mask-matching
  sources are deleted and re-added while the hand-added one is untouched; and asking about
  the edited value returns the **new** value. The first half matters as much as the second.
- **M6 — Second notebook, same bundle.** Load the same local files into a second notebook
  without touching them. Accept only if both notebooks end up correct and independent.
- **M7 — Long questions, with an attachment.** A ~500+ word analytical question plus a
  `--attach`ed JSON containing values that appear nowhere else, referenced by name from the
  question body. Accept only if: the `Q-` question and attachment sources appear and then
  disappear; the `_ask` Drive folder is removed too; the answer addresses the full
  instruction set rather than summarising; the answer **cites values that could only have
  come from inside the attachment**, proving it was ingested intact and that the §8.3
  name mapping worked; and the notebook is left with exactly its pre-question sources.
  Repeat once with `--keep`, then confirm `nlmt delete --mask Q<n>-` removes exactly that
  ask and nothing else, and that `nlmt sweep` clears any remaining ask sources and Drive
  folders. Confirm a subsequent `load` leaves a kept `Q<n>-` source untouched (§3.7).
- **M8 — Failure modes.**
  - Kill mid-load, rerun: converges, no duplicates, no stale-lock deadlock.
  - Delete the cache, rerun: slower, identical result. If behaviour changes, the cache is
    load-bearing and must be fixed.
  - Every command with `--json`, piped through a JSON parser: parses cleanly, nothing but
    the envelope on stdout. Repeat for a failure case and confirm the envelope survives.
  - **Self-teaching check (§5.5).** Every subcommand's `--help` carries a pasteable
    example. `nlmt --ai` covers every subcommand and every exit code. Provoke each usage
    error in turn and confirm each one names the corrective action; every error envelope
    carries a non-empty `hint`. As an end-to-end test, hand only `nlmt --ai` to an agent
    with no other context and confirm it can complete a load unaided.
  - Two notebooks with the same title: exit 14, both IDs printed, nothing modified.
  - Empty mask result: refuses; deletes nothing.
  - Both auth failures simulated: distinguishable exit codes 10 and 11.
  - Lock held: `ask` and a second `load` on that notebook refuse with 17, while a `load`
    on a different notebook proceeds.
- **M9 — Full bundle.** ~20 files, ~20 MB. Record total time, upload time and **observed
  indexing duration** in `NOTES.md`. Then load the same bundle into a second notebook and
  time that too.

---

## 14. Then, and only then: MCP

Once the CLI passes, register the MCP server for interactive use and trim its ~43-tool
surface to `notebooks_read`, `sources_read`, `sources_manage`, `chat`. Verify group names
against `nlm --ai` — unknown names are ignored silently, so a typo looks like it worked.

`CLAUDE.md` must carry the §1.1 rule that a source is **never** added as a local file
upload. An interactive session driven from a phone must not improvise that shortcut.

Remote access is via **Claude Code Remote Control**: the session runs on this host and is
continued from the mobile app. The MCP HTTP transport is never exposed to the network — it
has no authentication, and anyone reaching it controls the whole Google account.
