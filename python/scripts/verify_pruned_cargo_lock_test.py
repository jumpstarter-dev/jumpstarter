"""Check the packaging guard rejects dependency changes, including existing versions."""

from copy import deepcopy

import pytest
from verify_pruned_cargo_lock import verify_pruned_lock


def locks():
    original = {
        "version": 4,
        "package": [
            {"name": "binding", "version": "0.1.0", "dependencies": ["syn 2.0.1"]},
            {"name": "unused", "version": "0.1.0", "dependencies": ["syn 1.0.1"]},
            {"name": "syn", "version": "1.0.1", "source": "registry+fixture", "checksum": "old-version"},
            {"name": "syn", "version": "2.0.1", "source": "registry+fixture", "checksum": "current-version"},
        ],
    }
    pruned = {"version": 4, "package": deepcopy([original["package"][0], original["package"][3]])}
    pruned["package"][0]["dependencies"] = ["syn"]
    return original, pruned


def test_unused_packages_and_dependency_disambiguation_may_be_pruned():
    original, pruned = locks()
    removed = verify_pruned_lock(original, pruned)
    assert {item[0] for item in removed} == {"unused", "syn"}


def test_checksum_change_is_rejected():
    original, pruned = locks()
    pruned["package"][1]["checksum"] = "different-content"
    with pytest.raises(ValueError, match="checksum changed"):
        verify_pruned_lock(original, pruned)


def test_switching_to_another_already_locked_version_is_rejected():
    original, pruned = locks()
    pruned["package"][1] = deepcopy(original["package"][2])
    with pytest.raises(ValueError, match="Resolved dependencies changed"):
        verify_pruned_lock(original, pruned)


def test_new_version_is_rejected():
    original, pruned = locks()
    pruned["package"][1]["version"] = "3.0.1"
    with pytest.raises(ValueError, match="Resolved dependencies changed|version/source changed"):
        verify_pruned_lock(original, pruned)


def test_lock_format_change_is_rejected():
    original, pruned = locks()
    pruned["version"] = 3
    with pytest.raises(ValueError, match="metadata changed"):
        verify_pruned_lock(original, pruned)


def test_ambiguous_dependency_reference_is_rejected():
    original, pruned = locks()
    pruned["package"].append(deepcopy(original["package"][2]))
    with pytest.raises(ValueError, match="Cannot resolve.*uniquely"):
        verify_pruned_lock(original, pruned)
