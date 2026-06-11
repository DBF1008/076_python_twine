# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import json

import pretend
import pytest

from twine import repository
from twine import upload_result as result_mod


# --- FileUploadResult / UploadReport creation ---


def test_file_upload_result_fields():
    """Access all fields of a FileUploadResult."""
    r = result_mod.FileUploadResult(
        filename="pkg-1.0.whl",
        status="success",
        error_message=None,
        has_signature=True,
        has_attestations=False,
        release_url="https://pypi.org/project/pkg/1.0/",
    )
    assert r.filename == "pkg-1.0.whl"
    assert r.status == "success"
    assert r.error_message is None
    assert r.has_signature is True
    assert r.has_attestations is False
    assert r.release_url == "https://pypi.org/project/pkg/1.0/"


def test_upload_report_fields():
    """Create an UploadReport with multiple file results."""
    f1 = result_mod.FileUploadResult(
        "a.whl", "success", None, False, False, None
    )
    f2 = result_mod.FileUploadResult(
        "b.tar.gz", "skipped", None, False, False, None
    )
    report = result_mod.UploadReport(
        repository_url="https://upload.pypi.org/legacy/",
        files=[f1, f2],
    )
    assert report.repository_url == "https://upload.pypi.org/legacy/"
    assert len(report.files) == 2


# --- release_url_for_package ---


def test_release_url_for_package_pypi():
    """Return a release URL for packages uploaded to PyPI."""
    package = pretend.stub(safe_name="my-package", version="1.2.3")
    url = result_mod.release_url_for_package(repository.WAREHOUSE, package)
    assert url == "https://pypi.org/project/my-package/1.2.3/"


def test_release_url_for_package_testpypi():
    """Return a release URL for packages uploaded to TestPyPI."""
    package = pretend.stub(safe_name="my-package", version="0.1.0")
    url = result_mod.release_url_for_package(repository.TEST_WAREHOUSE, package)
    assert url == f"{repository.TEST_WAREHOUSE}project/my-package/0.1.0/"


def test_release_url_for_package_other():
    """Return None for packages uploaded to non-PyPI repositories."""
    package = pretend.stub(safe_name="my-package", version="1.0")
    url = result_mod.release_url_for_package("https://example.com/simple/", package)
    assert url is None


# --- determine_exit_code ---


def test_determine_exit_code_all_success():
    """Exit 0 when all files succeed."""
    report = result_mod.UploadReport("url", [
        result_mod.FileUploadResult("a.whl", "success", None, False, False, None),
        result_mod.FileUploadResult("b.whl", "success", None, False, False, None),
    ])
    assert result_mod.determine_exit_code(report) == 0


def test_determine_exit_code_all_skipped():
    """Exit 0 when all files are skipped."""
    report = result_mod.UploadReport("url", [
        result_mod.FileUploadResult("a.whl", "skipped", None, False, False, None),
    ])
    assert result_mod.determine_exit_code(report) == 0


def test_determine_exit_code_mixed_success_skip():
    """Exit 0 when files are a mix of success and skipped."""
    report = result_mod.UploadReport("url", [
        result_mod.FileUploadResult("a.whl", "success", None, False, False, None),
        result_mod.FileUploadResult("b.whl", "skipped", None, False, False, None),
    ])
    assert result_mod.determine_exit_code(report) == 0


def test_determine_exit_code_partial_failure():
    """Exit 2 when some files succeed and some fail."""
    report = result_mod.UploadReport("url", [
        result_mod.FileUploadResult("a.whl", "success", None, False, False, None),
        result_mod.FileUploadResult("b.whl", "failed", "403", False, False, None),
    ])
    assert result_mod.determine_exit_code(report) == 2


def test_determine_exit_code_all_failed():
    """Exit 1 when all files fail."""
    report = result_mod.UploadReport("url", [
        result_mod.FileUploadResult("a.whl", "failed", "403", False, False, None),
        result_mod.FileUploadResult("b.whl", "failed", "500", False, False, None),
    ])
    assert result_mod.determine_exit_code(report) == 1


def test_determine_exit_code_empty():
    """Exit 0 when no files are processed."""
    report = result_mod.UploadReport("url", [])
    assert result_mod.determine_exit_code(report) == 0


# --- print_summary ---


def test_print_summary_shows_filenames_and_statuses(capsys):
    """The summary table includes file names and status values."""
    report = result_mod.UploadReport("https://upload.pypi.org/legacy/", [
        result_mod.FileUploadResult(
            "pkg-1.0.whl", "success", None, True, False,
            "https://pypi.org/project/pkg/1.0/",
        ),
        result_mod.FileUploadResult(
            "pkg-1.0.tar.gz", "skipped", None, False, True, None,
        ),
        result_mod.FileUploadResult(
            "pkg-2.0.whl", "failed", "403 Forbidden", False, False, None,
        ),
    ])
    result_mod.print_summary(report)

    captured = capsys.readouterr().out
    assert "pkg-1.0.whl" in captured
    assert "pkg-1.0.tar.gz" in captured
    assert "pkg-2.0.whl" in captured
    assert "success" in captured
    assert "skipped" in captured
    assert "failed" in captured
    assert "Success: 1" in captured
    assert "Skipped: 1" in captured
    assert "Failed: 1" in captured


# --- write_result_file ---


def test_write_result_file_creates_valid_json(tmp_path):
    """Write a JSON file with the expected schema."""
    report = result_mod.UploadReport("https://upload.pypi.org/legacy/", [
        result_mod.FileUploadResult(
            "pkg-1.0.whl", "success", None, True, True,
            "https://pypi.org/project/pkg/1.0/",
        ),
        result_mod.FileUploadResult(
            "pkg-1.0.tar.gz", "skipped", None, False, False, None,
        ),
    ])
    path = str(tmp_path / "result.json")
    result_mod.write_result_file(report, path)

    with open(path) as f:
        data = json.load(f)

    assert data["schema_version"] == "1.0"
    assert data["repository_url"] == "https://upload.pypi.org/legacy/"
    assert data["summary"] == {
        "total": 2,
        "success": 1,
        "skipped": 1,
        "failed": 0,
    }
    assert len(data["files"]) == 2

    f0 = data["files"][0]
    assert f0["filename"] == "pkg-1.0.whl"
    assert f0["status"] == "success"
    assert f0["error_message"] is None
    assert f0["has_signature"] is True
    assert f0["has_attestations"] is True
    assert f0["release_url"] == "https://pypi.org/project/pkg/1.0/"

    f1 = data["files"][1]
    assert f1["filename"] == "pkg-1.0.tar.gz"
    assert f1["status"] == "skipped"
    assert f1["has_signature"] is False


def test_write_result_file_schema_keys(tmp_path):
    """Verify all required top-level keys are present in the JSON output."""
    report = result_mod.UploadReport("url", [
        result_mod.FileUploadResult("a.whl", "success", None, False, False, None),
    ])
    path = str(tmp_path / "result.json")
    result_mod.write_result_file(report, path)

    with open(path) as f:
        data = json.load(f)

    assert set(data.keys()) == {"schema_version", "repository_url", "summary", "files"}
    assert set(data["summary"].keys()) == {"total", "success", "skipped", "failed"}
    assert set(data["files"][0].keys()) == {
        "filename", "status", "error_message",
        "has_signature", "has_attestations", "release_url",
    }
