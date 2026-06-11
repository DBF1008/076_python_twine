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
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Set, Tuple

import requests
import requests_toolbelt
import rich.progress
from rich import print

from twine import package as package_file
from twine.utils import make_requests_session

LEGACY_PYPI = "https://pypi.python.org/"
LEGACY_TEST_PYPI = "https://testpypi.python.org/"
WAREHOUSE = "https://upload.pypi.org/"
OLD_WAREHOUSE = "https://upload.pypi.io/"
TEST_WAREHOUSE = "https://test.pypi.org/"
WAREHOUSE_WEB = "https://pypi.org/"

MAX_RETRY_DELAY = 300  # 5 minutes

logger = logging.getLogger(__name__)


class Repository:
    def __init__(
        self,
        repository_url: str,
        username: Optional[str],
        password: Optional[str],
        disable_progress_bar: bool = False,
        max_retries: int = 5,
        retry_delay: float = 10,
    ) -> None:
        self.url = repository_url

        self.session = make_requests_session()
        # requests.Session.auth should be Union[None, Tuple[str, str], ...]
        # But username or password could be None
        # See TODO for utils.RepositoryConfig
        self.session.auth = (
            (username or "", password or "") if username or password else None
        )
        logger.info(f"username: {username if username else '<empty>'}")
        logger.info(f"password: <{'hidden' if password else 'empty'}>")

        # Working around https://github.com/python/typing/issues/182
        self._releases_json_data: Dict[str, Dict[str, Any]] = {}
        self.disable_progress_bar = disable_progress_bar
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    def close(self) -> None:
        self.session.close()

    @staticmethod
    def _convert_metadata_to_list_of_tuples(
        data: package_file.PackageMetadata,
    ) -> List[Tuple[str, Any]]:
        # This does what ``warehouse.forklift.parse_form_metadata()`` does, in reverse.
        data_to_send: List[Tuple[str, Any]] = []
        for key, value in data.items():
            if key == "gpg_signature":
                assert isinstance(value, tuple)
                data_to_send.append((key, value))
            elif key == "project_urls":
                assert isinstance(value, dict)
                for name, url in value.items():
                    data_to_send.append((key, f"{name}, {url}"))
            elif key == "keywords":
                assert isinstance(value, list)
                data_to_send.append((key, ", ".join(value)))
            elif isinstance(value, (list, tuple)):
                data_to_send.extend((key, item) for item in value)
            else:
                assert isinstance(value, str)
                data_to_send.append((key, value))
        return data_to_send

    def set_certificate_authority(self, cacert: Optional[str]) -> None:
        if cacert:
            self.session.verify = cacert

    def set_client_certificate(self, clientcert: Optional[str]) -> None:
        if clientcert:
            self.session.cert = clientcert

    def register(self, package: package_file.PackageFile) -> requests.Response:
        print(f"Registering {package.basefilename}")

        metadata = package.metadata_dictionary()
        data_to_send = self._convert_metadata_to_list_of_tuples(metadata)
        data_to_send.append((":action", "submit"))
        data_to_send.append(("protocol_version", "1"))
        encoder = requests_toolbelt.MultipartEncoder(data_to_send)
        resp = self.session.post(
            self.url,
            data=encoder,
            allow_redirects=False,
            headers={"Content-Type": encoder.content_type},
        )
        # Bug 28. Try to silence a ResourceWarning by releasing the socket.
        resp.close()
        return resp

    def _upload(self, package: package_file.PackageFile) -> requests.Response:
        print(f"Uploading {package.basefilename}")

        metadata = package.metadata_dictionary()
        data_to_send = self._convert_metadata_to_list_of_tuples(metadata)
        data_to_send.append((":action", "file_upload"))
        data_to_send.append(("protocol_version", "1"))
        with open(package.filename, "rb") as fp:
            data_to_send.append(
                (
                    "content",
                    (package.basefilename, fp, "application/octet-stream"),
                )
            )
            encoder = requests_toolbelt.MultipartEncoder(data_to_send)

            with rich.progress.Progress(
                "[progress.percentage]{task.percentage:>3.0f}%",
                rich.progress.BarColumn(),
                rich.progress.DownloadColumn(),
                "•",
                rich.progress.TimeRemainingColumn(
                    compact=True,
                    elapsed_when_finished=True,
                ),
                "•",
                rich.progress.TransferSpeedColumn(),
                disable=self.disable_progress_bar,
            ) as progress:
                task_id = progress.add_task("", total=encoder.len)

                monitor = requests_toolbelt.MultipartEncoderMonitor(
                    encoder,
                    lambda monitor: progress.update(
                        task_id,
                        completed=monitor.bytes_read,
                    ),
                )

                resp = self.session.post(
                    self.url,
                    data=monitor,
                    allow_redirects=False,
                    headers={"Content-Type": monitor.content_type},
                )

        return resp

    @staticmethod
    def _parse_retry_after(response: requests.Response) -> Optional[float]:
        """Parse the Retry-After header from a response.

        Supports both integer seconds and HTTP-date formats (RFC 7231).
        Returns the number of seconds to wait, or None if absent/invalid.
        """
        retry_after = response.headers.get("Retry-After")
        if retry_after is None:
            return None

        # Try integer seconds first
        try:
            return max(0, int(retry_after))
        except ValueError:
            pass

        # Try HTTP-date format
        try:
            retry_date = parsedate_to_datetime(retry_after)
            delay = (retry_date - datetime.now(timezone.utc)).total_seconds()
            return max(0, delay)
        except (ValueError, TypeError):
            return None

    def _is_retryable(self, status_code: int) -> bool:
        """Determine if a response status code is retryable."""
        return status_code == 429 or 500 <= status_code < 600

    def upload(
        self, package: package_file.PackageFile, max_retries: Optional[int] = None
    ) -> requests.Response:
        retries = max_retries if max_retries is not None else self.max_retries
        attempt = 0
        while True:
            resp = self._upload(package)

            if resp.status_code == requests.codes.OK:
                return resp

            if not self._is_retryable(resp.status_code) or attempt >= retries:
                return resp

            attempt += 1

            retry_after = self._parse_retry_after(resp)
            if retry_after is not None:
                delay = retry_after
            else:
                delay = self.retry_delay * (2 ** (attempt - 1)) + random.uniform(0, 1)
            delay = min(delay, MAX_RETRY_DELAY)

            logger.warning(
                f'Received "{resp.status_code}: {resp.reason}". '
                f"Retry {attempt} of {retries} in {delay:.1f}s."
            )
            time.sleep(delay)

    def package_is_uploaded(
        self, package: package_file.PackageFile, bypass_cache: bool = False
    ) -> bool:
        """Determine if a package has been uploaded to PyPI already.

        .. warning:: This does not support indexes other than PyPI or TestPyPI

        :param package:
            The package file that will otherwise be uploaded.
        :type package:
            :class:`~twine.package.PackageFile`
        :param bypass_cache:
            Force a request to PyPI.
        :type bypass_cache:
            bool
        :returns:
            True if package has already been uploaded, False otherwise
        :rtype:
            bool
        """
        # NOTE(sigmavirus24): Not all indices are PyPI and pypi.io doesn't
        # have a similar interface for finding the package versions.
        if not self.url.startswith((LEGACY_PYPI, WAREHOUSE, OLD_WAREHOUSE)):
            return False

        safe_name = package.safe_name
        releases = None

        if not bypass_cache:
            releases = self._releases_json_data.get(safe_name)

        if releases is None:
            url = f"{LEGACY_PYPI}pypi/{safe_name}/json"
            headers = {"Accept": "application/json"}
            response = self.session.get(url, headers=headers)
            if response.status_code == 200:
                releases = response.json()["releases"]
            else:
                releases = {}
            self._releases_json_data[safe_name] = releases

        packages = releases.get(package.version, [])

        for uploaded_package in packages:
            if uploaded_package["filename"] == package.basefilename:
                return True

        return False

    def release_urls(self, packages: List[package_file.PackageFile]) -> Set[str]:
        if self.url.startswith(WAREHOUSE):
            url = WAREHOUSE_WEB
        elif self.url.startswith(TEST_WAREHOUSE):
            url = TEST_WAREHOUSE
        else:
            return set()

        return {
            f"{url}project/{package.safe_name}/{package.version}/"
            for package in packages
        }

    def verify_package_integrity(self, package: package_file.PackageFile) -> None:
        # TODO(sigmavirus24): Add a way for users to download the package and
        # check its hash against what it has locally.
        pass
