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
