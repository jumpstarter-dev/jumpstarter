from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from contextlib import contextmanager

import pytest
from jumpstarter_driver_composite.driver import Composite
from jumpstarter_driver_power.driver import MockPower
from jumpstarter_driver_shell.driver import Shell
from pydantic import BaseModel
from robot.api import Failure
from robot.libdoc import LibraryDocumentation
from robot.utils import DotDict

from . import sessions
from .driver_library import Driver, class_path, import_client_class, instances
from .extensions import register_node_extension
from .library import Jumpstarter
from .testing import use_client, use_session_factory
from jumpstarter.client import DriverClient
from jumpstarter.common.utils import serve
from jumpstarter.driver import Driver as JumpstarterDriver
from jumpstarter.driver import export

POWER = "jumpstarter_driver_power.client.PowerClient"
SHELL = "jumpstarter_driver_shell.client.ShellClient"
GADGET = f"{__name__}.GadgetClient"


class Settings(BaseModel):
    level: int = 1


class GadgetClient(DriverClient):
    """A client with every member shape."""

    @property
    def label(self) -> str:
        """The gadget's label."""
        return self.call("label")

    def numbers(self, start: int = 0) -> Generator[int, None, None]:
        """Count up from ``start`` forever."""
        yield from self.streamingcall("numbers", start)

    def apply(self, settings: Settings) -> int:
        """Apply settings and return the level."""
        return self.call("apply", settings.model_dump())

    def describe(self) -> Settings:
        return Settings.model_validate(self.call("describe"))

    @contextmanager
    def opened(self):
        yield self

    async def background(self) -> None: ...


class Gadget(JumpstarterDriver):
    @classmethod
    def client(cls) -> str:
        return GADGET

    @export
    def label(self) -> str:
        return "gadget-1"

    @export
    async def numbers(self, start: int) -> AsyncGenerator[int, None]:
        n = start
        while True:
            yield n
            n += 1

    @export
    def apply(self, settings: dict) -> int:
        return settings["level"]

    @export
    def describe(self) -> dict:
        return {"level": 7}


class Uninstalled(JumpstarterDriver):
    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_not_installed.client.MissingClient"

    @export
    def ping(self) -> str:
        return "pong"


class VirtualMockPower(MockPower):
    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_power.client.VirtualPowerClient"


@pytest.fixture(scope="module")
def served():
    root = Composite(
        children={
            "dut": Composite(children={"power": MockPower(), "vpower": VirtualMockPower()}),
            "dut2": Composite(children={"power": MockPower()}),
            "shell": Shell(methods={"hello": "echo hello $1 $GREETING", "fail": "echo oops >&2; exit 3"}),
            "gadget": Gadget(),
            "missing": Uninstalled(),
        }
    )
    with serve(root) as client:
        yield client


@pytest.fixture
def tree(served):
    with use_client(served):
        yield served


def run(library: Driver, name: str, *args, **named):
    return library.run_keyword(name, list(args), named)


class TestKeywords:
    def test_power_client(self):
        library = Driver("dut.power", client=POWER)
        assert library.get_keyword_names() == [
            "Dut Power On",
            "Dut Power Off",
            "Dut Power Rescue",
            "Dut Power Cycle",
            "Dut Power Status",
            "Dut Power Should Be On",
            "Dut Power Should Be Off",
            "Dut Power Read",
        ]
        assert library.get_keyword_arguments("Dut Power Cycle") == [("wait", 2)]
        assert library.get_keyword_types("Dut Power Cycle") == {"wait": int}
        assert library.get_keyword_arguments("Dut Power Read") == ["*", ("max_items", 1000)]
        assert library.get_keyword_tags("Dut Power Read") == [
            "jumpstarter",
            "driver:dut.power",
            "class:PowerClient",
            "generator",
        ]
        doc = library.get_keyword_documentation("Dut Power On")
        assert doc.startswith("Power on the device.")
        assert "client.dut.power.on(...)" in doc
        source = library.get_keyword_source("Dut Power On")
        assert source is not None
        assert source.split(":")[0].endswith("jumpstarter_driver_power/client.py")

    def test_prefix_rules(self):
        assert "Power On" in Driver("dut.power", client=POWER, prefix="Power").get_keyword_names()
        assert "On" in Driver("dut.power", client=POWER, prefix="").get_keyword_names()
        assert "Main Power On" in Driver("dut.power", client=POWER, prefix="Main Power").get_keyword_names()
        assert "Dut1 Power On" in Driver("dut1.power", client=POWER).get_keyword_names()
        assert "Serial Port On" in Driver("serial_port", client=POWER).get_keyword_names()

    def test_root_driver_has_no_prefix(self):
        assert "On" in Driver("", client=POWER).get_keyword_names()

    def test_member_shapes(self):
        library = Driver("gadget", client=GADGET)
        names = library.get_keyword_names()
        assert "Get Gadget Label" in names
        assert library.get_keyword_arguments("Get Gadget Label") == []
        assert library.get_keyword_arguments("Gadget Numbers") == [("start", 0), "*", ("max_items", 1000)]
        assert "Gadget Opened" not in names  # context managers go through Open Driver Context
        assert "Gadget Background" not in names  # async members are skipped
        assert "``opened``" in library.get_keyword_documentation("__intro__")
        assert library.get_keyword_documentation("__intro__").startswith("A client with every member shape.")

    def test_max_items_setting(self):
        library = Driver("gadget", client=GADGET, max_items=5)
        assert library.get_keyword_arguments("Gadget Numbers")[-1] == ("max_items", 5)

    def test_include_exclude(self):
        assert Driver("p", client=POWER, include="on,off").get_keyword_names() == ["P On", "P Off"]
        excluded = Driver("p", client=POWER, exclude=["should_*", "read"]).get_keyword_names()
        assert "P Read" not in excluded and "P Should Be On" not in excluded
        assert "P On" in excluded

    def test_shell_methods(self):
        library = Driver("shell", client=SHELL, methods="hello, fail")
        assert library.get_keyword_names()[-3:] == ["Shell Run Method", "Shell Hello", "Shell Fail"]
        assert library.get_keyword_arguments("Shell Hello") == ["*args", ("expected_rc", 0), "**env"]
        assert library.get_keyword_arguments("Shell Run Method") == ["method", "*args", ("expected_rc", 0), "**env"]

    def test_no_client_outside_a_run(self):
        library = Driver("dut.power")
        assert library.get_keyword_names() == []
        assert "client class unknown" in library.get_keyword_documentation("__intro__")

    def test_import_client_class(self):
        assert import_client_class(POWER).__name__ == "PowerClient"
        assert import_client_class("jumpstarter_driver_power.client:PowerClient").__name__ == "PowerClient"
        assert class_path(import_client_class(POWER)) == POWER
        with pytest.raises(ValueError, match="dotted class path"):
            import_client_class("PowerClient")
        with pytest.raises(TypeError, match="not a class"):
            import_client_class("jumpstarter_driver_power.client.time")
        with pytest.raises(ModuleNotFoundError):
            import_client_class("not_a_module.Client")


class TestCalls:
    def test_methods_and_status(self, tree):
        library = Driver("dut.power", client=POWER, prefix="Power")
        assert run(library, "Power On") is None
        assert run(library, "Power Status") == "on"
        run(library, "Power Should Be On")
        with pytest.raises(AssertionError, match="power is on, expected off"):
            run(library, "Power Should Be Off")
        run(library, "Power Off")
        assert tree.children["dut"].children["power"].status() == "off"

    def test_generator_max_items(self, tree):
        library = Driver("dut.power", client=POWER, prefix="Power")
        readings = run(library, "Power Read", max_items="1")
        assert len(readings) == 1
        assert isinstance(readings[0], DotDict)
        assert readings[0].voltage == 0.0
        assert len(run(library, "Power Read")) == 2  # the mock stream ends after two

    def test_infinite_generator_is_cut(self, tree):
        library = Driver("gadget", client=GADGET, max_items=4)
        assert run(library, "Gadget Numbers") == [0, 1, 2, 3]
        assert run(library, "Gadget Numbers", 10, max_items=2) == [10, 11]
        # the server-side stream was cancelled, so the connection stays usable
        assert run(library, "Get Gadget Label") == "gadget-1"

    def test_property(self, tree):
        assert run(Driver("gadget", client=GADGET), "Get Gadget Label") == "gadget-1"

    def test_pydantic_argument_and_result(self, tree):
        library = Driver("gadget", client=GADGET)
        assert run(library, "Gadget Apply", '{"level": 3}') == 3
        assert run(library, "Gadget Apply", settings={"level": 4}) == 4
        result = run(library, "Gadget Describe")
        assert isinstance(result, DotDict) and result.level == 7
        with pytest.raises(Failure, match="Invalid value for argument 'settings'"):
            run(library, "Gadget Apply", "not json")

    def test_subclass_instance_binds(self, tree):
        assert run(Driver("dut.vpower", client=POWER), "Dut Vpower Status") == "off"

    def test_class_mismatch(self, tree):
        library = Driver("dut.power", client=SHELL)
        with pytest.raises(Failure) as error:
            run(library, "Dut Power Run Method", "x")
        assert str(error.value) == (
            f"driver 'dut.power' on exporter 'test' is {POWER}, but this library was generated "
            f"for {SHELL}; regenerate the client resource"
        )

    def test_missing_required_and_optional(self, tree):
        with pytest.raises(Failure, match=r"driver 'camera' .* available: .*required driver is not on exporter 'test'"):
            run(Driver("camera", client=POWER), "Camera On")
        with pytest.raises(Failure, match=r"driver 'dut.camera' .*\['power', 'vpower'\].*optional driver"):
            run(Driver("dut.camera", client=POWER, required=False), "Dut Camera On")

    def test_stub_client(self, tree):
        with pytest.raises(Failure, match="Missing On: .*jumpstarter_driver_not_installed"):
            run(Driver("missing", client=POWER), "Missing On")

    def test_no_session(self):
        with pytest.raises(Failure, match="Dut Power On: no Jumpstarter session 'default'"):
            run(Driver("dut.power", client=POWER), "Dut Power On")

    def test_lease_end_policy(self, served):
        class Ended:
            lease_ended, lease_transferred = True, False

        from robot.api import FatalError

        sessions.install(sessions.Session(name="default", kind="lease", client=served, lease=Ended(), lease_name="l"))
        library = Driver("dut.power", client=POWER)
        with pytest.raises(FatalError, match="Dut Power On: lease l has ended"):
            run(library, "Dut Power On")
        sessions.set_lease_end_policy("fail")
        with pytest.raises(Failure) as error:
            run(library, "Dut Power On")
        assert not isinstance(error.value, FatalError)

    def test_binding_follows_a_new_session(self, served):
        library = Driver("dut.power", client=POWER)
        with use_client(served):
            run(library, "Dut Power On")
        # a different tree: dut.power does not exist under dut2, so the old binding must not be reused
        with use_client(served.children["dut2"], exporter="other"), pytest.raises(Failure, match="exporter 'other'"):
            run(library, "Dut Power On")

    def test_named_session(self, served):
        library = Driver("dut.power", client=POWER, session="lab")
        with use_client(served, "lab"):
            assert run(library, "Dut Power Status") in ("on", "off")
        with pytest.raises(Failure, match="no Jumpstarter session 'lab'"):
            run(library, "Dut Power Status")


class TestShell:
    def test_configured_method(self, tree):
        library = Driver("shell", client=SHELL, methods="hello,fail")
        result = run(library, "Shell Hello", "world", GREETING="hi")
        assert result.rc == 0
        assert result.stdout.strip() == "hello world hi"

    def test_expected_rc(self, tree):
        library = Driver("shell", client=SHELL, methods="hello,fail")
        with pytest.raises(Failure, match="shell method 'fail' returned 3, expected 0"):
            run(library, "Shell Fail")
        result = run(library, "Shell Fail", expected_rc=3)
        assert result.rc == 3 and isinstance(result.rc, int)
        assert "oops" in result.stderr
        assert run(library, "Shell Run Method", "fail", expected_rc="any").rc == 3


class TestExtensions:
    def test_node_extension(self, tree):
        calls = []

        def describe_node(path, session, flavor: str = "plain", *, loud: bool = False):
            """Say which node this is."""
            calls.append((path, session, flavor, loud))
            return f"{path}:{flavor}"

        register_node_extension({"GadgetClient"}, "Describe Node", describe_node, tags=("extra",))
        library = Driver("gadget", client=GADGET)
        assert "Gadget Describe Node" in library.get_keyword_names()
        assert library.get_keyword_arguments("Gadget Describe Node") == [("flavor", "plain"), "*", ("loud", False)]
        assert "extension" in library.get_keyword_tags("Gadget Describe Node")
        assert run(library, "Gadget Describe Node", "spicy", loud=True) == "gadget:spicy"
        assert calls == [("gadget", None, "spicy", True)]
        assert "Dut Power Describe Node" not in Driver("dut.power", client=POWER).get_keyword_names()


class TestRoles:
    def test_bind_and_reset(self, tree):
        library = Driver(role="power", client=POWER)
        assert library.path == "power"
        assert "Power On" in library.get_keyword_names()
        assert library.bind("dut2.power") == "power"
        run(library, "Power On")
        assert tree.children["dut2"].children["power"].status() == "on"
        library.end_test(None, None)
        assert library.path == "power"
        with pytest.raises(Failure, match="driver 'power'"):  # the default path is not on this exporter
            run(library, "Power Off")
        assert tree.children["dut2"].children["power"].status() == "on"

    def test_fixed_libraries_keep_their_binding(self, tree):
        library = Driver("dut.power", client=POWER)
        library.bind("dut2.power")
        library.end_test(None, None)
        assert library.path == "dut2.power"

    def test_role_with_default_path(self, tree):
        library = Driver("dut.power", role="power", client=POWER, prefix="Power")
        assert library.default_path == "dut.power"
        library.bind("dut2.power")
        library.reset_binding()
        assert library.path == "dut.power"

    def test_use_device(self, tree):
        power = Driver(role="power", client=POWER)
        other = Driver(role="power", client=POWER, session="elsewhere")
        library = Jumpstarter()
        assert library.use_device("dut2") == ["dut2.power"]
        assert power.path == "dut2.power"
        assert other.path == "power"  # a role library of another session is left alone
        with pytest.raises(Exception, match="no role library matches"):
            library.use_device("shell")

    def test_use_device_checks_the_class(self, tree):
        shell_role = Driver(role="power", client=SHELL)
        with pytest.raises(Failure, match="regenerate the client resource"):
            Jumpstarter().use_device("dut")
        assert shell_role.path == "power"

    def test_instances_are_tracked(self):
        library = Driver(role="x", client=POWER)
        assert library in instances()


class TestLibdoc:
    def test_static_documentation_opens_no_session(self, tmp_path):
        opened = []

        def never(*args, **kwargs):
            opened.append(args)
            raise AssertionError("libdoc must not open a session")

        sessions.register_factory("default", never)
        with use_session_factory(lease=never, local=never, shell=never):
            doc = LibraryDocumentation(f"jumpstarter_robot.Driver::dut.power::client={POWER}::prefix=Power")
            jumpstarter_doc = LibraryDocumentation(
                f"jumpstarter_robot.Jumpstarter::exporter_config={tmp_path / 'missing.yaml'}"
            )
            unknown = LibraryDocumentation("jumpstarter_robot.Driver::dut.power")
        assert opened == []
        keywords = {kw.name: kw for kw in doc.keywords}
        assert {"Power On", "Power Cycle", "Power Read", "Power Should Be On"} <= set(keywords)
        cycle = keywords["Power Cycle"].args
        assert [str(arg) for arg in cycle] == ["wait: int = 2"]
        assert [str(arg) for arg in keywords["Power Read"].args] == ["*", "max_items=1000"]
        assert "assert" in keywords["Power Should Be On"].tags
        assert keywords["Power On"].doc.startswith("Power on the device.")
        assert keywords["Power On"].source.endswith("client.py")
        assert "Acquire Lease" in {kw.name for kw in jumpstarter_doc.keywords}
        assert list(unknown.keywords) == []
