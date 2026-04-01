from __future__ import annotations

import dataclasses as dc
import hashlib
import logging
import time
from collections.abc import AsyncGenerator
from dataclasses import field
from typing import Any

from pydantic import ConfigDict, validate_call
from pydantic.dataclasses import dataclass

from .common import (
    DdsDurability,
    DdsParticipantInfo,
    DdsPublishResult,
    DdsReadResult,
    DdsReliability,
    DdsSample,
    DdsTopicInfo,
    DdsTopicQos,
)
from jumpstarter.driver import Driver, export

logger = logging.getLogger(__name__)


def _build_cyclonedds_qos(qos: DdsTopicQos):
    """Build a CycloneDDS Qos object from our config model."""
    from cyclonedds.core import Policy, Qos

    policies = []

    if qos.reliability == DdsReliability.RELIABLE:
        policies.append(Policy.Reliability.Reliable(max_blocking_time=1_000_000_000))
    else:
        policies.append(Policy.Reliability.BestEffort)

    if qos.durability == DdsDurability.VOLATILE:
        policies.append(Policy.Durability.Volatile)
    elif qos.durability == DdsDurability.TRANSIENT_LOCAL:
        policies.append(Policy.Durability.TransientLocal)
    elif qos.durability == DdsDurability.TRANSIENT:
        policies.append(Policy.Durability.Transient)
    elif qos.durability == DdsDurability.PERSISTENT:
        policies.append(Policy.Durability.Persistent)

    policies.append(Policy.History.KeepLast(qos.history_depth))

    return Qos(*policies)


def _make_idl_type(topic_name: str, fields: list[str]):
    """Dynamically create a CycloneDDS IdlStruct type for the given fields.

    Each field name maps to a ``str`` type. For complex or mixed-type
    schemas, define custom IdlStruct subclasses directly and register
    them with the backend.

    A hash suffix is appended to the generated class name to prevent
    collisions when distinct topic names sanitise to the same identifier
    (e.g. ``"sensor/temp"`` and ``"sensor-temp"``).
    """
    from cyclonedds.idl import IdlStruct

    sanitised = topic_name.replace("/", "_").replace("-", "_").replace(".", "_")
    hash_suffix = hashlib.md5(topic_name.encode()).hexdigest()[:8]
    cls_name = f"{sanitised}_{hash_suffix}_Type"
    if not cls_name.isidentifier():
        cls_name = f"Topic_{hash_suffix}_Type"
    dc_fields = [(f, str, dc.field(default="")) for f in fields]
    idl_cls = dc.make_dataclass(cls_name, dc_fields, bases=(IdlStruct,))
    return idl_cls


class DdsBackend:
    """Default CycloneDDS backend managing real DDS entities."""

    def __init__(self, domain_id: int):
        self._domain_id = domain_id
        self._participant = None
        self._topics: dict[str, Any] = {}
        self._writers: dict[str, Any] = {}
        self._readers: dict[str, Any] = {}
        self._idl_types: dict[str, type] = {}
        self._qos_map: dict[str, DdsTopicQos] = {}
        self._sample_counts: dict[str, int] = {}
        self._connected = False

    @property
    def is_connected(self) -> bool:
        """Whether the backend is currently connected to a DDS domain."""
        return self._connected

    def has_topic(self, name: str) -> bool:
        """Check whether a topic with the given name has been registered."""
        return name in self._topics

    def connect(self) -> DdsParticipantInfo:
        """Create a CycloneDDS DomainParticipant and mark the backend as connected."""
        if self._connected:
            raise RuntimeError("Already connected to DDS domain")
        from cyclonedds.domain import DomainParticipant

        self._participant = DomainParticipant(domain_id=self._domain_id)
        self._connected = True
        return DdsParticipantInfo(
            domain_id=self._domain_id,
            topic_count=len(self._topics),
            is_connected=True,
        )

    def disconnect(self) -> None:
        """Tear down all DDS entities and release the participant."""
        if not self._connected:
            raise RuntimeError("Not connected to DDS domain")
        for writer in self._writers.values():
            try:
                writer.close()
            except Exception:
                pass
        for reader in self._readers.values():
            try:
                reader.close()
            except Exception:
                pass
        for topic in self._topics.values():
            try:
                topic.close()
            except Exception:
                pass
        if self._participant is not None:
            try:
                self._participant.close()
            except Exception:
                pass
        self._writers.clear()
        self._readers.clear()
        self._topics.clear()
        self._idl_types.clear()
        self._qos_map.clear()
        self._sample_counts.clear()
        self._participant = None
        self._connected = False

    def _require_connected(self):
        """Raise ``RuntimeError`` if the backend is not connected."""
        if not self._connected:
            raise RuntimeError("Not connected -- call connect() first")

    def create_topic(
        self,
        name: str,
        fields: list[str],
        qos: DdsTopicQos,
    ) -> DdsTopicInfo:
        """Register a topic, create its writer/reader, and return topic info."""
        self._require_connected()
        if name in self._topics:
            raise ValueError(f"Topic '{name}' already exists")

        from cyclonedds.pub import DataWriter
        from cyclonedds.sub import DataReader
        from cyclonedds.topic import Topic

        idl_type = _make_idl_type(name, fields)
        self._idl_types[name] = idl_type

        cqos = _build_cyclonedds_qos(qos)
        topic = Topic(self._participant, name, idl_type, qos=cqos)
        self._topics[name] = topic
        self._qos_map[name] = qos
        self._sample_counts[name] = 0

        self._writers[name] = DataWriter(self._participant, topic, qos=cqos)
        self._readers[name] = DataReader(self._participant, topic, qos=cqos)

        return DdsTopicInfo(name=name, fields=fields, qos=qos)

    def list_topics(self) -> list[DdsTopicInfo]:
        """Return info for every registered topic."""
        self._require_connected()
        result = []
        for name in self._topics:
            idl_type = self._idl_types[name]
            fields = [f.name for f in dc.fields(idl_type)]
            result.append(
                DdsTopicInfo(
                    name=name,
                    fields=fields,
                    qos=self._qos_map[name],
                    sample_count=self._sample_counts[name],
                )
            )
        return result

    def publish(self, topic_name: str, data: dict[str, Any]) -> DdsPublishResult:
        """Write a data sample via the topic's DataWriter."""
        self._require_connected()
        if topic_name not in self._writers:
            raise ValueError(f"Topic '{topic_name}' not registered -- call create_topic() first")

        idl_type = self._idl_types[topic_name]
        valid_fields = {f.name for f in dc.fields(idl_type)}
        unknown = set(data) - valid_fields
        if unknown:
            raise ValueError(f"Unknown field(s) {unknown} for topic '{topic_name}'")

        sample = idl_type(**data)
        self._writers[topic_name].write(sample)
        self._sample_counts[topic_name] += 1

        return DdsPublishResult(topic_name=topic_name, samples_written=1)

    def read(self, topic_name: str, max_samples: int) -> DdsReadResult:
        """Take up to *max_samples* from the topic's DataReader.

        Note: ``read`` and ``monitor`` both consume from the same
        DataReader buffer; using them concurrently on the same topic
        will cause samples to be split unpredictably between the two.
        """
        self._require_connected()
        if topic_name not in self._readers:
            raise ValueError(f"Topic '{topic_name}' not registered -- call create_topic() first")

        reader = self._readers[topic_name]
        raw_samples = reader.take(N=max_samples)

        samples = []
        for s in raw_samples:
            data = {f.name: getattr(s, f.name) for f in dc.fields(s)}
            samples.append(DdsSample(topic_name=topic_name, data=data, timestamp=time.time()))

        return DdsReadResult(topic_name=topic_name, samples=samples, sample_count=len(samples))

    def get_participant_info(self) -> DdsParticipantInfo:
        """Return metadata about this DDS domain participant."""
        self._require_connected()
        return DdsParticipantInfo(
            domain_id=self._domain_id,
            topic_count=len(self._topics),
            is_connected=self._connected,
        )


class MockDdsBackend:
    """In-memory mock backend for testing without real CycloneDDS dependencies."""

    def __init__(self, domain_id: int = 0):
        self._domain_id = domain_id
        self._connected = False
        self._topics: dict[str, DdsTopicInfo] = {}
        self._topic_fields: dict[str, list[str]] = {}
        self._buffers: dict[str, list[DdsSample]] = {}
        self._sample_counts: dict[str, int] = {}

    @property
    def is_connected(self) -> bool:
        """Whether the backend is currently connected to a DDS domain."""
        return self._connected

    def has_topic(self, name: str) -> bool:
        """Check whether a topic with the given name has been registered."""
        return name in self._topics

    def connect(self) -> DdsParticipantInfo:
        """Mark the mock backend as connected."""
        if self._connected:
            raise RuntimeError("Already connected to DDS domain")
        self._connected = True
        return DdsParticipantInfo(
            domain_id=self._domain_id,
            topic_count=len(self._topics),
            is_connected=True,
        )

    def disconnect(self) -> None:
        """Disconnect and clear all in-memory state."""
        if not self._connected:
            raise RuntimeError("Not connected to DDS domain")
        self._connected = False
        self._topics.clear()
        self._topic_fields.clear()
        self._buffers.clear()
        self._sample_counts.clear()

    def _require_connected(self):
        """Raise ``RuntimeError`` if the backend is not connected."""
        if not self._connected:
            raise RuntimeError("Not connected -- call connect() first")

    def create_topic(
        self,
        name: str,
        fields: list[str],
        qos: DdsTopicQos,
    ) -> DdsTopicInfo:
        """Register a topic in the in-memory store."""
        self._require_connected()
        if name in self._topics:
            raise ValueError(f"Topic '{name}' already exists")

        info = DdsTopicInfo(name=name, fields=fields, qos=qos)
        self._topics[name] = info
        self._topic_fields[name] = fields
        self._buffers[name] = []
        self._sample_counts[name] = 0
        return info

    def list_topics(self) -> list[DdsTopicInfo]:
        """Return info for every registered topic in the mock store."""
        self._require_connected()
        result = []
        for name, info in self._topics.items():
            result.append(
                DdsTopicInfo(
                    name=info.name,
                    fields=info.fields,
                    qos=info.qos,
                    sample_count=self._sample_counts[name],
                )
            )
        return result

    def publish(self, topic_name: str, data: dict[str, Any]) -> DdsPublishResult:
        """Buffer a sample, filling defaults for missing fields.

        Mirrors the real ``DdsBackend`` where the CycloneDDS dataclass
        constructor fills unset fields with their defaults (empty string).
        """
        self._require_connected()
        if topic_name not in self._topics:
            raise ValueError(f"Topic '{topic_name}' not registered -- call create_topic() first")

        fields = self._topic_fields[topic_name]
        unknown = set(data) - set(fields)
        if unknown:
            raise ValueError(f"Unknown field(s) {unknown} for topic '{topic_name}'")

        full_data = {f: data.get(f, "") for f in fields}

        sample = DdsSample(topic_name=topic_name, data=full_data, timestamp=time.time())
        qos = self._topics[topic_name].qos
        buf = self._buffers[topic_name]
        buf.append(sample)
        if len(buf) > qos.history_depth:
            self._buffers[topic_name] = buf[-qos.history_depth :]
        self._sample_counts[topic_name] += 1
        return DdsPublishResult(topic_name=topic_name, samples_written=1)

    def read(self, topic_name: str, max_samples: int) -> DdsReadResult:
        """Take up to *max_samples* from the in-memory buffer."""
        self._require_connected()
        if topic_name not in self._topics:
            raise ValueError(f"Topic '{topic_name}' not registered -- call create_topic() first")

        buf = self._buffers[topic_name]
        taken = buf[:max_samples]
        self._buffers[topic_name] = buf[max_samples:]
        return DdsReadResult(topic_name=topic_name, samples=taken, sample_count=len(taken))

    def get_participant_info(self) -> DdsParticipantInfo:
        """Return metadata about this mock participant."""
        self._require_connected()
        return DdsParticipantInfo(
            domain_id=self._domain_id,
            topic_count=len(self._topics),
            is_connected=self._connected,
        )


@dataclass(kw_only=True, config=ConfigDict(arbitrary_types_allowed=True))
class Dds(Driver):
    """DDS (Data Distribution Service) driver using Eclipse CycloneDDS.

    Provides publish/subscribe messaging over DDS with configurable
    QoS, topics, and domain participation. Supports both real CycloneDDS
    transport and an in-memory mock backend for testing.
    """

    domain_id: int = 0
    default_reliability: DdsReliability = DdsReliability.RELIABLE
    default_durability: DdsDurability = DdsDurability.VOLATILE
    default_history_depth: int = 10
    use_mock: bool = False

    _backend: DdsBackend | MockDdsBackend = field(init=False, repr=False)

    def __post_init__(self):
        """Initialise the real or mock backend based on ``use_mock``."""
        if hasattr(super(), "__post_init__"):
            super().__post_init__()
        if self.use_mock:
            self._backend = MockDdsBackend(domain_id=self.domain_id)
        else:
            self._backend = DdsBackend(domain_id=self.domain_id)

    @classmethod
    def client(cls) -> str:
        """Return the fully-qualified path to the matching client class."""
        return "jumpstarter_driver_dds.client.DdsClient"

    def close(self):
        """Disconnect the backend (if connected) and release resources."""
        if self._backend.is_connected:
            try:
                self._backend.disconnect()
            except RuntimeError:
                logger.warning("Failed to disconnect DDS backend", exc_info=True)
        super().close()

    def _default_qos(self) -> DdsTopicQos:
        """Build a ``DdsTopicQos`` from this driver's default settings."""
        return DdsTopicQos(
            reliability=self.default_reliability,
            durability=self.default_durability,
            history_depth=self.default_history_depth,
        )

    @export
    @validate_call(validate_return=True)
    def connect(self) -> DdsParticipantInfo:
        """Connect to the DDS domain and create a domain participant."""
        return self._backend.connect()

    @export
    @validate_call(validate_return=True)
    def disconnect(self) -> None:
        """Disconnect from the DDS domain."""
        self._backend.disconnect()

    @export
    @validate_call(validate_return=True)
    def create_topic(
        self,
        name: str,
        fields: list[str],
        reliability: str | None = None,
        durability: str | None = None,
        history_depth: int | None = None,
    ) -> DdsTopicInfo:
        """Create a DDS topic with the given schema and QoS settings."""
        qos = self._default_qos()
        if reliability is not None:
            qos.reliability = DdsReliability(reliability)
        if durability is not None:
            qos.durability = DdsDurability(durability)
        if history_depth is not None:
            qos.history_depth = history_depth
        return self._backend.create_topic(name, fields, qos)

    @export
    @validate_call(validate_return=True)
    def list_topics(self) -> list[DdsTopicInfo]:
        """List all registered topics in this participant."""
        return self._backend.list_topics()

    @export
    @validate_call(validate_return=True)
    def publish(self, topic_name: str, data: dict[str, Any]) -> DdsPublishResult:
        """Publish a data sample to a DDS topic."""
        return self._backend.publish(topic_name, data)

    @export
    @validate_call(validate_return=True)
    def read(self, topic_name: str, max_samples: int = 10) -> DdsReadResult:
        """Read (take) samples from a DDS topic."""
        return self._backend.read(topic_name, max_samples)

    @export
    @validate_call(validate_return=True)
    def get_participant_info(self) -> DdsParticipantInfo:
        """Get information about the DDS domain participant."""
        return self._backend.get_participant_info()

    @export
    async def monitor(self, topic_name: str, max_iterations: int = 0) -> AsyncGenerator[DdsSample, None]:
        """Stream data samples from a topic as they arrive.

        Polls the topic reader periodically and yields new samples.
        If *max_iterations* is 0 (default), polls indefinitely until
        the client cancels the stream. ``read`` and ``monitor`` both
        consume from the same reader buffer; do not use them
        concurrently on the same topic.
        """
        import anyio

        if not self._backend.is_connected:
            raise RuntimeError("Not connected -- call connect() first")
        if not self._backend.has_topic(topic_name):
            raise ValueError(f"Topic '{topic_name}' not registered")

        iterations = 0
        while max_iterations == 0 or iterations < max_iterations:
            try:
                result = self._backend.read(topic_name, max_samples=10)
            except RuntimeError:
                return
            for sample in result.samples:
                yield sample
            await anyio.sleep(0.1)
            iterations += 1


@dataclass(kw_only=True, config=ConfigDict(arbitrary_types_allowed=True))
class MockDds(Driver):
    """Mock DDS driver for testing without real CycloneDDS.

    Wraps MockDdsBackend with the same @export interface as Dds,
    allowing full gRPC e2e testing without native dependencies.

    Accepts an optional ``backend`` parameter to inject a custom
    backend for stateful testing.
    """

    domain_id: int = 0
    default_reliability: DdsReliability = DdsReliability.RELIABLE
    default_durability: DdsDurability = DdsDurability.VOLATILE
    default_history_depth: int = 10
    backend: MockDdsBackend | None = field(default=None, repr=False)

    _internal_backend: MockDdsBackend = field(init=False, repr=False)

    def __post_init__(self):
        """Initialise the internal mock backend (or use the injected one)."""
        if hasattr(super(), "__post_init__"):
            super().__post_init__()
        self._internal_backend = self.backend or MockDdsBackend(domain_id=self.domain_id)

    @classmethod
    def client(cls) -> str:
        """Return the fully-qualified path to the matching client class."""
        return "jumpstarter_driver_dds.client.DdsClient"

    def close(self):
        """Disconnect the mock backend (if connected) and release resources."""
        if self._internal_backend.is_connected:
            try:
                self._internal_backend.disconnect()
            except RuntimeError:
                logger.warning("Failed to disconnect mock DDS backend", exc_info=True)
        super().close()

    def _default_qos(self) -> DdsTopicQos:
        """Build a ``DdsTopicQos`` from this driver's default settings."""
        return DdsTopicQos(
            reliability=self.default_reliability,
            durability=self.default_durability,
            history_depth=self.default_history_depth,
        )

    @export
    @validate_call(validate_return=True)
    def connect(self) -> DdsParticipantInfo:
        """Connect to the mock DDS domain."""
        return self._internal_backend.connect()

    @export
    @validate_call(validate_return=True)
    def disconnect(self) -> None:
        """Disconnect from the mock DDS domain."""
        self._internal_backend.disconnect()

    @export
    @validate_call(validate_return=True)
    def create_topic(
        self,
        name: str,
        fields: list[str],
        reliability: str | None = None,
        durability: str | None = None,
        history_depth: int | None = None,
    ) -> DdsTopicInfo:
        """Create a topic on the mock backend."""
        qos = self._default_qos()
        if reliability is not None:
            qos.reliability = DdsReliability(reliability)
        if durability is not None:
            qos.durability = DdsDurability(durability)
        if history_depth is not None:
            qos.history_depth = history_depth
        return self._internal_backend.create_topic(name, fields, qos)

    @export
    @validate_call(validate_return=True)
    def list_topics(self) -> list[DdsTopicInfo]:
        """List all topics on the mock backend."""
        return self._internal_backend.list_topics()

    @export
    @validate_call(validate_return=True)
    def publish(self, topic_name: str, data: dict[str, Any]) -> DdsPublishResult:
        """Publish data to a mock topic."""
        return self._internal_backend.publish(topic_name, data)

    @export
    @validate_call(validate_return=True)
    def read(self, topic_name: str, max_samples: int = 10) -> DdsReadResult:
        """Read samples from a mock topic."""
        return self._internal_backend.read(topic_name, max_samples)

    @export
    @validate_call(validate_return=True)
    def get_participant_info(self) -> DdsParticipantInfo:
        """Get mock participant info."""
        return self._internal_backend.get_participant_info()

    @export
    async def monitor(self, topic_name: str, max_iterations: int = 0) -> AsyncGenerator[DdsSample, None]:
        """Stream data samples from a mock topic.

        ``read`` and ``monitor`` both consume from the same buffer;
        do not use them concurrently on the same topic.
        """
        import anyio

        if not self._internal_backend.is_connected:
            raise RuntimeError("Not connected -- call connect() first")
        if not self._internal_backend.has_topic(topic_name):
            raise ValueError(f"Topic '{topic_name}' not registered")

        iterations = 0
        while max_iterations == 0 or iterations < max_iterations:
            try:
                result = self._internal_backend.read(topic_name, max_samples=10)
            except RuntimeError:
                return
            for sample in result.samples:
                yield sample
            await anyio.sleep(0.1)
            iterations += 1
