"""Planning: the fastboot vocabulary of a FlashManifest, turned into operations."""

import json

import pytest

from .manifest import FastbootOptions, FlashManifest, ManifestError, normalize_path
from .plan import build_plan, parse_android_info

MANIFEST = """
apiVersion: jumpstarter.dev/v1alpha1
kind: FlashManifest
metadata:
  name: test
spec:
  requires:
    product: testdev
  fastboot:
    slot: inactive
    finally: continue
  steps:
    - name: Bootloader
      critical: true
      fastboot:
        flash:
          - { partition: abl, file: data/abl.elf }
          - { partition: cdt, file: data/cdt-v1.bin, variant: v1 }
          - { partition: cdt, file: data/cdt-v2.bin, variant: v2 }
    - fastboot:
        flash:
          - { partition: boot, file: ./data/boot.img }
    - fastboot: {reboot: fastboot}
    - fastboot:
        flash:
          - { partition: system, file: data/system.img }
    - when: wipe
      fastboot: {erase: userdata}
    - fastboot: {reboot: bootloader}
    - fastboot: {set_active: inactive}
"""

FILES = {"data/abl.elf", "data/cdt-v1.bin", "data/cdt-v2.bin", "data/boot.img", "data/system.img"}


def parse(text=MANIFEST):
    return FlashManifest.parse(text)


def plan(text=MANIFEST, *, files=FILES, variant="v2", wipe=False, allowed_oem=(), critical=("abl*",)):
    return build_plan(parse(text), files=files, variant=variant, wipe=wipe, critical_partitions=list(critical),
                      allowed_oem_commands=list(allowed_oem))


def test_plan_keeps_manifest_order_and_tracks_mode():
    ops = plan()
    assert [(op["op"], op.get("partition") or op.get("target") or op.get("slot")) for op in ops] == [
        ("flash", "abl"),
        ("flash", "cdt"),
        ("flash", "boot"),
        ("mode", "userspace"),
        ("flash", "system"),
        ("mode", "bootloader"),
        ("set_active", "inactive"),
    ]
    assert ops[1]["file"] == "data/cdt-v2.bin"
    assert ops[2]["file"] == "data/boot.img"
    assert ops[4]["mode"] == "userspace"
    assert ops[0]["critical"] and ops[1]["critical"] and not ops[2]["critical"]
    assert ops[6]["critical"]  # set_active is always critical


def test_critical_partitions_by_pattern():
    ops = plan(MANIFEST.replace("      critical: true\n", ""))
    assert [op["critical"] for op in ops[:3]] == [True, False, False]  # abl* matches, cdt and boot don't


def test_wipe_steps_only_with_wipe():
    assert "erase" not in [op["op"] for op in plan()]
    assert "erase" in [op["op"] for op in plan(wipe=True)]


def test_variants_require_exporter_variant():
    with pytest.raises(ManifestError, match="variant"):
        plan(variant=None)


def test_variant_lists_select_any_listed_variant():
    text = MANIFEST.replace("variant: v2", "variant: [v2, v3]")
    assert "data/cdt-v2.bin" in [op.get("file") for op in plan(text, variant="v3")]
    assert "data/cdt-v2.bin" not in [op.get("file") for op in plan(text, variant="v4")]


def test_missing_file_rejected():
    with pytest.raises(ManifestError, match="not in the bundle"):
        plan(files=FILES - {"data/boot.img"})


def test_a_plan_must_write_something():
    text = MANIFEST.split("  steps:")[0] + "  steps:\n    - fastboot: {reboot: fastboot}\n    - sleep: 1\n"
    with pytest.raises(ManifestError, match="nothing to do"):
        plan(text)


def test_sleep_steps_are_plain_operations():
    reboot = "    - fastboot: {reboot: fastboot}\n"
    op = plan(MANIFEST.replace(reboot, "    - sleep: 0.5\n" + reboot))[3]
    assert op["op"] == "sleep" and op["seconds"] == 0.5 and op["describe"] == "sleep 0.5s" and not op["critical"]


def test_path_traversal_rejected():
    for bad in ("../etc/passwd", "/etc/passwd", "data/../../x"):
        with pytest.raises(ManifestError):
            normalize_path(bad)
        with pytest.raises(ManifestError, match="escapes the bundle"):
            parse(MANIFEST.replace("data/abl.elf", bad))
    assert normalize_path("./data/x.img") == "data/x.img"


def test_fastboot_step_needs_exactly_one_action():
    with pytest.raises(ManifestError, match="exactly one of flash/erase"):
        parse(MANIFEST.replace("fastboot: {reboot: fastboot}", "fastboot: {reboot: fastboot, erase: misc}"))


def test_step_needs_exactly_one_action():
    with pytest.raises(ManifestError, match="exactly one action"):
        parse(MANIFEST.replace("- fastboot: {reboot: fastboot}", "- fastboot: {reboot: fastboot}\n      sleep: 1"))


def test_unknown_actions_are_rejected():
    with pytest.raises(ManifestError, match=r"unknown action 'qdl' \(expected fastboot or sleep\)"):
        parse(MANIFEST.replace("- fastboot: {reboot: fastboot}", "- qdl: {storage: ufs}"))


def test_old_kind_rejected():
    with pytest.raises(ManifestError, match="expected 'FlashManifest'"):
        parse(MANIFEST.replace("kind: FlashManifest", "kind: FastbootBundleManifest"))


def oem_manifest(command):
    return MANIFEST.replace("- fastboot: {set_active: inactive}", f"- fastboot: {{oem: {command!r}}}")


@pytest.mark.parametrize("command", ["lock", "unlock", "lock_critical", "unlock_critical now"])
def test_lock_unlock_never_allowed(command):
    with pytest.raises(ManifestError, match="never allowed"):
        plan(oem_manifest(command), allowed_oem=[command])


def test_oem_requires_allowlist():
    with pytest.raises(ManifestError, match="allowed_oem_commands"):
        plan(oem_manifest("select-display-panel x"))
    op = plan(oem_manifest("select-display-panel x"), allowed_oem=["select-display-panel x"])[-1]
    assert op == {"name": "step 7", "op": "oem", "command": "select-display-panel x",
                  "critical": False, "mode": "bootloader", "describe": "oem select-display-panel x"}


def test_oem_is_never_retried(tmp_path):
    from .runner import Runner

    (tmp_path / "job.json").write_text(json.dumps({
        "settings": {"step_retries": 3},
        "fastboot": {"binary": "fastboot", "usb_port": "usb:1-2", "command_timeout": 1},
    }))
    runner = Runner(tmp_path)
    try:
        assert runner.retries({"op": "oem"}) == 0
        assert runner.retries({"op": "flash"}) == 3
    finally:
        runner.output.close()


def test_options_default_and_finally_alias():
    options = parse().options
    assert isinstance(options, FastbootOptions) and options.slot == "inactive" and options.finally_ == "continue"
    defaults = parse(MANIFEST.replace("  fastboot:\n    slot: inactive\n    finally: continue\n", "")).options
    assert defaults.slot == "current" and defaults.finally_ == "reboot"
    with pytest.raises(ManifestError, match="spec.fastboot"):
        parse(MANIFEST.replace("slot: inactive", "slot: c"))


def test_synthesized_manifest_parses():
    from .driver import FastbootFlasher

    raw = FastbootFlasher.synthesize(None, [("boot", "boot/boot.img"), ("vbmeta", "vbmeta/v.img")], "local")  # ty: ignore[invalid-argument-type]
    manifest = parse(raw)
    assert manifest.files == ["boot/boot.img", "vbmeta/v.img"]
    assert manifest.options.finally_ == "continue"


def test_yaml_booleans_in_requires():
    manifest = parse(MANIFEST.replace("product: testdev", "unlocked: yes\n    secure: {equals: no, optional: true}"))
    assert manifest.requires["unlocked"].allowed == ["yes"]
    assert manifest.requires["secure"].allowed == ["no"] and manifest.requires["secure"].optional


def test_android_info_follows_aosp_fastboot():
    text = (
        "require board=alpha|beta\n"                      # board means product
        "require version-bootloader=abl-2.*\n"            # prefix match
        "reject version-baseband=g5300\n"
        "require-for-product:beta version-bootloader=istanbul|constantinople\n"
        "require partition-exists=vendor_dlkm\n"
        "variant=evt\n"                                  # 'require' is optional
        "\n"
        "this line is not a requirement\n"               # skipped, as fastboot does
    )
    reqs = parse_android_info(text)
    assert [(r.variable, r.allowed, r.reject, r.for_product) for r in reqs] == [
        ("product", ["alpha", "beta"], False, None),
        ("version-bootloader", ["abl-2.*"], False, None),
        ("version-baseband", ["g5300"], True, None),
        ("version-bootloader", ["istanbul", "constantinople"], False, "beta"),
        ("has-slot:vendor_dlkm", ["yes", "no"], False, None),
        ("variant", ["evt"], False, None),
    ]
    assert reqs[1].matches("abl-2.0.7") and not reqs[1].matches("abl-1.9")
    assert not reqs[2].matches("g5300") and reqs[2].matches("g5400")


def test_android_info_is_a_bundle_file():
    option = "    android_info: {file: android-info.txt}\n"
    manifest = MANIFEST.replace("    finally: continue\n", "    finally: continue\n" + option)
    assert "android-info.txt" in parse(manifest).files
