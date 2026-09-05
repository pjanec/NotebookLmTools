# CLAUDE.md — NotebookLmTools

Working notes and hard rules for agents operating in this repository.

## Secrets: never commit, never write into tracked files

**Credentials, tokens, cookies, passwords, API keys, and OAuth client secrets must never
appear in any tracked file — not in docs, not in config, not in code, not in test
fixtures, not in `NOTES.md`, not in commit messages.**

This applies to everything this project touches, including:

- Google Drive: the rclone OAuth token and the `rclone.conf` encryption password
- NotebookLM: browser cookies, `NOTEBOOKLM_COOKIES`, the saved `nlm` browser profile
- Any bearer token, session cookie, or password pasted into the session by the operator

Rules:

1. **Nothing sensitive sits in plaintext on disk.** The rclone token lives in an encrypted
   `rclone.conf`; its password lives in **Windows Credential Manager** (DPAPI) and reaches
   rclone only through `RCLONE_CONFIG_PASS` on the child process. Never write it to a file,
   never log it, never put it in the JSON envelope. See `docs/design.md` §4.3.
2. Before writing any file, check whether the content carries a secret. If it does, write
   it outside the repo, or redact it (`<REDACTED>`) in the tracked version.
3. When documenting a procedure, describe *how to obtain* the credential; never paste an
   example of a real one. Placeholders only.
4. Anything secret-bearing goes in `.gitignore` **before** the file is created, not after.
5. If a secret does get committed, treat it as leaked: rotate/revoke it, then clean history.
   Do not assume a follow-up commit removing it is sufficient.

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

Two clarifications, so they are not re-litigated:

- **The question *text* bypasses Drive; attached *data files* do not.** `nlmt ask` adds the
  question body directly as `source_type="text"` — correct, and not an exception to the
  rule above, which forbids `source_type="file"` (a local *file upload*). A question is
  prose with no code bodies to lose, and is deleted minutes later. But an `--attach`ed JSON
  or CSV **is** the data the answer depends on, so it goes the proven route: uploaded to
  Drive as `<original-name>.txt`, checksum-verified, added as `source_type="drive"`, then
  deleted with the question. See `docs/design.md` §8.
- **The tools must teach themselves.** Help text and error messages are part of the
  product, not documentation about it: every argument documented with a pasteable example,
  `nlmt --ai` as a one-shot agent reference, and every error naming its corrective action.
  See `docs/design.md` §5.5. A behaviour change that does not update help is incomplete.

## Documents

- `docs/design.md` — **the authoritative spec.** Read this before implementing.
- `docs/requirements-original-brief.md` — frozen original requirements; superseded where
  it differs from the design. Do not edit it.
- `NOTES.md` — running log of API surprises, timings, and decisions. Where the installed
  `nlm` contradicts the design, the tool wins; record it here.
