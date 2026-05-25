#!/usr/bin/env python3
"""Bump the patch component of ``pyproject.toml``'s ``version`` field.

Called at the top of both installer entry points (Rule 51, 2026-05-25)
so every build of the macOS .app / DMG and every Windows source-zip
package gets a fresh patch version:

    0.1.0 → 0.1.1 → 0.1.2 → …

The new version is written back to ``pyproject.toml`` in place and
printed to stdout so shell callers can capture it::

    NEW_VERSION=$(python3 scripts/bump_patch_version.py)

Flags:

* ``--check`` — print the *would-be* next version to stdout but DO
  NOT write to the file (dry-run; used by CI to validate the parse).
* ``--target X.Y.Z`` — set an exact version instead of incrementing.

The file is parsed with a strict ``^version = "X.Y.Z"`` regex on a
line of its own. PEP 440 pre-release suffixes (``a1``, ``rc2``) are
NOT supported — the project uses plain semver-shaped versions only.
A failure to parse is a hard error so silent drift in pyproject.toml
can't masquerade as a successful build.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"

_VERSION_RE = re.compile(r'^(version\s*=\s*")(\d+)\.(\d+)\.(\d+)(")', re.MULTILINE)


def _parse_current(src: str) -> tuple[int, int, int]:
    m = _VERSION_RE.search(src)
    if not m:
        raise RuntimeError(
            "Could not find a 'version = \"X.Y.Z\"' line in pyproject.toml. "
            "The build cannot stamp a patch version without it."
        )
    return int(m.group(2)), int(m.group(3)), int(m.group(4))


def _replace(src: str, new_version: str) -> str:
    return _VERSION_RE.sub(
        lambda m: f'{m.group(1)}{new_version}{m.group(5)}',
        src,
        count=1,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Print the next version without writing pyproject.toml.",
    )
    parser.add_argument(
        "--target",
        metavar="X.Y.Z",
        help="Set this exact version instead of incrementing the patch.",
    )
    args = parser.parse_args(argv)

    src = PYPROJECT.read_text(encoding="utf-8")
    major, minor, patch = _parse_current(src)

    if args.target:
        target_match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", args.target)
        if not target_match:
            print(
                f"--target must look like X.Y.Z (got {args.target!r})",
                file=sys.stderr,
            )
            return 2
        new_version = args.target
    else:
        new_version = f"{major}.{minor}.{patch + 1}"

    if args.check:
        print(new_version)
        return 0

    PYPROJECT.write_text(_replace(src, new_version), encoding="utf-8")
    print(new_version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
