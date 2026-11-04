"""The Robot Framework examples run green, and their generated resources are current."""

from __future__ import annotations

from pathlib import Path

import pytest
import robot
from jumpstarter_robot.__main__ import static_tree
from jumpstarter_robot.project import Project
from jumpstarter_robot.render import write_resource
from robot.api import ExecutionResult

EXAMPLES = Path(__file__).resolve().parent.parent
BASIC = EXAMPLES / "basic"
# One folder per example; each has its own robot.toml.
FOLDERS = ["basic", "multi-dut", "lab", "zero-config", "remote-library", "linux-ssh", "mobile"]
PROJECTS = [EXAMPLES / folder / "robot.toml" for folder in FOLDERS]


def _all_pass(output: Path, rc: int) -> None:
    result = ExecutionResult(str(output))
    failed = [f"{test.longname}: {test.message}" for test in result.suite.all_tests if test.status != "PASS"]
    assert not failed, "\n".join(failed)
    assert rc == 0
    assert result.statistics.total.passed > 0


def _run(tmp_path: Path, *sources: Path, **options) -> None:
    output = tmp_path / "output.xml"
    rc = robot.run(
        *map(str, sources),
        outputdir=str(tmp_path),
        output=str(output),
        report="NONE",
        log="NONE",
        console="none",
        **options,
    )
    _all_pass(output, rc)


@pytest.mark.parametrize("folder", ["basic", "multi-dut", "zero-config"])
def test_local_suites_pass(tmp_path, folder):
    """The basic, multi-DUT and zero-config suites pass against mock boards served in-process."""
    _run(tmp_path, EXAMPLES / folder / "tests")


@pytest.mark.parametrize("testbed", [None, "mock.yaml"])
def test_lab_suites_pass_on_the_mock_testbed(tmp_path, testbed):
    """The lab suites pass on the mock board, with and without its testbed."""
    variablefile = [str(EXAMPLES / "lab" / "testbeds" / testbed)] if testbed else []
    _run(tmp_path, EXAMPLES / "lab" / "tests", variablefile=variablefile)


@pytest.mark.parametrize("testbed", ["lab.yaml", "lab-pool.yaml"])
def test_lab_suites_acquire_their_lease(tmp_path, testbed, monkeypatch):
    """On a lab testbed, the suite setup's Acquire Lease is what leases the board; nothing attaches first."""
    from jumpstarter_robot import sessions, testing

    monkeypatch.delenv("JUMPSTARTER_HOST", raising=False)
    monkeypatch.delenv("JMP_LEASE", raising=False)
    calls = []

    def controller(name="default", **kwargs):
        calls.append((name, kwargs.get("selector"), kwargs.get("acquire", True)))
        if kwargs.get("acquire", True) is False:
            raise sessions.NoSession(f"session '{name}': no active lease matches")
        return sessions.Session.from_exporter_config(name, config=str(BASIC / "exporter.yaml"))

    testing.reset()
    with testing.use_session_factory(lease=controller):
        _run(tmp_path, EXAMPLES / "lab" / "tests", variablefile=[str(EXAMPLES / "lab" / "testbeds" / testbed)])
    testing.reset()
    selector = "board=mockdut,lab=eu" if testbed == "lab.yaml" else "board=mockdut,pool=ci"
    assert calls == [("board", selector, True)]


def test_remote_library_over_a_port_forward(tmp_path):
    """remote-library imports a remote library through Open Port Forward."""
    _run(tmp_path, EXAMPLES / "remote-library" / "tests")


def test_suites_pass_in_parallel_with_pabot(tmp_path):
    """pabot runs the suites in parallel processes, each serving its own mock board."""
    import os
    import subprocess
    import sys

    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "pabot.pabot",
            "--processes",
            "3",
            "--outputdir",
            str(tmp_path),
            "-V",
            str(EXAMPLES / "lab" / "testbeds" / "mock.yaml"),
            str(EXAMPLES / "lab" / "tests"),
        ],
        cwd=EXAMPLES / "lab",
        # pabot starts `robot` from PATH; use the one installed next to this interpreter
        env={**os.environ, "PATH": os.pathsep.join([str(Path(sys.executable).parent), os.environ.get("PATH", "")])},
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    _all_pass(tmp_path / "output.xml", process.returncode)


def _clients():
    for project in PROJECTS:
        for settings in Project.load(project).clients.values():
            yield pytest.param(settings, id=f"{project.parent.name}:{settings.name}")


@pytest.mark.parametrize("settings", _clients())
def test_generated_resources_are_current(settings, tmp_path):
    """The committed client resources match what `jmp robot generate` writes now."""
    committed = settings.resource_path
    fresh = tmp_path / committed.name
    write_resource(
        fresh,
        static_tree(settings),
        resource_dir=committed.parent,
        session=settings.session,
        source_args=settings.source_args(),
        aliases=settings.aliases,
        roles=settings.roles,
        include=settings.include,
        exclude=settings.exclude,
    )
    hint = f"stale; run `jmp robot generate` in {committed.parent}"
    assert fresh.read_text() == committed.read_text(), f"{committed.name} is {hint}"


@pytest.mark.parametrize("platform", ["android", "ios"])
@pytest.mark.parametrize(
    ("variables", "remote"),
    [
        ([], False),
        (["JMP_{SESSION}_SELECTOR:board=phone"], True),
        (["JMP_{SESSION}_LEASE_NAME:my-lease"], True),
        (["JMP_SELECTOR:board=phone"], True),
    ],
)
def test_mobile_device_is_remote(platform, variables, remote, tmp_path, monkeypatch):
    """The mobile suites lease their device when the session's own variables ask for it."""
    monkeypatch.delenv("JUMPSTARTER_HOST", raising=False)
    resource = EXAMPLES / "mobile" / "resources" / f"{platform}.resource"
    suite = tmp_path / "remote.robot"
    suite.write_text(
        "*** Settings ***\n"
        f"Resource    {resource}\n\n"
        "*** Test Cases ***\n"
        "Remote\n"
        "    ${remote}=    Device Is Remote\n"
        f"    Should Be Equal    ${{remote}}    ${{{remote}}}\n"
    )
    output = tmp_path / "output.xml"
    rc = robot.run(
        str(suite),
        outputdir=str(tmp_path),
        output=str(output),
        report="NONE",
        log="NONE",
        console="none",
        variable=[v.format(SESSION=platform.upper()) for v in variables],
    )
    result = ExecutionResult(str(output))
    assert rc == 0, [test.message for test in result.suite.all_tests]


def test_robotcode_repl_drives_the_board():
    """RobotCode's REPL runs the same keywords interactively, one at a time."""
    import subprocess
    import sys

    commands = [
        "Import Resource    ${CURDIR}/client.resource",
        "Dut Power On",
        "${status}=    Dut Power Status",
        "Log To Console    STATUS=${status}",
        "${exporter}=    Get Exporter Name",
        "Log To Console    EXPORTER=${exporter}",
        "Dut Power Off",
    ]
    process = subprocess.run(
        [sys.executable, "-m", "robotcode.cli", "repl", "--plain", "--no-history"],
        input="".join(f"{command}\n" for command in commands),
        cwd=BASIC,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    assert "STATUS=on" in process.stdout
    assert "EXPORTER=mock-bench" in process.stdout
    assert "FAIL" not in process.stdout


LEASING_REPL = """
import os
from jumpstarter_robot import sessions

def lease(name="default", **kwargs):
    # stands in for the controller: the "leased" exporter is the example's, served here
    opened = sessions.Session.from_exporter_config(name, config=os.environ["LEASED_EXPORTER"])
    opened.lease_name = f"{name}-lease"
    print(f"LEASED {name} {kwargs['selector']}")
    return opened

sessions.open_lease = lease
from robotcode.cli import robotcode
robotcode()
"""


def test_robotcode_repl_leases_and_discovers_keywords(tmp_path):
    """In the REPL, Acquire Lease imports the board's keywords, which List Driver Keywords names."""
    import os
    import subprocess
    import sys

    harness = tmp_path / "repl.py"
    harness.write_text(LEASING_REPL)
    commands = [
        "Import Library    jumpstarter_robot.Jumpstarter",
        "${lease}=    Acquire Lease    selector=board=mock",
        "Log To Console    LEASE=${lease}",
        "${keywords}=    List Driver Keywords    dut.power",
        "Log To Console    KEYWORDS=${keywords}",
        "Dut Power On",
        "${status}=    Dut Power Status",
        "Log To Console    STATUS=${status}",
        "Release Lease",
        "Run Keyword And Expect Error    *no Jumpstarter session*    Dut Power Off",
        "Log To Console    RELEASED",
    ]
    process = subprocess.run(
        [sys.executable, str(harness), "repl", "--plain", "--no-history"],
        input="".join(f"{command}\n" for command in commands),
        cwd=BASIC,
        env={**os.environ, "LEASED_EXPORTER": str(BASIC / "exporter.yaml")},
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    output = process.stdout + process.stderr
    assert process.returncode == 0, output
    assert "LEASED default board=mock" in output
    assert "LEASE=default-lease" in output
    assert "'Dut Power On'" in output and "'Dut Power Should Be On'" in output
    assert "STATUS=on" in output
    assert "RELEASED" in output
    # the REPL echoes the failure Run Keyword And Expect Error expected; nothing else may fail
    failures = [line for line in output.splitlines() if "[ FAIL ]" in line]
    assert len(failures) == 1 and "no Jumpstarter session" in failures[0], output


@pytest.mark.parametrize("folder", FOLDERS)
def test_robotcode_resolves_every_keyword(folder):
    """RobotCode's analysis, which its language server shares, finds every keyword without a lease."""
    import subprocess
    import sys

    process = subprocess.run(
        [sys.executable, "-m", "robotcode.cli", "analyze", "code", "tests"],
        cwd=EXAMPLES / folder,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert "Errors: 0" in process.stdout, process.stdout + process.stderr
