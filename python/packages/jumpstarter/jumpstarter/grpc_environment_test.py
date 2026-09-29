import json
import os
import signal
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace

import pytest

import jumpstarter

# A fresh interpreter is necessary: changing the environment after gRPC has
# initialized does not reconfigure its native fork handlers.
_RPC_SCRIPT = """
import asyncio
import json
import os
import sys
import traceback

if sys.argv[1] == "grpc-first":
    import grpc
import jumpstarter
import grpc


async def echo_once(payload):
    async def echo(request, context):
        return request

    server = grpc.aio.server()
    server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler(
        "test.Echo", {"Call": grpc.unary_unary_rpc_method_handler(echo)}
    ),))
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            response = await channel.unary_unary("/test.Echo/Call")(payload, timeout=5)
            assert response == payload
    finally:
        await server.stop(grace=None)


if sys.argv[2] == "fork":
    # Match jmp run: the supervisor imports/configures before forking, while
    # each child creates its own gRPC servers and channels after the fork.
    for restart in range(2):
        pid = os.fork()
        if pid == 0:
            try:
                asyncio.run(echo_once(f"child-{restart}".encode()))
            except BaseException:
                traceback.print_exc()
                os._exit(1)
            os._exit(0)
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0, status
else:
    asyncio.run(echo_once(b"configured grpc"))

print(json.dumps({"fork_support": os.environ.get("GRPC_ENABLE_FORK_SUPPORT"), "rpc": "passed"}))
"""


@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
@pytest.mark.parametrize("override", [None, "0", "1"])
def test_grpc_fork_default_is_platform_scoped_and_preserves_overrides(monkeypatch, platform, override):
    # Exercise the macOS policy on Linux CI without changing the interpreter's
    # global platform or relying on subprocess coverage collection.
    monkeypatch.setattr(jumpstarter, "sys", SimpleNamespace(platform=platform))
    # Register an undo even if the variable was initially absent, so a default
    # added by configure_grpc_env cannot leak into subsequent Linux tests.
    monkeypatch.setenv("GRPC_ENABLE_FORK_SUPPORT", override if override is not None else "")
    if override is None:
        monkeypatch.delenv("GRPC_ENABLE_FORK_SUPPORT")
    expected = "0" if platform == "darwin" and override is None else override

    for _ in range(2):
        jumpstarter.configure_grpc_env()
        assert os.environ.get("GRPC_ENABLE_FORK_SUPPORT") == expected


def _fresh_rpc(*, fork_support=None, import_order="jumpstarter-first", fork=False):
    env = os.environ.copy()
    env.pop("GRPC_ENABLE_FORK_SUPPORT", None)
    if fork_support is not None:
        env["GRPC_ENABLE_FORK_SUPPORT"] = fork_support
    package_root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [package_root, env.get("PYTHONPATH")]))
    process = subprocess.Popen(
        [sys.executable, "-c", _RPC_SCRIPT, import_order, "fork" if fork else "direct"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=os.name == "posix",
    )
    try:
        stdout, stderr = process.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        # Reap the complete test process group, including either forked child.
        with suppress(ProcessLookupError):
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        stdout, stderr = process.communicate(timeout=5)
        pytest.fail(f"Fresh gRPC process timed out:\n{stdout}\n{stderr}")
    assert process.returncode == 0, f"{stdout}\n{stderr}"
    return json.loads(stdout)


@pytest.mark.parametrize(
    "override, expected", [(None, "0" if sys.platform == "darwin" else None), ("0", "0"), ("1", "1")]
)
def test_grpc_uses_configured_default_or_explicit_override(override, expected):
    assert _fresh_rpc(fork_support=override) == {"fork_support": expected, "rpc": "passed"}


@pytest.mark.skipif(not hasattr(os, "fork"), reason="Requires POSIX fork")
@pytest.mark.parametrize("import_order", ["jumpstarter-first", "grpc-first"])
def test_grpc_works_in_fresh_exporter_children_and_after_restart(import_order):
    expected = "0" if sys.platform == "darwin" else None
    assert _fresh_rpc(import_order=import_order, fork=True) == {"fork_support": expected, "rpc": "passed"}
