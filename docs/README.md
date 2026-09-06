# docs/

Two documents, with different standing. Do not confuse them.

| File | Status | Authority |
|---|---|---|
| `requirements-original-brief.md` | **Frozen.** As received, never edited. | The original statement of intent. Historical. |
| `design.md` | **Living.** Kept in sync with the implementation. | **Authoritative.** Where it differs from the brief, it wins. |
| `bootstrap-new-machine.md` | Procedure. | Installing and **verifying** on a new Windows machine, written to be followed by an agent. Ends in a live end-to-end check. |
| `google-oauth-setup.md` | Procedure. | The one-time Google Cloud step, written from an actual run through the console. |
| `configuration.md` | Reference. | Every config file with sample content, what is generated, and what must never be committed. Enough to install from the documents alone. |
| `remote-control.md` | **Design + procedure.** | Driving the Windows machine from a Claude cloud VM through a private git queue: why it is shaped that way, what the trust boundary guarantees, how to install it, and the full config reference. |
| `onboarding-a-project.md` | Procedure. | Adding a new project to a working installation: what to do on GitHub, on Windows, and on the cloud VM. |
| `consulting-the-architect.md` | **For consuming projects.** | The canonical guide an agent follows: when to ask, how, keeping the snapshot current, and what standing an answer has. Link to it from a project's `CLAUDE.md` rather than copying it. |

`requirements-original-brief.md` is the brief the operator handed over at the start. It is
kept verbatim so the reasoning behind the non-negotiables — above all *why* a `.txt` must
never be uploaded to NotebookLM as a local file — stays readable in its original form. It
is **not** an accurate description of what gets built: several of its decisions were
overturned during design review (see "Deviations from the original brief" in `design.md`).

`design.md` is the spec. If you are implementing, read that one. Every deviation from the
brief is listed there with its rationale, so nothing is silently dropped.

`../NOTES.md` is the running log: undocumented-API surprises, measured timings, and
decisions made while building.
