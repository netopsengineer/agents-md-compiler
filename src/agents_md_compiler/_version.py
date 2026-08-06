"""Installed distribution version discovery.

The compiler reports its own version from installed package metadata rather than
from a hard-coded string, so a wheel and its metadata can never disagree. The
version deliberately never enters rendered output: a tool upgrade must not change
policy bytes when the format, manifest, lock, and modules are identical.
"""

from importlib.metadata import PackageNotFoundError, version

DISTRIBUTION_NAME = "agents-md-compiler"
"""Distribution name used for metadata lookup."""

UNKNOWN_VERSION = "0.0.0+unknown"
"""Reported when the package is not installed, as in a bare source checkout."""


def distribution_version() -> str:
    """Report the installed distribution version.

    Returns:
        The version from installed package metadata, or ``0.0.0+unknown`` when no
        installed distribution provides it.
    """
    try:
        return version(DISTRIBUTION_NAME)
    except PackageNotFoundError:
        return UNKNOWN_VERSION
