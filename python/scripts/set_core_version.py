"""Stamp the jumpstarter-core distribution with the Jumpstarter release version.

Release builds give the native wheel the same version as the other Jumpstarter
packages, which pin it exactly on Windows. The checked-in version is a
placeholder for local and development builds.
"""

import argparse
import re
from pathlib import Path

from packaging.version import Version

PYPROJECT = Path(__file__).resolve().parents[1] / "native" / "jumpstarter-core" / "pyproject.toml"


def set_version(pyproject: Path, version: str, tag: str | None = None) -> str:
    normalized = str(Version(version))
    if tag is not None and Version(tag.removeprefix("v")) != Version(normalized):
        raise SystemExit(f"Computed version {normalized} does not match release tag {tag}")
    text = pyproject.read_text(encoding="utf-8")
    updated, count = re.subn(r'(?m)^version = "[^"]*"$', f'version = "{normalized}"', text, count=1)
    if count != 1:
        raise SystemExit(f"No version field in {pyproject}")
    pyproject.write_text(updated, encoding="utf-8")
    return normalized


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="Version computed for the other Jumpstarter packages")
    parser.add_argument("--tag", help="Release tag that the version must match")
    parser.add_argument("--pyproject", type=Path, default=PYPROJECT)
    args = parser.parse_args(argv)
    print(set_version(args.pyproject, args.version, args.tag))


if __name__ == "__main__":
    main()
