import sys
from dataclasses import field
from typing import ClassVar

import pytest
from jumpstarter_driver_composite.driver import Composite
from jumpstarter_driver_power.driver import MockPower
from pydantic.dataclasses import dataclass

from .base import Driver
from .decorators import export
from .guards import guards_of
from .scripts import DriverScript, ScriptError, ScriptRunnerMixin, script_caller, tree_path
from jumpstarter.common.utils import serve


@dataclass(kw_only=True)
class Procedure(ScriptRunnerMixin, Driver):
    """A toy driver that runs a script from its config."""

    script_callable_methods: ClassVar[frozenset[str]] = frozenset({"status"})

    procedure: DriverScript
    extra_env: dict[str, str] = field(default_factory=dict)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter.client.DriverClient"

    @export
    async def run(self) -> str:
        try:
            return (await self.run_script(self.procedure, env=self.extra_env)).output
        except ScriptError as e:
            return f"failed: {e}"

    @export
    def ping(self) -> str:
        return "pong"

    @export
    def status(self) -> str:
        return "idle"


class Relay(MockPower):
    @export
    async def on(self):
        self.events = [*getattr(self, "events", []), "on"]


def bench(procedure: dict, **kwargs) -> tuple[Composite, Relay]:
    relay = Relay()
    dut = Procedure(procedure=DriverScript.model_validate(procedure), **kwargs)
    return Composite(children={"bench": Composite(children={"dut1": dut}), "relay": relay}), relay


PY_CLIENT = (
    "import os\n"
    "from functools import reduce\n"
    "from jumpstarter.utils.env import env\n"
    "with env() as client:\n"
)


def test_bash_script_sees_its_path_and_env():
    tree, _ = bench(
        {"script": 'echo "path=$JMP_DRIVER_PATH extra=$EXTRA cfg=$FROM_CFG"', "env": {"FROM_CFG": "1"}},
        extra_env={"EXTRA": "2"},
    )
    with serve(tree) as client:
        assert client.bench.dut1.call("run") == "path=bench dut1 extra=2 cfg=1"


def test_python_script_reaches_other_drivers():
    script = PY_CLIENT + "    client.relay.on()\n    print('relay on via', os.environ['JMP_DRIVER_PATH'])\n"
    tree, relay = bench({"script": script, "exec": sys.executable})
    with serve(tree) as client:
        output = client.bench.dut1.call("run")
    assert output.endswith("relay on via bench dut1"), output
    assert relay.events == ["on"]


def test_python_file_uses_the_exporters_python(tmp_path):
    path = tmp_path / "procedure.py"
    path.write_text("import sys\nprint(sys.executable)\n")
    tree, _ = bench({"script": str(path)})
    with serve(tree) as client:
        assert client.bench.dut1.call("run") == sys.executable


def test_block_self_refuses_calls_to_the_driver_while_its_script_runs():
    script = PY_CLIENT + (
        "    dut = reduce(getattr, os.environ['JMP_DRIVER_PATH'].split(), client)\n"
        "    print('status:', dut.call('status'))\n"
        "    try:\n"
        "        dut.call('ping')\n"
        "        print('allowed')\n"
        "    except Exception as e:\n"
        "        print('refused:', e)\n"
    )
    tree, _ = bench({"script": script, "exec": sys.executable})
    with serve(tree) as client:
        output = client.bench.dut1.call("run")
        assert "status: idle" in output  # script_callable_methods stay callable
        assert "refused" in output and "running its script" in output
        assert client.bench.dut1.call("ping") == "pong"  # allowed again afterwards
    assert len(guards_of(tree.children["bench"].children["dut1"])) == 1

    tree, _ = bench({"script": script, "exec": sys.executable, "block_self": False})
    with serve(tree) as client:
        assert client.bench.dut1.call("run").endswith("allowed")
    assert guards_of(tree.children["bench"].children["dut1"]) == []  # never guarded


def test_self_path_override():
    tree, _ = bench({"script": 'echo "$JMP_DRIVER_PATH"', "self_path": "lab.rack3.dut1"})
    with serve(tree) as client:
        assert client.bench.dut1.call("run") == "lab rack3 dut1"


@pytest.mark.anyio
async def test_failure_timeout_and_missing_interpreter():
    dut = Procedure(procedure=DriverScript(script="echo boom; exit 4"))
    assert dut.exporter_root is None  # not enumerated into an exporter tree yet
    with pytest.raises(ScriptError, match=r"(?s)exit code 4.*boom"):
        await dut.run_script(dut.procedure)

    with pytest.raises(ScriptError, match="exit code 127.*jumpstarter-cli"):
        await dut.run_script(DriverScript(script="no-such-command-xyz"))

    with pytest.raises(ScriptError, match="timed out after 1 seconds"):
        await dut.run_script(DriverScript(script="sleep 30", timeout=1))

    with pytest.raises(ScriptError, match="Error executing script"):
        await dut.run_script(DriverScript(script="x", exec_="/nonexistent/sh"))

    assert dut.exporter_root is dut  # a standalone driver serves its scripts itself
    assert not dut.script_running()


def test_config_model():
    cfg = DriverScript.model_validate({"script": "echo", "exec": "/bin/bash"})
    assert cfg.exec_ == "/bin/bash" and cfg.block_self is True and cfg.timeout == 120
    with pytest.raises(ValueError):
        DriverScript.model_validate({"script": "echo", "unknown": 1})
    with pytest.raises(ValueError):
        DriverScript.model_validate({"script": "echo", "timeout": 0})


def test_tree_path():
    leaf = Procedure(procedure=DriverScript(script="true"))
    root = Composite(children={"a": Composite(children={"b": leaf})})
    assert tree_path(root, leaf) == ["a", "b"]
    assert tree_path(root, root) == []
    assert tree_path(root, Procedure(procedure=DriverScript(script="true"))) is None


def test_guards_can_tell_a_drivers_own_scripts_from_other_callers():
    from .guards import add_guard

    tree, relay = bench({"script": PY_CLIENT + "    client.relay.on()\n", "exec": sys.executable})
    dut = tree.children["bench"].children["dut1"]
    seen = []

    def guard(method):
        seen.append(script_caller())
        return None if script_caller() is dut else "only dut1's own scripts may switch the relay"

    add_guard(relay, guard)
    with serve(tree) as client:
        assert not client.bench.dut1.call("run").startswith("failed"), "the driver's own script is allowed"
        with pytest.raises(Exception, match="only dut1's own scripts"):
            client.relay.on()  # the lease holder isn't
    assert seen == [dut, None] and relay.events == ["on"]
