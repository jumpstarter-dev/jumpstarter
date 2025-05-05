import os

import pytest

# import websockets
from .api import ApiClient
from .exceptions import CorelliumApiException
from .types import Device, Instance, Project


def fixture(path):
    """
    Load file contents from fixtures/$path.
    """
    cwd = os.path.dirname(os.path.abspath(__file__))
    fixtures_dir = f"{cwd}/../../fixtures"

    with open(f"{fixtures_dir}/{path}", "r") as f:
        return f.read()


@pytest.mark.parametrize(
    "project_name,data,has_results",
    [
        ("OtherProject", fixture("http/get-projects-200.json"), True),
        (None, fixture("http/get-projects-200.json"), True),
        ("notfound", fixture("http/get-projects-200.json"), False),
    ],
)
def test_get_project_ok(requests_mock, project_name, data, has_results):
    requests_mock.get("https://api-host/api/v1/projects", status_code=200, text=data)
    api = ApiClient("api-host", "api-token")

    args = []
    if project_name:
        args.append(project_name)
    project = api.get_project(*args)

    if has_results:
        assert project is not None
        assert project.name == project_name if project_name is not None else "Default Project"
    else:
        assert project is None


@pytest.mark.parametrize(
    "status_code,data,msg",
    [
        (403, fixture("http/403.json"), "Invalid or missing authorization token"),
        (404, fixture("http/get-projects-404.json"), ""),
    ],
)
def test_get_project_error(requests_mock, status_code, data, msg):
    requests_mock.get("https://api-host/api/v1/projects", status_code=status_code, text=data)
    api = ApiClient("api-host", "api-token")

    with pytest.raises(CorelliumApiException) as e:
        api.get_project()

    assert msg in str(e.value)


@pytest.mark.parametrize(
    "model,data,has_results",
    [("rpi4b", fixture("http/get-models-200.json"), True), ("notfound", fixture("http/get-models-200.json"), False)],
)
def test_get_device_ok(requests_mock, model, data, has_results):
    requests_mock.get("https://api-host/api/v1/models", status_code=200, text=data)
    api = ApiClient("api-host", "api-token")

    device = api.get_device(model)

    if has_results:
        assert device is not None
        assert device.model == model
    else:
        assert device is None


@pytest.mark.parametrize(
    "status_code,data,msg",
    [
        (403, fixture("http/403.json"), "Invalid or missing authorization token"),
    ],
)
def test_get_device_error(requests_mock, status_code, data, msg):
    requests_mock.get("https://api-host/api/v1/models", status_code=status_code, text=data)
    api = ApiClient("api-host", "api-token")

    with pytest.raises(CorelliumApiException) as e:
        api.get_device("mymodel")

    assert msg in str(e.value)


def test_create_instance_ok(requests_mock):
    data = fixture("http/create-instance-200.json")
    requests_mock.post("https://api-host/api/v1/instances", status_code=200, text=data)
    api = ApiClient("api-host", "api-token")

    project = Project("d59db33d-27bd-4b22-878d-49e4758a648e", "Default Project")
    device = Device(
        name="rd1ae", type="automotive", flavor="kronos", description="", model="kronos", peripherals=False, quotas={}
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
def test_create_instance_error(requests_mock, status_code, data, msg):
    requests_mock.post("https://api-host/api/v1/instances", status_code=status_code, text=data)
    api = ApiClient("api-host", "api-token")

    with pytest.raises(CorelliumApiException) as e:
        project = Project("d59db33d-27bd-4b22-878d-49e4758a648e", "Default Project")
        device = Device(
            name="rd1ae",
            type="automotive",
            flavor="kronos",
            description="",
            model="kronos",
            peripherals=False,
            quotas={},
        )
        api.create_instance("my-instance", project, device, "1.1.1", "Critical Application Monitor (Baremetal)")

    assert msg in str(e.value)


def test_destroy_instance_state_ok(requests_mock):
    instance = Instance(id="d59db33d-27bd-4b22-878d-49e4758a648e")

    requests_mock.delete(f"https://api-host/api/v1/instances/{instance.id}", status_code=204, text="")
    api = ApiClient("api-host", "api-token")
    api.destroy_instance(instance)


@pytest.mark.parametrize(
    "status_code,data,msg",
    [
        (403, fixture("http/403.json"), "Invalid or missing authorization token"),
        (404, fixture("http/get-instance-404.json"), "No instance associated with this value"),
    ],
)
def test_destroy_instance_error(requests_mock, status_code, data, msg):
    instance = Instance(id="d59db33d-27bd-4b22-878d-49e4758a648e")

    requests_mock.delete(f"https://api-host/api/v1/instances/{instance.id}", status_code=status_code, text=data)
    api = ApiClient("api-host", "api-token")

    with pytest.raises(CorelliumApiException) as e:
        api.destroy_instance(instance)

    assert msg in str(e.value)


@pytest.mark.parametrize(
    "console_name,console_id",
    [
        (
            "Console 1",
            "port-1-cons",
        ),
        (
            "Console 10",
            None,
        ),
        (
            "Console 2",
            "port-2-cons",
        ),
    ],
)
def test_get_instance_console_id_ok(requests_mock, console_name, console_id):
    data = fixture("http/get-instance-200.json")
    instance = Instance(id="d59db33d-27bd-4b22-878d-49e4758a648e")
    requests_mock.get(f"https://api-host/api/v1/instances/{instance.id}", status_code=200, text=data)
    api = ApiClient("api-host", "api-token")

    current = api.get_instance_console_id(instance, console_name)

    assert console_id == current


@pytest.mark.parametrize(
    "status_code,data,msg",
    [
        (403, fixture("http/403.json"), "Invalid or missing authorization token"),
        (404, fixture("http/get-instance-404.json"), "No instance associated with this value"),
    ],
)
def test_get_instance_console_id_error(requests_mock, status_code, data, msg):
    instance = Instance(id="d59db33d-27bd-4b22-878d-49e4758a648e")
    requests_mock.get(f"https://api-host/api/v1/instances/{instance.id}", status_code=status_code, text=data)
    api = ApiClient("api-host", "api-token")

    with pytest.raises(CorelliumApiException) as e:
        api.get_instance_console_id(instance, "Console 1")

    assert msg in str(e.value)


def test_get_instance_console_url_ok(requests_mock):
    console_id = "port-1-cons"
    data = fixture("http/get-instance-console-url-200.json")
    instance = Instance(id="d59db33d-27bd-4b22-878d-49e4758a648e")
    requests_mock.get(
        f"https://api-host/api/v1/instances/{instance.id}/console?type=1-cons", status_code=200, text=data
    )
    api = ApiClient("api-host", "api-token")

    current = api.get_instance_console_url(instance, console_id)
    expected = "wss://api-host/port-cons-1"

    assert expected == current


@pytest.mark.parametrize(
    "status_code,data,msg",
    [
        (400, fixture("http/get-instance-console-url-400.json"), "not a valid type"),
        (403, fixture("http/403.json"), "Invalid or missing authorization token"),
        (404, fixture("http/get-instance-404.json"), "No instance associated with this value"),
    ],
)
def test_get_instance_console_url_error(requests_mock, status_code, data, msg):
    console_id = "port-1-cons"
    instance = Instance(id="d59db33d-27bd-4b22-878d-49e4758a648e")
    requests_mock.get(
        f"https://api-host/api/v1/instances/{instance.id}/console?type=1-cons", status_code=status_code, text=data
    )
    api = ApiClient("api-host", "api-token")

    with pytest.raises(CorelliumApiException) as e:
        api.get_instance_console_url(instance, console_id)

    assert msg in str(e.value)
