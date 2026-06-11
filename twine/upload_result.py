"""Structured upload result types and reporting."""

# Copyright 2024 Twine Contributors
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
import json
from typing import List, NamedTuple, Optional

import rich.table
from rich import print

from twine import package as package_file
from twine import repository


class FileUploadResult(NamedTuple):
    """Result of uploading a single distribution file."""

    filename: str
    status: str
    error_message: Optional[str]
    has_signature: bool
    has_attestations: bool
    release_url: Optional[str]


class UploadReport(NamedTuple):
    """Aggregated results of an upload operation."""

    repository_url: str
    files: List[FileUploadResult]


def release_url_for_package(
    repository_url: str, package: package_file.PackageFile
) -> Optional[str]:
    """Compute the release URL for a single package on a known index.

    Returns ``None`` for repositories that are not PyPI or TestPyPI.
    """
    if repository_url.startswith(repository.WAREHOUSE):
        base = repository.WAREHOUSE_WEB
    elif repository_url.startswith(repository.TEST_WAREHOUSE):
        base = repository.TEST_WAREHOUSE
    else:
        return None

    return f"{base}project/{package.safe_name}/{package.version}/"


def determine_exit_code(report: UploadReport) -> int:
    """Return an exit code reflecting the overall upload outcome.

    - ``0``: all files succeeded or were skipped.
    - ``1``: all files failed (or no files were processed).
    - ``2``: some files succeeded/skipped and some failed (partial success).
    """
    if not report.files:
        return 0

    statuses = {r.status for r in report.files}
    has_failed = "failed" in statuses
    has_ok = "success" in statuses or "skipped" in statuses

    if has_failed and has_ok:
        return 2
    if has_failed:
        return 1
    return 0


def print_summary(report: UploadReport) -> None:
    """Print a human-readable Rich table summarising the upload results."""
    status_style = {"success": "green", "skipped": "yellow", "failed": "red"}

    table = rich.table.Table(title="Upload Summary")
    table.add_column("File")
    table.add_column("Status")
    table.add_column("Signature")
    table.add_column("Attestations")
    table.add_column("Release URL")

    for f in report.files:
        style = status_style.get(f.status, "")
        table.add_row(
            f.filename,
            f"[{style}]{f.status}[/{style}]",
            "yes" if f.has_signature else "no",
            "yes" if f.has_attestations else "no",
            f.release_url or "",
        )

    counts = {"success": 0, "skipped": 0, "failed": 0}
    for f in report.files:
        counts[f.status] = counts.get(f.status, 0) + 1

    print()
    print(table)
    print(
        f"\nTotal: {len(report.files)}  "
        f"[green]Success: {counts['success']}[/green]  "
        f"[yellow]Skipped: {counts['skipped']}[/yellow]  "
        f"[red]Failed: {counts['failed']}[/red]"
    )


def write_result_file(report: UploadReport, path: str) -> None:
    """Write the upload report as stable JSON to *path*."""
    counts = {"success": 0, "skipped": 0, "failed": 0}
    for f in report.files:
        counts[f.status] = counts.get(f.status, 0) + 1

    data = {
        "schema_version": "1.0",
        "repository_url": report.repository_url,
        "summary": {
            "total": len(report.files),
            "success": counts["success"],
            "skipped": counts["skipped"],
            "failed": counts["failed"],
        },
        "files": [
            {
                "filename": f.filename,
                "status": f.status,
                "error_message": f.error_message,
                "has_signature": f.has_signature,
                "has_attestations": f.has_attestations,
                "release_url": f.release_url,
            }
            for f in report.files
        ],
    }

    with open(path, "w", encoding="utf-8") as fp:
        json.dump(data, fp, indent=2, ensure_ascii=False)
        fp.write("\n")
