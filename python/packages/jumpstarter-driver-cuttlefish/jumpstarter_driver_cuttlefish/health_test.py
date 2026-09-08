import io
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from .health import check, listening_ports


@pytest.fixture
def health_state(tmp_path):
    runtime_id = tmp_path / "runtime-id"
    runtime_id.write_text("runtime-1")
    return tmp_path / "health.json", {
        "runtime_id_path": str(runtime_id), "runtime_id": "runtime-1",
        "url": "http://127.0.0.1:2081", "state": "running",
        "group": "cvd", "name": "1", "ports": [7681, 7300, 17681, 17300],
    }


def run_check(health_state, cvds=None, ports=None):
    path, state = health_state
    path.write_text(json.dumps(state))
    if cvds is None:
        cvds = [{"group": "cvd", "name": "1", "status": "Running"}]
    inventory = json.dumps({"cvds": cvds}).encode()
    with patch("jumpstarter_driver_cuttlefish.health.urllib.request.urlopen",
               side_effect=lambda *args, **kwargs: io.BytesIO(inventory)), \
         patch("jumpstarter_driver_cuttlefish.health.listening_ports",
               return_value=set(state["ports"] if ports is None else ports)):
        check(str(path))


def test_running_guest_and_simulators(health_state):
    run_check(health_state)


@pytest.mark.parametrize("cvds", [[], [{"group": "cvd", "name": "1", "status": "Stopped"}],
                                   [{"group": "other", "name": "1", "status": "Running"}]])
def test_guest_failure(health_state, cvds):
    with pytest.raises(RuntimeError):
        run_check(health_state, cvds=cvds)


@pytest.mark.parametrize("missing", [7681, 7300, 17681, 17300])
def test_simulator_or_relay_failure(health_state, missing):
    ports = set(health_state[1]["ports"]) - {missing}
    with pytest.raises(RuntimeError, match="listener is missing"):
        run_check(health_state, ports=ports)


def test_intentional_power_off(health_state):
    health_state[1]["state"] = "off"
    run_check(health_state, cvds=[], ports=[])


def test_bounded_transition(health_state):
    health_state[1].update(state="transition", deadline=time.monotonic() + 60)
    run_check(health_state, cvds=[], ports=[])
    health_state[1]["deadline"] = time.monotonic() - 1
    with pytest.raises(RuntimeError, match="timed out"):
        run_check(health_state)


def test_operation_failure(health_state):
    health_state[1]["state"] = "failed"
    with pytest.raises(RuntimeError, match="failed"):
        run_check(health_state)


@pytest.mark.parametrize("state", ["off", "running", "transition"])
def test_runtime_restart_fails_even_when_api_returns(health_state, state):
    health_state[1]["state"] = state
    Path(health_state[1]["runtime_id_path"]).write_text("runtime-2")
    with pytest.raises(RuntimeError, match="runtime restarted"):
        run_check(health_state)


def test_listener_state_and_ipv6():
    tcp = "header\n0: 0100007F:1E01 00000000:0000 0A\n1: 0100007F:1C84 00000000:0000 01\n"
    tcp6 = "header\n0: 00000000000000000000000000000000:45B1 00000000:0000 0A\n"
    with patch.object(Path, "read_text", side_effect=[tcp, tcp6]):
        assert listening_ports() == {7681, 17841}


def test_warm_exporter_before_first_lease(tmp_path):
    from .health import initialize

    runtime_id = tmp_path / "runtime-id"
    runtime_id.write_text("runtime-1")
    state_path = tmp_path / "health.json"
    initialize(str(state_path), str(runtime_id), "http://127.0.0.1:2081")
    with patch("jumpstarter_driver_cuttlefish.health.urllib.request.urlopen", return_value=io.BytesIO()):
        check(str(state_path))
    runtime_id.write_text("runtime-2")
    with pytest.raises(RuntimeError, match="runtime restarted"):
        check(str(state_path))
