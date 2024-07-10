from jumpstarter.exporter import Exporter, Session
from jumpstarter.client import Client
from concurrent import futures
import pytest
import grpc


@pytest.fixture(scope="module")
def setup_exporter(request):
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))

    try:
        devices = request.param()
    except FileNotFoundError:
        pytest.skip("fail to find required devices")

    s = Session(devices=devices)
    e = Exporter(labels={"jumpstarter.dev/name": "exporter"}, session=s)
    e.add_to_server(server)

    server.add_insecure_port("localhost:50051")
    server.start()

    with grpc.insecure_channel("localhost:50051") as channel:
        yield Client(channel)

    server.stop(grace=None)
    server.wait_for_termination()
