# Keeping the architect's copy current

Guidance for an AI agent working in the source project. Companion to
[`ask-the-architect-guide.md`](ask-the-architect-guide.md), which covers asking; this covers
when what it is reading is too old to trust.

The architect answers from a **snapshot** of the code and documentation, not from the
working tree. Everything it says is therefore true of the snapshot, and only as true of your
working copy as the two still agree.

---

## The rule

**Establish a baseline before the first question of a session. After that, ask freely, and
refresh only when the working copy has drifted far enough that answers about the changed
areas would mislead.**

A refresh costs a few minutes. A wrong answer accepted because the snapshot was stale costs
much more, and is hard to notice — the architect will describe the old design confidently
and correctly, for a version of the code that no longer exists.

---

## Deciding whether to refresh

### 1. How old is the baseline?

```
dir /O-D "D:\WORK\IOS-IG-SimHost-FDP\.dumps\*.txt"
```

The newest timestamp there is when the snapshot was taken.

### 2. What has changed since?

The source project is a git repository, so this is answerable rather than a guess. Find the
commit that was current when the dump was taken, then diff to now:

```
cd D:\WORK\IOS-IG-SimHost-FDP
git rev-list -1 --before="<dump timestamp>" HEAD
git diff --stat <that commit>..HEAD
git status --porcelain
```

The first two show committed drift; the third shows uncommitted work, which the last dump
also missed.

### 3. Judge

Refresh when **any** of these holds:

- Files that your question is about have changed since the dump. This is the important one:
  drift only matters where you are asking.
- A subsystem was renamed, moved, split or deleted. The architect will keep describing a
  structure that no longer exists, and — worse — will do so plausibly.
- Roughly 50+ files or 2000+ lines have changed. A rough line, not a rule.
- The design documents changed, and your question is about intent rather than mechanism.

Do **not** refresh when:

- The drift is confined to areas your question does not touch.
- Only tests, build files or generated output changed.
- You already refreshed this session and nothing has been rebuilt since.

When it is genuinely marginal, ask anyway and **verify the specific claims against the
working copy**, which you must do regardless (see the companion guide). If verification
keeps finding the architect describing code that has since moved, that is your answer:
refresh.

---

## Refreshing

### 1. Regenerate the bundle

```
cd D:\WORK\IOS-IG-SimHost-FDP
dump.bat
```

This writes a **new batch with a new ordinal** — `HROT.Eng_275.01.txt` becomes
`HROT.Eng_276.01.txt`, and so on.

### 2. Check that only one batch is present

**This matters.** The ordinal is part of the filename, so a new batch does not overwrite the
old one. If both remain in `.dumps\`, the next load uploads **both**, and the architect ends
up reading two versions of the same code — which is worse than a stale snapshot, because it
will answer from whichever it happens to retrieve.

```
dir "D:\WORK\IOS-IG-SimHost-FDP\.dumps\*.txt"
```

Every `FDP.*` and `HROT.*` file must carry the same ordinal. Delete any older-ordinal files
before loading.

### 3. Load

```
D:\WORK\NotebookLM\.venv\Scripts\nlmt.exe load ^
  --notebook "SimHost FDP review" ^
  --local-folder "D:\WORK\IOS-IG-SimHost-FDP\.dumps" ^
  --mask "HROT." --mask "FDP." --mask "Docs." ^
  --project simhost --json
```

**The old batch is removed automatically.** The masks match on a prefix, so `HROT.` matches
both `HROT.Eng_275.01.txt` and `HROT.Eng_276.01.txt`: `load` deletes every source starting
with a mask and replaces them with what is on disk now. There is no separate delete step,
and no need to name the old ordinal.

Nothing is deleted from the notebook until the new files are on Drive and checksum-verified,
and the command returns only once every source has finished indexing — so when it exits 0,
the notebook is ready to query.

**Expect three to five minutes** for a full bundle. Exit 0 with `ready: true` and matching
`selected` / `uploaded` / `verified` / `added` counts means it worked. Any other exit code
carries a `hint` saying what to do; exit 13 means content did not survive intact and the
notebook should not be trusted until it is understood.

### 4. Confirm before relying on it

```
D:\WORK\NotebookLM\.venv\Scripts\nlmt.exe status --notebook "SimHost FDP review"
```

Every source should be `ready`, the titles should all carry the **new** ordinal, and there
should be no `Q<n>-` sources left over.

---

## About the design documents

`dump.bat` regenerates the **source** bundle only — the `FDP.*` and `HROT.*` files. The
`Docs.*` files come from a separate process that is not part of that script, and they carry
no ordinal, so a refresh of them would overwrite in place.

If a question turns on design intent and the documentation has moved on, regenerating the
sources alone will not help. **Ask the operator how the `Docs.*` bundle is produced** rather
than assuming it is current or trying to reproduce it.

---

## Cadence, in practice

| Situation | Do |
|---|---|
| First question of a session | Check the baseline age and drift. Refresh if it is stale. |
| Subsequent questions, no rebuild | Ask freely. No refresh. |
| You have just changed code the question is about | Refresh, or restrict the question to the parts you have not touched. |
| A long session with continuous edits | Refresh at natural boundaries — after a subsystem lands — not after every commit. |
| The architect describes something you cannot find | Suspect drift. Verify against the working copy; if it has moved, refresh. |

The architect is a second opinion over a corpus too large to hold in context, not a source
of truth. **The working copy is the truth**, a refresh only narrows the gap, and every
concrete claim still has to be checked against the real code before you act on it.
