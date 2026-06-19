"""Tests for the FFI-backed Lease shim (jumpstarter.client.lease).

The controller/lease protocol lives in the Rust core; here we mock
``jumpstarter_core.ControllerSession`` and assert the Python shim drives it correctly
(acquire → expose host path → release) and preserves the historic ``Lease`` API. End-to-end
behavior against a real controller is covered by the e2e env.
"""

from datetime import timedelta

import jumpstarter_core as jc
import pytest
from anyio.from_thread import start_blocking_portal

from jumpstarter.client.exceptions import LeaseError
from jumpstarter.client.lease import DirectLease, Lease


class FakeTransport:
    def __init__(self, host):
        self._host = host
        self.closed = False

    async def jumpstarter_host(self):
        return self._host

    async def close(self):
        self.closed = True


class FakeSession:
    def __init__(self):
        self.released = []
        self.served = []
        self.acquire_calls = []

    async def acquire_lease(self, selector, exporter_name, existing_name, duration_secs, timeout_secs):
        self.acquire_calls.append((selector, exporter_name, existing_name, duration_secs, timeout_secs))
        return jc.AcquiredLease(name="lease-xyz", exporter="exp-1")

    async def serve_lease(self, name):
        transport = FakeTransport(f"/tmp/sock-{name}")
        self.served.append(transport)
        return transport

    async def release_lease(self, name):
        self.released.append(name)


@pytest.fixture
def session(monkeypatch):
    fake = FakeSession()

    class FakeControllerSession:
        @staticmethod
        async def connect(*args, **kwargs):
            return fake

    monkeypatch.setattr(jc, "ControllerSession", FakeControllerSession)
    return fake


def _lease(portal, **overrides):
    kwargs = {
        "endpoint": "controller:443",
        "namespace": "ns",
        "token": "tok",
        "duration": timedelta(minutes=1),
        "selector": "board=inc1",
        "allow": [],
        "unsafe": True,
        "portal": portal,
    }
    kwargs.update(overrides)
    return Lease(**kwargs)


def test_request_acquires_and_populates(session):
    with start_blocking_portal() as portal:
        lease = _lease(portal)
        result = lease.request()
        assert result is lease
        assert lease.name == "lease-xyz"
        assert lease.exporter_name == "exp-1"
        # selector + duration forwarded to the FFI surface
        sel, _exp, _existing, dur, _to = session.acquire_calls[0]
        assert sel == "board=inc1"
        assert dur == 60


def test_serve_unix_yields_host_and_closes(session):
    with start_blocking_portal() as portal:
        lease = _lease(portal)
        lease.request()
        with lease.serve_unix() as path:
            assert path == "/tmp/sock-lease-xyz"
        assert session.served[0].closed is True


def test_context_manager_releases_when_release_true(session):
    with start_blocking_portal() as portal:
        with portal.wrap_async_context_manager(_lease(portal)) as lease:
            assert lease.name == "lease-xyz"
        assert session.released == ["lease-xyz"]


def test_context_manager_keeps_preexisting_lease(session):
    with start_blocking_portal() as portal:
        with portal.wrap_async_context_manager(_lease(portal, release=False, name="preexisting")):
            pass
        assert session.released == []


def test_acquire_error_maps_to_lease_error(session, monkeypatch):
    async def boom(*args, **kwargs):
        raise jc.ControllerError.Unsatisfiable("no matching exporter")

    monkeypatch.setattr(session, "acquire_lease", boom)
    with start_blocking_portal() as portal:
        lease = _lease(portal)
        with pytest.raises(LeaseError):
            lease.request()


def test_direct_lease_serves_address():
    with start_blocking_portal() as portal:
        direct = DirectLease(address="exporter.host:1234", portal=portal, allow=[], unsafe=True)
        with portal.wrap_async_context_manager(direct.serve_unix_async()) as addr:
            assert addr == "exporter.host:1234"
