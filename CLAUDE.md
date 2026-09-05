# CLAUDE.md — NotebookLmTools

Working notes and hard rules for agents operating in this repository.

## Secrets: never commit, never write into tracked files

**Credentials, tokens, cookies, passwords, API keys, and OAuth client secrets must never
appear in any tracked file — not in docs, not in config, not in code, not in test
fixtures, not in `NOTES.md`, not in commit messages.**

This applies to everything this project touches, including:

- Google Drive OAuth: `credentials.json`, `token.json`, client IDs/secrets, refresh tokens
- NotebookLM: browser cookies, `NOTEBOOKLM_COOKIES`, the saved `nlm` browser profile
- Any bearer token, session cookie, or password pasted into the session by the operator

Rules:

1. Secret material lives **outside the repo**, mode `0600`, referenced from config by
   **path only**. A config file may contain `token_path = "/home/op/.secrets/token.json"`;
   it may never contain the token itself.
2. Before writing any file, check whether the content carries a secret. If it does, write
   it outside the repo, or redact it (`<REDACTED>`) in the tracked version.
3. When documenting a procedure, describe *how to obtain* the credential; never paste an
   example of a real one. Placeholders only.
4. Anything secret-bearing goes in `.gitignore` **before** the file is created, not after.
5. If a secret does get committed, treat it as leaked: rotate/revoke it, then clean history.
   Do not assume a follow-up commit removing it is sufficient.

## Docs

Design documents and session-produced notes live in `docs/` and are tracked.
Current: `docs/notebooklm-bridge-design.md` — the build & test brief for the batch loader.

## Interaction style

**No question widgets.** Never use interactive option pickers, multiple-choice prompts,
or any other UI widget to ask the operator something. All conversation happens as plain
chat text: ask the question in prose, and let the operator answer in prose.

## The rule that must never be improvised away

**A `.txt` is NEVER added to a NotebookLM notebook as a local file upload.**
Always `source_type="drive"` with a Drive file ID — never `source_type="file"`.

NotebookLM's own text ingestion strips function bodies out of bundled source code, keeping
only headers and signatures. The failure is silent: the sources are there, the answers read
fluently, and the code is gutted. Routing through Google Drive is the entire reason Drive is
in this pipeline. If a shortcut ever looks tempting — "we could just upload the file
directly" — that is the exact failure this project exists to prevent.

Related: files on Drive stay **plain UTF-8 `text/plain`, byte-identical, BOM preserved**.
Never convert them to native Google Docs.

## Documents

- `docs/design.md` — **the authoritative spec.** Read this before implementing.
- `docs/requirements-original-brief.md` — frozen original requirements; superseded where
  it differs from the design. Do not edit it.
- `NOTES.md` — running log of API surprises, timings, and decisions. Where the installed
  `nlm` contradicts the design, the tool wins; record it here.

Two clarifications that follow from that rule, so they are not re-litigated:

- **Question sources bypass Drive.** `nlmt ask` adds the question with
  `source_type="text"`, directly. That is correct and not an exception to the rule above:
  what is forbidden is `source_type="file"` (a local *file upload*), because file ingestion
  strips code bodies. A question is prose and is deleted minutes later.
- **The tools must teach themselves.** Help text and error messages are part of the
  product, not documentation about it: every argument documented with a pasteable example,
  `nlmt --ai` as a one-shot agent reference, and every error naming its corrective action.
  See `docs/design.md` §5.5. A behaviour change that does not update help is incomplete.
