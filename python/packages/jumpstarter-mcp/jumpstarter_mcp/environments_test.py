import json
import sys
from pathlib import Path

import pytest

from jumpstarter_mcp import environments
from jumpstarter_mcp.environments import NOT_DELEGATED, Environment, EnvironmentError_, Router


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


def test_a_call_goes_to_the_environment_of_its_connection(tmp_path):
    phones = environments.resolve(make_env(tmp_path / "phones"))
    boards = environments.resolve(make_env(tmp_path / "boards"))
    router = Router(selected=phones)
    assert router.target({}) == phones
    router.connections["c-boards"] = boards
    assert router.target({"connection_id": "c-boards"}) == boards
    assert router.target({"connection_id": "unknown"}) == phones
    assert router.target({"environment": str(tmp_path / "boards")}) == boards
    # this server's own environment, and a server started by another, do the work themselves
    assert Router(selected=Environment("own", ("jmp",), path=Path(sys.prefix))).target({}) is None
    assert Router().target({"connection_id": "anything"}) is None
    assert Router(selected=phones, worker=True).target({}) is None


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
    phones = environments.resolve(make_env(tmp_path / "phones"))
    router = Router()
    worker = FakeWorker(
        {
            "jmp_connect": json.dumps({"connection_id": "abc", "exporter_name": "phone-1"}),
            "jmp_read": json.dumps({"result": "on"}),
            "jmp_disconnect": json.dumps({"connection_id": "abc", "status": "disconnected"}),
            "jmp_list_connections": "[]",
            "jmp_run": EnvironmentError_("it broke"),
        }
    )

    async def the_worker(environment):
        return worker

    monkeypatch.setattr(router, "_worker", the_worker)
    assert await router.forward("jmp_read", {"connection_id": "abc"}) is NOT_DELEGATED

    connected = json.loads(await router.forward("jmp_connect", {"lease_id": "l1", "environment": str(phones.path)}))
    assert connected == {"connection_id": "abc", "exporter_name": "phone-1", "environment": str(phones.path)}
    assert worker.calls[-1] == ("jmp_connect", {"lease_id": "l1"})  # the environment is this server's to know
    assert router.connections == {"abc": phones}

    read = json.loads(await router.forward("jmp_read", {"connection_id": "abc", "tool": "power_status"}))
    assert read == {"result": "on", "environment": str(phones.path)}
    router.selected = phones
    assert await router.forward("jmp_list_connections", {}) == "[]"
    assert json.loads(await router.forward("jmp_run", {"connection_id": "abc"})) == {"error": "it broke"}

    await router.forward("jmp_disconnect", {"connection_id": "abc"})
    assert router.connections == {}
    router.selected = None
    assert json.loads(await router.forward("jmp_connect", {"environment": "/nowhere"}))["error"].startswith("/nowhere")


@pytest.mark.anyio
async def test_the_server_in_another_environment_is_started_and_answers(tmp_path):
    # an environment whose `jmp` is this one's: another place on disk, the same Jumpstarter
    real = Path(sys.executable).parent / "jmp"
    other = environments.resolve(make_env(tmp_path / "other", script=f'#!/bin/sh\nexec "{real}" "$@"\n'))
    router = Router(selected=other)
    async with router.running():
        answered = json.loads(await router.forward("jmp_environments", {}))
        # the other server does its own work and names no environment of its own to pass on to
        assert answered["selected"] is None and answered["found"] == []
        assert answered["environment"] == str(other.path)
        assert await router.forward("jmp_list_connections", {}) == "[]"
        missing = json.loads(await router.forward("jmp_not_a_tool", {}))
        assert "has no jmp_not_a_tool tool" in missing["error"]


@pytest.mark.anyio
async def test_an_environment_whose_server_cannot_start_is_an_answer_not_a_failure(tmp_path):
    broken = environments.resolve(make_env(tmp_path / "broken", script="#!/bin/sh\nexit 3\n"))
    router = Router(selected=broken)
    async with router.running():
        answered = json.loads(await router.forward("jmp_list_leases", {}))
    assert "could not be started" in answered["error"]


def test_the_environments_are_described_for_an_agent(tmp_path):
    phones = environments.resolve(make_env(tmp_path / "phones"))
    router = Router(selected=phones)
    router.connections["abc"] = phones
    said = router.describe(tmp_path)
    assert said["selected"] == {"name": "phones", "kind": "venv", "where": str(phones.path), "own": False}
    assert said["found"][0]["name"] == "phones" and said["connections"] == {"abc": str(phones.path)}
    assert said["own"]["path"] == sys.prefix
