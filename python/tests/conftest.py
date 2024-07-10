from jumpstarter.exporter import Exporter
from concurrent import futures
import pytest
import grpc


@pytest.fixture(scope="module")
def setup_exporter(request):
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))

    e = Exporter(devices=request.param)
    e.add_to_server(server)

    server.add_insecure_port("localhost:50051")
    server.start()

    yield None

    server.stop(grace=None)
    server.wait_for_termination()
