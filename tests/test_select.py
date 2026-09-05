"""M2: selection is pure, offline, and refuses to select nothing."""

from __future__ import annotations

import pytest

from nlmtools.common.exits import EMPTY_SELECTION, ToolError
from nlmtools.loader.select import select


def write(folder, name, text="x"):
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def bundle(tmp_path):
    write(tmp_path, "Engine_b17_01.txt")
    write(tmp_path, "Engine_b17_02.txt")
    write(tmp_path, "Core_b17_01.txt")
    write(tmp_path, "notes.md")
    write(tmp_path, "readme.TXT")
    write(tmp_path, "Q7-q-old-question.txt")
    write(tmp_path, "Q12-a1-config.json.txt")
    (tmp_path / "nested").mkdir()
    write(tmp_path / "nested", "Engine_b17_99.txt")
    return tmp_path


def names(paths):
    return sorted(p.name for p in paths)


def test_masks_select_only_matching_files(bundle):
    assert names(select(bundle, ["Engine_"])) == [
        "Engine_b17_01.txt",
        "Engine_b17_02.txt",
    ]


def test_multiple_masks_are_a_union(bundle):
    assert names(select(bundle, ["Engine_", "Core_"])) == [
        "Core_b17_01.txt",
        "Engine_b17_01.txt",
        "Engine_b17_02.txt",
    ]


def test_no_mask_selects_every_txt_except_ask_sources(bundle):
    assert names(select(bundle)) == [
        "Core_b17_01.txt",
        "Engine_b17_01.txt",
        "Engine_b17_02.txt",
        "readme.TXT",
    ]


def test_extension_match_is_case_insensitive(bundle):
    assert "readme.TXT" in names(select(bundle, ["readme"]))


def test_non_txt_files_are_never_selected(bundle):
    assert "notes.md" not in names(select(bundle))


def test_ask_sources_are_never_selected_even_when_masked(bundle):
    """The Q<n>- reservation wins over any mask (design.md 3.7)."""
    with pytest.raises(ToolError) as caught:
        select(bundle, ["Q7-"])
    assert caught.value.code == EMPTY_SELECTION


def test_selection_is_not_recursive(bundle):
    assert "Engine_b17_99.txt" not in names(select(bundle))


def test_empty_selection_is_an_error_not_a_no_op(bundle):
    with pytest.raises(ToolError) as caught:
        select(bundle, ["Nope_"])
    error = caught.value
    assert error.code == EMPTY_SELECTION
    # The message has to say what went wrong, and the hint how to fix it.
    assert "Nope_" in str(error.detail["masks"])
    assert error.hint


def test_missing_folder_is_an_error(tmp_path):
    with pytest.raises(ToolError) as caught:
        select(tmp_path / "does-not-exist", ["Engine_"])
    assert caught.value.code == EMPTY_SELECTION


def test_folder_with_no_txt_at_all_says_so(tmp_path):
    write(tmp_path, "notes.md")
    with pytest.raises(ToolError) as caught:
        select(tmp_path)
    assert "no .txt files at all" in caught.value.message


def test_masks_are_case_sensitive(bundle):
    with pytest.raises(ToolError):
        select(bundle, ["engine_"])


def test_result_is_sorted(bundle):
    result = select(bundle, ["Engine_", "Core_"])
    assert [p.name for p in result] == sorted(p.name for p in result)
