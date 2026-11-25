"""Keyword curation with Robot Framework's own ``robot.api.deco`` decorators."""

from __future__ import annotations

from robot.api import deco

from .curation import matches
from .driver_library import Driver
from jumpstarter.client import DriverClient


class PowerClient(DriverClient):
    def on(self) -> None:
        """Turn on."""

    @deco.keyword("Should Be On", tags=["assert"])
    def should_be_on(self) -> None:
        """Fail unless on."""

    @deco.not_keyword
    def read(self) -> None: ...

    @deco.keyword(types={"count": str})
    def typed(self, count: int, other: int = 0) -> None: ...


@deco.library(auto_keywords=False)
class CuratedClient(DriverClient):
    def plain(self) -> None: ...

    @deco.keyword
    def chosen(self) -> None: ...


def library(cls: type, **kwargs) -> Driver:
    return Driver("dut.power", client=f"{cls.__module__}.{cls.__qualname__}", **kwargs)


def test_keyword_renames_and_not_keyword_hides():
    names = library(PowerClient).get_keyword_names()
    assert names == ["Dut Power On", "Dut Power Should Be On", "Dut Power Typed"]
    assert "Should Be On" in library(PowerClient, prefix="").get_keyword_names()


def test_tags_docs_and_types():
    power = library(PowerClient)
    assert power.get_keyword_tags("Dut Power Should Be On")[-1] == "assert"
    assert power.get_keyword_documentation("Dut Power Should Be On").startswith("Fail unless on.")
    assert power.get_keyword_types("Dut Power Typed") == {"count": str, "other": int}


def test_auto_keywords_off_exposes_only_decorated_methods():
    assert library(CuratedClient).get_keyword_names() == ["Dut Power Chosen"]


def test_matches():
    assert matches("power_on", ["power_*"])
    assert not matches("power_on", ["off"])
    assert not matches("power_on", None)
