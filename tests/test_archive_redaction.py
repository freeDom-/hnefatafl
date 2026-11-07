"""Credential boundaries and bounded diagnostic/log sanitization."""

import io
import hashlib
import json
import random
import subprocess
import sys
import tracemalloc

import pytest
from auth_support import AuthenticatedClient as TestClient
from test_job_archives import export, job_record, legacy_archive, upload
from test_job_tar_archives import members, tar_bytes

from hnefatafl import job_archives
from hnefatafl.server import create_app


@pytest.mark.parametrize(
    "text,secret",
    [
        ('password="correct horse battery staple" done', "correct horse battery staple"),
        ("PASSWORD='violet moon lantern' done", "violet moon lantern"),
        ('api-key="silver tree key" done', "silver tree key"),
        ('{"access_token":"winter snow token","ok":true}', "winter snow token"),
        (r'password="correct \"horse\" battery\\staple" done', "battery"),
        (r"password='violet \'moon\' lantern' done", "lantern"),
        ("Authorization: Basic dXNlcjpzZWNyZXQ=\nnext line", "dXNlcjpzZWNyZXQ="),
        ("Authorization: Bearer bearer-secret\nnext line", "bearer-secret"),
        ('Bearer "purple cloud token" done', "purple cloud token"),
        ('password="雪 馬 電池" done', "雪 馬 電池"),
    ],
)
def test_complete_credential_values_are_redacted(text, secret):
    cleaned = job_archives.clean_text(text)
    assert secret not in cleaned
    assert "[redacted]" in cleaned
    if text.endswith(" done"):
        assert cleaned.endswith(" done")
    if text.endswith("next line"):
        assert cleaned.endswith("next line")


def test_nested_json_diagnostics_and_portable_share_redaction():
    text = json.dumps({"nested": {"password": "correct horse battery staple"}, "count": 4})
    encoded = json.dumps({"message": 'password="violet moon lantern"', "count": 4})
    report = job_archives.portable({"metrics": {"loss": 0.125}, "notes": [text, encoded]})
    assert report["metrics"] == {"loss": 0.125}
    for fragment in ("correct", "horse", "battery", "staple", "violet", "moon", "lantern"):
        assert fragment not in json.dumps(report)
    assert '"count": 4' in report["notes"][0]
    assert '"count": 4' in report["notes"][1]


@pytest.mark.parametrize(
    "text",
    [
        'password="" end',
        "password='' end",
        "password=\nnormal diagnostics\n",
        "password=\"one two\" api_key=three access_token='four five' end",
    ],
)
def test_empty_and_multiple_values_do_not_consume_following_diagnostics(text):
    cleaned = job_archives.clean_text(text)
    assert all(secret not in cleaned for secret in ("one", "two", "three", "four", "five"))
    if text.endswith(" end"):
        assert cleaned.endswith(" end")
    else:
        assert cleaned.endswith("normal diagnostics\n")


@pytest.mark.parametrize(
    "text",
    [
        "No password is configured; tokenization completed.",
        "policy_loss=0.125 learning_rate=0.001 games=1000",
        'The diagnostic says "password policy" without an assignment.',
        "x" * 800000,
        "password" * 100000,
    ],
)
def test_nonsecret_diagnostic_text_is_preserved(text):
    assert job_archives.clean_text(text) == text


@pytest.mark.parametrize("boundary", [1, 7, 65535, 65536, 65537])
def test_streaming_sanitization_matches_complete_text_at_every_boundary(boundary):
    source = 'before password="correct \\"horse\\" battery staple" api_key=secret-key\n'
    source += "path=/srv/private/jobs/run.log\nordinary diagnostic data\n"
    expected = job_archives.clean_text(source)

    class Reader(io.StringIO):
        def read(self, size=-1):
            assert 0 < size <= 65536
            return super().read(min(size, boundary))

    output = io.StringIO()
    job_archives.clean_log(Reader(source), output)
    assert output.getvalue() == expected
    assert "battery" not in expected and "secret-key" not in expected
    assert "/srv/private" not in expected and "[local path]" in expected
    assert expected.endswith("ordinary diagnostic data\n")


def test_large_quoted_value_uses_bounded_memory_and_fixed_reads():
    class Reader:
        remaining = 32 * 1024 * 1024
        prefix = True

        def read(self, size):
            assert 0 < size <= 65536
            if self.prefix:
                self.prefix = False
                return 'password="'
            if self.remaining:
                length = min(size, self.remaining)
                self.remaining -= length
                return "x" * length
            return ""

    class Sink:
        characters = 0

        def write(self, text):
            assert len(text) < 2 * 65536
            assert "x" not in text
            self.characters += len(text)

    sink = Sink()
    tracemalloc.start()
    try:
        job_archives.clean_log(Reader(), sink)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 4 * 1024 * 1024
    assert sink.characters < 100


def test_long_nonsecret_diagnostics_remain_linear():
    script = "from hnefatafl.job_archives import clean_text; s='password'*1000000; assert clean_text(s)==s"
    subprocess.run([sys.executable, "-c", script], check=True, timeout=20)


def test_export_redacts_quoted_worker_log_across_read_boundaries(tmp_path):
    with TestClient(create_app(tmp_path / "data")) as client:
        job, directory = job_record(client)
        text = "prefix " + "x" * 65515 + ' password="correct horse battery staple"\n'
        text += 'api_key="violet moon lantern"\nordinary diagnostic data\n'
        (directory / "worker.log").write_text(text)
        contents = members(export(client, job))
        log = contents["worker.log"].decode()
        for fragment in ("correct", "horse", "battery", "staple", "violet", "moon", "lantern"):
            assert fragment not in log
        assert log.endswith("ordinary diagnostic data\n")
        assert "[redacted]" in log


@pytest.mark.parametrize(
    "text",
    [
        r"password=correct\ horse\ battery\ staple end",
        r"password=correct\"horse\"battery end",
        "Proxy-Authorization: Basic dXNlcjpzZWNyZXQ= status=done\nnormal\n",
        'Authorization: Digest username="violet", response="moon lantern"\nnormal\n',
        'password={"part":"violet moon", "nested":["lantern"]} end',
        'password="unfinished violet moon\nlantern',
        'password="雪 \\"馬\\" 電池" end',
    ],
)
def test_escaped_and_structured_values_are_safe_for_all_small_read_sizes(text):
    expected = job_archives.clean_text(text)
    assert all(
        part not in expected
        for part in (
            "correct",
            "horse",
            "battery",
            "staple",
            "violet",
            "moon",
            "lantern",
            "雪",
            "電池",
            "dXNlcjpzZWNyZXQ=",
        )
    )
    assert job_archives.clean_text(expected) == expected
    for size in range(1, len(text) + 1):

        class Reader(io.StringIO):
            def read(self, maximum=-1):
                return super().read(min(maximum, size))

        output = io.StringIO()
        job_archives.clean_log(Reader(text), output)
        assert output.getvalue() == expected, (size, text)


@pytest.mark.parametrize("encoding", range(5))
def test_nested_json_escaping_levels_do_not_reveal_quote_contents(encoding):
    text = 'password="violet \\"moon\\" lantern\\\\ending" after'
    for _ in range(encoding):
        text = json.dumps({"message": text, "count": 4}, ensure_ascii=False)
    cleaned = job_archives.clean_text(text)
    assert all(part not in cleaned for part in ("violet", "moon", "lantern", "ending"))
    assert "after" in cleaned
    for size in (1, 2, 3, 7, 31):

        class Reader(io.StringIO):
            def read(self, maximum=-1):
                return super().read(min(maximum, size))

        output = io.StringIO()
        job_archives.clean_log(Reader(text), output)
        assert output.getvalue() == cleaned


@pytest.mark.parametrize("padding", [65520, 65525, 65529, 65534, 65535, 65536, 131060])
def test_keys_and_values_cross_actual_64k_boundaries(padding):
    text = "x " * (padding // 2) + ' password="violet \\"moon\\" lantern" next\n'
    output = io.StringIO()
    job_archives.clean_log(io.StringIO(text), output)
    assert output.getvalue() == job_archives.clean_text(text)
    assert all(part not in output.getvalue() for part in ("violet", "moon", "lantern"))
    assert output.getvalue().endswith(" next\n")


def test_sanitized_log_expansion_respects_existing_artifact_limit(monkeypatch):
    monkeypatch.setattr(job_archives, "MAX_FILE_BYTES", 20)
    output = io.StringIO()
    with pytest.raises(job_archives.ArchiveError, match="file size limit"):
        job_archives.clean_log(io.StringIO('password="" password=""'), output)
    assert len(output.getvalue().encode()) <= 20


def test_valid_random_quoted_values_and_serialized_diagnostics_across_boundaries():
    randomizer = random.Random(58)
    for _ in range(100):
        value = "".join(randomizer.choices("ab \\\"'\n\t雪{},[]", k=40)) + "UNIQUE_SECRET_END"
        text = "before password=" + json.dumps(value, ensure_ascii=False) + " ordinary after\n"
        for _ in range(randomizer.randrange(4)):
            text = json.dumps({"message": text, "count": 4}, ensure_ascii=False)
        cleaned = job_archives.clean_text(text)
        assert "UNIQUE_SECRET_END" not in cleaned
        assert "ordinary after" in cleaned
        size = randomizer.randrange(1, 40)

        class Reader(io.StringIO):
            def read(self, maximum=-1):
                return super().read(min(maximum, size))

        output = io.StringIO()
        job_archives.clean_log(Reader(text), output)
        assert output.getvalue() == cleaned


def test_absolute_path_redaction_is_identical_for_chunked_and_complete_text():
    text = "before /srv/private/log.txt and C:\\private\\jobs\\log.txt after\n"
    expected = job_archives.clean_text(text)
    assert "/srv/private" not in expected and "C:" not in expected
    for size in range(1, len(text)):

        class Reader(io.StringIO):
            def read(self, maximum=-1):
                return super().read(min(maximum, size))

        output = io.StringIO()
        job_archives.clean_log(Reader(text), output)
        assert output.getvalue() == expected


def test_portable_reference_shortcuts_do_not_bypass_credential_redaction(tmp_path):
    suffix = 'notes password="violet moon lantern"'
    for text in ("job://" + suffix, str(tmp_path) + "/" + suffix):
        cleaned = job_archives.portable(text, tmp_path)
        assert cleaned.startswith("job://")
        assert all(part not in cleaned for part in ("violet", "moon", "lantern"))
    assert job_archives.portable(str(tmp_path / "last.pt"), tmp_path) == "job://last.pt"


def test_pretty_json_whitespace_does_not_terminate_a_quoted_credential_assignment():
    text = '{"password"\n:\n"violet moon lantern", "count": 4}'
    expected = job_archives.clean_text(text)
    assert all(part not in expected for part in ("violet", "moon", "lantern"))
    assert json.loads(expected) == {"password": "[redacted]", "count": 4}
    for size in range(1, len(text)):

        class Reader(io.StringIO):
            def read(self, maximum=-1):
                return super().read(min(maximum, size))

        output = io.StringIO()
        job_archives.clean_log(Reader(text), output)
        assert output.getvalue() == expected


@pytest.mark.parametrize("version", [1, 2, 3])
def test_import_sanitizes_untrusted_worker_logs_with_shared_streaming_rules(tmp_path, version):
    with TestClient(create_app(tmp_path / "data")) as client:
        job, directory = job_record(client)
        (directory / "worker.log").write_text("ordinary data\n")
        files = members(export(client, job))
        log = b"x " * 32760 + b'password="violet moon lantern"\nordinary after\n'
        files["worker.log"] = log
        manifest = json.loads(files["manifest.json"])
        manifest["files"]["worker.log"] = {
            "size": len(log),
            "sha256": hashlib.sha256(log).hexdigest(),
        }
        files["manifest.json"] = json.dumps(manifest).encode()
        content = tar_bytes(files)
        if version != 3:
            content = legacy_archive(content, version)
        response = upload(client, content)
        assert response.status_code == 201, response.text
        restored = (
            client.app.state.data_dir / "jobs" / response.json()["id"] / "worker.log"
        ).read_text()
        assert restored.endswith("ordinary after\n")
        assert all(part not in restored for part in ("violet", "moon", "lantern"))
