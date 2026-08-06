#!/usr/bin/env python3
"""Mock Codex CLI for verifier tests.

Behavior is selected by the ``MOCK_CODEX_MODE`` environment variable so one script
covers every documented failure mode. The bundle bytes it echoes come from
``MOCK_CODEX_BUNDLE``, a file the test writes, so no fixture here contains policy
content of its own.

This script never contacts a network, never reads a real Codex home, and never
sends a model request.
"""

import json
import os
import pathlib
import sys
import time

MODE = os.environ.get("MOCK_CODEX_MODE", "ok")
BUNDLE = os.environ.get("MOCK_CODEX_BUNDLE", "")


def bundle_text() -> str:
    """Read the bundle the test wants echoed back.

    Returns:
        The bundle text, or an empty string when none was configured.
    """
    if not BUNDLE:
        return ""
    return pathlib.Path(BUNDLE).read_text(encoding="utf-8")


def prompt_input_payload(text: str) -> object:
    """Build a nested prompt-input document.

    The nesting and key order deliberately differ from anything the verifier could
    assume, so the recursive extraction is what makes the search work.

    Args:
        text: Bundle text to embed.

    Returns:
        A JSON-serializable document containing the bundle text.
    """
    return {
        "items": [
            {"type": "message", "role": "developer", "content": [{"text": text}]},
            {"type": "message", "role": "user", "content": [{"text": "probe"}]},
        ],
        "metadata": {"turn": 1, "nested": {"deeper": ["a", {"also": "b"}]}},
    }


def main() -> int:
    """Dispatch on the requested mode.

    Returns:
        The process exit code.
    """
    argv = sys.argv[1:]
    if MODE == "timeout":
        # Sleeps in every branch, so a deadline is exceeded at capability detection.
        time.sleep(5)
        return 0
    if argv == ["--version"]:
        if MODE == "version-fails":
            sys.stderr.write("mock codex: version unavailable\n")
            return 3
        sys.stdout.write("codex-cli 9.9.9-mock\n")
        return 0
    if argv[:2] == ["debug", "prompt-input"] and "--help" in argv:
        if MODE == "no-capability":
            sys.stderr.write("error: unrecognized subcommand 'prompt-input'\n")
            return 2
        sys.stdout.write("Render the model-visible prompt input list as JSON\n")
        return 0
    if argv[:2] == ["debug", "prompt-input"]:
        return prompt_input(argv[2:])
    sys.stderr.write(f"mock codex: unexpected arguments {argv}\n")
    return 64


def prompt_input(rest: list[str]) -> int:
    """Emit a prompt-input document according to the selected mode.

    Args:
        rest: Arguments after the subcommand.

    Returns:
        The process exit code.
    """
    if MODE == "probe-fails":
        sys.stderr.write("mock codex: probe failed\n")
        return 7
    if MODE == "timeout-probe":
        # Sleeps only here, so capability detection succeeds and the probe is what
        # exceeds the deadline.
        time.sleep(5)
        return 0
    if MODE == "invalid-json":
        sys.stdout.write("not json at all\n")
        return 0
    text = bundle_text()
    if MODE == "missing-marker":
        text = text.replace("agents-md-compiler:module-end", "removed-end-marker")
    if MODE == "missing-header":
        text = text.replace("agents-md-compiler:generated", "removed-generated")
    if MODE == "duplicated-marker":
        text = text + "\n" + text
    if MODE == "truncated-tail":
        text = text[: len(text) // 3]
    if MODE == "huge-output":
        sys.stdout.write(json.dumps({"pad": "x" * (33 * 1024 * 1024)}))
        return 0
    sys.stdout.write(json.dumps(prompt_input_payload(text)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
