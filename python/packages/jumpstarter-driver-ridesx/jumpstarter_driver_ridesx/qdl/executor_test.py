import subprocess

from jumpstarter_driver_ridesx.qdl.executor import build_qdl_command, check_dmesg, fix_provision_default_xml
from jumpstarter_driver_ridesx.qdl.firmware_id import identify_firmware_variant
from jumpstarter_driver_ridesx.qdl.schema import QdlConfig, QdlStep


def test_identify_firmware_variant_known_es22():
    assert identify_firmware_variant("BOOT.MXF.1.2-00541-LEMANS-1") == "ES22"


def test_identify_firmware_variant_cs4_cs5_disambiguation():
    assert identify_firmware_variant("BOOT.MXF.1.2-00568-LEMANS-2", rm_version="55198a39") == "CS4"
    assert identify_firmware_variant("BOOT.MXF.1.2-00568-LEMANS-2", rm_version="ba2475df") == "CS5"
    assert identify_firmware_variant("BOOT.MXF.1.2-00568-LEMANS-2") == "CS4/CS5"


def test_build_qdl_command_expands_globs(tmp_path):
    ufs_dir = tmp_path / "ufs"
    ufs_dir.mkdir()
    programmer = ufs_dir / "prog_firehose_ddr.elf"
    programmer.write_bytes(b"elf")
    (ufs_dir / "rawprogram0.xml").write_text("<xml/>", encoding="utf-8")
    (ufs_dir / "patch0.xml").write_text("<xml/>", encoding="utf-8")

    step = QdlStep(
        qdl=QdlConfig(
            storage="ufs",
            programmer="prog_firehose_ddr.elf",
            files=["rawprogram*.xml", "patch*.xml"],
        )
    )
    cmd, workdir = build_qdl_command(step, tmp_path)
    assert workdir == ufs_dir
    assert cmd[:4] == ["qdl", "-s", "ufs", str(programmer)]
    assert str(ufs_dir / "rawprogram0.xml") in cmd
    assert str(ufs_dir / "patch0.xml") in cmd


def test_fix_provision_default_xml_strips_invalid_header(tmp_path):
    ufs_dir = tmp_path / "ufs"
    ufs_dir.mkdir()
    provision = ufs_dir / "provision_default.xml"
    provision.write_text(
        "\n".join(
            [
                "<!-- bad -->",
                "<!-- still bad -->",
                "<!-- x -->",
                "<!-- y -->",
                "<!-- z -->",
                "<!-- a -->",
                "<!-- b -->",
                "<!-- c -->",
                "<!-- d -->",
                '<?xml version="1.0" ?><data></data>',
            ]
        ),
        encoding="utf-8",
    )
    fix_provision_default_xml(ufs_dir)
    content = provision.read_text(encoding="utf-8")
    assert content.startswith("<?xml")


def test_check_dmesg_does_not_clear_kernel_log(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="line1\nUSB QTI_HS seen\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    check_dmesg("USB QTI_HS", baseline="line1\n")
    assert calls == [["dmesg"]]
    assert "-c" not in calls[0]


def test_check_dmesg_finds_marker_in_tail(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="old\nProduct: Android\n"),
    )
    check_dmesg("Product: Android")
