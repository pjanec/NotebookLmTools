# Consulting the architect

**For an AI agent working in a project whose corpus is loaded into NotebookLM.** This is the
whole procedure: when to ask, how to ask, how to keep the snapshot current, and — the part
that matters most — what standing the answer has.

Link to this document from your project's `CLAUDE.md` rather than copying it. It changes as
the tools do.

---

## What it is

A NotebookLM notebook holds a snapshot of a project's **sources** (which might be a bit
older than your current working tree) **and its design documentation** (which might not
always be up to date — the sources are the king). It acts as a **system architect**: it
reasons across a corpus far larger than any context window, and routinely surfaces design
intent, cross-subsystem relationships and prior art that no amount of local grepping would
find.

That is the whole reason it is worth minutes of waiting: **cross-module questions.** A
coding agent already handles a single small area well on its own.

## What standing its answers have

**It is additional feedback, and never a decision.** Treat it as an expert colleague who is
often right and occasionally confidently wrong. It hallucinates plausible class and method
names, misses things that are genuinely present, and states inference as fact.

Four rules follow, and they are not optional:

1. **The user has the last word.** An answer contributes to a resolution; it never
   constitutes one. Agreement from the architect is not approval to act.
2. **Never consult it silently.** Say you intend to ask, show the question first, and report
   the answer **as an input, not as a finding**. The user must be able to see that a machine
   opinion entered the discussion, and to discount it.
3. **Verify every load-bearing claim against source** before acting on it or recording it as
   settled: open the named file, confirm the class, method or field exists and behaves as
   described. Prefer claims that carry citations; treat an uncited claim as weak.
4. **Attribute it.** Say a claim came from the architect and whether it was verified. A
   claim whose origin is invisible cannot be re-checked later.

Your project's own convention for recording architectural questions — how the documents are
named, structured and revised — is **unchanged** by any of this. A relayed answer is folded
in as evidence like any other; it does not replace the document and does not get a format of
its own.

## When to ask, and when not to

**Worth asking:** how a subsystem is meant to fit together; what the intended design or
prior art was; why something is shaped the way it is; what already exists before you build
something new; how a change propagates end to end across subsystems.

**Not worth asking:** anything a local grep or file read settles. That is faster, cheaper and
more certain. Also skip it for questions confined to code you have just written — the
snapshot does not contain it.

---

## Asking

Write the question to a **file**. Long, specific questions get far better answers than short
ones; up to about 8,000 characters travel inline.

### From a cloud VM, through the relay

The credentials and the corpus live on an always-on machine, which you reach through a
git-backed job queue. Nothing on your side holds credentials.

```
git clone https://github.com/<you>/nlm-ops.git ~/nlm-ops          # once per VM

python ~/nlm-ops/client.py --ops-repo ~/nlm-ops --project <project> \
    --notebook "<notebook>" ask --question-file <question>.md
```

`--attach <file>` adds a text-like data file the question refers to; repeatable. Omit
`--notebook` to reuse whichever was last used for that project.

**The answer comes back to you.** `client.py` prints it, and it is in the result's `answer`
field, so a cloud session reads the answer directly and never needs anyone to paste it
across. A full transcript — question, attachments, answer, and the live source list at the
time of asking — is also saved on the always-on machine, outside any repository, because it
quotes proprietary source. That is a record for the operator, not the delivery mechanism:
**do not go looking for the transcript file; use the answer you were given.**

### On the machine itself

```
nlmt ask --notebook "<notebook>" --question-file <question>.md
```

## Keeping the snapshot current

The architect reads a snapshot, not your working tree. Refresh it **before the first
question of a session**, and after changing code the question is about:

```
python ~/nlm-ops/client.py --ops-repo ~/nlm-ops --project <project> sync --ref <branch>
python ~/nlm-ops/client.py --ops-repo ~/nlm-ops --project <project> refresh
```

`sync` puts the always-on machine on your branch; `refresh` regenerates the bundle and
reloads it. Refresh at natural boundaries — after a subsystem lands — not after every commit.

**Deciding whether it is stale enough to matter.** The decisive question is not *how much*
has changed but **whether what changed is what you are asking about**:

- Refresh if files your question concerns have changed, if a subsystem was renamed, moved or
  deleted, or if the design documents moved on and your question is about intent.
- Do not bother if the drift is confined to areas your question does not touch, or if only
  tests and build files changed.
- If the architect describes something you cannot find in the working copy, suspect drift
  and refresh.

A stale answer is dangerous precisely because it arrives confident and well-formed,
describing a design that was correct until recently.

## Cost

| | |
|---|---|
| a question | one to four minutes |
| a refresh | several minutes |

Ask few well-formed questions rather than many small ones, and **never in a loop**.

## When it fails

A non-zero exit means the architect could not answer, and the message carries a `hint`
saying what to do. **Carry on with what you can do without it** rather than blocking or
retrying.

| Exit | Meaning |
|---|---|
| 11 | the NotebookLM session could not be renewed automatically; a human must sign in on the always-on machine |
| 13 | content did not survive ingestion — **do not trust that notebook** until it is understood |
| 16 | indexing did not finish in time; retry later |
| 17 | another job holds the lock; wait |
| 19 | the command line was wrong; the message names the fix |

Two symptoms worth recognising, since neither announces itself:

- **A slice missing from the notebook** — a dump output whose prefix is not covered by a
  mask is never uploaded, and the architect answers as though that code did not exist.
- **Leftover `Q<n>-` sources** in `nlmt status` — an interrupted ask. They corrupt later
  answers, because a question source is instruction-shaped text sitting in the corpus. The
  next ask clears them automatically; `nlmt sweep` does it directly.

---

## Further reading

| | |
|---|---|
| [`remote-control.md`](remote-control.md) | how the relay works and why it is safe |
| [`onboarding-a-project.md`](onboarding-a-project.md) | adding a project to the relay |
| [`design.md`](design.md) | how the tools themselves work |
