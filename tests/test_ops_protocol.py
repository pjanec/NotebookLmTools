"""The job protocol's validation, which is the trust boundary.

Everything arriving in a job file is untrusted, whatever repository it came from. These
tests pin the rules that keep a leaked ops-repo token from becoming a shell on the machine
that holds the Google credentials.
"""

from __future__ import annotations

import base64

import pytest

from nlmtools.ops.protocol import Job, JobRejected, validate


def job(**kwargs) -> Job:
    return Job(**kwargs)


# -- verbs --------------------------------------------------------------------------


def test_only_known_verbs_are_accepted():
    with pytest.raises(JobRejected) as caught:
        validate(job(verb="run"))
    assert "unknown verb" in str(caught.value)


@pytest.mark.parametrize("verb", ["refresh", "status"])
def test_parameterless_verbs_need_nothing(verb):
    validate(job(verb=verb))


# -- refs: the sharpest edge --------------------------------------------------------


@pytest.mark.parametrize("ref", [
    "main", "feature/my-branch", "release-1.2", "v2.0.0",
    "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0",
])
def test_ordinary_refs_are_accepted(ref):
    validate(job(verb="refresh", ref=ref))


@pytest.mark.parametrize("ref", [
    "https://evil.example/repo.git",          # a URL, never a ref
    "--upload-pack=/bin/sh",                  # an option masquerading as a ref
    "-x",                                     # ditto
    "../../../etc/passwd",                    # traversal
    "main..evil",                             # range expression
    "main; rm -rf /",                         # shell metacharacters
    "main\nrm -rf /",                         # newline injection
    "ssh://host/repo",
    "",
])
def test_refs_that_are_not_plain_names_are_refused(ref):
    with pytest.raises(JobRejected):
        validate(job(verb="refresh", ref=ref))


def test_a_refresh_needs_no_ref():
    """Omitted, the project's default branch is synced. There is no unsynced mode."""
    validate(job(verb="refresh"))


def test_the_retired_sync_verb_says_what_replaced_it():
    """A caller working from older documentation gets an instruction, not a puzzle."""
    with pytest.raises(JobRejected) as caught:
        validate(job(verb="sync", ref="main"))
    assert "refresh --ref" in str(caught.value)


# -- questions and attachments ------------------------------------------------------


def test_ask_requires_a_question():
    with pytest.raises(JobRejected):
        validate(job(verb="ask", question="   "))


def test_an_overlong_question_is_refused():
    with pytest.raises(JobRejected) as caught:
        validate(job(verb="ask", question="x" * 200_001))
    assert "limit" in str(caught.value)


def encoded(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def test_a_text_attachment_is_accepted():
    validate(job(verb="ask", question="q", attachments=[
        {"name": "data.json", "content_b64": encoded(b'{"a": 1}')},
    ]))


@pytest.mark.parametrize("name", [
    "../../evil.json",        # traversal
    "C:\\Windows\\evil.json",  # absolute path
    "evil.exe",                # not text-like
    "evil.ps1",
    "",
])
def test_attachment_names_are_constrained(name):
    with pytest.raises(JobRejected):
        validate(job(verb="ask", question="q", attachments=[
            {"name": name, "content_b64": encoded(b"x")},
        ]))


def test_a_binary_attachment_is_refused():
    with pytest.raises(JobRejected) as caught:
        validate(job(verb="ask", question="q", attachments=[
            {"name": "data.json", "content_b64": encoded(b"abc\x00def")},
        ]))
    assert "binary" in str(caught.value)


def test_too_many_attachments_are_refused():
    many = [{"name": f"f{i}.json", "content_b64": encoded(b"{}")} for i in range(6)]
    with pytest.raises(JobRejected):
        validate(job(verb="ask", question="q", attachments=many))


def test_invalid_base64_is_refused():
    with pytest.raises(JobRejected):
        validate(job(verb="ask", question="q", attachments=[
            {"name": "data.json", "content_b64": "not base64!!"},
        ]))


# -- identity and round trip ---------------------------------------------------------


def test_a_malformed_id_is_refused():
    with pytest.raises(JobRejected):
        validate(job(verb="refresh", id="../../escape"))


def test_a_malformed_name_is_refused():
    with pytest.raises(JobRejected):
        validate(job(verb="ask", question="q", name="../evil"))


def test_json_round_trip_preserves_a_job():
    original = Job(verb="ask", question="why?", name="probe",
                   attachments=[{"name": "d.json", "content_b64": encoded(b"{}")}])
    restored = Job.from_json(original.to_json())
    assert restored.verb == original.verb
    assert restored.question == original.question
    assert [a.name for a in restored.attachments] == ["d.json"]


def test_unknown_fields_in_a_job_are_ignored():
    """A future or hostile sender must not be able to set attributes we did not define."""
    restored = Job.from_json('{"verb": "refresh", "id": "x1", "notebook_id": "sneaky"}')
    assert restored.verb == "refresh"
    assert not hasattr(restored, "notebook_id")


def test_malformed_json_is_refused():
    with pytest.raises(JobRejected):
        Job.from_json("{not json")
