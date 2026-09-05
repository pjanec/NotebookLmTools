"""Synthetic test bundles (design.md 9).

No real source data exists yet, so tests run against generated bundles that are
structurally realistic: bundled source with file headers, class and method declarations,
and -- the part that matters -- **facts buried inside method bodies**.

Those buried facts are the whole point. NotebookLM's local-file ingestion strips function
bodies while keeping headers and signatures (design.md 1.1), so a fact that lives only
inside a body is a detector for that exact failure: if the answer comes back right, the
bodies survived. The manifest turns every buried fact into a question/expected pair, so
the smoke probe is derived from ground truth instead of hand-picked.

A bundle deliberately mixes BOM and non-BOM UTF-8 files, because both occur in the real
data and both must round-trip byte-identically.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path

BOM = "﻿"

_MODULES = [
    ("Engine", ["ConnectionPool", "RetryPolicy", "SessionCache", "FrameDecoder"]),
    ("Core", ["MessageBus", "LockManager", "ClockSource", "BufferAllocator"]),
    ("Storage", ["PageWriter", "IndexScanner", "CompactionJob", "JournalReader"]),
    ("Net", ["SocketReader", "BackoffTimer", "HandshakeState", "PacketRouter"]),
]

_KNOBS = [
    ("retryLimit", "the retry limit"),
    ("backoffMs", "the backoff in milliseconds"),
    ("poolCeiling", "the connection pool ceiling"),
    ("staleAfterSeconds", "the staleness threshold in seconds"),
    ("maxFrameBytes", "the maximum frame size in bytes"),
    ("compactionBatch", "the compaction batch size"),
]

_FILLER_BODY = """        var scratch = context.Rent({rent});
        try
        {{
            for (var i = 0; i < scratch.Length; i++)
            {{
                scratch[i] = unchecked((byte)(i * {mult} + {offset}));
            }}
            context.Commit(scratch, "{tag}");
        }}
        finally
        {{
            context.Return(scratch);
        }}"""


@dataclass
class Fact:
    """A value that exists only inside a method body."""

    file: str
    cls: str
    method: str
    variable: str
    value: int
    question: str
    expect: str


def _fact_method(cls: str, method: str, variable: str, value: int, description: str) -> str:
    return f"""    public int {method}(ExecutionContext context)
    {{
        // {description} for {cls}; tuned against the 2026 soak run.
        int {variable} = {value};
        if (context.Degraded)
        {{
            return {variable} / 2;
        }}
        return {variable};
    }}
"""


def _filler_method(index: int, rng: random.Random) -> str:
    body = _FILLER_BODY.format(
        rent=rng.randrange(64, 4096),
        mult=rng.randrange(3, 97),
        offset=rng.randrange(1, 255),
        tag=f"span-{index:03d}",
    )
    return f"""    public void Process{index:03d}(ExecutionContext context)
    {{
{body}
    }}
"""


def _render_file(
    module: str,
    batch: int,
    index: int,
    classes: list[str],
    rng: random.Random,
    target_bytes: int,
    facts: list[Fact],
    file_name: str,
) -> str:
    parts = [
        "// ---------------------------------------------------------------------------",
        f"// bundle: {module} batch {batch:02d} chunk {index:02d}",
        f"// file:   {file_name}",
        "// generated fixture - synthetic source, no real code",
        "// ---------------------------------------------------------------------------",
        "",
        f"namespace {module}.Generated",
        "{",
    ]

    filler_index = 0
    for cls in classes:
        parts.append(f"    public sealed class {cls}")
        parts.append("    {")

        variable, description = _KNOBS[rng.randrange(len(_KNOBS))]
        value = rng.randrange(101, 9973)
        method = f"Resolve{variable[0].upper()}{variable[1:]}"
        facts.append(
            Fact(
                file=file_name,
                cls=cls,
                method=method,
                variable=variable,
                value=value,
                question=(
                    f"In class {cls}, method {method}, what is the value assigned to "
                    f"the local variable {variable}? Answer with the number only."
                ),
                expect=str(value),
            )
        )
        parts.append(_fact_method(cls, method, variable, value, description))

        while sum(len(p) for p in parts) < target_bytes * (classes.index(cls) + 1) // len(
            classes
        ):
            parts.append(_filler_method(filler_index, rng))
            filler_index += 1

        parts.append("    }")
        parts.append("")

    parts.append("}")
    parts.append("")
    return "\n".join(parts)


def generate_bundle(
    out_dir: Path,
    *,
    files: int = 3,
    batch: int = 17,
    target_bytes: int = 8_000,
    seed: int = 20260905,
    prefix: str | None = None,
) -> dict:
    """Write a bundle plus its manifest. Returns the manifest.

    `target_bytes` is the approximate size of each file; the default is small so tests
    stay fast. M9 scales it to ~1 MB across ~20 files.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)

    facts: list[Fact] = []
    entries = []
    for index in range(1, files + 1):
        module, classes = _MODULES[(index - 1) % len(_MODULES)]
        name_prefix = prefix or module
        file_name = f"{name_prefix}_b{batch:02d}_{index:02d}.txt"
        text = _render_file(
            module, batch, index, classes, rng, target_bytes, facts, file_name
        )
        # Alternate BOM and non-BOM: both occur in the real data and both must
        # round-trip byte-identically through Drive.
        with_bom = index % 2 == 1
        payload = ((BOM + text) if with_bom else text).encode("utf-8")
        path = out_dir / file_name
        path.write_bytes(payload)
        entries.append(
            {
                "name": file_name,
                "bytes": len(payload),
                "bom": with_bom,
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )

    manifest = {
        "bundle": out_dir.name,
        "batch": batch,
        "seed": seed,
        "files": entries,
        "facts": [asdict(f) for f in facts],
        "smoke": {
            # A question answerable only from inside a method body, so every load
            # re-verifies the property design.md 1.1 exists to protect.
            "question": facts[0].question,
            "expect": facts[0].expect,
            "source": facts[0].file,
        },
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return manifest
