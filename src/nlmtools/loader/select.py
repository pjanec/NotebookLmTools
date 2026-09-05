"""Mask -> file list (design.md 3.4, 6 step 3).

Pure and unit-testable; no network, no side effects. The rules:

* `.txt` only, matched case-insensitively on the extension.
* `starts-with` prefixes, matched case-sensitively on the filename.
* Non-recursive: one folder, not a tree.
* Ask sources (`Q<n>-`) are never selected, whatever the mask says.
* No masks means every `.txt` in the folder.
* An empty result is an error, never a silent no-op -- loading nothing would delete the
  notebook's existing sources and replace them with nothing.
"""

from __future__ import annotations

from pathlib import Path

from ..common.exits import EMPTY_SELECTION, ToolError
from ..common.naming import is_ask_source


def matches_masks(name: str, masks: list[str]) -> bool:
    if not masks:
        return True
    return any(name.startswith(mask) for mask in masks)


def select(folder: Path, masks: list[str] | None = None) -> list[Path]:
    """Return the selected files, sorted by name. Raises on an empty selection."""
    masks = list(masks or [])
    folder = Path(folder)
    if not folder.is_dir():
        raise ToolError(
            EMPTY_SELECTION,
            f"local folder does not exist or is not a directory: {folder}",
            hint="check --local-folder; nothing was changed",
        )

    selected = [
        path
        for path in sorted(folder.iterdir(), key=lambda p: p.name)
        if path.is_file()
        and path.suffix.lower() == ".txt"
        and not is_ask_source(path.name)
        and matches_masks(path.name, masks)
    ]

    if not selected:
        txt_count = sum(
            1 for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".txt"
        )
        if txt_count == 0:
            detail_msg = f"no .txt files at all in {folder}"
            hint = "check --local-folder; nothing was changed"
        else:
            shown = ", ".join(masks) or "(none)"
            detail_msg = (
                f"{txt_count} .txt file(s) in {folder}, but none start with any of: {shown}"
            )
            hint = "widen or correct --mask, or omit it to select every .txt"
        raise ToolError(
            EMPTY_SELECTION,
            f"mask matched no files: {detail_msg}",
            hint=hint,
            detail={"folder": str(folder), "masks": masks},
        )
    return selected
