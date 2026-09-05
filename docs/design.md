# NotebookLM Tools — Design

**Status:** living specification, kept in step with the implementation. Authoritative.
Supersedes `requirements-original-brief.md` wherever the two differ (see §11).

**Implemented and exercised against a real 45 MB corpus:** `load`, `status`, `ask`,
`delete`, `sweep`, `prune`, `doctor`, `gen-fixtures`. Measurements quoted throughout are
from those runs, not estimates; `NOTES.md` carries the raw findings.

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

Files are stored on Drive as **plain `text/plain`, byte-for-byte identical to the local
file**, BOM included, and registered with that same mime type. They are **never** converted
to native Google Docs.

This was measured rather than assumed, and both halves matter:

| Route | Result |
|---|---|
| plain `.txt` on Drive, registered as `text/plain` | ready in ~13s; ingested text **exactly 100.0%** of the local file, at 1 MB |
| the same content converted to a Google Doc | **failed to import at all** at 1 MB; at 500 KB it imported but reflowed to 137% of the original |

The mime type is the load-bearing detail. Registering a plain `.txt` while *claiming* it is
a Google Doc — which is all the `nlm` CLI can express — produces a source that appears in
the listing but never resolves, keeping the raw Drive file id as its title and holding
`status: 3` forever. That is why §4.2 drives the library directly.

Consequence: fidelity is verifiable rather than hoped for. The bytes on Drive are checksum
-matched to the local file, and what NotebookLM ingested is compared against it (§6.5).

## 2. Scope

**In:** upload a local `.txt` bundle to Drive; load it into a named notebook as Drive
sources; wait for indexing; verify what was ingested; ask long questions, with attached
data files where needed; prune old Drive bundles; generate test fixtures.

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

### 4.2 NotebookLM — the `notebooklm_tools` library

NotebookLM is driven through **`notebooklm_tools` as a Python library, not through the
`nlm` CLI**. The CLI cannot express the one thing this project depends on: its `--type`
accepts only `doc|slides|sheets|pdf` (`services/sources.py: DRIVE_MIME_TYPES`), with no
plain-text option, so a `.txt` on Drive is announced as a Google Doc and never resolves.
The library's `add_drive_source` takes a free-form `mime_type`, and `text/plain` is what
makes the whole pipeline work.

Driving the library also gives real exceptions instead of parsed subprocess output, and
lets failures map onto this project's exit codes (§4.4).

Authentication still belongs to `nlm`: it holds cookies in its own Chrome profile under
`~/.notebooklm-mcp-cli/`, outside the repository.

**Sessions are short, and renewal is automatic.** Measured, a session lasts hours rather
than the weeks the original brief assumed. But `nlm login` on an expired session completes
**without a human**: it re-extracts cookies from its own still-signed-in Chrome profile,
which outlives the NotebookLM cookies it issues. So any call failing with `NLM_AUTH`
triggers one `nlm login`, rebuilds the client and retries.

That recovery is bounded at 90 seconds and attempted once per process. If the browser
profile's own Google session has *also* lapsed, `nlm login` waits interactively for a
human and would otherwise hang an unattended run for its full five-minute timeout; the
bound turns that into a clean exit 11 instead.

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
| `--concurrency N` | parallel uploads and source registrations (default 8) |
| `--verify-ingest MODE` | read back and compare: `sample` (default), `all`, `none` (§6.5) |
| `--smoke-question Q` / `--smoke-expect S` | fallback readiness probe (§7.2) |
| `--question TEXT` / `--question-file PATH` | (`ask`) the question body; stdin if neither is given |
| `--attach FILE` | (`ask`) repeatable; a text-like data file the question refers to (§8.2) |
| `--fresh` | (`ask`) start a new conversation instead of continuing (§8.5) |
| `--conversation ID` | (`ask`) continue one specific thread (§8.5) |
| `--keep` | (`ask`) do not delete the ask's sources; reports the mask to remove them |
| `--keep-last N` | (`prune`) how many recent bundles to keep |
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
 4. upload to a fresh Drive bundle folder, in parallel -> exit 10 on auth failure
 5. rclone check --checksum against the local folder   -> exit 13 on any mismatch
 6. resolve the uploaded files' Drive IDs
 7. resolve the notebook by title; create if absent    -> exit 14 on duplicates (§6.2)
 8. delete existing sources matching the masks         (§6.3)
 9. register every uploaded file as a source, in parallel  (§6.4)
10. wait for indexing                                  -> exit 16 on timeout (§7)
11. verify what was ingested against the local files   -> exit 13 on damage (§6.5)
12. release the lock; emit the envelope
```

**Each stage completes before the next begins**, and the independent work inside a stage
runs concurrently: one `rclone copy` with `--transfers` moves the whole set at once, then
source registration, deletion and read-back verification each fan out to `--concurrency`
(default 8). Doing this a file at a time dominated the run — the transfers were never the
bottleneck, the per-file server-side calls were. NotebookLM indexes the batch in parallel
regardless.

Measured on a real 25 MB bundle of 10 files: **1m40s end to end**, every file verified
intact.

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
add_drive_source(notebook_id, drive_file_id, title, mime_type="text/plain")
```

The mime type is the point (§1.2). Never a Google Docs type, and never a local file upload
(§1.1).

`--drive` takes a single document id — `--url` and `--youtube` are the repeatable ones — so
the batch is added one call per file. Those calls are independent and each costs several
seconds server-side, so they run **concurrently** (`--concurrency`, default 8). Registering
20 files one at a time dominated the whole run; NotebookLM indexes the batch in parallel
regardless.

**A Drive source's title is not ours to choose.** NotebookLM names it after the Drive file
and ignores the title argument — true only for *text* sources, which do honour it. So
anything the title must carry has to be part of the Drive filename, which is why an ask's
attachments are uploaded as `Q<n>-a<i>-<original-name>.txt` (§8.2). For the corpus this is
free: those files keep their own names, so a source's title equals the local filename,
which is what the masks already assume.

### 6.5 Verify the ingested text

`get_source_fulltext` returns a source's text **as NotebookLM ingested it**, which is a
direct measurement of the property §1.1 exists to protect. After indexing, `load` pulls it
back and compares it against the local file.

**Identifier coverage, not exact equality.** The check compares identifier-like tokens
(`[A-Za-z_][A-Za-z0-9_]{2,}`) and requires 99.5% of the local file's tokens to be present.
An exact comparison was tried first and reported damage on two real files, both false
alarms:

* One dump contained an **embedded TrueType font** — NUL bytes, 659 undecodable sequences.
  NotebookLM stripped the unprintable bytes, so 256,121 characters "vanished" and not one
  of them was source code.
* Another file was not valid UTF-8 in one place. NotebookLM decoded those bytes as cp1252
  and we decoded them as UTF-8, so identical content compared unequal.

Tokens ignore both artefacts while still catching what matters: stripping a function body
removes the identifiers inside it, which shows up immediately. On a real 25 MB bundle the
check reports 100.000% for every file.

**How much to check.** `--verify-ingest` takes `sample` (the default, one file), `all`, or
`none`. The failure this guards against is systemic — NotebookLM either strips this kind of
source or it does not — so one sampled file detects it, where reading back a 25 MB bundle
would roughly double the run's network traffic for no extra information. `all` is worth it
on a first run against an unfamiliar corpus. Damage is exit 13, and the report names the
file and how much is missing.

## 7. Readiness

Indexing takes time, and a notebook queried too early answers from an incomplete corpus
without saying so. Every load waits for its sources before returning.

### 7.1 Per-source status

Enumeration exposes a status, and the client defines what the values mean:

| Value | Meaning |
|---|---|
| 1 | processing |
| 2 | ready |
| 3 | error |
| 5 | preparing |

**Only `3` is terminal.** `1` and `5` are transient, and a source sat at `preparing` for
over a minute during testing before becoming `ready` — an early version read that as
failure and produced a false negative. Poll every `--poll-interval` (default 15s) until
every source in the batch reports ready; give up after `--ready-timeout` (default 20
minutes) with exit 16, and report which sources were still pending and in what state.

### 7.2 Smoke query (fallback)

The per-source status is available, so this is not needed in practice. It remains
specified for the case where a future version stops exposing status: run
`--smoke-question` and check the answer contains `--smoke-expect`, backing off 30s / 60s /
120s / 240s, then exit 16. A good smoke question is answerable only from **inside a
function body**, so that it re-verifies §1.1's property as a side effect.

## 8. Long questions — `nlmt ask`

A long question is often more than text: it references attached data files — a JSON export,
a CSV, a log extract — that the question asks about.

```
 1. acquire the notebook lock (take it, do not merely check it)
 2. clear any stranded Q<n>- source from a killed run          (§8.1)
 3. n = next free ask ordinal for this notebook
 4. for each --attach FILE, i = 1..k:                          (§8.2)
      upload to /NotebookLmTools/<project>/_ask/Q<n>/Q<n>-a<i>-<name>.txt
      verify by checksum                                       -> exit 13 on mismatch
      register as a source (text/plain)
 5. if the question fits inline: send it in the prompt         (§8.3)
    otherwise: add it as a source Q<n>-q-<slug> and wait for indexing
 6. query, with the generated prompt                           (§8.4)
 7. save the answer under answers/
 8. delete everything created, and the _ask/Q<n>/ Drive folder  # in a finally block
 9. release the lock
```

### 8.1 A stranded ask source hijacks answers

An abandoned `Q<n>-` source is not litter, it is a correctness bug. A question source is
instruction-shaped text sitting in the corpus, so NotebookLM retrieves it and answers
**about it** rather than about the question asked. Measured, same notebook, same
conversation, one variable: a query asking only for the word MANGO returned a description
of a leftover source, and returned `MANGO` the moment that source was deleted.

Cleanup in `finally` cannot cover the case that actually produces strays — a killed
process, where `finally` never runs. So `ask` **also clears them before asking**. It holds
the notebook lock, so no legitimate ask can be in flight and anything found is by
definition abandoned. The envelope reports `counts.orphans_cleared`; a non-zero value means
an earlier run was interrupted.

### 8.2 Attachments go through Drive

The **question** is prose with no code bodies to lose. An **attached JSON or CSV is the
data the answer depends on**, where a dropped element or truncated field produces a
confidently wrong answer — the same silent failure §1.1 exists to prevent. So attachments
take the proven path: Drive, plain text, checksum-verified, registered as `text/plain`.

Attachments must be text-like; a file with NUL bytes in its first 8 KB is refused (exit 19)
rather than ingested as mojibake.

**Naming.** An attachment is uploaded as `Q<n>-a<i>-<original-name>.txt`:

* `.txt` because NotebookLM refuses other types.
* The `Q<n>-` prefix **on the Drive filename**, because a Drive source's title is not ours
  to set — NotebookLM names it after the file (§6.4). Getting this wrong once produced a
  source called `metrics.json.txt` with no prefix: invisible to `sweep` and to
  `delete --mask Q7-`, stranded in the corpus, and quietly poisoning later answers.
* The original name preserved, because §8.4's prompt maps it back to what the question
  calls it.

So every source of one ask shares the prefix, and the whole ask is one mask:

```
Q7-q-locking-review        the question body, when it is sent as a source
Q7-a1-config.json.txt      first attachment
Q7-a2-metrics.csv.txt      second attachment
```

`nlmt delete --mask Q7-` removes exactly that ask. The ordinal is one greater than the
highest already present, derived from a listing rather than persisted (principle 3.3), and
the notebook lock makes read-then-allocate safe. Ordinals are reused after deletion; the
durable record is the transcript under `answers/`, whose filename carries the timestamp.

### 8.3 The question is sent inline

The question travels **in the prompt**, not as a source. Measured against the live API: a
payload of 8,403 characters was accepted and echoed back intact, while 12,352 was rejected
with `INVALID_ARGUMENT`, so the ceiling is near 10,000. `INLINE_LIMIT` is 8,000, leaving
room for the prompt scaffolding.

This removes a source creation, an indexing wait and a deletion — and, worth more, removes
the thing that gets stranded and hijacks later answers (§8.1). A plain question then creates
nothing at all in the notebook.

Two fallbacks to the question-as-source route, which remains fully supported:

* the question is longer than `INLINE_LIMIT`;
* the API rejects the query anyway. Rejection is distinct and fast — about two seconds —
  so a misjudged threshold costs seconds rather than the run.

The envelope reports `question_inline` either way.

### 8.4 The generated prompt

The prompt must **bridge the naming gap**: the question refers to `config.json`, while the
notebook knows a source titled `Q7-a1-config.json.txt`. Without an explicit mapping the
model cannot connect them.

```
<the question body, inline>

The data referred to above is attached as these sources:
  config.json   -> source "Q7-a1-config.json.txt"
  metrics.csv   -> source "Q7-a2-metrics.csv.txt"

Use the other sources in this notebook as evidence.
```

When the question was too long to inline, the first line becomes
`Answer the question in source "Q7-q-<slug>".` instead.

### 8.5 Conversations

A query **continues the notebook's existing conversation by default**, and that is
deliberate: the architect answers better warmed up, so a caller can ask how something works
and then ask the real question with that context in play. The envelope returns
`conversation_id`, `--conversation ID` continues a specific thread, and `--fresh` starts a
clean one for a question that must stand alone.

### 8.6 Notes

- The lock is **taken**, not merely checked: otherwise a concurrent `load` could delete the
  corpus out from under a running question.
- Cleanup runs in `finally` and deletes **by the `Q<n>-` mask**, not by the ids it happens
  to hold. A source can exist server-side while the call that created it reported failure,
  and deleting by id alone would strand exactly that source.
- `--keep` skips cleanup and reports `cleanup_mask`, so the ask can be removed later with
  `nlmt delete --mask Q<n>-`. `nlmt sweep` is the blunt version.
- Answers are written to `answers/<notebook-slug>/<timestamp>-Q<n>-<slug>.md` with the
  question, the attachment list, the answer, and the live source list at the time of
  asking. `answers/` is gitignored — transcripts contain source excerpts.
- **Cost.** Measured against a 45 MB notebook: a plain question takes about two minutes, a
  question with one attachment about four. The cost is the query itself, not the tooling.
- **Chat history caveat:** reloading a bundle breaks citations in older chat sessions that
  pointed at the sources it replaced. Expected, not a bug.


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
    common/
      spec.py             # the single definition of the CLI; help is generated from it
      exits.py            # exit codes, and the error type that carries a hint
      envelope.py         # the output contract
      nlm.py              # NotebookLM via the notebooklm_tools library, with auto re-login
      secrets.py          # Windows Credential Manager access
      process.py          # child processes, with secrets scrubbed from logs
      locking.py          # per-notebook locks with stale reclaim
      naming.py           # slugs, the Q<n>- reservation, ask titles
      topics.py           # concept help, including the agent guide
    loader/
      select.py           # masks -> file list; pure, unit-testable
      drive.py            # rclone: bundles, parallel upload, checksum verification
      load.py             # the load pipeline
    ask/ask.py            # the long-question protocol
    fixtures/generate.py  # synthetic bundle generator
    cli.py                # dispatcher, help rendering, --ai
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
| §0.2 drive NotebookLM through the `nlm` CLI | drive the `notebooklm_tools` library | the CLI's four-value `--type` cannot express `text/plain`, which the pipeline depends on (§4.2) |
| §6 the question is always a source | the question is sent inline, with the source route as fallback | measured ~10,000-character ceiling; inline removes an indexing wait and the stranded-source failure (§8.3) |
| §0.3.8 `Q-` is a plain prefix | `Q<n>-` carried on the **Drive filename** | a Drive source's title is set by the file, not by us (§6.4) |
| — | `ask` clears stranded ask sources before asking | `finally` cannot run after a kill, and a stray hijacks later answers (§8.1) |
| — | automatic re-authentication on `NLM_AUTH` | sessions last hours, and `nlm login` renews them without a human (§4.2) |
| §4 exact-text fidelity comparison | identifier-coverage comparison at 99.5% | exact matching cried wolf on embedded binary and on non-UTF-8 bytes (§6.5) |
| §4 one file at a time | parallel upload and registration | per-file server-side calls, not transfers, dominated the run (§6) |

---

## 12. Reconnaissance — answered

Settled against `nlm` / `notebooklm_tools` 0.10.1 on 2026-09-05. Detail in `NOTES.md`.

1. **Multiple document ids in one call?** No. `--drive` takes one id; `--url` and
   `--youtube` are the repeatable ones. `load` registers sources concurrently instead
   (§6.4).
2. **Index status exposed?** Yes — `1` processing, `2` ready, `3` error, `5` preparing.
   Only `3` is terminal (§7.1).
3. **Does a plain `.txt` on Drive work?** **Yes, and only with `mime_type="text/plain"`.**
   This is the fact the whole pipeline rests on: the CLI's four-value type enum cannot
   express it, which is why the library is driven directly (§1.2, §4.2).
4. **What is a source's title derived from?** For a **Drive** source, the Drive filename —
   the title argument is ignored. For a **text** source, the title given. This asymmetry
   is why an attachment carries its prefix in the Drive filename (§6.4, §8.2).
5. **Is enumeration paginated?** Not observed at 18 sources. Unresolved above that.
6. **Which version?** `notebooklm-mcp-cli` 0.10.1.

Two limits worth recording alongside them:

* **Inline query ceiling ~10,000 characters.** 8,403 accepted, 12,352 rejected with
  `INVALID_ARGUMENT` in about two seconds (§8.3).
* **Session lifetime is hours, not weeks**, and renewal is automatic (§4.2).

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
