# Copyright 2015 Ian Cordasco
#
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
import logging
from contextlib import contextmanager
from unittest.mock import call, patch

import packaging
import pretend
import pytest
import requests
from packaging import version

from twine import package
from twine import repository
from twine import utils


@pytest.fixture()
def default_repo():
    return repository.Repository(
        repository_url=utils.DEFAULT_REPOSITORY,
        username="username",
        password="password",
    )


def test_gpg_signature_structure_is_preserved():
    """Preserve 'gpg_signature' key when converting metadata."""
    data = {
        "gpg_signature": ("filename.asc", "filecontent"),
    }

    tuples = repository.Repository._convert_metadata_to_list_of_tuples(data)
    assert tuples == [("gpg_signature", ("filename.asc", "filecontent"))]


def test_iterables_are_flattened():
    """Flatten iterable metadata to list of tuples."""
    data = {
        "platform": ["UNKNOWN"],
    }

    tuples = repository.Repository._convert_metadata_to_list_of_tuples(data)
    assert tuples == [("platform", "UNKNOWN")]

    data = {
        "platform": ["UNKNOWN", "ANOTHERPLATFORM"],
    }

    tuples = repository.Repository._convert_metadata_to_list_of_tuples(data)
    assert tuples == [("platform", "UNKNOWN"), ("platform", "ANOTHERPLATFORM")]


def test_all_metadata_fields_are_flattened(monkeypatch):
    """Verify that package metadata fields are correctly flattened."""
    if version.Version(packaging.__version__) < version.Version("24.1"):
        # All metadata fields up to metadata version 2.3
        metadata = open("tests/fixtures/everything.metadata23")
    else:
        # All metadata fields up to metadata version 2.4
        metadata = open("tests/fixtures/everything.metadata24")
    monkeypatch.setattr(package.wheel.Wheel, "read", metadata.read)
    filename = "tests/fixtures/twine-4.0.2-py3-none-any.whl"
    data = package.PackageFile.from_filename(
        filename, comment="comment"
    ).metadata_dictionary()
    tuples = repository.Repository._convert_metadata_to_list_of_tuples(data)
    # Verifies that all metadata fields parsed by ``packaging.metadata`` are
    # correctly flattened into a list of (str, str) tuples. This does not
    # apply to the ``gpg_signature`` field, but this field is not added here
    # as there are specific tests for it.
    for key, value in tuples:
        assert isinstance(key, str)
        assert isinstance(value, str)


def test_set_client_certificate(default_repo):
    """Set client certificate for session."""
    assert default_repo.session.cert is None

    default_repo.set_client_certificate(("/path/to/cert", "/path/to/key"))
    assert default_repo.session.cert == ("/path/to/cert", "/path/to/key")


def test_set_certificate_authority(default_repo):
    """Set certificate authority for session."""
    assert default_repo.session.verify is True

    default_repo.set_certificate_authority("/path/to/cert")
    assert default_repo.session.verify == "/path/to/cert"


def test_make_user_agent_string(default_repo):
    """Add twine to User-Agent session header."""
    assert "twine/" in default_repo.session.headers["User-Agent"]


def response_with(**kwattrs):
    resp = requests.Response()
    for attr, value in kwattrs.items():
        if hasattr(resp, attr):
            setattr(resp, attr, value)

    return resp


def test_package_is_uploaded_404s(default_repo):
    """Return False when the project API response status isn't 200."""
    default_repo.session = pretend.stub(
        get=lambda url, headers: response_with(status_code=404)
    )
    package = pretend.stub(safe_name="fake", version="2.12.0")

    assert default_repo.package_is_uploaded(package) is False


def test_package_is_uploaded_200s_with_no_releases(default_repo):
    """Return False when the list of releases for a project is empty."""
    default_repo.session = pretend.stub(
        get=lambda url, headers: response_with(
            status_code=200, _content=b'{"releases": {}}', _content_consumed=True
        ),
    )
    package = pretend.stub(safe_name="fake", version="2.12.0")

    assert default_repo.package_is_uploaded(package) is False


def test_package_is_uploaded_with_releases_using_cache(default_repo):
    """Return True when the package is in the releases cache."""
    default_repo._releases_json_data = {"fake": {"0.1": [{"filename": "fake.whl"}]}}
    package = pretend.stub(
        safe_name="fake",
        version="0.1",
        basefilename="fake.whl",
    )

    assert default_repo.package_is_uploaded(package) is True


def test_package_is_uploaded_with_releases_not_using_cache(default_repo):
    """Return True when the package is in the list of releases for a project."""
    default_repo.session = pretend.stub(
        get=lambda url, headers: response_with(
            status_code=200,
            _content=b'{"releases": {"0.1": [{"filename": "fake.whl"}]}}',
            _content_consumed=True,
        ),
    )
    package = pretend.stub(
        safe_name="fake",
        version="0.1",
        basefilename="fake.whl",
    )

    assert default_repo.package_is_uploaded(package, bypass_cache=True) is True


def test_package_is_uploaded_different_filenames(default_repo):
    """Return False when the package is not in the list of releases for a project."""
    default_repo.session = pretend.stub(
        get=lambda url, headers: response_with(
            status_code=200,
            _content=b'{"releases": {"0.1": [{"filename": "fake.whl"}]}}',
            _content_consumed=True,
        ),
    )
    package = pretend.stub(
        safe_name="fake",
        version="0.1",
        basefilename="foo.whl",
    )

    assert default_repo.package_is_uploaded(package) is False


def test_package_is_registered(default_repo):
    """Return API response from registering a package."""
    package = pretend.stub(
        basefilename="fake.whl", metadata_dictionary=lambda: {"name": "fake"}
    )

    resp = response_with(status_code=200)
    setattr(resp, "raw", pretend.stub())
    setattr(resp.raw, "close", lambda: None)
    default_repo.session = pretend.stub(
        post=lambda url, data, allow_redirects, headers: resp
    )

    assert default_repo.register(package)


@pytest.mark.parametrize("disable_progress_bar", [True, False])
def test_disable_progress_bar_is_forwarded_to_rich(
    monkeypatch, tmpdir, disable_progress_bar, default_repo
):
    """Toggle display of upload progress bar."""

    @contextmanager
    def ProgressStub(*args, **kwargs):
        assert "disable" in kwargs
        assert kwargs["disable"] == disable_progress_bar
        yield pretend.stub(
            add_task=lambda description, total: None,
            update=lambda task_id, completed: None,
        )

    monkeypatch.setattr(repository.rich.progress, "Progress", ProgressStub)
    default_repo.disable_progress_bar = disable_progress_bar

    default_repo.session = pretend.stub(
        post=lambda url, data, allow_redirects, headers: response_with(status_code=200)
    )

    fakefile = tmpdir.join("fake.whl")
    fakefile.write(".")

    def dictfunc():
        return {"name": "fake"}

    package = pretend.stub(
        safe_name="fake",
        metadata=pretend.stub(version="2.12.0"),
        basefilename="fake.whl",
        filename=str(fakefile),
        metadata_dictionary=dictfunc,
    )

    default_repo.upload(package)


def test_upload_retry(tmpdir, default_repo, caplog):
    """Print retry messages with backoff when the upload response is a server error."""
    default_repo.disable_progress_bar = True

    default_repo.session = pretend.stub(
        post=lambda url, data, allow_redirects, headers: response_with(
            status_code=500, reason="Internal server error"
        )
    )

    fakefile = tmpdir.join("fake.whl")
    fakefile.write(".")

    package = pretend.stub(
        safe_name="fake",
        metadata=pretend.stub(version="2.12.0"),
        basefilename="fake.whl",
        filename=str(fakefile),
        metadata_dictionary=lambda: {"name": "fake"},
    )

    with patch("twine.repository.time.sleep") as mock_sleep, patch(
        "twine.repository.random.uniform", return_value=0.0
    ):
        # Upload with default max_retries of 5
        default_repo.upload(package)

        assert mock_sleep.call_count == 5

    assert len(caplog.messages) == 5
    for i, msg in enumerate(caplog.messages, 1):
        assert f"Retry {i} of 5" in msg
        assert '500: Internal server error' in msg

    caplog.clear()

    with patch("twine.repository.time.sleep") as mock_sleep, patch(
        "twine.repository.random.uniform", return_value=0.0
    ):
        # Upload with custom max_retries of 3
        default_repo.upload(package, max_retries=3)

        assert mock_sleep.call_count == 3

    assert len(caplog.messages) == 3
    for i, msg in enumerate(caplog.messages, 1):
        assert f"Retry {i} of 3" in msg


def test_upload_retry_429(tmpdir, default_repo, caplog):
    """Retry on 429 Too Many Requests status."""
    default_repo.disable_progress_bar = True

    default_repo.session = pretend.stub(
        post=lambda url, data, allow_redirects, headers: response_with(
            status_code=429, reason="Too Many Requests"
        )
    )

    fakefile = tmpdir.join("fake.whl")
    fakefile.write(".")

    package = pretend.stub(
        safe_name="fake",
        metadata=pretend.stub(version="2.12.0"),
        basefilename="fake.whl",
        filename=str(fakefile),
        metadata_dictionary=lambda: {"name": "fake"},
    )

    with patch("twine.repository.time.sleep"), patch(
        "twine.repository.random.uniform", return_value=0.0
    ):
        resp = default_repo.upload(package, max_retries=2)

    assert resp.status_code == 429
    assert len(caplog.messages) == 2
    assert "429: Too Many Requests" in caplog.messages[0]


def test_upload_retry_respects_retry_after_seconds(tmpdir, default_repo, caplog):
    """Use Retry-After header value (seconds) as delay."""
    default_repo.disable_progress_bar = True

    resp_with_header = requests.Response()
    resp_with_header.status_code = 429
    resp_with_header.reason = "Too Many Requests"
    resp_with_header.headers["Retry-After"] = "30"

    default_repo.session = pretend.stub(
        post=lambda url, data, allow_redirects, headers: resp_with_header
    )

    fakefile = tmpdir.join("fake.whl")
    fakefile.write(".")

    package = pretend.stub(
        safe_name="fake",
        metadata=pretend.stub(version="2.12.0"),
        basefilename="fake.whl",
        filename=str(fakefile),
        metadata_dictionary=lambda: {"name": "fake"},
    )

    with patch("twine.repository.time.sleep") as mock_sleep:
        default_repo.upload(package, max_retries=1)

    mock_sleep.assert_called_once_with(30)
    assert "in 30.0s" in caplog.messages[0]


def test_upload_retry_respects_retry_after_date(tmpdir, default_repo, caplog):
    """Use Retry-After header value (HTTP-date) as delay."""
    default_repo.disable_progress_bar = True

    resp_with_header = requests.Response()
    resp_with_header.status_code = 503
    resp_with_header.reason = "Service Unavailable"
    resp_with_header.headers["Retry-After"] = "Wed, 21 Oct 2099 07:28:00 GMT"

    default_repo.session = pretend.stub(
        post=lambda url, data, allow_redirects, headers: resp_with_header
    )

    fakefile = tmpdir.join("fake.whl")
    fakefile.write(".")

    package = pretend.stub(
        safe_name="fake",
        metadata=pretend.stub(version="2.12.0"),
        basefilename="fake.whl",
        filename=str(fakefile),
        metadata_dictionary=lambda: {"name": "fake"},
    )

    with patch("twine.repository.time.sleep") as mock_sleep:
        default_repo.upload(package, max_retries=1)

    # The delay should be capped at MAX_RETRY_DELAY (300s) since the date
    # is far in the future.
    mock_sleep.assert_called_once_with(repository.MAX_RETRY_DELAY)


def test_upload_retry_exponential_backoff(tmpdir, default_repo):
    """Verify delay doubles each attempt with exponential backoff."""
    default_repo.disable_progress_bar = True
    default_repo.retry_delay = 2

    default_repo.session = pretend.stub(
        post=lambda url, data, allow_redirects, headers: response_with(
            status_code=500, reason="Internal server error"
        )
    )

    fakefile = tmpdir.join("fake.whl")
    fakefile.write(".")

    package = pretend.stub(
        safe_name="fake",
        metadata=pretend.stub(version="2.12.0"),
        basefilename="fake.whl",
        filename=str(fakefile),
        metadata_dictionary=lambda: {"name": "fake"},
    )

    with patch("twine.repository.time.sleep") as mock_sleep, patch(
        "twine.repository.random.uniform", return_value=0.0
    ):
        default_repo.upload(package, max_retries=4)

    # delay = retry_delay * 2^(attempt-1): 2, 4, 8, 16
    assert mock_sleep.call_args_list == [
        call(2.0),
        call(4.0),
        call(8.0),
        call(16.0),
    ]


def test_upload_retry_max_delay_cap(tmpdir, default_repo):
    """Verify delay is capped at MAX_RETRY_DELAY (300s)."""
    default_repo.disable_progress_bar = True
    default_repo.retry_delay = 200

    default_repo.session = pretend.stub(
        post=lambda url, data, allow_redirects, headers: response_with(
            status_code=500, reason="Internal server error"
        )
    )

    fakefile = tmpdir.join("fake.whl")
    fakefile.write(".")

    package = pretend.stub(
        safe_name="fake",
        metadata=pretend.stub(version="2.12.0"),
        basefilename="fake.whl",
        filename=str(fakefile),
        metadata_dictionary=lambda: {"name": "fake"},
    )

    with patch("twine.repository.time.sleep") as mock_sleep, patch(
        "twine.repository.random.uniform", return_value=0.0
    ):
        default_repo.upload(package, max_retries=2)

    # 200*1=200, 200*2=400 -> capped to 300
    assert mock_sleep.call_args_list == [
        call(200.0),
        call(repository.MAX_RETRY_DELAY),
    ]


def test_upload_retry_success_after_transient_failure(tmpdir, default_repo, caplog):
    """Succeed after a transient 429 followed by 200."""
    default_repo.disable_progress_bar = True

    responses = [
        response_with(status_code=429, reason="Too Many Requests"),
        response_with(status_code=200, reason="OK"),
    ]
    call_count = {"n": 0}

    def fake_post(url, data, allow_redirects, headers):
        resp = responses[call_count["n"]]
        call_count["n"] += 1
        return resp

    default_repo.session = pretend.stub(post=fake_post)

    fakefile = tmpdir.join("fake.whl")
    fakefile.write(".")

    package = pretend.stub(
        safe_name="fake",
        metadata=pretend.stub(version="2.12.0"),
        basefilename="fake.whl",
        filename=str(fakefile),
        metadata_dictionary=lambda: {"name": "fake"},
    )

    with patch("twine.repository.time.sleep"), patch(
        "twine.repository.random.uniform", return_value=0.0
    ):
        resp = default_repo.upload(package)

    assert resp.status_code == 200
    assert len(caplog.messages) == 1
    assert "429: Too Many Requests" in caplog.messages[0]


def test_upload_no_retry_on_non_retryable(tmpdir, default_repo, caplog):
    """Return immediately on non-retryable status codes like 400 and 403."""
    default_repo.disable_progress_bar = True

    default_repo.session = pretend.stub(
        post=lambda url, data, allow_redirects, headers: response_with(
            status_code=403, reason="Forbidden"
        )
    )

    fakefile = tmpdir.join("fake.whl")
    fakefile.write(".")

    package = pretend.stub(
        safe_name="fake",
        metadata=pretend.stub(version="2.12.0"),
        basefilename="fake.whl",
        filename=str(fakefile),
        metadata_dictionary=lambda: {"name": "fake"},
    )

    with patch("twine.repository.time.sleep") as mock_sleep:
        resp = default_repo.upload(package)

    assert resp.status_code == 403
    mock_sleep.assert_not_called()
    assert len(caplog.messages) == 0


def test_parse_retry_after_integer():
    """Parse integer Retry-After header."""
    resp = requests.Response()
    resp.headers["Retry-After"] = "120"
    assert repository.Repository._parse_retry_after(resp) == 120


def test_parse_retry_after_missing():
    """Return None when Retry-After header is absent."""
    resp = requests.Response()
    assert repository.Repository._parse_retry_after(resp) is None


def test_parse_retry_after_invalid():
    """Return None when Retry-After header is unparseable."""
    resp = requests.Response()
    resp.headers["Retry-After"] = "not-a-number-or-date"
    assert repository.Repository._parse_retry_after(resp) is None


@pytest.mark.parametrize(
    "package_meta,repository_url,release_urls",
    [
        # Single package
        (
            [("fake", "2.12.0")],
            utils.DEFAULT_REPOSITORY,
            {"https://pypi.org/project/fake/2.12.0/"},
        ),
        # Single package to testpypi
        (
            [("fake", "2.12.0")],
            utils.TEST_REPOSITORY,
            {"https://test.pypi.org/project/fake/2.12.0/"},
        ),
        # Multiple packages (faking a wheel and an sdist)
        (
            [("fake", "2.12.0"), ("fake", "2.12.0")],
            utils.DEFAULT_REPOSITORY,
            {"https://pypi.org/project/fake/2.12.0/"},
        ),
        # Multiple releases
        (
            [("fake", "2.12.0"), ("fake", "2.12.1")],
            utils.DEFAULT_REPOSITORY,
            {
                "https://pypi.org/project/fake/2.12.0/",
                "https://pypi.org/project/fake/2.12.1/",
            },
        ),
        # Not pypi
        ([("fake", "2.12.0")], "http://devpi.example.com", set()),
        # No packages
        ([], utils.DEFAULT_REPOSITORY, set()),
    ],
)
def test_release_urls(package_meta, repository_url, release_urls):
    """Generate a set of PyPI release URLs for a list of packages."""
    packages = [
        pretend.stub(safe_name=name, version=version) for name, version in package_meta
    ]

    repo = repository.Repository(
        repository_url=repository_url,
        username="username",
        password="password",
    )

    assert repo.release_urls(packages) == release_urls


def test_package_is_uploaded_incorrect_repo_url():
    """Return False when using an incorrect repository URL."""
    repo = repository.Repository(
        repository_url="https://bad.repo.com/legacy",
        username="username",
        password="password",
    )

    repo.url = "https://bad.repo.com/legacy"

    assert repo.package_is_uploaded(None) is False


@pytest.mark.parametrize(
    "username, password, messages",
    [
        (None, None, ["username: <empty>", "password: <empty>"]),
        ("", "", ["username: <empty>", "password: <empty>"]),
        ("username", "password", ["username: username", "password: <hidden>"]),
    ],
)
def test_logs_username_and_password(username, password, messages, caplog):
    caplog.set_level(logging.INFO, "twine")

    repository.Repository(
        repository_url=utils.DEFAULT_REPOSITORY,
        username=username,
        password=password,
    )

    assert caplog.messages == messages
