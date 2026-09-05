"""Names: slugs, the reserved ask prefix, and ask source titles (design.md 3.7, 8.2).

The reserved pattern is ``^Q\\d+-``. Every source belonging to one ask shares that short
ordinal prefix, which makes the whole ask a single mask -- ``nlmt delete --mask Q7-``.
"""

from __future__ import annotations

import re
import unicodedata

#: A source title belonging to an ask. Group 1 is the ordinal.
ASK_TITLE_RE = re.compile(r"^Q(\d+)-")

#: A mask that targets an ask, e.g. "Q7-". Group 1 is the ordinal.
ASK_MASK_RE = re.compile(r"^Q(\d+)-?$")


def slugify(text: str, *, max_length: int = 40) -> str:
    """A filesystem- and title-safe slug. Never empty."""
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)[:max_length].strip("-")
    return slug or "untitled"


def is_ask_source(title: str) -> bool:
    """True for any source belonging to an ask.

    Such sources are excluded from every mask operation in `load`, always, even if a
    configured mask would match them (design.md 3.7).
    """
    return ASK_TITLE_RE.match(title) is not None


def ask_ordinal(title: str) -> int | None:
    match = ASK_TITLE_RE.match(title)
    return int(match.group(1)) if match else None


def next_ask_ordinal(titles: list[str]) -> int:
    """One greater than the highest ordinal in use; 1 if there are none.

    Derived from a listing rather than persisted (design.md 3.3, 8.2). The caller must
    hold the notebook lock, which is what makes read-then-allocate safe.
    """
    used = [n for n in (ask_ordinal(t) for t in titles) if n is not None]
    return max(used) + 1 if used else 1


def ask_mask(ordinal: int) -> str:
    """The mask that selects exactly one ask's sources."""
    return f"Q{ordinal}-"


def question_title(ordinal: int, slug: str) -> str:
    return f"Q{ordinal}-q-{slugify(slug)}"


def attachment_title(ordinal: int, index: int, original_name: str) -> str:
    """Title for an attachment source.

    The original filename survives after the ``a<i>-`` marker because the generated
    prompt has to map it back to the name the question body uses (design.md 8.3). The
    marker itself keeps an attachment named ``question.txt`` from colliding with the
    question body's title.
    """
    return f"Q{ordinal}-a{index}-{drive_attachment_name(original_name)}"


def drive_attachment_name(original_name: str) -> str:
    """`config.json` -> `config.json.txt`.

    NotebookLM will not accept arbitrary file types, so `.txt` is appended to the *full*
    original filename. The bytes are never touched; only the name gains a suffix.
    """
    return original_name if original_name.endswith(".txt") else original_name + ".txt"


def original_attachment_name(drive_name: str) -> str:
    """Inverse of `drive_attachment_name`, for the prompt's name mapping."""
    if drive_name.endswith(".txt"):
        stripped = drive_name[: -len(".txt")]
        if "." in stripped:
            return stripped
    return drive_name
