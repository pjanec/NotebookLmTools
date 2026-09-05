"""The fixture generator must produce bundles that can actually detect body-stripping."""

from __future__ import annotations

import hashlib
import json

from nlmtools.fixtures.generate import BOM, generate_bundle


def test_generates_files_and_manifest(tmp_path):
    manifest = generate_bundle(tmp_path, files=3, target_bytes=4000)
    assert len(manifest["files"]) == 3
    assert (tmp_path / "manifest.json").exists()
    for entry in manifest["files"]:
        assert (tmp_path / entry["name"]).exists()


def test_bom_and_non_bom_are_both_present(tmp_path):
    """Both occur in the real data and both must round-trip byte-identically."""
    manifest = generate_bundle(tmp_path, files=4, target_bytes=2000)
    flags = {entry["bom"] for entry in manifest["files"]}
    assert flags == {True, False}
    for entry in manifest["files"]:
        raw = (tmp_path / entry["name"]).read_bytes()
        has_bom = raw.startswith(BOM.encode("utf-8"))
        assert has_bom is entry["bom"]


def test_recorded_checksums_match_the_files_on_disk(tmp_path):
    manifest = generate_bundle(tmp_path, files=3, target_bytes=2000)
    for entry in manifest["files"]:
        raw = (tmp_path / entry["name"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == entry["sha256"]
        assert len(raw) == entry["bytes"]


def test_every_fact_lives_inside_a_method_body(tmp_path):
    """The whole point: a fact outside a body would not detect stripping."""
    manifest = generate_bundle(tmp_path, files=3, target_bytes=4000)
    assert manifest["facts"]
    for fact in manifest["facts"]:
        text = (tmp_path / fact["file"]).read_text(encoding="utf-8-sig")
        assignment = f"{fact['variable']} = {fact['value']};"
        assert assignment in text

        # The assignment must sit after the method's opening brace, not at class level.
        signature_at = text.index(f"public int {fact['method']}(")
        body_opens_at = text.index("{", signature_at)
        assert text.index(assignment) > body_opens_at


def test_fact_values_are_unique_enough_to_be_diagnostic(tmp_path):
    """A value that appears elsewhere by chance would make a false positive."""
    manifest = generate_bundle(tmp_path, files=3, target_bytes=4000)
    for fact in manifest["facts"]:
        text = (tmp_path / fact["file"]).read_text(encoding="utf-8-sig")
        assert text.count(f"= {fact['value']};") == 1


def test_smoke_probe_is_derived_from_ground_truth(tmp_path):
    manifest = generate_bundle(tmp_path, files=3, target_bytes=4000)
    smoke = manifest["smoke"]
    assert smoke["expect"] in smoke["question"] or smoke["expect"].isdigit()
    matching = [f for f in manifest["facts"] if f["question"] == smoke["question"]]
    assert len(matching) == 1
    assert matching[0]["expect"] == smoke["expect"]


def test_generation_is_reproducible(tmp_path):
    a = generate_bundle(tmp_path / "a", files=3, target_bytes=3000, seed=42)
    b = generate_bundle(tmp_path / "b", files=3, target_bytes=3000, seed=42)
    assert [e["sha256"] for e in a["files"]] == [e["sha256"] for e in b["files"]]


def test_different_seeds_differ(tmp_path):
    a = generate_bundle(tmp_path / "a", files=3, target_bytes=3000, seed=1)
    b = generate_bundle(tmp_path / "b", files=3, target_bytes=3000, seed=2)
    assert [e["sha256"] for e in a["files"]] != [e["sha256"] for e in b["files"]]


def test_size_knob_scales_output(tmp_path):
    small = generate_bundle(tmp_path / "s", files=2, target_bytes=2_000)
    large = generate_bundle(tmp_path / "l", files=2, target_bytes=40_000)
    assert sum(e["bytes"] for e in large["files"]) > 4 * sum(
        e["bytes"] for e in small["files"]
    )


def test_manifest_is_valid_json(tmp_path):
    generate_bundle(tmp_path, files=2, target_bytes=2000)
    json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
