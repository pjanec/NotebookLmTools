"""Ask naming and the Q<n>- reservation (design.md 3.7, 8.2)."""

from __future__ import annotations

import pytest

from nlmtools.common import naming


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Q7-q-locking-review", True),
        ("Q7-a1-config.json.txt", True),
        ("Q12-a2-metrics.csv.txt", True),
        ("Q1-", True),
        ("Q-old-style", False),      # the superseded literal prefix is not reserved
        ("Query_engine.txt", False),  # a corpus file that merely starts with Q
        ("Engine_b17_01.txt", False),
        ("QA-notes.txt", False),
    ],
)
def test_reserved_pattern(title, expected):
    assert naming.is_ask_source(title) is expected


def test_ordinal_extraction():
    assert naming.ask_ordinal("Q7-q-thing") == 7
    assert naming.ask_ordinal("Q108-a3-x.txt") == 108
    assert naming.ask_ordinal("Engine_b17.txt") is None


def test_next_ordinal_is_max_plus_one():
    titles = ["Engine_b17_01.txt", "Q3-q-a", "Q7-a1-x.txt", "Q2-q-b"]
    assert naming.next_ask_ordinal(titles) == 8


def test_next_ordinal_starts_at_one():
    assert naming.next_ask_ordinal(["Engine_b17_01.txt"]) == 1
    assert naming.next_ask_ordinal([]) == 1


def test_ordinals_are_reused_after_deletion():
    """Documented and harmless: the durable record is the answers/ transcript."""
    assert naming.next_ask_ordinal(["Q1-q-a", "Q2-q-b"]) == 3
    assert naming.next_ask_ordinal(["Q1-q-a"]) == 2


def test_ask_mask_selects_only_that_ask():
    mask = naming.ask_mask(7)
    assert mask == "Q7-"
    titles = ["Q7-q-x", "Q7-a1-c.json.txt", "Q71-q-y", "Q1-q-z"]
    matched = [t for t in titles if t.startswith(mask)]
    assert matched == ["Q7-q-x", "Q7-a1-c.json.txt"]


def test_attachment_marker_prevents_collision_with_the_question():
    """An attachment literally named question.txt must not collide."""
    question = naming.question_title(7, "question")
    attachment = naming.attachment_title(7, 1, "question.txt")
    assert question != attachment
    assert question.startswith("Q7-") and attachment.startswith("Q7-")


def test_drive_name_appends_txt_to_the_full_original_name():
    assert naming.drive_attachment_name("config.json") == "config.json.txt"
    assert naming.drive_attachment_name("metrics.csv") == "metrics.csv.txt"
    # Already a .txt: no double suffix.
    assert naming.drive_attachment_name("notes.txt") == "notes.txt"


def test_original_name_round_trips_for_the_prompt_mapping():
    for original in ("config.json", "metrics.csv", "trace.log"):
        drive = naming.drive_attachment_name(original)
        assert naming.original_attachment_name(drive) == original


def test_slugify():
    assert naming.slugify("Engine — locking review") == "engine-locking-review"
    assert naming.slugify("  ") == "untitled"
    assert len(naming.slugify("x" * 200)) <= 40
