from pathlib import Path

import grpc
import pytest
from anyio import Event, fail_after, run

CONFTEST = Path(__file__).parents[1] / "conftest.py"


@pytest.mark.anyio
async def test_a_call_counts_as_unfinished_until_it_completes(unfinished_grpc_operations):
    handling = Event()
    release = Event()

    async def echo_when_released(request, context):
        handling.set()
        await release.wait()
        return request

    handler = grpc.unary_unary_rpc_method_handler(echo_when_released)
    server = grpc.aio.server()
    server.add_generic_rpc_handlers((grpc.method_handlers_generic_handler("test.Echo", {"Call": handler}),))
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
        call = channel.unary_unary("/test.Echo/Call")(b"ping")
        with fail_after(5):
            await handling.wait()
            during = unfinished_grpc_operations()
            release.set()
            assert await call == b"ping"
    await server.stop(grace=1)

    assert during
    assert not unfinished_grpc_operations()


def test_a_server_that_never_started_leaves_nothing_unfinished(unfinished_grpc_operations):
    async def create_a_server():
        grpc.aio.server()

    run(create_a_server)

    assert not unfinished_grpc_operations()


def test_only_the_test_that_leaks_is_blamed(pytester):
    pytester.makeconftest(CONFTEST.read_text())
    pytester.makepyfile(
        """
        import grpc
        import pytest

        servers = []


        @pytest.mark.anyio
        async def test_leaves_a_server_running():
            server = grpc.aio.server()
            server.add_insecure_port("127.0.0.1:0")
            await server.start()
            servers.append(server)


        def test_runs_after_the_leak():
            pass
        """
    )

    result = pytester.runpytest_subprocess(timeout=60)

    result.assert_outcomes(passed=2, errors=1)
    result.stdout.fnmatch_lines(["*ERROR at teardown of test_leaves_a_server_running*"])


def test_a_leak_names_the_batch_whose_waiter_was_cancelled(pytester):
    pytester.makeconftest(CONFTEST.read_text())
    pytester.makepyfile(
        """
        import asyncio

        from grpc._cython import cygrpc


        def test_cancels_the_waiter_of_a_batch():
            loop = asyncio.new_event_loop()
            waiter = loop.create_future()
            waiter.cancel()
            batch = (cygrpc.ReceiveCloseOnServerOperation(0),)
            failure = cygrpc.CallbackFailureHandler("execute_batch", batch, cygrpc.ExecuteBatchError)
            cygrpc.CallbackWrapper(waiter, loop, failure)
        """
    )

    result = pytester.runpytest_subprocess(timeout=60)

    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*- batch of ReceiveCloseOnServerOperation, waiter cancelled*"])


def test_a_leak_names_the_task_still_waiting_on_it(pytester):
    pytester.makeconftest(CONFTEST.read_text())
    pytester.makepyfile(
        """
        import asyncio
        import threading

        import grpc

        servers = []


        async def start_a_server():
            server = grpc.aio.server()
            server.add_insecure_port("127.0.0.1:0")
            await server.start()
            servers.append(server)


        def test_leaves_a_server_running_on_a_loop_that_outlives_it():
            loop = asyncio.new_event_loop()
            threading.Thread(target=loop.run_forever, daemon=True).start()
            asyncio.run_coroutine_threadsafe(start_a_server(), loop).result(timeout=10)
        """
    )

    result = pytester.runpytest_subprocess(timeout=60)

    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(
        ["*- operation failing with _RequestCallError, waiter pending in AioServer._server_main_loop*"]
    )
