import base64
import getpass
import logging
import platform
import re
import time
import typing as t

import pytest
import requests.auth

from twine import auth
from twine import exceptions
from twine import utils


@pytest.fixture
def config() -> utils.RepositoryConfig:
    return dict(repository="system")


def test_get_username_keyring_defers_to_prompt(monkeypatch, entered_username, config):
    class MockKeyring:
        @staticmethod
        def get_credential(system, user):
            return None

    monkeypatch.setattr(auth, "keyring", MockKeyring)

    username = auth.Resolver(config, auth.CredentialInput()).username
    assert username == "entered user"


def test_get_username_keyring_not_installed_defers_to_prompt(
    monkeypatch, entered_username, config
):
    monkeypatch.setattr(auth, "keyring", None)

    username = auth.Resolver(config, auth.CredentialInput()).username
    assert username == "entered user"


def test_get_password_keyring_defers_to_prompt(monkeypatch, entered_password, config):
    class MockKeyring:
        @staticmethod
        def get_password(system, user):
            return None

    monkeypatch.setattr(auth, "keyring", MockKeyring)

    pw = auth.Resolver(config, auth.CredentialInput("user")).password
    assert pw == "entered pw"


def test_get_password_keyring_not_installed_defers_to_prompt(
    monkeypatch, entered_password, config
):
    monkeypatch.setattr(auth, "keyring", None)

    pw = auth.Resolver(config, auth.CredentialInput("user")).password
    assert pw == "entered pw"


def test_no_password_defers_to_prompt(monkeypatch, entered_password, config):
    config.update(password=None)
    pw = auth.Resolver(config, auth.CredentialInput("user")).password
    assert pw == "entered pw"


def test_empty_password_bypasses_prompt(monkeypatch, entered_password, config):
    config.update(password="")
    pw = auth.Resolver(config, auth.CredentialInput("user")).password
    assert pw == ""


def test_no_username_non_interactive_aborts(config):
    with pytest.raises(exceptions.NonInteractive):
        auth.Private(config, auth.CredentialInput()).username


def test_no_password_non_interactive_aborts(config):
    with pytest.raises(exceptions.NonInteractive):
        auth.Private(config, auth.CredentialInput("user")).password


def test_get_username_and_password_keyring_overrides_prompt(
    monkeypatch, config, caplog
):
    caplog.set_level(logging.INFO, "twine")

    class MockKeyring:
        @staticmethod
        def get_credential(system, user):
            return auth.CredentialInput(
                "real_user", f"real_user@{system} sekure pa55word"
            )

        @staticmethod
        def get_password(system, user):
            cred = MockKeyring.get_credential(system, user)
            if user != cred.username:
                raise RuntimeError("unexpected username")
            return cred.password

    monkeypatch.setattr(auth, "keyring", MockKeyring)

    res = auth.Resolver(config, auth.CredentialInput())

    assert res.username == "real_user"
    assert res.password == "real_user@system sekure pa55word"

    assert caplog.messages == [
        "Querying keyring for username",
        "username set from keyring",
        "Querying keyring for password",
        "password set from keyring",
    ]


@pytest.fixture
def keyring_missing_get_credentials(monkeypatch):
    """Simulate keyring prior to 15.2 that does not have the 'get_credential' API."""
    monkeypatch.delattr(auth.keyring, "get_credential")


@pytest.fixture
def entered_username(monkeypatch):
    monkeypatch.setattr(auth, "input", lambda prompt: "entered user", raising=False)


def test_get_username_keyring_missing_get_credentials_prompts(
    entered_username, keyring_missing_get_credentials, config
):
    assert auth.Resolver(config, auth.CredentialInput()).username == "entered user"


def test_get_username_keyring_missing_non_interactive_aborts(
    entered_username, keyring_missing_get_credentials, config
):
    with pytest.raises(exceptions.NonInteractive):
        auth.Private(config, auth.CredentialInput()).username


def test_get_password_keyring_missing_non_interactive_aborts(
    entered_username, keyring_missing_get_credentials, config
):
    with pytest.raises(exceptions.NonInteractive):
        auth.Private(config, auth.CredentialInput("user")).password


def test_get_username_keyring_runtime_error_logged(
    entered_username, monkeypatch, config, caplog
):
    class FailKeyring:
        """Simulate missing keyring backend raising RuntimeError on get_credential."""

        @staticmethod
        def get_credential(system, username):
            raise RuntimeError("fail!")

    monkeypatch.setattr(auth, "keyring", FailKeyring)

    assert auth.Resolver(config, auth.CredentialInput()).username == "entered user"

    assert re.search(
        r"Error getting username from keyring.+Traceback.+RuntimeError: fail!",
        caplog.text,
        re.DOTALL,
    )


def test_get_password_keyring_runtime_error_logged(
    entered_username, entered_password, monkeypatch, config, caplog
):
    class FailKeyring:
        """Simulate missing keyring backend raising RuntimeError on get_password."""

        @staticmethod
        def get_password(system, username):
            raise RuntimeError("fail!")

    monkeypatch.setattr(auth, "keyring", FailKeyring)

    assert auth.Resolver(config, auth.CredentialInput()).password == "entered pw"

    assert re.search(
        r"Error getting password from keyring.+Traceback.+RuntimeError: fail!",
        caplog.text,
        re.DOTALL,
    )


def _raise_home_key_error():
    """Simulate environment from https://github.com/pypa/twine/issues/889."""
    try:
        raise KeyError("HOME")
    except KeyError:
        raise KeyError("uid not found: 999")


def test_get_username_keyring_key_error_logged(
    entered_username, monkeypatch, config, caplog
):
    class FailKeyring:
        @staticmethod
        def get_credential(system, username):
            _raise_home_key_error()

    monkeypatch.setattr(auth, "keyring", FailKeyring)

    assert auth.Resolver(config, auth.CredentialInput()).username == "entered user"

    assert re.search(
        r"Error getting username from keyring"
        r".+Traceback"
        r".+KeyError: 'HOME'"
        r".+KeyError: 'uid not found: 999'",
        caplog.text,
        re.DOTALL,
    )


def test_get_password_keyring_key_error_logged(
    entered_username, entered_password, monkeypatch, config, caplog
):
    class FailKeyring:
        @staticmethod
        def get_password(system, username):
            _raise_home_key_error()

    monkeypatch.setattr(auth, "keyring", FailKeyring)

    assert auth.Resolver(config, auth.CredentialInput()).password == "entered pw"

    assert re.search(
        r"Error getting password from keyring"
        r".+Traceback"
        r".+KeyError: 'HOME'"
        r".+KeyError: 'uid not found: 999'",
        caplog.text,
        re.DOTALL,
    )


def test_logs_cli_values(caplog, config):
    caplog.set_level(logging.INFO, "twine")

    res = auth.Resolver(config, auth.CredentialInput("username", "password"))

    assert res.username == "username"
    assert res.password == "password"

    assert caplog.messages == [
        "username set by command options",
        "password set by command options",
    ]


def test_logs_config_values(config, caplog):
    caplog.set_level(logging.INFO, "twine")

    config.update(username="username", password="password")
    res = auth.Resolver(config, auth.CredentialInput())

    assert res.username == "username"
    assert res.password == "password"

    assert caplog.messages == [
        "username set from config file",
        "password set from config file",
    ]


@pytest.mark.parametrize(
    "password, warning",
    [
        ("", "Your password is empty"),
        ("\x16", "Your password contains control characters"),
        ("entered\x16pw", "Your password contains control characters"),
    ],
)
def test_warns_for_empty_password(
    password,
    warning,
    monkeypatch,
    entered_username,
    config,
    caplog,
):
    # Avoiding additional warning "No recommended backend was available"
    monkeypatch.setattr(auth.keyring, "get_password", lambda system, user: None)

    monkeypatch.setattr(getpass, "getpass", lambda prompt: password)

    assert auth.Resolver(config, auth.CredentialInput()).password == password

    assert caplog.messages[0].startswith(warning)


@pytest.mark.skipif(
    platform.machine() in {"ppc64le", "s390x"},
    reason="keyring module is optional on ppc64le and s390x",
)
def test_keyring_module():
    assert auth.keyring is not None


def test_resolver_authenticator_config_authentication(config):
    config.update(username="username", password="password")
    res = auth.Resolver(config, auth.CredentialInput())
    assert isinstance(res.authenticator, requests.auth.HTTPBasicAuth)


def test_resolver_authenticator_credential_input_authentication(config):
    res = auth.Resolver(config, auth.CredentialInput("username", "password"))
    assert isinstance(res.authenticator, requests.auth.HTTPBasicAuth)


def test_resolver_authenticator_trusted_publishing_authentication(config):
    res = auth.Resolver(
        config, auth.CredentialInput(username="__token__", password="skip-stdin")
    )
    res._tp_token = auth.TrustedPublishingToken(
        success=True,
        token="fake-tp-token",
    )
    assert isinstance(res.authenticator, auth.TrustedPublishingAuthenticator)


class MockResponse:
    def __init__(self, status_code: int, json: t.Any) -> None:
        self.status_code = status_code
        self._json = json

    def json(self, *args, **kwargs) -> t.Any:
        return self._json

    def raise_for_status(self) -> None:
        if 400 <= self.status_code:
            raise requests.exceptions.HTTPError()

    def ok(self) -> bool:
        return self.status_code == 200


class MockSession:
    def __init__(
        self,
        get_response_list: t.List[MockResponse],
        post_response_list: t.List[MockResponse],
    ) -> None:
        self.post_counter = self.get_counter = 0
        self.get_response_list = get_response_list
        self.post_response_list = post_response_list

    def get(self, url: str, **kwargs) -> MockResponse:
        response = self.get_response_list[self.get_counter]
        self.get_counter += 1
        return response

    def post(self, url: str, **kwargs) -> MockResponse:
        response = self.post_response_list[self.post_counter]
        self.post_counter += 1
        return response


def test_trusted_publish_authenticator_refreshes_token(monkeypatch, config):
    def make_session():
        return MockSession(
            get_response_list=[
                MockResponse(status_code=200, json={"audience": "fake-aud"})
            ],
            post_response_list=[
                MockResponse(
                    status_code=200,
                    json={
                        "success": True,
                        "token": "new-token",
                        "expires": int(time.time()) + 900,
                    },
                ),
            ],
        )

    def detect_credential(*args, **kwargs) -> str:
        return "fake-oidc-token"

    config.update({"repository": utils.TEST_REPOSITORY})
    res = auth.Resolver(config, auth.CredentialInput(username="__token__"))
    res._tp_token = auth.TrustedPublishingToken(
        success=True,
        token="expiring-tp-token",
    )
    res._expires = int(time.time()) + 4 * 60
    res._tp_repository = "test.pypi.org"
    monkeypatch.setattr(auth, "detect_credential", detect_credential)
    monkeypatch.setattr(auth.utils, "make_requests_session", make_session)
    authenticator = auth.TrustedPublishingAuthenticator(resolver=res)
    prepped_req = requests.models.PreparedRequest()
    prepped_req.prepare_headers({})
    request = authenticator(prepped_req)
    assert (
        request.headers["Authorization"]
        == f"Basic {base64.b64encode(b'__token__:new-token').decode()}"
    )


def test_trusted_publish_authenticator_reuses_token(monkeypatch, config):
    def make_session():
        return MockSession(
            get_response_list=[
                MockResponse(status_code=200, json={"audience": "fake-aud"})
            ],
            post_response_list=[
                MockResponse(
                    status_code=200,
                    json={
                        "success": True,
                        "token": "new-token",
                        "expires": int(time.time()) + 900,
                    },
                ),
            ],
        )

    def detect_credential(*args, **kwargs) -> str:
        return "fake-oidc-token"

    config.update({"repository": utils.TEST_REPOSITORY})
    res = auth.Resolver(config, auth.CredentialInput(username="__token__"))
    res._tp_token = auth.TrustedPublishingToken(
        success=True,
        token="valid-tp-token",
    )
    res._expires = int(time.time()) + 900
    res._tp_repository = "test.pypi.org"
    monkeypatch.setattr(auth, "detect_credential", detect_credential)
    monkeypatch.setattr(auth.utils, "make_requests_session", make_session)
    authenticator = auth.TrustedPublishingAuthenticator(resolver=res)
    prepped_req = requests.models.PreparedRequest()
    prepped_req.prepare_headers({})
    request = authenticator(prepped_req)
    assert (
        request.headers["Authorization"]
        == f"Basic {base64.b64encode(b'__token__:valid-tp-token').decode()}"
    )


def test_inability_to_make_token_raises_error():
    class MockResolver:
        def make_trusted_publishing_token(self) -> None:
            return None

    authenticator = auth.TrustedPublishingAuthenticator(
        resolver=MockResolver(),
    )
    with pytest.raises(exceptions.TrustedPublishingFailure):
        authenticator(None)


class TestTrustedPublishingRepositoryIsolation:
    """Tests that trusted publishing tokens are scoped per-repository and never
    leak across indices (e.g., TestPyPI -> PyPI) within the same process."""

    def test_different_resolver_instances_do_not_share_token(self):
        """Two Resolver instances for different repos must have independent caches."""
        testpypi_config: utils.RepositoryConfig = dict(
            repository=utils.TEST_REPOSITORY
        )
        pypi_config: utils.RepositoryConfig = dict(
            repository=utils.DEFAULT_REPOSITORY
        )
        resolver_test = auth.Resolver(
            testpypi_config, auth.CredentialInput(username="__token__")
        )
        resolver_prod = auth.Resolver(
            pypi_config, auth.CredentialInput(username="__token__")
        )

        # Simulate TestPyPI resolver acquiring a token
        resolver_test._tp_token = auth.TrustedPublishingToken(
            success=True, token="testpypi-token"
        )
        resolver_test._expires = int(time.time()) + 900
        resolver_test._tp_repository = "test.pypi.org"

        # The PyPI resolver must not see the TestPyPI token
        assert resolver_prod._tp_token is None
        assert resolver_prod._expires is None
        assert resolver_prod._tp_repository is None

    def test_cached_token_not_reused_for_different_repository(self, monkeypatch):
        """A cached token for TestPyPI must not pass _has_valid_cached_tp_token
        when the resolver is now pointing at PyPI."""
        config: utils.RepositoryConfig = dict(repository=utils.TEST_REPOSITORY)
        resolver = auth.Resolver(config, auth.CredentialInput(username="__token__"))

        # Simulate a valid cached token for TestPyPI
        resolver._tp_token = auth.TrustedPublishingToken(
            success=True, token="testpypi-token"
        )
        resolver._expires = int(time.time()) + 900
        resolver._tp_repository = "test.pypi.org"

        # Token is valid for the same repo
        assert resolver._has_valid_cached_tp_token() is True

        # Switch to PyPI — the cached token must NOT be considered valid
        resolver.config["repository"] = utils.DEFAULT_REPOSITORY
        assert resolver._has_valid_cached_tp_token() is False

    def test_repository_switch_invalidates_and_mints_new_token(self, monkeypatch):
        """Switching repositories mid-session must invalidate the old token and
        mint a fresh one for the new repository."""
        call_log: t.List[str] = []

        def make_session_for_testpypi():
            return MockSession(
                get_response_list=[
                    MockResponse(200, {"audience": "testpypi-aud"})
                ],
                post_response_list=[
                    MockResponse(
                        200,
                        {
                            "success": True,
                            "token": "testpypi-token",
                            "expires": int(time.time()) + 900,
                        },
                    )
                ],
            )

        def make_session_for_pypi():
            return MockSession(
                get_response_list=[
                    MockResponse(200, {"audience": "pypi-aud"})
                ],
                post_response_list=[
                    MockResponse(
                        200,
                        {
                            "success": True,
                            "token": "pypi-token",
                            "expires": int(time.time()) + 900,
                        },
                    )
                ],
            )

        sessions = iter([make_session_for_testpypi(), make_session_for_pypi()])

        def detect_credential(audience: str) -> str:
            call_log.append(audience)
            return "fake-oidc-token"

        monkeypatch.setattr(auth, "detect_credential", detect_credential)
        monkeypatch.setattr(
            auth.utils, "make_requests_session", lambda: next(sessions)
        )

        config: utils.RepositoryConfig = dict(repository=utils.TEST_REPOSITORY)
        resolver = auth.Resolver(config, auth.CredentialInput(username="__token__"))

        # First call — mint for TestPyPI
        token1 = resolver.make_trusted_publishing_token()
        assert token1 == "testpypi-token"
        assert resolver._tp_repository == "test.pypi.org"

        # Switch repository to PyPI
        resolver.config["repository"] = utils.DEFAULT_REPOSITORY

        # Second call — must invalidate TestPyPI token and mint for PyPI
        token2 = resolver.make_trusted_publishing_token()
        assert token2 == "pypi-token"
        assert resolver._tp_repository == "upload.pypi.org"

        # Verify both audiences were requested (proving two separate OIDC flows)
        assert call_log == ["testpypi-aud", "pypi-aud"]

    def test_consecutive_resolvers_testpypi_then_pypi(self, monkeypatch):
        """End-to-end: two separate Resolver instances (TestPyPI then PyPI) in
        the same process each mint their own token without cross-contamination."""

        def make_testpypi_session():
            return MockSession(
                get_response_list=[
                    MockResponse(200, {"audience": "testpypi-aud"})
                ],
                post_response_list=[
                    MockResponse(
                        200,
                        {
                            "success": True,
                            "token": "testpypi-token-value",
                            "expires": int(time.time()) + 900,
                        },
                    )
                ],
            )

        def make_pypi_session():
            return MockSession(
                get_response_list=[
                    MockResponse(200, {"audience": "pypi-aud"})
                ],
                post_response_list=[
                    MockResponse(
                        200,
                        {
                            "success": True,
                            "token": "pypi-token-value",
                            "expires": int(time.time()) + 900,
                        },
                    )
                ],
            )

        monkeypatch.setattr(auth, "detect_credential", lambda aud: "oidc-tok")

        # --- Upload to TestPyPI ---
        monkeypatch.setattr(
            auth.utils, "make_requests_session", make_testpypi_session
        )
        resolver1 = auth.Resolver(
            dict(repository=utils.TEST_REPOSITORY),
            auth.CredentialInput(username="__token__"),
        )
        t1 = resolver1.make_trusted_publishing_token()
        assert t1 == "testpypi-token-value"
        assert resolver1._tp_repository == "test.pypi.org"

        # --- Upload to PyPI ---
        monkeypatch.setattr(
            auth.utils, "make_requests_session", make_pypi_session
        )
        resolver2 = auth.Resolver(
            dict(repository=utils.DEFAULT_REPOSITORY),
            auth.CredentialInput(username="__token__"),
        )
        t2 = resolver2.make_trusted_publishing_token()
        assert t2 == "pypi-token-value"
        assert resolver2._tp_repository == "upload.pypi.org"

        # Resolvers must be fully independent
        assert resolver1._tp_token["token"] == "testpypi-token-value"
        assert resolver2._tp_token["token"] == "pypi-token-value"

    def test_expired_token_falls_back_to_prompt_interactive(self, monkeypatch):
        """When the cached token has expired and OIDC returns None, an
        interactive Resolver must fall back to prompting."""
        config: utils.RepositoryConfig = dict(repository=utils.TEST_REPOSITORY)
        resolver = auth.Resolver(config, auth.CredentialInput(username="__token__"))

        # Expired token
        resolver._tp_token = auth.TrustedPublishingToken(
            success=True, token="expired-token"
        )
        resolver._expires = int(time.time()) - 60  # expired 1 minute ago
        resolver._tp_repository = "test.pypi.org"

        def detect_credential(audience: str) -> None:
            return None

        monkeypatch.setattr(auth, "detect_credential", detect_credential)
        monkeypatch.setattr(
            auth.utils,
            "make_requests_session",
            lambda: MockSession(
                get_response_list=[
                    MockResponse(200, {"audience": "test-aud"})
                ],
                post_response_list=[],
            ),
        )

        # _make_trusted_publishing_token should return None (trigger fallback)
        result = resolver._make_trusted_publishing_token()
        assert result is None

    def test_expired_token_noninteractive_raises(self, monkeypatch):
        """When the cached token has expired and OIDC returns None, a
        non-interactive Private resolver must raise NonInteractive when
        the full credential chain runs."""
        config: utils.RepositoryConfig = dict(repository=utils.TEST_REPOSITORY)
        resolver = auth.Private(config, auth.CredentialInput(username="__token__"))

        # Expired token
        resolver._tp_token = auth.TrustedPublishingToken(
            success=True, token="expired-token"
        )
        resolver._expires = int(time.time()) - 60
        resolver._tp_repository = "test.pypi.org"

        monkeypatch.setattr(auth, "detect_credential", lambda aud: None)
        monkeypatch.setattr(auth, "keyring", None)
        monkeypatch.setattr(
            auth.utils,
            "make_requests_session",
            lambda: MockSession(
                get_response_list=[
                    MockResponse(200, {"audience": "test-aud"})
                ],
                post_response_list=[],
            ),
        )

        # The full password resolution chain should hit NonInteractive
        with pytest.raises(exceptions.NonInteractive):
            resolver.password_from_keyring_or_trusted_publishing_or_prompt()

    def test_valid_token_still_reused_for_same_repository(self, monkeypatch):
        """A non-expired token for the current repository should be reused
        without any network calls (regression check)."""
        config: utils.RepositoryConfig = dict(repository=utils.TEST_REPOSITORY)
        resolver = auth.Resolver(config, auth.CredentialInput(username="__token__"))

        resolver._tp_token = auth.TrustedPublishingToken(
            success=True, token="reusable-token"
        )
        resolver._expires = int(time.time()) + 900
        resolver._tp_repository = "test.pypi.org"

        # Should NOT call make_requests_session at all
        monkeypatch.setattr(
            auth.utils,
            "make_requests_session",
            lambda: (_ for _ in ()).throw(AssertionError("unexpected network call")),
        )

        token = resolver.make_trusted_publishing_token()
        assert token == "reusable-token"
