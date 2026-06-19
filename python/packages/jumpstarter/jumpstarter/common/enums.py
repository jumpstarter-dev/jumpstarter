"""Exporter status / log source enums.

These mirror the ``jumpstarter.v1`` protobuf enums (``ExporterStatus``/``LogSource``) — the
Rust core sends these integer values across the FFI boundary (in the get_status / log_stream
JSON), so the values here must stay in lock-step with the proto definition.
"""

from enum import IntEnum


class ExporterStatus(IntEnum):
    """Exporter status states (proto ``jumpstarter.v1.ExporterStatus``)."""

    UNSPECIFIED = 0
    """Unknown/unspecified exporter status"""

    OFFLINE = 1
    """The exporter is currently offline"""

    AVAILABLE = 2
    """Exporter is available to be leased"""

    BEFORE_LEASE_HOOK = 3
    """Exporter is leased, but currently executing before lease hook"""

    LEASE_READY = 4
    """Exporter is leased and ready to accept commands"""

    AFTER_LEASE_HOOK = 5
    """Lease was released, but exporter is executing after lease hook"""

    BEFORE_LEASE_HOOK_FAILED = 6
    """The before lease hook failed and the exporter is no longer available"""

    AFTER_LEASE_HOOK_FAILED = 7
    """The after lease hook failed and the exporter is no longer available"""

    def __str__(self):
        return self.name

    @classmethod
    def from_proto(cls, value: int) -> "ExporterStatus":
        return cls(value)

    def to_proto(self) -> int:
        return self.value


class LogSource(IntEnum):
    """Log source types (proto ``jumpstarter.v1.LogSource``)."""

    UNSPECIFIED = 0
    """Unspecified/unknown log source"""

    DRIVER = 1
    """Logs produced by a Jumpstarter driver"""

    BEFORE_LEASE_HOOK = 2
    """Logs produced by a before lease hook"""

    AFTER_LEASE_HOOK = 3
    """Logs produced by an after lease hook"""

    SYSTEM = 4
    """System/exporter logs"""

    def __str__(self):
        return self.name

    @classmethod
    def from_proto(cls, value: int) -> "LogSource":
        return cls(value)

    def to_proto(self) -> int:
        return self.value
