# NOTES

Running log: undocumented-API surprises, contradictions with the design, measured timings,
and decisions made while building. Where the installed `nlm` contradicts `docs/design.md`,
**the installed tool wins** — record it here and adjust the design.

## Environment

| Item | Value | When |
|---|---|---|
| Host | Windows 11 Home 10.0.26200 | 2026-09-05 |
| Python (system) | 3.13.5 at `C:\Python313`; a Store build also present | 2026-09-05 |
| Python (project venv) | 3.13, created by `setup.ps1` | 2026-09-05 |
| `nlm` version | **0.10.1** (`notebooklm-mcp-cli`), reports "latest" | 2026-09-05 |
| rclone version | *not yet installed* | — |

`py -3.13` resolves to the **Microsoft Store** build under `WindowsApps` on this machine,
not `C:\Python313`. The Store build redirects file writes, so `setup.ps1` now prefers a
non-Store interpreter and warns if only the Store one is available.

## Reconnaissance answers (design.md §12)

Against `nlm` 0.10.1, from `nlm --ai` and `--help`. Items marked **LIVE** still need a
logged-in run to settle.

| # | Question | Answer |
|---|---|---|
| 1 | Does `source add` take multiple document IDs? | **No, not for Drive.** `--url` and `--youtube` are explicitly "repeatable for bulk"; `--drive` is a single `<str>`. So the batch is added by looping. |
| 2 | Does enumeration expose index status? | **Partly.** No status field is documented, but `source add --wait` blocks until processing completes (`--wait-timeout`, default 600s), and `source list --full --json` may carry a state. **LIVE** |
| 3 | Does `--drive` accept a plain `.txt` Drive file? | **Unknown, and the main open risk.** `--type` accepts only `doc, slides, sheets, pdf` — there is no plain-text type. **LIVE**, and blocking for M3/M4. See "Risks" below. |
| 4 | What is a source title derived from? | Settable directly: `source add --title`. So titles are ours to choose and the mask matcher is safe. |
| 5 | Is enumeration paginated? | Not documented as paginated; `--full` returns all columns. **LIVE** |
| 6 | Which `nlm` version is this verified against? | 0.10.1 |

### The commands this project will drive

```
nlm notebook list --json
nlm notebook create "Title"
nlm notebook query <nb> "question" --json [--source-ids a,b] [--conversation-id <cid>]
nlm source list <nb> --full --json
nlm source add <nb> --drive <doc-id> --type doc --title "T" --wait --json
nlm source add <nb> --text "..."     --title "T" --wait --json
nlm source content <source-id> --json
nlm source delete <source-id> --confirm --json
```

## Surprises and contradictions

- **2026-09-05 — `nlm source content` lets us verify ingestion directly.** It returns a
  source's raw text *after* NotebookLM ingested it. That is a much stronger check than the
  smoke question the design planned: instead of inferring "the function bodies survived"
  from an answer, we can pull the ingested text back and compare it to the local file. The
  smoke query is now a fallback, not the primary fidelity evidence. Design updated
  (§6 step 11a, §7.1).

- **2026-09-05 — Drive sources are not obviously import-time copies.** The original brief
  (§0.1 fact 2) states there is no sync and `source_sync_drive` is irrelevant. But `nlm`
  0.10.1 has `source list --drive` "with freshness", `source stale <nb>`, and
  `source sync <nb> --confirm`. So the *tool* believes Drive sources retain a link. This
  does not change the design — we still refresh by delete-and-add, which works either way
  — but the brief's fact 2 is at best incomplete. Retest once sources exist. **LIVE**

- **2026-09-05 — `notebook query --source-ids` can scope a query to chosen sources.** Not
  needed for the current design (an ask deliberately uses the whole corpus as evidence),
  but it is the escape hatch if attachment-scoped questions are ever wanted.

## Risks

**The plain-`.txt`-on-Drive assumption is not yet proven through the CLI.** `--type` offers
only `doc, slides, sheets, pdf`. The operator's manual workflow proves NotebookLM's *web
picker* accepts a `.txt` from Drive, but that is not the same code path. Three outcomes:

1. `--type doc` accepts a `text/plain` Drive file and ingests it verbatim — the design
   stands as written.
2. It is rejected outright — a loud failure, easy to detect, and we go back to the operator
   with the fork rather than silently converting anything.
3. **The dangerous one:** it is accepted but ingested lossily. `nlm source content` is the
   defence: compare the ingested text against the local file before trusting anything.

This is the first thing M3/M4 must establish, and no other work should build on top of it.

## Design decisions

- **2026-09-05 — No Google Docs conversion.** The original brief (§4.2) specified
  converting uploads to native Google Docs. The operator's proven manual workflow stores
  plain `.txt` on Drive and imports that. Docs conversion cannot preserve a BOM and caps
  near 1.02M characters against ~1 MB chunks. Plain text it is; fidelity becomes a checksum
  comparison. See `docs/design.md` §1.2.
- **2026-09-05 — rclone instead of a self-registered OAuth client.** Avoids all Cloud
  console setup, and rclone's OAuth client is Google-verified so refresh tokens do not
  expire on the 7-day "Testing"-status clock. `rclone check --checksum` supplies the
  byte-identity proof. See `docs/design.md` §4.1.
- **2026-09-05 — Secrets in Windows Credential Manager.** `rclone.conf` is encrypted; its
  password lives in Credential Manager (DPAPI) and reaches rclone only through
  `RCLONE_CONFIG_PASS` on the child process. Machine-bound by design; a new machine means
  one re-login. See `docs/design.md` §4.3.
- **2026-09-05 — Ingested-content verification is now part of `load`.** Prompted by the
  `nlm source content` discovery above.

## Observed timings

| Date | Bundle | Files | Size | Upload | Indexing | Total |
|---|---|---|---|---|---|---|
| | | | | | | |

## 2026-09-05 — rclone's shared Google client id is being retired

`rclone config create <name> drive ...` silently did nothing: no remote, no browser, no
prompt. Probing with `--non-interactive` showed why. The first question rclone asks is:

> rclone's shared Google Drive client_id is being retired and will stop working during
> 2026. Create your own to avoid interruption. Continue using the shared client_id
> anyway?  **Default: false**

`config create` takes the default for any question it is not given an answer to, so it
answered "no" and aborted — exit code 0, no output, nothing created.

Two consequences.

**Immediate:** every question must be answered explicitly. The remote is now created with
`config_shared_client_id=true` (or with our own client id, if one is configured), and
afterwards the script verifies the remote exists *and* can reach Drive with `rclone lsd`.
An interactive tool reporting success is not evidence that it worked.

**Strategic, and the operator's decision:** the design chose rclone over a self-registered
OAuth client specifically to avoid Google Cloud console setup (`docs/design.md` §4.1). That
rationale has an expiry date, and rclone says the date is during 2026 — which is now. The
options are to keep using the shared client id until it breaks, or to create one Google
Cloud OAuth client (~10 minutes, once) and pass it via `-DriveClientId` /
`-DriveClientSecret`, which `setup.ps1` now supports and stores in Credential Manager.

Note the scope analysis is unchanged and still favours our own client if we go that way:
`drive.file` is non-sensitive, so the app can be published to production without Google
verification, and its refresh tokens do not expire on the 7-day "Testing" clock.

### Resolution (2026-09-05): keep rclone, bring our own client id

Considered replacing rclone with a Python Google Drive client, on the reasoning that the
Cloud console step is now unavoidable so rclone's main advantage had evaporated. Rejected
after re-examining it, because the argument does not survive contact with what is already
built:

| Claimed gain from dropping rclone | Reality |
|---|---|
| token in Credential Manager rather than an encrypted `rclone.conf` | marginal; rclone 1.75.1 does support `config encryption`, and the password is already in Credential Manager |
| removes PowerShell 5.1 native-command fragility | already solved by `Invoke-Native` |
| no binary to fetch and pin | already done, checksum-verified against the published `SHA256SUMS` and pinned in `tools/rclone.lock.json` |
| free md5 verification | `rclone check --checksum` already provides it |

Against those: roughly 250 lines of new code to own, and losing rclone's resumable
uploads, retries and throttling handling, which would have had to be reimplemented worse.

**What actually changed today is only that the shared client id is going away.** rclone
itself is actively maintained and remains the right tool; it just has to be handed our own
credentials. That is one argument pair to `config create`, already implemented.

`docs/google-oauth-setup.md` has the console steps.

### 2026-09-05 — Drive authorization succeeded; the installer crashed after it

The OAuth flow completed (`NOTICE: Got code`) and rclone wrote a working `[nlmtools]`
remote with a token. `rclone lsd nlmtools:` then exits 0 with no output — correct for a
fresh `drive.file` scope, which can only see files the app created.

The crash was in `Invoke-NativeInteractive`: it deliberately does not capture the
program's output, so everything rclone wrote to stdout joined the function's **return
value**. The caller got an array of output lines with the status object buried at the end,
and `$created.Ok` failed under StrictMode with `PropertyNotFoundStrict`. Fixed by piping
the program's output to `Out-Host`, which puts it on the console and keeps the return
value to exactly one object.

Also learned: `rclone config userinfo` is not supported by the Drive backend, so `doctor`
cannot get the Drive account's email that way. M0 wants both identities compared. Options
are to add the non-sensitive `userinfo.email` scope (needs one re-auth), or to replace the
comparison with a functional check — upload a marker file as rclone, then confirm
NotebookLM can add it as a Drive source, which proves the accounts match and is a stronger
test than comparing strings. Deferred until `doctor` is implemented.

### 2026-09-05 — where `nlm` keeps its credentials

`nlm login` does **not** use the operator's everyday Chrome profile. It launches Chrome
with its own profile directory, which starts empty — hence a full Google login the first
time, even on a machine where Chrome is signed in. `nlm login --clear` wipes that profile,
and exists so you can switch Google accounts.

Location on this host:

```
C:\Users\pjane\.notebooklm-mcp-cli\
    chrome-profiles\      the isolated Chrome profile (cookies live here)
    profiles\             named auth profiles
    cache\
```

Outside the repository, as `docs/design.md` §4.2 assumed. Two consequences worth keeping
in mind: the session survives across runs so the full login is a one-time cost per machine
(cookies last roughly 2-4 weeks and refresh from this profile), and this directory is
credential-bearing, so it must never be copied into the repo or into a backup that gets
committed.

## 2026-09-05 — SOLVED: the CLI's mime enum was the whole problem

The operator reported that the web UI ingests a 2 MB `.txt` from Drive **in seconds**,
while `nlm source add --drive` took minutes at 300 KB and failed outright at 1 MB. That
gap said the CLI was not doing what the web UI does.

Reading `notebooklm_tools/core/sources.py`, `add_drive_source()` takes a free-form
`mime_type` argument. The restriction lives one layer up, in
`notebooklm_tools/services/sources.py`:

```python
DRIVE_MIME_TYPES = {
    "doc":    "application/vnd.google-apps.document",
    "slides": "application/vnd.google-apps.presentation",
    "sheets": "application/vnd.google-apps.spreadsheet",
    "pdf":    "application/pdf",
}
```

Four values, no `text/plain`. So `nlm source add --drive` on a plain `.txt` was telling
NotebookLM "this is a Google Doc" about a file that is not one — hence a source that
listed but never resolved (its title stayed the raw Drive file ID, `status: 3`).

**Calling the library directly with the correct mime type fixes it completely:**

```python
client.add_drive_source(notebook_id, drive_file_id, title, mime_type="text/plain")
```

Measured against a 1,002,523-byte plain `.txt` on Drive:

| | Doc conversion route | `text/plain` route |
|---|---|---|
| 1 MB source | failed (`status` 3/5) | **ready in ~13 s** |
| ingested vs local | 137.8% (Doc reflow) at 500 KB | **100.0% — exact** |
| buried body values | complete up to 500 KB | **complete at 1 MB** |
| title | Doc name | real filename |

### What this settles

- **§12 item 3 is answered: yes**, `source_type="drive"` accepts a plain `.txt`, provided
  the mime type says so. `docs/design.md` §1.2 — plain text on Drive, no conversion — was
  right, and the operator's manual workflow is reproducible after all.
- **Do not drive NotebookLM through the `nlm` CLI.** Use `notebooklm_tools` as a Python
  library. The CLI cannot express the one thing this project depends on, and shelling out
  also costs subprocess parsing and loses real exceptions. `get_client()` in
  `notebooklm_tools.cli.utils` builds an authenticated client from the saved profile.
- **Source status values, observed:** `2` = ready, `3` and `5` = failure states. Status is
  not immediate — a 500 KB source read `5` for over a minute before settling at `2`, so
  readiness polling must not treat a transient non-2 as failure. Judge only after the
  source stops changing or a timeout expires.
- Google Docs conversion is now irrelevant: no BOM loss, no 1.02M-character ceiling, and
  no fidelity question, because the bytes are never transformed.

### Querying verified (2026-09-05)

`notebook query` against the probe notebook, asking for a value that exists only inside a
method body 75% of the way through the 1 MB source:

- Correct answer (**9632**) retrieved from `Big_b17_01.txt`. Deep retrieval from a
  full-size chunk works, so ingestion is not merely complete but usable.
- **Citations are structured**: the response carries `sources_used` (source ids) and
  `citations` (footnote number -> source id). M4's "returns the expected value with a
  citation" is therefore machine-checkable, not a judgement call.
- Sources were disambiguated correctly. The fixtures deliberately reuse class and method
  names across files; the answer listed each value with the file it came from rather than
  conflating them.

Still untested: the long-question protocol end to end (`ask`), including attachments and
cleanup. Its parts are proven individually — a text source added inline ingested intact,
and query returns citations — but the sequence has not been run.

### 2026-09-05 — NotebookLM cookies expired within ~15 minutes

`nlm login` completed at 13:34 and reported 50 cookies plus a CSRF token for
`pjanec2@gmail.com`. Loads and queries worked. By roughly 13:50 every NotebookLM call
returned:

```
Authentication expired. Run 'nlm login' in your terminal to re-authenticate.
```

That is far short of the 2-4 weeks the original brief expected, and short enough to matter
for an unattended run. Possible causes, none yet confirmed: the login was captured while
the browser session was still settling (the sign-in took over 210 seconds of waiting), the
Chrome profile was reused between attempts, or Google invalidated the session. Watch
whether a fresh login lasts longer; if this recurs, the readiness and retry design needs
to account for re-authentication mid-batch rather than assuming a stable session.

Worth noting the failure was legible: `status` and `doctor` both reported
`NLM_AUTH, exit 11` with the hint "run 'nlm login' on this host, then retry", rather than
surfacing a library traceback. That is the error mapping from design.md 4.4 working.

### 2026-09-05 — correction: a Drive source's title is NOT ours to set

Recon item 4 was recorded as "settable directly: `source add --title`". That is true for a
**text** source and false for a **Drive** source: NotebookLM names a Drive source after the
Drive file and ignores the title argument. It was visible earlier and I misread it — adding
a Doc with `--title "EngineDoc_as_google_doc"` produced a source titled `Engine_b17_01`.

The consequence surfaced in `ask --keep`: the attachment was uploaded as `metrics.json.txt`
and became a source titled `metrics.json.txt`, with no `Q<n>-` prefix. So it was invisible
to `sweep` and to `delete --mask Q1-`, and was stranded in the corpus, where it would have
quietly polluted every later answer — the exact failure design.md 8 exists to prevent.

**Fix:** the prefix goes on the Drive filename. An attachment is uploaded as
`Q<n>-a<i>-<original-name>.txt`, so the source title carries the prefix that makes the ask
one mask. Verified: `Q1-a1-metrics.json.txt` and `Q1-q-keeptest2` both appear, and
`delete --mask Q1-` removes exactly those two and nothing else.

For the corpus this was never a problem: those files keep their own names on Drive, so the
source title equals the local filename, which is what the masks already assume.

### `ask` verified end to end (2026-09-05)

A 391-word analytical question with a JSON attachment whose values appear in no source
file. Answer: 3650 characters, 3 citations, in ~2 minutes.

- Every attachment-only value was used: 4127, 9318, 7756, 2044, the 5000 ms budget, and the
  `regressed` / `watch` verdicts. So the attachment was ingested and the 8.3 name mapping
  worked -- the model connected "metrics.json" in the question to the prefixed source.
- The answer followed the instruction set: four headed sections, the requested table with
  computed excesses, and the exclusions honoured.
- Cleanup left the notebook exactly as it was found.

Two bugs found and fixed by running it:

1. The attachment was uploaded under its own name rather than with `.txt` appended, so
   NotebookLM rejected it (`API error (code 9)`).
2. When that add failed, the source existed server-side but its id was never returned, so
   the `finally` block could not delete it and it was left stranded. Cleanup now deletes by
   the `Q<n>-` mask rather than by the ids it happens to hold, which covers exactly this
   partial-failure case. `nlmt sweep` also cleared it, as designed.

## 2026-09-05 — first real bundle: 10 files, 25.4 MB

`D:\WORK\IOS-IG-SimHost-FDP\.dumps`, masks `HROT.` and `FDP.` (the eight `Docs.*` files in
the same folder are correctly excluded). Files average 2.5 MB, largest 3.8 MB — well beyond
the 1 MB previously proven.

**Result: every file ingested intact.** 100.000% of identifiers present in all ten,
zero missing. The whole run took **1m40s**.

### Sequential work was the bottleneck, not the transfer

The first attempt uploaded and registered one file at a time, each NotebookLM call costing
several seconds server-side. Now:

1. Drive upload: one `rclone copy` with `--transfers`, so the whole set moves at once.
2. Barrier, then checksum verification.
3. Source registration: concurrent, `--concurrency` (default 8).
4. Barrier, then a single readiness wait — NotebookLM indexes the batch in parallel anyway.

Deletes and read-back verification are parallel for the same reason.

### The fidelity check had to be rewritten to survive real data

The exact-text comparison reported damage on two files, and **both were false alarms**:

* `FDP.Eng_275.01.txt` contains an **embedded TrueType font** — `OS/2`, `cmap`, `glyf`,
  `head`, `hhea`, `loca`, `maxp`, `post` table names, NUL bytes, 659 undecodable
  sequences. NotebookLM stripped the unprintable bytes, so 256,121 characters "vanished"
  and not one of them was source code. Worth telling the operator: that font is occupying
  a quarter of a megabyte of a source dump for no benefit.
* `HROT.Eng_275.01.txt` is not valid UTF-8 in one place (double-encoded mojibake).
  NotebookLM decoded those bytes as cp1252 and we decoded them as UTF-8, so identical
  content compared unequal — same length, different code points.

The check now compares **identifier-like tokens** (`[A-Za-z_][A-Za-z0-9_]{2,}`) and
requires 99.5% coverage. That ignores both artefacts while still catching the failure that
matters: stripping a function body removes the identifiers inside it, which shows up
immediately. On the real bundle it reports 100.000%.

### One more crash worth recording

Rendering the failure report raised `UnicodeEncodeError` on the cp1252 console, because
the error detail quoted source text. A tool whose most important message is a fidelity
failure must not crash while printing it. stdout and stderr are now reconfigured to UTF-8
with `errors="replace"` at startup.

## 2026-09-05 — a stranded ask source hijacks answers

Symptom: a question about entity damage state came back describing MCP servers; later, a
query asking only for the word MANGO came back as *"The core question posed by
`Q9-q-probe` is whether..."*.

The cause is not retrieval failure, conversation contamination, or the source-reference
prompt — all three were suspected and cleared. It is a **stranded `Q<n>-` source**. A
question source is instruction-shaped text sitting in the corpus; NotebookLM retrieves it
and answers *about it* rather than about the question asked.

Proof, same notebook, same conversation, one variable:

```
stranded ask sources found: ['Q1-q-arch-probe3']
  deleted Q1-q-arch-probe3
query took 69.2s
answer: 'MANGO'
```

Wrong answer with the stray present; correct the moment it was gone.

The strays came from runs killed mid-flight — `finally` does not run on SIGKILL, so the
one cleanup path the design relied on cannot cover the case that actually happens.

**Fix:** `ask` now clears any `Q<n>-` source *before* asking, as well as after. Holding the
notebook lock means no legitimate ask can be in flight, so anything found is by definition
abandoned. The envelope reports `counts.orphans_cleared`, and a non-zero value is a warning
that a previous run was interrupted. `docs/design.md` §8 already said an abandoned question
source "pollutes retrieval for every later answer" — that was an understatement, and it is
now self-healing rather than merely documented.

### Query latency

A query against this notebook (18 sources, 45 MB) takes **65-110 seconds** through the API,
against "a few seconds" in the web UI. Not caused by anything on our side: the same
measurement holds for a bare query with no source indirection and no history rebuild. The
likely explanation is that the web UI streams tokens as they arrive and so *feels*
immediate, while the API returns only on completion. Worth remembering when budgeting an
agent's time: an ask is a minute or two, not seconds.

### NotebookLM session lifetime, measured (2026-09-05)

| Time | Event |
|---|---|
| 13:34 | `nlm login` succeeds — 50 cookies, CSRF token, account confirmed |
| ~13:50 | every call returns "Authentication expired" |
| ~14:05 | works again with no re-login (a `setup.ps1` run reported the session valid) |
| 15:00-16:20 | loads, asks and queries all succeed |
| ~16:35 | "Authentication expired" again; three consecutive `doctor` runs all fail |

So a session lasted roughly **three hours**, not the 2-4 weeks the original brief assumed,
and re-authentication is interactive — a browser sign-in a human must perform.

The three consecutive failures matter: an earlier recovery had suggested exit 11 might be
transient and worth retrying automatically. It is not. Retrying would have burned time and
still needed a human, so `NLM_AUTH` stays a stop-and-escalate condition.

**Consequence for autonomous use.** An agent asking the architect must expect exit 11 as a
normal outcome, not an exception: it means "the architect is unavailable until a human
signs in again", and the agent should carry on with whatever it can do without it rather
than blocking or looping. Any unattended schedule (a nightly load, say) will fail unless
someone has signed in recently.
