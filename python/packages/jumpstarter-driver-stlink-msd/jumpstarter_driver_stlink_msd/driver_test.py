import pytest

from .driver import _validate_firmware_name
from .stlink_mount import looks_like_stlink_volume


def test_looks_like_stlink_volume_with_mbed(tmp_path):
    (tmp_path / "MBED.HTM").touch()
    assert looks_like_stlink_volume(tmp_path) is True


def test_looks_like_stlink_volume_with_details(tmp_path):
    (tmp_path / "DETAILS.TXT").touch()
    assert looks_like_stlink_volume(tmp_path) is True


def test_looks_like_stlink_volume_no_markers(tmp_path):
    assert looks_like_stlink_volume(tmp_path) is False


def test_looks_like_stlink_volume_known_prefix(tmp_path):
    nod_dir = tmp_path / "NOD_H755ZI"
    nod_dir.mkdir()
    assert looks_like_stlink_volume(nod_dir) is True


def test_looks_like_stlink_volume_nucleo_prefix(tmp_path):
    nucleo_dir = tmp_path / "NUCLEO"
    nucleo_dir.mkdir()
    assert looks_like_stlink_volume(nucleo_dir) is True


def test_looks_like_stlink_volume_not_dir(tmp_path):
    f = tmp_path / "MBED.HTM"
    f.touch()
    assert looks_like_stlink_volume(f) is False


def test_validate_firmware_name_bin():
    _validate_firmware_name("firmware.bin")


def test_validate_firmware_name_hex():
    _validate_firmware_name("firmware.hex")


def test_validate_firmware_name_bin_uppercase():
    _validate_firmware_name("FIRMWARE.BIN")


def test_validate_firmware_name_elf_rejected():
    with pytest.raises(ValueError, match="Unsupported firmware format"):
        _validate_firmware_name("firmware.elf")


def test_validate_firmware_name_unknown_rejected():
    with pytest.raises(ValueError, match="Unsupported firmware format"):
        _validate_firmware_name("firmware.xyz")
