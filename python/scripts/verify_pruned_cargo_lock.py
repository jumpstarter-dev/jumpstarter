"""Verify that packaging only pruned unused entries from a Cargo lockfile.

Maturin reduces workspace membership in an sdist but copies its full lockfile.
Cargo may therefore remove unused packages and shorten dependency references.
Retained packages and their resolved dependencies must remain unchanged.
"""

import argparse
import tomllib
from pathlib import Path


def package_map(lock):
    result = {}
    for package in lock.get("package", []):
        identity = package["name"], package["version"], package.get("source")
        if identity in result:
            raise ValueError(f"Duplicate locked package: {identity}")
        result[identity] = package
    return result


def resolve_dependency(reference, packages):
    parts = reference.split(" ", 2)
    candidates = [
        identity
        for identity in packages
        if identity[0] == parts[0]
        and (len(parts) < 2 or identity[1] == parts[1])
        and (len(parts) < 3 or identity[2] == parts[2].removeprefix("(").removesuffix(")"))
    ]
    if len(candidates) != 1:
        raise ValueError(f"Cannot resolve locked dependency uniquely: {reference!r}")
    return candidates[0]


def verify_pruned_lock(original, pruned):
    before, after = package_map(original), package_map(pruned)
    if {key: value for key, value in original.items() if key != "package"} != {
        key: value for key, value in pruned.items() if key != "package"
    }:
        raise ValueError("Cargo lockfile metadata changed")
    for identity, package in after.items():
        if identity not in before:
            raise ValueError(f"Package added or version/source changed: {identity}")
        previous = before[identity]
        if {key: value for key, value in previous.items() if key != "dependencies"} != {
            key: value for key, value in package.items() if key != "dependencies"
        }:
            raise ValueError(f"Package metadata or checksum changed: {identity}")
        old_dependencies = {resolve_dependency(item, before) for item in previous.get("dependencies", [])}
        new_dependencies = {resolve_dependency(item, after) for item in package.get("dependencies", [])}
        if old_dependencies != new_dependencies:
            raise ValueError(f"Resolved dependencies changed: {identity}")
    return sorted(before.keys() - after.keys(), key=repr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original", type=Path)
    parser.add_argument("pruned", type=Path)
    args = parser.parse_args()
    with args.original.open("rb") as original, args.pruned.open("rb") as pruned:
        removed = verify_pruned_lock(tomllib.load(original), tomllib.load(pruned))
    print(f"Retained Cargo packages, checksums, and dependencies unchanged; pruned {len(removed)} unused entries.")
    for name, version, _source in removed:
        print(f"  {name} {version}")


if __name__ == "__main__":
    main()
