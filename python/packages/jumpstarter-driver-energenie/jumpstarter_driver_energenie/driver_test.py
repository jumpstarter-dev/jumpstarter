from pytest_httpserver import HTTPServer

from .driver import EnerGenie
from jumpstarter.common.utils import serve


def test_drivers_energenie(httpserver: HTTPServer):
    # Configure mock responses
    # 1. Login response - Match raw data string
    httpserver.expect_request(
        "/login.html",
        method="POST",
        data="pw=1"
    ).respond_with_data("Login successful") # Defaults to status 200

    # 2. Response for turning ON switch 1 - Match raw data string
    httpserver.expect_request(
        "/",
        method="POST",
        data="cte1=1"
    ).respond_with_data("Switch turned ON") # Defaults to status 200

    # 3. Response for turning OFF switch 1 - Match raw data string
    httpserver.expect_request(
        "/",
        method="POST",
        data="cte1=0"
    ).respond_with_data("Switch turned OFF") # Defaults to status 200

    # Get the mock server's host and port
    host = f"{httpserver.host}:{httpserver.port}"

    # Create EnerGenie instance with the mock server's URL
    instance = EnerGenie(host=host)

    with serve(instance) as client:
        client.on()
        client.off()

    # check_assertions will verify that all expected requests were received
    # in the correct order and that no unexpected requests arrived.
    try:
        httpserver.check_assertions()
    except AssertionError as e:
        print(f"httpserver assertions FAILED: {e}")
        raise # Re-raise the assertion error to fail the test
