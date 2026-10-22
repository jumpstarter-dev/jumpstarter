from __future__ import annotations

import pytest

from .driver import MockDds, MockDdsBackend
from jumpstarter.common.utils import serve


class DdsNotConnectedError(RuntimeError):
    pass


class DdsAlreadyConnectedError(RuntimeError):
    pass


class DdsTopicError(ValueError):
    pass


class StatefulDdsBackend(MockDdsBackend):
    """Drop-in replacement for MockDdsBackend that enforces
    the same rules as the real and mock backends while tracking
    operation history for test assertions.

    Tracks:
    - Connection lifecycle (connected/disconnected)
    - Topic creation and uniqueness
    - Publish field validation (same as MockDdsBackend)
    - Read buffer management with history depth
    - Operation ordering (must connect before other ops)
    - Call log for audit -- intentionally preserved across disconnect
      so callers can inspect the full session history
    """

    def __init__(self, domain_id: int = 0):
        super().__init__(domain_id=domain_id)
        self._call_log: list[str] = []
        self._total_published: int = 0
        self._total_read: int = 0

    def connect(self):
        """Connect and record the operation in the call log."""
        if self._connected:
            raise DdsAlreadyConnectedError("Already connected to DDS domain")
        result = super().connect()
        self._call_log.append("connect")
        return result

    def disconnect(self):
        """Disconnect, reset counters, and record in the call log."""
        if not self._connected:
            raise DdsNotConnectedError("Not connected to DDS domain")
        super().disconnect()
        self._total_published = 0
        self._total_read = 0
        self._call_log.append("disconnect")

    def _require_connected(self):
        """Raise ``DdsNotConnectedError`` if the backend is not connected."""
        if not self._connected:
            raise DdsNotConnectedError("Not connected -- call connect() first")

    def create_topic(self, name, fields, qos):
        """Create a topic, enforcing uniqueness."""
        self._require_connected()
        if name in self._topics:
            raise DdsTopicError(f"Topic '{name}' already exists")
        result = super().create_topic(name, fields, qos)
        self._call_log.append(f"create_topic({name})")
        return result

    def publish(self, topic_name, data):
        """Publish data after validating unknown fields (matching MockDdsBackend)."""
        self._require_connected()
        if topic_name not in self._topics:
            raise DdsTopicError(f"Topic '{topic_name}' not registered")

        fields = self._topic_fields[topic_name]
        unknown = set(data) - set(fields)
        if unknown:
            raise DdsTopicError(f"Unknown field(s) {unknown} for topic '{topic_name}', valid: {fields}")

        result = super().publish(topic_name, data)
        self._total_published += 1
        self._call_log.append(f"publish({topic_name})")
        return result

    def read(self, topic_name, max_samples):
        """Read samples and update the total-read counter."""
        self._require_connected()
        if topic_name not in self._topics:
            raise DdsTopicError(f"Topic '{topic_name}' not registered")
        result = super().read(topic_name, max_samples)
        self._total_read += result.sample_count
        self._call_log.append(f"read({topic_name})")
        return result


@pytest.fixture(params=["mock", "stateful"])
def any_backend(request):
    """Yield every backend implementation so parity tests run against all."""
    if request.param == "mock":
        return MockDdsBackend()
    return StatefulDdsBackend()


@pytest.fixture
def stateful_backend():
    return StatefulDdsBackend(domain_id=0)


@pytest.fixture
def stateful_client(stateful_backend):
    """Create a MockDds driver backed by StatefulDdsBackend and serve it.

    The MockDds @export methods delegate to the stateful backend,
    so gRPC routing works correctly while enforcing DDS lifecycle rules.
    """
    driver = MockDds(backend=stateful_backend)
    with serve(driver) as client:
        yield client, stateful_backend
