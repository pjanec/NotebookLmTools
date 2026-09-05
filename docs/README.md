# docs/

Two documents, with different standing. Do not confuse them.

| File | Status | Authority |
|---|---|---|
| `requirements-original-brief.md` | **Frozen.** As received, never edited. | The original statement of intent. Historical. |
| `design.md` | **Living.** Kept in sync with the implementation. | **Authoritative.** Where it differs from the brief, it wins. |

`requirements-original-brief.md` is the brief the operator handed over at the start. It is
kept verbatim so the reasoning behind the non-negotiables — above all *why* a `.txt` must
never be uploaded to NotebookLM as a local file — stays readable in its original form. It
is **not** an accurate description of what gets built: several of its decisions were
overturned during design review (see "Deviations from the original brief" in `design.md`).

`design.md` is the spec. If you are implementing, read that one. Every deviation from the
brief is listed there with its rationale, so nothing is silently dropped.

`../NOTES.md` is the running log: undocumented-API surprises, measured timings, and
decisions made while building.
