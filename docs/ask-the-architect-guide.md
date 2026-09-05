## Asking the architect (NotebookLM)

A NotebookLM notebook — **"SimHost FDP review"** — holds this project's entire source
bundle (migth be a bit older that your current sources) plus all design documentation
(which might not always ne up to date and sources are the king) and acts as a **system
architect**. Consult it whenever a question is architectural rather than mechanical:
how a subsystem is meant to fit together, what the intended design or prior art was,
why something is structured the way it is, or what already exists before you build
something new. Do not use it for anything a local grep or file read answers — it is
slower and less certain than looking.

    D:\WORK\NotebookLM\.venv\Scripts\nlmt.exe ask ^
      --notebook "SimHost FDP review" --question-file <question>.md --json

Write the question to a file; long, specific questions get far better answers than
short ones, and up to ~8000 characters are sent inline. Attach a data file the question
refers to with `--attach <file>`. **Each call takes about two minutes** (roughly four
with an attachment), so ask few well-formed questions rather than many small ones, and
never in a loop. The conversation continues between calls by default, so you may warm
the architect up with a scoping question before asking the real one; `--fresh` starts a
clean thread. The answer is saved under `answers/` and the envelope reports `citations`.
Exit 11 means the session could not be renewed even automatically, and a human must run
`nlm login` — treat the architect as temporarily unavailable and continue without it
rather than blocking.

**Treat the architect as an expert colleague who is often right and occasionally
confidently wrong.** It reasons across multi-megabyte corpus you cannot hold in context
and routinely
surfaces design intent, cross-subsystem relationships and prior art that no amount of
local grepping would find — that is why it is worth two minutes. But it is an AI reading
a snapshot: it hallucinates plausible class and method names, misses things that are
genuinely present, and states inference as fact. **Every concrete claim is a lead, not a
finding.** Before acting on anything it says, open the named file and confirm the class,
method or field actually exists and behaves as described. Prefer claims carrying
citations and treat an uncited answer as weaker still. Never cite the architect as
authority in a commit message, a code comment or a decision record — cite the code you
verified instead.
