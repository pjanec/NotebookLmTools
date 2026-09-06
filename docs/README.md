# docs/

Two documents, with different standing. Do not confuse them.

| File | Status | Authority |
|---|---|---|
| `requirements-original-brief.md` | **Frozen.** As received, never edited. | The original statement of intent. Historical. |
| `design.md` | **Living.** Kept in sync with the implementation. | **Authoritative.** Where it differs from the brief, it wins. |
| `bootstrap-new-machine.md` | Procedure. | Installing and **verifying** on a new Windows machine, written to be followed by an agent. Ends in a live end-to-end check. |
| `google-oauth-setup.md` | Procedure. | The one-time Google Cloud step, written from an actual run through the console. |
| `remote-control-setup.md` | Procedure + threat model. | Driving the Windows machine from a Claude cloud VM through a private git queue, with nothing listening on either side. |
| `refreshing-the-bundle-guide.md` | Operator-facing. | When the architect's snapshot has drifted too far from the working copy, and how to replace it. Companion to the guide below. |
| `ask-the-architect-guide.md` | Operator-authored. | Drop-in guidance for a consuming project's `CLAUDE.md`: how an AI agent should consult the notebook, and why it must verify every claim against the code. |

`requirements-original-brief.md` is the brief the operator handed over at the start. It is
kept verbatim so the reasoning behind the non-negotiables — above all *why* a `.txt` must
never be uploaded to NotebookLM as a local file — stays readable in its original form. It
is **not** an accurate description of what gets built: several of its decisions were
overturned during design review (see "Deviations from the original brief" in `design.md`).

`design.md` is the spec. If you are implementing, read that one. Every deviation from the
brief is listed there with its rationale, so nothing is silently dropped.

`../NOTES.md` is the running log: undocumented-API surprises, measured timings, and
decisions made while building.
