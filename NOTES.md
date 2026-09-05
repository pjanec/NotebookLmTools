# NOTES

Running log: undocumented-API surprises, contradictions with the design, measured timings,
and decisions made while building. Where the installed `nlm` contradicts `docs/design.md`,
**the installed tool wins** — record it here and adjust the design.

Newest entries at the bottom of each section.

## Environment

| Item | Value | When |
|---|---|---|
| Host | Windows 11 Home 10.0.26200 | 2026-09-05 |
| Python (system) | 3.13.5 at `C:\Python313` | 2026-09-05 |
| `nlm` version | *not yet installed* | — |
| rclone version | *not yet installed* | — |

## Reconnaissance answers (design.md §12)

| # | Question | Answer | When |
|---|---|---|---|
| 1 | Does `source_add` take multiple document IDs? | *open* | |
| 2 | Does enumeration expose index status? | *open* | |
| 3 | Does `source_type="drive"` accept a plain `.txt`? | *open* | |
| 4 | What is a source title derived from; does `.txt` survive? | *open* | |
| 5 | Is enumeration paginated? | *open* | |
| 6 | Which `nlm` version is this verified against? | *open* | |

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

## Observed timings

| Date | Bundle | Files | Size | Upload | Indexing | Total |
|---|---|---|---|---|---|---|
| | | | | | | |

## Surprises and contradictions

*(none yet)*
