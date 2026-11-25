from driver_index import index_problems

INDEX = "- {doc}`Power <power>` (`jumpstarter-driver-power`) - power control\n"


def test_a_listed_driver_with_a_page_passes():
    assert index_problems(["power"], {"power"}, INDEX) == []


def test_a_driver_without_a_page_is_reported():
    assert index_problems(["power"], set(), INDEX) == ["jumpstarter-driver-power has no page in the driver reference"]


def test_a_driver_missing_from_the_index_is_reported():
    assert index_problems(["power", "qemu"], {"power", "qemu"}, INDEX) == [
        "jumpstarter-driver-qemu is not listed in the driver index"
    ]
