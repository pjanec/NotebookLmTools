# NotebookLM Batch Loader — Build & Test Brief

**Audience:** the Claude Code agent running on the always-on host.
**Goal:** automate an already-proven manual workflow. A *batch* of local `.txt` chunks (bundled source code, headers already present) is uploaded to Google Drive as native Google Docs, then loaded into a named Gemini Notebook (NotebookLM) notebook as Drive sources. The notebook is then queried — including long questions stored as a temporary source and referenced by a short prompt.

Build incrementally. Each milestone has an acceptance test. Do not start the next until the previous passes.

---

## 0. Read this first

### 0.1 The workflow is proven — you are automating, not designing

The operator does this by hand today and it works. Reproduce it; do not improve it. Three facts came from that manual experience and are **not open for reinterpretation**:

1. **The Google Docs detour is mandatory.** Uploading a `.txt` directly to NotebookLM as a local file causes it to strip out function bodies, keeping only headers and signatures. Importing the same content via a Google Doc preserves it verbatim. This is the entire reason Drive is in the pipeline. If you find yourself thinking "we could skip Drive and upload the file directly" — no. That is the failure this design exists to prevent, and it is silent: sources present, answers fluent, code gutted.

2. **Sources are import-time copies. There is no sync.** These sources do not stay linked to Drive, so `source_sync_drive` and Drive freshness are irrelevant. Refreshing content means deleting the old sources and adding new ones.

3. **Indexing takes minutes.** The web UI shows a spinner per source while it indexes. The notebook must not be queried until every source in the batch has finished (§5).

### 0.2 Everything downstream is an undocumented API

`notebooklm-mcp-cli` (the `nlm` CLI + `notebooklm-mcp` MCP server) drives Google's **internal** NotebookLM endpoints using browser cookies. Verify against the installed version before building:

```bash
nlm --version
nlm --ai                    # full AI-oriented reference — read this early
nlm notebook list --help
nlm notebook get --help     # source enumeration: what fields per source?
nlm source add --help       # does it accept multiple document_ids in one call?
nlm source delete --help
nlm notebook query --help
nlm doctor
```

If anything here contradicts `nlm --ai` or `--help`, **the installed tool wins**. Log it in `NOTES.md` and carry on.

Two specific things to determine early, because they shape §4 and §5:

- **Multi-add:** the web picker loads a whole batch in one action. Does `source_add` accept a list of `document_id`s? If yes, use it. If not, loop and say so in `NOTES.md`.
- **Index status:** the web UI's per-source spinner implies the underlying data carries a processing state. Does enumeration expose it? If yes, that is the readiness signal. If not, fall back to the smoke query (§5.2).

### 0.3 Design principles

1. **Names are the only interface.** The tool's inputs are a notebook *title*, a Google Drive *folder path*, and a local *folder path*. Internal identifiers — notebook IDs, Drive file IDs, source IDs — are never accepted as input and never required from the operator. They may be cached internally, and they appear in output only in the exit-14 ambiguity message, where they are the only way to tell two same-named things apart.
2. **Dumb tool, smart caller.** This is a mechanical automation of a manual procedure. It makes no decisions about *what* to load or *what* to ask. It runs from a shell by hand and is equally drivable by an AI agent, which means its output is a contract, not prose (§1.1).
3. **No persisted state beyond a cache.** Everything is derivable from three listings: the local folder, the Drive folder, and the notebook's sources. Any cache is a latency optimisation that can be deleted at any moment without changing behaviour — test that (M8).
4. **Masks scope every operation.** A batch is a set of `starts-with` prefixes plus `.txt`. The tool only ever touches files and sources matching them. **The notebook is not a mirror** — sources outside the mask set are left untouched, always.
5. **Never add a source with `source_type="file"`.** Always `source_type="drive"` with a `document_id`. See §0.1.1.
6. **Ambiguity is a hard stop.** Two notebooks with the target title, or two Drive files with the same name in the folder, mean the tool cannot know what the operator meant. Exit with a clear message; never guess.
7. **Whole-batch rerun is the recovery path.** No partial-resume logic. A failed run is fixed by running it again.
8. **Question sources are reserved.** The `Q-` prefix belongs to §6 and is excluded from every mask operation.

### 0.4 Out of scope

No web UI, no daemon/watcher, no Studio artifacts, no multi-user support, no public network listener, no Drive cleanup (old batches accumulate in Drive by design). Single user, single host, invoked on demand.

---

## 1. Concepts

- **Batch** — a set of local `.txt` files selected by `starts-with` masks from one folder. Filenames carry a batch number, so successive batches do not collide.
- **Project** — a named config entry: local folder path, mask list, Drive folder path. Several projects exist and may be operated in parallel against different notebooks.
- **Notebook** — addressed **by name**, given on every invocation. If it does not exist, it is created.

The same batch may be loaded into more than one notebook (same sources, different discussion). Re-uploading to Drive on each load is fine and expected — no attempt should be made to skip it.

### 1.1 Output contract

Every command supports `--json`. Human-readable text is the default; JSON is what an agent parses. Exit codes remain the coarse signal, the JSON carries detail. One envelope for every command:

```json
{
  "ok": true,
  "action": "load",
  "notebook": "Engine — locking review",
  "notebook_created": false,
  "counts": {"selected": 20, "deleted": 20, "uploaded": 20, "added": 20},
  "ready": true,
  "readiness_signal": "per_source_status",
  "elapsed_s": 412,
  "warnings": [],
  "error": null
}
```

On failure: `ok: false`, `error: {"code": 14, "class": "AMBIGUOUS", "message": "...", "detail": {...}}`. The `detail` object is where internal IDs appear when disambiguation requires them (§0.3.1).

Rules: the envelope is written to stdout and nothing else is; logs and progress go to stderr. Field names never change meaning between versions — add fields, don't repurpose them. A partial failure still emits a complete envelope with accurate counts, so a caller can see how far it got.

### 1.2 Division of labour

- **The loader (this tool)** owns Drive and source management. It knows nothing about the content of questions or answers.
- **The AI agent** owns querying, via the MCP server, and calls the loader as a subprocess when it needs files moved.

The long-question protocol (§6) sits across that line: it is a query concern but it manipulates sources. It belongs in the loader as an `ask` subcommand, implemented once and correctly, so the agent calls it rather than improvising `source_add` + `source_delete` through MCP. An agent doing it by hand is exactly how question sources get stranded in the notebook.

---

## 2. Repository layout

Suggested `~/nlm-bridge`:

```
nlm-bridge/
  README.md               # how to run it, written last
  NOTES.md                # running log: API surprises, doc discrepancies, decisions
  config.toml             # projects: local folder path, masks, drive folder path
  locks/<notebook>.lock   # per-notebook, so projects can run in parallel
  src/
    config.py             # load + validate; fail loudly on missing keys
    select.py             # masks -> file list; pure, unit-testable
    drive_upload.py       # local .txt -> Google Docs (overwrite by name)
    nlm_bridge.py         # notebook resolve/create, delete-by-mask, add, enumerate
    readiness.py          # index-wait logic
    ask.py                # long-question protocol
    cli.py                # load | status | ask | sweep | doctor
  answers/                # saved Q&A transcripts
  tests/
    test_select.py
    test_drive_upload.py  # mocked Drive API
    test_integration.md   # manual checklist (§7)
  .gitignore              # MUST exclude: token.json, credentials.json, locks/
```

Use `uv`. Python 3.11+.

Example `config.toml`:

```toml
[projects.engine]
local_folder = "/home/op/exports/engine"
masks = ["Engine_b17_", "Core_b17_"]
drive_folder = "/Exports/Engine"      # path, not an ID (§0.3.1)
smoke_question = "Which class handles connection retries, and what is the retry limit?"
smoke_expect = "5"
```

Invocation: `cli.py load --project engine --notebook "Engine — locking review"`

---

## 3. Credentials

### 3.1 Google Drive

**Installed-app OAuth flow under the operator's own Google account.** Not a service account: a service account owns files in its own Drive, and NotebookLM reads Drive as the signed-in human.

- Scope: `https://www.googleapis.com/auth/drive`. This is wider than the files this app creates, and that is deliberate: the interface takes a **folder path**, and `drive.file` scope cannot resolve a path to a folder the operator made by hand in the web UI. The narrower scope would force folder IDs into the config, which §0.3.1 rules out. Note the tradeoff in `README.md`: the token on this host can read the operator's whole Drive.
- Resolve the configured folder path to an ID once per run by walking path segments from `root` (`name = '<segment>' and '<parent>' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false`). Zero matches → clear error naming the missing segment. More than one → exit 14, same rule as everything else.
- Cloud console: project → enable **Google Drive API** → OAuth consent screen (External, testing, operator as test user) → **OAuth client ID**, type *Desktop app* → download `credentials.json`.
- First run opens a browser and writes `token.json`. No browser on the host → device/console flow, note it in `NOTES.md`.
- Both files mode `0600`, outside the repo, paths in `config.toml`.

### 3.2 NotebookLM

```bash
uv tool install notebooklm-mcp-cli
nlm login            # browser login, extracts cookies, saves a persistent profile
nlm login --check
nlm doctor
```

Cookies last roughly 2–4 weeks and auto-refresh from the saved browser profile; a fully expired Google session needs an interactive `nlm login`. Keep that profile on this host.

### 3.3 Failure must be legible

Distinct, greppable exit per class. The operator often reads this from a phone.

| Exit | Class | Message contains | Action |
|---|---|---|---|
| 10 | Drive auth | `DRIVE_AUTH` | re-run OAuth consent |
| 11 | NotebookLM auth | `NLM_AUTH` | `nlm login` on the host |
| 12 | Quota | `NLM_QUOTA` | wait (Pro limits; not currently binding) |
| 13 | Conversion damage | `FIDELITY` | inspect the Doc; do not proceed |
| 14 | Ambiguous target | `AMBIGUOUS` | duplicate notebook or Drive name — resolve by hand |
| 15 | Post-load mismatch | `MISMATCH` | expected vs live source count differs; rerun |
| 16 | Indexing never finished | `NOT_READY` | wait, then `cli.py status` |

Never let a Drive failure surface as a traceback that looks like a NotebookLM failure.

---

## 4. Load

`cli.py load --project X --notebook "Name"`:

```
1. resolve notebook by name (§4.1); create if absent
2. acquire locks/<notebook>.lock  (exit 14 if held)
3. files = select(local_folder, masks)          # exit if empty — never load nothing
4. delete every existing source whose title matches the masks (§4.3)
5. upload every file to Drive, overwriting by name (§4.2)
6. add all Docs as Drive sources, one call if multi-add exists (§4.4)
7. enumerate; assert one source per file (§4.5)
8. wait for indexing (§5)
9. release lock
```

Order note: deleting before uploading keeps the source count at steady state and matches the manual habit. Since a rerun is always safe, there is no need to overlap the phases for safety.

### 4.1 Notebook by name

`notebook_list`, match on exact title. Zero matches → `notebook_create`, and report `notebook_created: true` in the envelope. **Two or more matches → exit 14**; NotebookLM permits duplicate titles and the tool must not pick one. The IDs go in `error.detail` — this is the one place they surface (§0.3.1) — so the operator can rename or delete the stray in the UI.

### 4.2 Drive upload, overwrite by name

For each file, list the target folder for an existing Doc of that name.

- None → create:

```python
meta = {"name": stem,
        "mimeType": "application/vnd.google-apps.document",
        "parents": [folder_id]}
media = MediaFileUpload(path, mimetype="text/plain", resumable=False)
drive.files().create(body=meta, media_body=media, fields="id").execute()
```

- Exactly one → update in place (file ID and URL preserved, body replaced):

```python
media = MediaFileUpload(path, mimetype="text/plain", resumable=False)
drive.files().update(fileId=existing_id, media_body=media, fields="id").execute()
```

- More than one → **exit 14.** Duplicate Drive names are how the same chunk gets loaded twice.

Uploading `text/plain` against a target of `application/vnd.google-apps.document` triggers Google's conversion; update re-applies it.

**Fidelity check.** After writing, `files().export(fileId, mimeType="text/plain")` and diff against the local file. These are bundled source files: confirm function *bodies* survive, not just that byte counts are close. Whitespace-only drift is acceptable; anything touching identifiers or string literals is exit 13, stop before loading anything into the notebook.

Old batches are never deleted from Drive. This is deliberate.

### 4.3 Delete by mask

Enumerate the notebook's sources; delete those whose title starts with any configured mask. Never delete anything else — not unmatched sources, not `Q-` sources, not sources the operator added by hand.

Source titles derive from Doc names, so mask matching works on titles directly. Confirm that in M4; if titles are rewritten or truncated, record it in `NOTES.md` and adjust the matcher before proceeding.

### 4.4 Add sources

```
source_add(notebook_id, source_type="drive", document_id=<id>,
           doc_type="doc", wait=True, wait_timeout=300)
```

If `source_add` accepts a list of document IDs, load the batch in one call — that mirrors the web picker and is likely to be faster and less error-prone than 20 sequential calls. If not, loop, and treat `wait=True` per source as processing-started rather than proof of readiness.

### 4.5 Verify presence

Enumerate. Assert exactly one source per uploaded file, matching by title. Mismatch → exit 15 with the diff printed both ways (missing / unexpected). Confirm in M4 that enumeration is not silently paginated — page it if it is.

---

## 5. Readiness

Indexing takes minutes for a batch this size. Two possible signals, in order of preference:

### 5.1 Per-source status (preferred)

If enumeration exposes a processing/indexing state — the data behind the web UI's spinner — poll until every source in the batch reports ready. This is the honest signal. Poll every 15s, ceiling from config (default 20 minutes), then exit 16.

### 5.2 Smoke query (fallback)

If no status field exists, run the project's `smoke_question` and check the answer contains `smoke_expect`. Pick a question answerable only from inside a function body — that also re-verifies the §0.1.1 fidelity property on every load. Back off 30s / 60s / 120s / 240s, then exit 16.

Either way: log observed indexing time per run in `NOTES.md`. After a few batches the operator will know the real ceiling. `ask` refuses to run while the lock is held.

---

## 6. The long-question protocol — `ask.py`

```
1. refuse to run if the notebook's lock is held
2. source_add(nb, source_type="text", title="Q-<utc-timestamp>-<slug>",
              text=<long question body>, wait=True)      -> qid
3. notebook_query(nb, "Answer the question in source <title>, "
                      "using the other sources as evidence.")
4. save the answer
5. source_delete(nb, source_id=qid, confirm=True)         # in a finally block
```

- Step 5 in `finally`. An abandoned question source pollutes retrieval for every later answer.
- `--keep` skips deletion when debugging; `cli.py sweep --notebook X` deletes every source titled `Q-*`. You will strand some during development.
- Default query budget is 120 seconds wall-clock. For heavy questions over a 20-source code corpus, use `notebook_query_start` + poll `notebook_query_status` until `completed` or `error`, rather than raising the sync timeout.
- Queries issued via CLI/MCP persist into the web UI chat history; `chat_get` / `chat_export` retrieve transcripts.
- Write each answer to `answers/<notebook-slug>/<timestamp>-<slug>.md` with the question body, the answer, and the live source list at time of asking.
- **Chat history caveat:** reloading a batch breaks citations in older chat sessions that pointed at deleted sources. Expected; note it in `README.md` so it isn't mistaken for a bug.

Question sources live in the same notebook as the corpus — a separate notebook cannot see the sources, so referencing questions would break there.

---

## 7. Milestones and acceptance tests

Work in order. Record results in `tests/test_integration.md` with timestamps.

### M0 — Environment
`nlm login --check` passes, `nlm doctor` clean, Drive OAuth completes. Print both identities — the Google account for Drive and the account `nlm` is logged into — and **confirm they match**. Mismatch causes confusing permission errors much later.

### M1 — Fixtures and API reconnaissance
Copy **three real chunks** from one real batch into a test folder — real naming, real bundled classes, not synthetic prose. Record which classes each contains, and choose `smoke_question` / `smoke_expect` from inside a function body. Then answer the two §0.2 questions in `NOTES.md`: does `source_add` take multiple IDs, and does enumeration expose index status?

### M2 — Selection
`select.py` unit tests: masks match the right files, `.txt` only, `Q-` never matched, empty result is an error not a no-op. No network.

### M3 — Drive upload and fidelity
`cli.py load --project test --drive-only`. **Accept only if:** three native Google Docs appear, the export round-trip diff shows function bodies intact (pick the longest method in each chunk and read it), and a second run **updates in place** — same file IDs, no duplicates in the folder.

### M4 — Load into a new notebook
`cli.py load --project test --notebook "Bridge M4"`. **Accept only if:**

1. The notebook is created (it did not exist), and rerunning targets the same one rather than making a second.
2. Three sources appear, titled as the Doc names — confirm masks match those titles (§4.3).
3. Enumeration returns all three, and a second call agrees.
4. Readiness completes via whichever signal M1 established, and the smoke query returns `smoke_expect` with a citation. If that fails, the problem is M3's fidelity, not this step.

### M5 — Reload, and the scoping guarantee
Add a fourth source **by hand** in the web UI, named outside the masks. Edit one chunk locally. Rerun `load`. **Accept only if:**

1. The three mask-matching sources are deleted and re-added; the hand-added one is untouched.
2. The Drive Doc for the edited chunk keeps its file ID and shows new content.
3. Asking about the edited value returns the **new** value.

Point 1 is the scoping guarantee from §0.3.2 and matters as much as point 3.

### M6 — Second notebook, same batch
`cli.py load --project test --notebook "Bridge M6"` without touching local files. **Accept only if:** a second notebook is created with the same three sources, and "Bridge M4" is unaffected. Confirms two notebooks over one batch, and that re-upload to Drive is harmless.

### M7 — Long-question protocol
Write a ~500+ word question — real analytical instructions about the three chunks (comparison criteria, output structure, exclusions), not filler. Run `cli.py ask`. **Accept only if:** a `Q-...` source appears then disappears; the answer addresses the full instruction set rather than a generic summary; the notebook is left with exactly its pre-question sources.

### M8 — Failure modes
- Kill the process mid-load. Rerun the whole batch. Converges, no duplicates.
- **Delete any cache the implementation keeps.** Rerun. Slower, identical result (§0.3.3). If behaviour changes, the cache is load-bearing and must be fixed.
- **Agent-drivability:** run every command with `--json`, pipe stdout through a JSON parser, confirm it parses cleanly with nothing but the envelope on stdout. Repeat for a failure case (exit 14) and confirm the envelope is still complete and parseable.
- Create a second notebook titled "Bridge M4" by hand. Confirm exit 14, both IDs printed, nothing modified. Delete it afterwards.
- Point a project at an empty mask result. Confirm it refuses rather than deleting sources and loading nothing.
- Simulate both auth failures (bad `token.json`; garbage `NOTEBOOKLM_COOKIES`) → distinguishable exit codes.
- Hold a lock; confirm `ask` refuses and a second `load` on that notebook refuses, while a `load` on a *different* notebook proceeds.

### M9 — Full batch
Point at a real full batch (~20 files). Record total time, Drive upload time, and **observed indexing duration** in `NOTES.md`. Then load the same batch into a second notebook and time that.

---

## 8. Then, and only then: MCP

Once the CLI passes, register the MCP server for interactive use:

```bash
nlm setup add claude-code
nlm skill install claude-code
```

Trim the tool surface — the server exposes ~43 tools:

```
NOTEBOOKLM_DISABLED_GROUPS="studio,research,sharing,notes,organization,automation"
```

Keep `notebooks_read`, `sources_read`, `sources_manage`, `chat`. Verify group names against `nlm --ai`; unknown names are ignored silently, so a typo looks like it worked.

Add to the repo's `CLAUDE.md`: the design principles (§0.3), the long-question protocol (§6), and above all the rule from §0.1.1 that a source is **never** added as a local file upload. An interactive session from a phone must not improvise that shortcut.

Remote access is via **Claude Code Remote Control** — the session runs on this host and is continued from the Claude mobile app. Do not expose the MCP HTTP transport to the network: it has no authentication, and anyone reaching it controls the whole Google account.

---

## 9. Report back

Produce a short summary containing: the exact `nlm` version tested against, the answers to the two §0.2 reconnaissance questions, every place this brief was wrong (from `NOTES.md`), the M3 fidelity result, the M5 before/after answers verbatim, the observed indexing duration from M9, and any principle in §0.3 you could not honour. If something here is unbuildable as specified, stop and say so rather than substituting an approach that breaks §0.1 or §0.3.
