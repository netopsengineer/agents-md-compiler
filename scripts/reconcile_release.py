"""Read-only reconciliation for main commits whose bot push emitted no workflow.

This does not publish or relax automatic-release eligibility. Any missing tag,
registry evidence, or unreviewed package input fails for explicit release review.
An owner-approved, exact-tree deferral never changes automatic release eligibility.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from scripts.release_policy import is_published_path, parse_paths

VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


def validate_evidence(
    version: str, paths: tuple[str, ...], registry: Any, *, deferred: bool = False
) -> None:
    """Fail unless unchanged package inputs have a non-yanked wheel and sdist.

    Args:
        version: Stable version declared by the exact checked-out commit.
        paths: Changed paths from the published tag to that commit.
        registry: Public PyPI JSON for the exact declared version.
        deferred: Exact package tree has passed the approved deferral verification.

    Raises:
        TypeError: Registry evidence is not an object.
        ValueError: Publication evidence is missing, malformed, or inconsistent.
    """
    if VERSION.fullmatch(version) is None:
        message = "expected a stable MAJOR.MINOR.PATCH version"
        raise ValueError(message)
    changed = tuple(path for path in paths if is_published_path(path))
    if changed and not deferred:
        message = f"package inputs changed since v{version}; release review required: {changed}"
        raise ValueError(message)
    if not isinstance(registry, dict):
        message = "registry response must be an object"
        raise TypeError(message)
    record = cast("dict[str, Any]", registry)
    info = record.get("info")
    if (
        not isinstance(info, dict)
        or cast("dict[str, Any]", info).get("version") != version
    ):
        message = "registry version does not match the declared version"
        raise ValueError(message)
    files: Any = record.get("urls")
    expected = {
        f"agents_md_compiler-{version}-py3-none-any.whl",
        f"agents_md_compiler-{version}.tar.gz",
    }
    if not isinstance(files, list) or len(cast("list[Any]", files)) != 2:
        message = "expected exactly the published wheel and sdist"
        raise ValueError(message)
    names: set[str] = set()
    for item in cast("list[Any]", files):
        if (
            not isinstance(item, dict)
            or cast("dict[str, Any]", item).get("yanked") is not False
        ):
            message = "registry file is malformed or yanked"
            raise ValueError(message)
        item = cast("dict[str, Any]", item)
        name: Any = item.get("filename")
        digests: Any = item.get("digests")
        digest: Any = (
            cast("dict[str, Any]", digests).get("sha256")
            if isinstance(digests, dict)
            else None
        )
        if (
            not isinstance(name, str)
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            message = "registry filename or SHA256 evidence is missing"
            raise ValueError(message)
        names.add(name)
    if names != expected:
        message = "registry files do not match the expected wheel and sdist"
        raise ValueError(message)


def verify_downloads(registry: dict[str, Any]) -> None:
    """Hash public distribution bytes without installing or executing them.

    Args:
        registry: Registry metadata already checked by validate_evidence.

    Raises:
        TypeError: An artifact URL is not a string.
        ValueError: An artifact URL, response, size, or digest is inconsistent.
    """
    for item in registry["urls"]:
        raw_url: object = item.get("url")
        if not isinstance(raw_url, str):
            message = "artifact URL must be a string"
            raise TypeError(message)
        url = urlsplit(raw_url)
        if (
            url.scheme != "https"
            or url.netloc != "files.pythonhosted.org"
            or not url.path.startswith("/packages/")
            or url.query
            or url.fragment
        ):
            message = "artifact URL must use the fixed HTTPS PyPI file host"
            raise ValueError(message)
        connection = http.client.HTTPSConnection("files.pythonhosted.org", timeout=30)
        try:
            connection.request("GET", url.path)
            response = connection.getresponse()
            data = response.read(8 * 1024 * 1024 + 1)
            if response.status != 200 or len(data) > 8 * 1024 * 1024:
                message = "artifact download failed or exceeded the size bound"
                raise ValueError(message)
        finally:
            connection.close()
        if hashlib.sha256(data).hexdigest() != item["digests"]["sha256"]:
            message = "downloaded artifact SHA256 differs from registry evidence"
            raise ValueError(message)
        print(f"Verified public artifact bytes: {item['filename']}")


def git_bytes(*arguments: str) -> bytes:
    """Read repository evidence with fixed Git operations and no shell.

    Args:
        *arguments: Internally constructed Git arguments.

    Returns:
        Git's raw standard output; command failures propagate.

    Raises:
        ValueError: Git is unavailable.
    """
    executable = shutil.which("git")
    if executable is None:
        message = "git is required for release reconciliation"
        raise ValueError(message)
    # Fixed read-only Git operations, immutable refs, argument vector, no shell.
    return subprocess.check_output([executable, *arguments])  # noqa: S603  # nosec B603


def package_inputs(ref: str) -> dict[str, str]:
    """Hash every tracked published input at one immutable tree.

    Args:
        ref: Internally resolved commit identifier.

    Returns:
        Complete published-path to SHA256 mapping with regular file modes enforced.

    Raises:
        ValueError: A published input is not a regular non-executable file.
    """
    entries = git_bytes("ls-tree", "-r", "-z", ref).split(b"\0")
    result: dict[str, str] = {}
    for entry in entries:
        if not entry:
            continue
        metadata, raw_path = entry.split(b"\t", 1)
        path = raw_path.decode("utf-8")
        if is_published_path(path):
            mode, kind, object_id = metadata.split()
            if kind != b"blob" or mode != b"100644":
                msg = "deferred package input must be a regular non-executable file"
                raise ValueError(msg)
            data = git_bytes("cat-file", "blob", object_id.decode("ascii"))
            result[path] = hashlib.sha256(data).hexdigest()
    return result


def verify_deferral(version: str, record: Any) -> bool:
    """Accept only the exact owner-reviewed package tree and public baseline.

    A different package version retires the record automatically. Registry
    metadata and actual artifact downloads remain independently mandatory.

    Args:
        version: Current declared package version.
        record: Reviewed deferral JSON loaded from the trusted main checkout.

    Returns:
        True only for the exact approved version, ancestry and package hashes.

    Raises:
        TypeError: The deferral record is not an object.
        ValueError: The active record or repository evidence differs.
    """
    if not isinstance(record, dict):
        msg = "release deferral must be an object"
        raise TypeError(msg)
    record = cast("dict[str, Any]", record)
    if record.get("version") != version:
        return False
    if set(record) != {
        "version",
        "reviewed_commit",
        "published_commit",
        "package_inputs",
    }:
        msg = "unexpected release deferral fields"
        raise ValueError(msg)
    reviewed = record["reviewed_commit"]
    published = record["published_commit"]
    for value in (reviewed, published):
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
            msg = "release deferral commit must be an exact SHA"
            raise ValueError(msg)
    expected = record["package_inputs"]
    if not isinstance(expected, dict) or not expected:
        msg = "release deferral requires complete package hashes"
        raise ValueError(msg)
    head = git_bytes("rev-parse", "HEAD").decode("ascii").strip()
    actual_tag = (
        git_bytes("rev-parse", f"refs/tags/v{version}^{{commit}}")
        .decode("ascii")
        .strip()
    )
    if actual_tag != published:
        msg = "release deferral published baseline changed"
        raise ValueError(msg)
    git_bytes("merge-base", "--is-ancestor", published, reviewed)
    git_bytes("merge-base", "--is-ancestor", reviewed, head)
    if package_inputs(reviewed) != expected or package_inputs(head) != expected:
        msg = "release deferral package inputs changed; release review required"
        raise ValueError(msg)
    return True


def main() -> int:
    """Verify release evidence without changing repository or registry state.

    Returns:
        Zero for a verified no-op; nonzero on missing or inconsistent evidence.

    Raises:
        ValueError: The declared version is not stable.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--paths", required=True, type=Path)
    parser.add_argument("--registry", required=True, type=Path)
    arguments = parser.parse_args()
    version = arguments.version
    paths = arguments.paths.read_bytes()
    registry = json.loads(arguments.registry.read_text(encoding="utf-8"))
    if VERSION.fullmatch(version) is None:
        msg = "expected a stable MAJOR.MINOR.PATCH version"
        raise ValueError(msg)
    record_path = Path("ci/release-deferral.json")
    deferred = record_path.exists() and verify_deferral(
        version, json.loads(record_path.read_text(encoding="utf-8"))
    )
    validate_evidence(version, parse_paths(paths), registry, deferred=deferred)
    verify_downloads(registry)
    disposition = (
        "exact reviewed deferred package inputs"
        if deferred
        else "unchanged package inputs"
    )
    print(
        f"Published {version} has {disposition} and verified registry evidence. No release needed."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
