import json
import sys
from pathlib import Path

import pytest

from jumpstarter_mcp import environments
from jumpstarter_mcp.environments import NOT_DELEGATED, Environment, EnvironmentError_, Router, ToolFailed


def make_env(root: Path, name: str = ".venv", script: str = "#!/bin/sh\nexit 0\n") -> Path:
    """A directory that looks like a Python environment with Jumpstarter: it has a `jmp` command."""
    path = root / name
    (path / "bin").mkdir(parents=True)
    jmp = path / "bin" / "jmp"
    jmp.write_text(script)
    jmp.chmod(0o755)
    return path


@pytest.fixture(autouse=True)
def no_file(monkeypatch, tmp_path):
    monkeypatch.setenv(environments.FILE_VARIABLE, str(tmp_path / "none.yaml"))
    for variable in (environments.ENV_VARIABLE, environments.WORKER_VARIABLE, "VIRTUAL_ENV"):
        monkeypatch.delenv(variable, raising=False)


def test_an_environment_is_named_by_its_directory_its_project_or_a_command_in_it(tmp_path):
    project = tmp_path / "phones"
    venv = make_env(project)
    for spec in (venv, project, venv / "bin" / "jmp"):
        found = environments.resolve(spec)
        assert (found.path, found.name, found.kind) == (venv, "phones", "venv")
        assert found.command == (str(venv / "bin" / "jmp"), "mcp", "serve")
    with pytest.raises(EnvironmentError_, match="does not exist"):
        environments.resolve(tmp_path / "nowhere")
    (tmp_path / "empty").mkdir()
    with pytest.raises(EnvironmentError_, match="no `jmp` command"):
        environments.resolve(tmp_path / "empty")


def test_a_container_is_an_environment_too(monkeypatch):
    monkeypatch.setattr(environments.shutil, "which", lambda name: "/usr/bin/podman" if name == "podman" else None)
    running = environments.resolve("container:rig-a")
    assert running.command == ("podman", "exec", "-i", "-e", "JMP_MCP_WORKER=1", "rig-a", "jmp", "mcp", "serve")
    assert (running.kind, running.where, running.is_own, running.path) == ("container", "container:rig-a", False, None)
    image = environments.resolve("image:quay.io/lab/rig:1")
    assert image.command[:4] == ("podman", "run", "--rm", "-i") and image.kind == "image"
    monkeypatch.setattr(environments.shutil, "which", lambda name: None)
    with pytest.raises(EnvironmentError_, match="podman nor docker"):
        environments.resolve("container:rig-a")


def test_the_environments_file_names_them(tmp_path, monkeypatch):
    venv = make_env(tmp_path / "phones")
    file = tmp_path / "mcp-environments.yaml"
    file.write_text(
        "environments:\n"
        f"  phones: {{path: {tmp_path / 'phones'}}}\n"
        "  lab: {command: [ssh, lab, jmp, mcp, serve]}\n"
        "  rig: {container: rig-a}\n"
        "  broken: {path: /nowhere/at/all}\n"
        "  empty: {}\n"
    )
    monkeypatch.setenv(environments.FILE_VARIABLE, str(file))
    monkeypatch.setattr(environments.shutil, "which", lambda name: f"/usr/bin/{name}")
    named = environments.named()
    assert sorted(named) == ["lab", "phones", "rig"]
    assert named["phones"].path == venv
    assert (named["lab"].kind, named["lab"].command) == ("command", ("ssh", "lab", "jmp", "mcp", "serve"))
    assert environments.resolve("lab") == named["lab"]
    assert named["rig"].name == "rig" and named["rig"].kind == "container"
    with pytest.raises(EnvironmentError_, match="The environments file names: phones, lab, rig"):
        environments.resolve("typo")


def test_the_ones_near_where_the_server_was_started_are_found_each_once(tmp_path, monkeypatch):
    outer = make_env(tmp_path / "work")
    inner = make_env(tmp_path / "work" / "phones")
    active = make_env(tmp_path / "elsewhere", "venv")
    monkeypatch.setenv("VIRTUAL_ENV", str(active))
    found = environments.discover(tmp_path / "work" / "phones" / "tests")
    assert [environment.path for environment in found] == [active, inner, outer]
    monkeypatch.setenv(environments.ENV_VARIABLE, str(inner))
    assert [e.path for e in environments.discover(tmp_path / "work" / "phones")] == [inner, active, outer]


def name_them(tmp_path, monkeypatch, **paths: Path) -> None:
    """Writes an environments file, as a person would, and has the server read it."""
    file = tmp_path / "mcp-environments.yaml"
    file.write_text("environments:\n" + "".join(f"  {name}: {{path: {path}}}\n" for name, path in paths.items()))
    file.chmod(0o600)
    monkeypatch.setenv(environments.FILE_VARIABLE, str(file))


def test_a_call_goes_to_the_environment_of_its_connection(tmp_path, monkeypatch):
    phones = environments.resolve(make_env(tmp_path / "phones"))
    boards = environments.resolve(make_env(tmp_path / "boards"))
    name_them(tmp_path, monkeypatch, boards=tmp_path / "boards")
    router = Router(selected=phones)
    assert router.target({}) == phones
    router.connections["c-boards"] = boards
    assert router.target({"connection_id": "c-boards"}) == boards
    assert router.target({"connection_id": "unknown"}) == phones
    named = router.target({"environment": "boards"})
    assert named is not None and named.path == boards.path
    # this server's own environment, and a server started by another, do the work themselves
    assert Router(selected=Environment("own", ("jmp",), path=Path(sys.prefix))).target({}) is None
    assert Router().target({"connection_id": "anything"}) is None
    assert Router(selected=phones, worker=True).target({}) is None


def test_an_agent_chooses_among_what_a_person_allowed_and_names_nothing_into_being(tmp_path, monkeypatch):
    started = environments.resolve(make_env(tmp_path / "started"))
    planted = make_env(tmp_path / "cloned-repo")  # a repository that carries a .venv of its own
    name_them(tmp_path, monkeypatch, phones=make_env(tmp_path / "phones"))
    monkeypatch.setattr(environments.shutil, "which", lambda name: f"/usr/bin/{name}")
    router = Router(selected=started, cwd=tmp_path / "cloned-repo")

    assert router.choose("phones").name == "phones"
    assert router.choose("started") == started
    assert router.choose(str(started.path)) == started
    # a path, a container and an image an agent gives are compared with the list, and are not on it
    for given in (str(planted), str(tmp_path), "container:anything", "image:evil.example/x:latest", "../phones"):
        with pytest.raises(EnvironmentError_, match="Allowed now: phones, started"):
            router.choose(given)
        with pytest.raises(EnvironmentError_):
            router.target({"environment": given})
        with pytest.raises(EnvironmentError_):
            router.use(given)
    assert router.selected == started  # a refusal changes nothing

    # what was only found says so, and how a person allows it
    with pytest.raises(EnvironmentError_, match="nobody said it may be used"):
        router.choose("cloned-repo")
    said = router.describe()
    assert [e["name"] for e in said["allowed"]] == ["started", "phones"]
    assert [e["name"] for e in said["found_not_allowed"]] == ["cloned-repo"]
    # ... unless the person who started the server said found ones may be used
    assert Router(allow_found=True, cwd=tmp_path / "cloned-repo").choose("cloned-repo").path == planted


def test_the_environments_file_is_read_only_when_it_is_its_owners_alone_to_write(tmp_path, monkeypatch):
    name_them(tmp_path, monkeypatch, phones=make_env(tmp_path / "phones"))
    file = tmp_path / "mcp-environments.yaml"
    assert sorted(environments.named()) == ["phones"]
    file.chmod(0o666)
    assert environments.named() == {}
    assert "others may write" in (environments.unsafe_file(file) or "")
    file.chmod(0o644)
    assert sorted(environments.named()) == ["phones"]


class FakeWorker:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    async def call(self, tool, arguments):
        self.calls.append((tool, arguments))
        answer = self.answers[tool]
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.mark.anyio
async def test_what_a_connection_answers_is_remembered_as_its_environments(tmp_path, monkeypatch):
    name_them(tmp_path, monkeypatch, phones=make_env(tmp_path / "phones"))
    router = Router()
    phones = router.choose("phones")
    worker = FakeWorker(
        {
            "jmp_connect": json.dumps({"connection_id": "abc", "exporter_name": "phone-1"}),
            "jmp_read": json.dumps({"result": "on"}),
            "jmp_disconnect": json.dumps({"connection_id": "abc", "status": "disconnected"}),
            "jmp_list_connections": "[]",
            "jmp_run": ToolFailed("exit code 2"),
        }
    )

    async def the_worker(environment):
        return worker

    monkeypatch.setattr(router, "_worker", the_worker)
    assert await router.forward("jmp_read", {"connection_id": "abc"}) is NOT_DELEGATED

    connected = json.loads(await router.forward("jmp_connect", {"lease_id": "l1", "environment": "phones"}))
    assert connected == {"connection_id": "abc", "exporter_name": "phone-1", "environment": "phones"}
    assert worker.calls[-1] == ("jmp_connect", {"lease_id": "l1"})  # the environment is this server's to know
    assert router.connections == {"abc": phones}

    read = json.loads(await router.forward("jmp_read", {"connection_id": "abc", "tool": "power_status"}))
    assert read == {"result": "on", "environment": "phones"}
    router.selected = phones
    assert await router.forward("jmp_list_connections", {}) == "[]"
    # a tool that failed where it ran failed: it is not an answer
    with pytest.raises(ToolFailed, match="exit code 2"):
        await router.forward("jmp_run", {"connection_id": "abc"})

    await router.forward("jmp_disconnect", {"connection_id": "abc"})
    assert router.connections == {}
    router.selected = None
    with pytest.raises(EnvironmentError_, match="not an environment this server may use"):
        await router.forward("jmp_connect", {"environment": "/nowhere"})
    assert worker.calls[-1][0] == "jmp_disconnect"  # nothing was started or asked for the refused one


@pytest.mark.anyio
async def test_the_server_in_another_environment_is_started_and_answers(tmp_path):
    # an environment whose `jmp` is this one's: another place on disk, the same Jumpstarter
    real = Path(sys.executable).parent / "jmp"
    other = environments.resolve(make_env(tmp_path / "other", script=f'#!/bin/sh\nexec "{real}" "$@"\n'))
    router = Router(selected=other)
    async with router.running():
        answered = json.loads(await router.forward("jmp_environments", {}))
        # the other server does its own work and names no environment of its own to pass on to
        assert answered["selected"] is None and answered["allowed"] == []
        assert answered["environment"] == "other"
        assert await router.forward("jmp_list_connections", {}) == "[]"
        with pytest.raises(EnvironmentError_, match="has no jmp_not_a_tool tool"):
            await router.forward("jmp_not_a_tool", {})
        # a tool that fails there fails here, with what it said
        with pytest.raises(ToolFailed, match="uses its own environment"):
            await router.forward("jmp_use_environment", {})


@pytest.mark.anyio
async def test_an_environment_whose_server_cannot_start_is_said_not_hung_on(tmp_path):
    broken = environments.resolve(make_env(tmp_path / "broken", script="#!/bin/sh\nexit 3\n"))
    router = Router(selected=broken)
    async with router.running():
        with pytest.raises(EnvironmentError_, match="could not be started"):
            await router.forward("jmp_list_leases", {})


@pytest.mark.anyio
async def test_no_more_servers_are_started_than_a_few(tmp_path, monkeypatch):
    monkeypatch.setattr(environments, "MAX_WORKERS", 1)
    first = environments.resolve(make_env(tmp_path / "one", script="#!/bin/sh\nsleep 30\n"))
    second = environments.resolve(make_env(tmp_path / "two", script="#!/bin/sh\nsleep 30\n"))
    router = Router()
    async with router.running():
        await router._worker(first)
        with pytest.raises(EnvironmentError_, match="in use already"):
            await router._worker(second)


@pytest.mark.anyio
async def test_the_server_says_a_failure_the_protocols_way_and_what_each_tool_does(tmp_path):
    from mcp.server.mcpserver.exceptions import ToolError

    from jumpstarter_mcp.server import create_server

    mcp, _ = create_server(Router(cwd=tmp_path))
    with pytest.raises(ToolError, match="not an environment this server may use"):
        await mcp.call_tool("jmp_use_environment", {"environment": "image:evil.example/x"})
    with pytest.raises(ToolError, match="not an environment this server may use"):
        await mcp.call_tool("jmp_connect", {"lease_id": "l1", "environment": str(tmp_path)})
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    # every tool says whether it only reads: what a host shows a person, and decides with
    reads = {name: tool.annotations.read_only_hint for name, tool in tools.items() if tool.annotations is not None}
    assert set(reads) == set(tools) and None not in reads.values()
    assert (reads["jmp_read"], reads["jmp_call"], reads["jmp_use_environment"]) == (True, False, False)
    call = tools["jmp_call"].annotations
    assert call is not None and call.destructive_hint is True
    # the tool keeps its arguments when it is registered through the router
    assert "environment" in tools["jmp_connect"].input_schema["properties"]


def test_the_environments_are_described_for_an_agent(tmp_path):
    phones = environments.resolve(make_env(tmp_path / "phones"))
    router = Router(selected=phones, cwd=tmp_path)
    router.connections["abc"] = phones
    said = router.describe()
    assert said["selected"] == {"name": "phones", "kind": "venv", "where": str(phones.path), "own": False}
    assert said["allowed"][0]["name"] == "phones" and said["connections"] == {"abc": "phones"}
    assert said["own"]["path"] == sys.prefix and "found_not_allowed" not in said
