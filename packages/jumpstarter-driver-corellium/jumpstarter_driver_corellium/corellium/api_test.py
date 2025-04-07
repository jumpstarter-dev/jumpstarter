import os

import pytest

from .api import ApiClient
from .exceptions import CorelliumApiException
from .types import Device, Instance, Project, Session

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def fixture(path):
    """
    Load file contents from fixtures/$path.
    """
    cwd = os.path.dirname(os.path.abspath(__file__))
    fixtures_dir = f"{cwd}/../../fixtures"

    with open(f"{fixtures_dir}/{path}", "r") as f:
        return f.read()


async def test_login_ok(requests_mock):
    requests_mock.post("https://api-host/api/v1/auth/login", text=fixture("http/login-200.json"))

    api = ApiClient("api-host", "api-token")
    api.login()

    assert "session-token" == api.session.token
    assert "2022-03-20T01:50:10.000Z" == api.session.expiration
    assert {"Authorization": "Bearer session-token"} == api.session.as_header()


@pytest.mark.parametrize(
    "status_code,data,msg",
    [
        (403, fixture("http/403.json"), "Invalid or missing authorization token"),
        (200, fixture("http/json-error.json"), "Invalid control character at"),
    ],
)
async def test_login_error(requests_mock, status_code, data, msg):
    requests_mock.post("https://api-host/api/v1/auth/login", status_code=status_code, text=data)
    api = ApiClient("api-host", "api-token")

    with pytest.raises(CorelliumApiException) as e:
        api.login()

    assert msg in str(e.value)
    assert api.session is None


async def test_create_instance_ok(requests_mock):
    data = fixture("http/create-instance-200.json")
    requests_mock.post("https://api-host/api/v1/instances", status_code=200, text=data)
    api = ApiClient("api-host", "api-token")
    api.session = Session("session-token", "2022-03-20T01:50:10.000Z")

    project = Project("d59db33d-27bd-4b22-878d-49e4758a648e", "Default Project")
    device = Device(
        name="rd1ae",
        type="automotive",
        flavor="kronos",
        description="",
        model="kronos",
        peripherals=False,
    )
    instance = api.create_instance("my-instance", project, device, "1.1.1", "Critical Application Monitor (Baremetal)")

    assert instance is not None
    assert instance.id


@pytest.mark.parametrize(
    "status_code,data,msg",
    [
        (403, fixture("http/403.json"), "Invalid or missing authorization token"),
        (400, fixture("http/create-instance-400.json"), "Unsupported device model"),
    ],
)
async def test_create_instance_error(requests_mock, status_code, data, msg):
    requests_mock.post("https://api-host/api/v1/instances", status_code=status_code, text=data)
    api = ApiClient("api-host", "api-token")
    api.session = Session("session-token", "2022-03-20T01:50:10.000Z")

    with pytest.raises(CorelliumApiException) as e:
        project = Project("d59db33d-27bd-4b22-878d-49e4758a648e", "Default Project")
        device = Device(
            name="rd1ae",
            type="automotive",
            flavor="kronos",
            description="",
            model="kronos",
            peripherals=False,
        )
        api.create_instance("my-instance", project, device, "1.1.1", "Critical Application Monitor (Baremetal)")

    assert msg in str(e.value)


async def test_destroy_instance_state_ok(requests_mock):
    instance = Instance(id="d59db33d-27bd-4b22-878d-49e4758a648e")

    requests_mock.delete(f"https://api-host/api/v1/instances/{instance.id}", status_code=204, text="")
    api = ApiClient("api-host", "api-token")
    api.session = Session("session-token", "2022-03-20T01:50:10.000Z")
    api.destroy_instance(instance)


@pytest.mark.parametrize(
    "status_code,data,msg",
    [
        (403, fixture("http/403.json"), "Invalid or missing authorization token"),
        (404, fixture("http/get-instance-state-404.json"), "No instance associated with this value"),
    ],
)
async def test_destroy_instance_error(requests_mock, status_code, data, msg):
    instance = Instance(id="d59db33d-27bd-4b22-878d-49e4758a648e")

    requests_mock.delete(f"https://api-host/api/v1/instances/{instance.id}", status_code=status_code, text=data)
    api = ApiClient("api-host", "api-token")
    api.session = Session("session-token", "2022-03-20T01:50:10.000Z")

    with pytest.raises(CorelliumApiException) as e:
        api.destroy_instance(instance)

    assert msg in str(e.value)
