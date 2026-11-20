"""Curation through jumpstarter.client.decorators.robot and robot.api.deco gives the same keywords."""

from __future__ import annotations

import pytest
from robot.api import deco

from .curation import auto_keywords, documentation, is_exposed, matches, method_words, tags, type_overrides
from .driver_library import Driver
from jumpstarter.client import DriverClient
from jumpstarter.client.decorators import robot as jrobot
from jumpstarter.client.introspect import iter_client_members


class JsPowerClient(DriverClient):
    """Power through Jumpstarter's decorators."""

    def on(self) -> None:
        """Turn on."""

    @jrobot.keyword(tags=["power"])
    def cycle(self, wait: int = 2) -> None:
        """Cycle the power."""

    @jrobot.keyword("Should Be On", tags=["assert"])
    def should_be_on(self) -> None:
        """Fail unless on."""

    @jrobot.not_keyword
    def read(self) -> None: ...

    @jrobot.keyword(types={"count": str})
    def typed(self, count: int, other: int = 0) -> None: ...

    @jrobot.keyword(types=None)
    def raw(self, value: int) -> None: ...

    @jrobot.keyword
    def bare(self) -> None:
        """Bare decorator."""


class RfPowerClient(DriverClient):
    """Power through Robot Framework's decorators."""

    def on(self) -> None:
        """Turn on."""

    @deco.keyword(tags=["power"])
    def cycle(self, wait: int = 2) -> None:
        """Cycle the power."""

    @deco.keyword("Should Be On", tags=["assert"])
    def should_be_on(self) -> None:
        """Fail unless on."""

    @deco.not_keyword
    def read(self) -> None: ...

    @deco.keyword(types={"count": str})
    def typed(self, count: int, other: int = 0) -> None: ...

    @deco.keyword(types=None)
    def raw(self, value: int) -> None: ...

    @deco.keyword
    def bare(self) -> None:
        """Bare decorator."""


@jrobot.library(auto_keywords=False)
class JsCurated(DriverClient):
    def plain(self) -> None: ...

    @jrobot.keyword
    def chosen(self) -> None: ...


@deco.library(auto_keywords=False)
class RfCurated(DriverClient):
    def plain(self) -> None: ...

    @deco.keyword
    def chosen(self) -> None: ...


@jrobot.library
class JsBareLibrary(DriverClient):
    def plain(self) -> None: ...

    @jrobot.keyword("Picked")
    def chosen(self) -> None: ...


@jrobot.library(auto_keywords=True, doc="Curated introduction.")
class JsDocumented(DriverClient):
    """Docstring introduction."""

    def plain(self) -> None: ...


class JsChild(JsPowerClient):
    @jrobot.not_keyword
    def on(self) -> None: ...

    def extra(self) -> None:
        """Only on the child."""


class JsCuratedChild(JsCurated):
    def child_plain(self) -> None: ...

    @jrobot.keyword
    def child_chosen(self) -> None: ...


def keywords(cls: type, path: str = "dut.power", **kwargs) -> dict[str, dict]:
    """Keyword name → what Robot Framework sees, minus the class-specific footer and tag."""
    library = Driver(path, client=f"{cls.__module__}.{cls.__qualname__}", **kwargs)
    found = {}
    for name in library.get_keyword_names():
        found[name] = {
            "args": library.get_keyword_arguments(name),
            "types": library.get_keyword_types(name),
            "tags": [t for t in library.get_keyword_tags(name) if not t.startswith("class:")],
            "doc": library.get_keyword_documentation(name).split("\n\n_Driver")[0],
        }
    return found


class TestDecoratorEquivalence:
    def test_same_keywords(self):
        assert keywords(JsPowerClient) == keywords(RfPowerClient)

    def test_names(self):
        assert sorted(keywords(JsPowerClient)) == [
            "Dut Power Bare",
            "Dut Power Cycle",
            "Dut Power On",
            "Dut Power Raw",
            "Dut Power Should Be On",
            "Dut Power Typed",
        ]

    def test_rename_keeps_prefix(self):
        assert "Power Should Be On" in keywords(JsPowerClient, prefix="Power")
        assert "Should Be On" in keywords(JsPowerClient, prefix="")

    def test_tags_and_docs(self):
        found = keywords(JsPowerClient)
        assert found["Dut Power Cycle"]["tags"] == ["jumpstarter", "driver:dut.power", "method", "power"]
        assert found["Dut Power Should Be On"]["tags"][-1] == "assert"
        assert found["Dut Power Cycle"]["doc"] == "Cycle the power."
        assert found["Dut Power Cycle"]["args"] == [("wait", 2)]
        assert found["Dut Power Cycle"]["types"] == {"wait": int}

    def test_type_overrides(self):
        found = keywords(JsPowerClient)
        assert found["Dut Power Typed"]["types"] == {"count": str, "other": int}
        assert found["Dut Power Raw"]["types"] is None

    def test_auto_keywords_off(self):
        assert list(keywords(JsCurated)) == ["Dut Power Chosen"]
        assert keywords(JsCurated) == keywords(RfCurated)

    def test_bare_library_decorator_turns_auto_keywords_off(self):
        assert list(keywords(JsBareLibrary)) == ["Dut Power Picked"]

    def test_library_doc_is_the_introduction(self):
        library = Driver("thing", client=f"{__name__}.JsDocumented")
        intro = library.get_keyword_documentation("__intro__")
        assert intro.startswith("Curated introduction.")
        assert "Docstring introduction." not in intro
        assert list(keywords(JsDocumented)) == ["Dut Power Plain"]


class TestInheritance:
    def test_override_hides_and_inherited_decorations_stay(self):
        found = keywords(JsChild)
        assert "Dut Power On" not in found
        assert "Dut Power Extra" in found
        assert found["Dut Power Cycle"]["tags"][-1] == "power"
        assert "Dut Power Should Be On" in found
        assert "Dut Power Read" not in found

    def test_auto_keywords_flag_is_inherited(self):
        assert sorted(keywords(JsCuratedChild)) == ["Dut Power Child Chosen", "Dut Power Chosen"]


class TestCurationHelpers:
    def member(self, cls: type, name: str):
        return next(m for m in iter_client_members(cls) if m.name == name)

    def test_auto_keywords(self):
        assert auto_keywords(JsPowerClient) is True
        assert auto_keywords(JsCurated) is False
        assert auto_keywords(RfCurated) is False

    @pytest.mark.parametrize("cls", [JsPowerClient, RfPowerClient])
    def test_is_exposed(self, cls):
        assert is_exposed(self.member(cls, "on"), cls)
        assert not is_exposed(self.member(cls, "read"), cls)

    def test_method_words(self):
        assert method_words(self.member(JsPowerClient, "should_be_on")) == "Should Be On"
        assert method_words(self.member(JsPowerClient, "cycle")) == "Cycle"
        assert method_words(self.member(JsPowerClient, "on")) == "On"

    def test_tags(self):
        assert tags(self.member(JsPowerClient, "cycle")) == ("power",)
        assert tags(self.member(JsPowerClient, "on")) == ()

    def test_documentation_prefers_curated_doc(self):
        class Client(DriverClient):
            @jrobot.keyword(doc="Curated.")
            def documented(self):
                """Docstring."""

        assert documentation(self.member(Client, "documented")) == "Curated."
        assert documentation(self.member(JsPowerClient, "on")) == "Turn on."
        assert documentation(self.member(JsPowerClient, "read")) == ""

    def test_sequence_type_overrides(self):
        class Client(DriverClient):
            @jrobot.keyword(types=[int, None, str])
            def three(self, a, b, c): ...

        assert type_overrides(self.member(Client, "three")) == {"a": int, "c": str}
        assert type_overrides(self.member(JsPowerClient, "on")) == {}
        assert type_overrides(self.member(JsPowerClient, "raw")) is None

    def test_matches(self):
        assert matches("power_on", ["power_*"])
        assert not matches("power_on", ["off"])
        assert not matches("power_on", None)
