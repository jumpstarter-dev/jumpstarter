"""The FlashManifest format: envelope, steps, options, requirements, and file references."""

from __future__ import annotations

import pytest

from .manifest import FastbootOptions, FlashManifest, ManifestError, Requirement, referenced_files, synthesized_manifest


def manifest(steps, **spec):
    return {"apiVersion": "jumpstarter.dev/v1alpha1", "kind": "FlashManifest", "metadata": {"name": "m"},
            "spec": {"steps": steps, **spec}}


def flash(partition, file=None, **entry):
    return {"fastboot": {"flash": [{"partition": partition, "file": file or f"data/{partition}.img", **entry}]}}


def test_envelope_options_and_steps():
    parsed = FlashManifest.parse(manifest(
        [{"name": "one", "critical": True, **flash("abl", "data/abl.elf")},
         {"sleep": 2},
         {"when": "wipe", "fastboot": {"erase": "userdata"}},
         flash("boot", "./data/boot.img")],
        fastboot={"slot": "inactive", "finally": "stay"}, manufacturer="ACME",
        requires={"board": ["x", "y"], "secure": {"equals": False}},
    ))
    assert parsed.name == "m"
    assert isinstance(parsed.options, FastbootOptions)
    assert parsed.options.slot == "inactive" and parsed.options.finally_ == "stay"
    assert [(s.action, s.label, s.critical, s.when) for s in parsed.steps] == [
        ("fastboot", "one", True, None), ("sleep", "step 2", False, None),
        ("fastboot", "step 3", False, "wipe"), ("fastboot", "step 4", False, None)]
    assert parsed.files == ["data/abl.elf", "data/boot.img"]
    assert parsed.requires["board"].allowed == ["x", "y"] and parsed.requires["secure"].allowed == ["no"]
    assert parsed.requires["board"].variable == "board"


def test_options_are_optional():
    parsed = FlashManifest.parse(manifest([flash("boot")]))
    assert parsed.options.slot == "current" and parsed.options.finally_ == "reboot"
    assert FlashManifest.parse(manifest([flash("boot")], fastboot=None)).options.slot == "current"


def test_yaml_text_is_accepted():
    text = ("apiVersion: jumpstarter.dev/v1alpha1\nkind: FlashManifest\nspec:\n  steps:\n"
            "    - fastboot: {erase: userdata}\n")
    assert FlashManifest.parse(text).steps[0].body.erase == "userdata"


@pytest.mark.parametrize(("data", "error"), [
    ("- just a list", "not a YAML mapping"),
    ({"kind": "FlashBundleManifest", "spec": {"steps": []}}, "expected 'FlashManifest'"),
    (manifest([]), "no steps"),
    (manifest([{**flash("a"), "sleep": 1}]), "exactly one action"),
    (manifest([{"name": "x"}]), "exactly one action"),
    (manifest([{"gamma": {}}]), r"unknown action 'gamma' \(expected fastboot or sleep\)"),
    (manifest([{"fastboot": {"flash": [{"partition": "a", "file": "x", "extra": 1}]}}]), "invalid 'fastboot' step"),
    (manifest([{"fastboot": {"erase": "a", "oem": "b"}}]), "exactly one of flash/erase"),
    (manifest([{"sleep": -1}]), "number of seconds"),
    (manifest([{"sleep": True}]), "number of seconds"),
    (manifest([{"when": "always", **flash("a")}]), "step 1"),
    (manifest([flash("a")], gamma={}), "invalid FlashManifest"),
    (manifest([flash("a")], fastboot={"slot": "c"}), "spec.fastboot"),
    (manifest([flash("a")], fastboot={"speed": 1}), "spec.fastboot"),
    (manifest([flash("a", "../../etc/shadow")]), "escapes the bundle"),
    (manifest([flash("a")]) | {"extra": 1}, "invalid FlashManifest"),
])
def test_invalid_manifests_are_refused(data, error):
    with pytest.raises(ManifestError, match=error):
        FlashManifest.parse(data)


def test_file_references_are_every_file_key():
    raw = manifest([flash("a", "x/one"), {"fastboot": {"flash": [{"partition": "b", "file": "x/two"},
                                                                 {"partition": "c", "file": "x/one"}]}}])
    assert referenced_files(raw) == ["x/one", "x/two"]


def test_options_can_reference_files():
    raw = manifest([flash("a", "x/one")], fastboot={"android_info": {"file": "meta/android-info.txt"}})
    assert referenced_files(raw) == ["x/one", "meta/android-info.txt"]
    assert FlashManifest.parse(raw).files == ["x/one", "meta/android-info.txt"]


def test_synthesized_manifests_are_ordinary_manifests():
    raw = synthesized_manifest("local files", [flash("boot", "boot/img")], fastboot={"slot": "all"})
    parsed = FlashManifest.parse(raw)
    assert parsed.raw["metadata"]["synthesized"] and parsed.options.slot == "all"


@pytest.mark.parametrize(("equals", "reject", "value", "ok"), [
    ("abc", False, "abc", True),
    (["x", "abc"], False, "abc", True),
    ("ab*", False, "abc", True),
    ("ab*", False, "xabc", False),
    ("abc", True, "abc", False),
    ("ab*", True, "zzz", True),
    (True, False, "yes", True),  # unquoted YAML yes
])
def test_requirement_matching_follows_android_info(equals, reject, value, ok):
    assert Requirement(equals=equals, reject=reject).matches(value) is ok
