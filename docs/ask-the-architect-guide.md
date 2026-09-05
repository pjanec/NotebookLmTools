## Asking the architect (NotebookLM)

A NotebookLM notebook — **"SimHost FDP review"** — holds this project's entire source
bundle (~25 MB) plus all design documentation (~20 MB), and acts as a **system
architect**. Consult it whenever a question is architectural rather than mechanical:
how a subsystem is meant to fit together, what the intended design or prior art was,
why something is structured the way it is, or what already exists before you build
something new. Ask it from the repository root of the tools:

    D:\WORK\NotebookLM\.venv\Scripts\nlmt.exe ask ^
      --notebook "SimHost FDP review" --question-file <question>.md --fresh --json

Write the question to a file — it can be long, and long, specific questions get far
better answers than short ones. Use `--fresh` for a self-contained question; omit it to
continue the previous thread, which is useful when you deliberately warm the architect
up with a scoping question before asking the real one. Each call takes a minute or two,
so ask few, well-formed questions rather than many small ones, and never in a loop. The
answer is saved under `answers/` and the JSON envelope reports `citations`.

**Treat the architect as an expert colleague who is often right and occasionally
confidently wrong.** It reasons across 45 MB that you cannot hold in context, and it
routinely surfaces design intent, cross-subsystem relationships and prior art that no
amount of local grepping would find — that is why it is worth asking. But it is an AI
reading a snapshot: it hallucinates plausible class and method names, misses things that
are genuinely present, and states inferences as fact. **Every concrete claim is a lead,
not a finding.** Before acting on anything it says, verify it against the real code —
open the named file, confirm the class, method or field actually exists and behaves as
described. Prefer claims that come with citations, and treat an answer with no citations
as weaker still. Never quote it as authority in a commit message, a code comment or a
decision record; cite the code you verified instead.