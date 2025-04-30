#!/usr/bin/env python3

import argparse
import os
from pathlib import Path
from typing import List

import tomli
import tomli_w


def find_pyproject_files(root_dir: str) -> List[str]:
    """Find all pyproject.toml files recursively in the given directory."""
    pyproject_files = []
    for root, _, files in os.walk(root_dir):
        for file in files:
            if file == "pyproject.toml":
                pyproject_files.append(os.path.join(root, file))
    return pyproject_files


def process_dependencies(deps: List[str], version: str, unpin: bool) -> List[str]:
    """Process dependencies to pin or unpin jumpstarter packages."""
    if deps is None:
        return []

    result = []
    for pkg in deps:
        pkg = pkg.strip()
        if not pkg.startswith("jumpstarter"):
            result.append(pkg)
        else:
            # split package name and version name
            pkg_name, _, pkg_version = pkg.partition("~=")
            pkg_name = pkg_name.strip()
            pkg_version = pkg_version.strip()
            if unpin:
                result.append(pkg_name)
            else:
                result.append(f"{pkg_name} ~= {version}")

    return result


def modify_pyproject(file_path: str, version: str, unpin: bool) -> bool:
    """Modify a pyproject.toml file to pin or unpin jumpstarter dependencies."""
    try:
        with open(file_path, "rb") as f:
            pyproject = tomli.load(f)

        modified = False

        # Process dependencies in [project.dependencies]
        if "project" in pyproject and "dependencies" in pyproject["project"]:
            deps = pyproject["project"]["dependencies"]
            new_deps = process_dependencies(deps, version, unpin)
            if new_deps != deps:
                pyproject["project"]["dependencies"] = new_deps
                modified = True

        # Write back if modified
        if modified:
            with open(file_path, "wb") as f:
                tomli_w.dump(pyproject, f)
            return True

        return False

    except Exception as e:
        print(f"Error processing {file_path}: {e}")
        raise e
        return False


def main():
    parser = argparse.ArgumentParser(description="Pin or unpin jumpstarter package versions in pyproject.toml files")
    parser.add_argument("--pin", metavar="VERSION", help="Pin jumpstarter dependencies to specified version")
    parser.add_argument("--unpin", action="store_true", help="Remove version constraints from jumpstarter dependencies")
    parser.add_argument("--root", default=".", help="Root directory to search for pyproject.toml files")

    args = parser.parse_args()

    if not args.pin and not args.unpin:
        parser.error("Either --pin VERSION or --unpin must be specified")

    if args.pin and args.unpin:
        parser.error("Cannot specify both --pin and --unpin")

    root_dir = os.path.abspath(args.root)
    pyproject_files = find_pyproject_files(root_dir)

    modified_count = 0
    for file_path in pyproject_files:
        if modify_pyproject(file_path, args.pin or "", args.unpin):
            print(f"Modified: {Path(file_path).relative_to(root_dir)}")
            modified_count += 1

    action = "unpinned" if args.unpin else f"pinned to ~={args.pin}"
    print(f"\nProcessed {len(pyproject_files)} pyproject.toml files, {modified_count} " +
          f"files had jumpstarter dependencies {action}")


if __name__ == "__main__":
    main()
