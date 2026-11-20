import pytest

from .naming import keyword_name, normalize, split_path, title, words


@pytest.mark.parametrize(
    "parts, expected",
    [
        (("power",), ["Power"]),
        (("dut_link", "powerOn"), ["Dut", "Link", "Power", "On"]),
        (("dutlink.power",), ["Dutlink", "Power"]),
        (("read-until", "a/b"), ["Read", "Until", "A", "B"]),
        (("HTTPServer",), ["HTTP", "Server"]),
        (("dut1", "power2"), ["Dut1", "Power2"]),
        (("dut1Power",), ["Dut1", "Power"]),
        (("getUSBDevice",), ["Get", "USB", "Device"]),
        (("  spaced  out ",), ["Spaced", "Out"]),
        (("__private__",), ["Private"]),
        (("",), []),
    ],
)
def test_words(parts, expected):
    assert words(*parts) == expected


def test_title_joins_words():
    assert title("dut1.power", "cycle") == "Dut1 Power Cycle"


def test_normalize_matches_robot_rules():
    assert normalize("Dut Power_On") == normalize("dutpoweron") == "dutpoweron"
    assert normalize("A B") != normalize("A.B")


def test_split_path():
    assert split_path("dut.power") == ("dut", "power")
    assert split_path("dut..power.") == ("dut", "power")
    assert split_path(["a", "b"]) == ("a", "b")
    assert split_path("") == ()


def test_keyword_name():
    assert keyword_name("Power", "On") == "Power On"
    assert keyword_name("", "On") == "On"
    assert keyword_name("Get", keyword_name("Dut Power", "Voltage")) == "Get Dut Power Voltage"
