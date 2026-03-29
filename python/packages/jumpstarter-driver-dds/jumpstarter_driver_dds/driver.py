from __future__ import annotations

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

    Each field name maps to a ``str`` type. The generated class name is
    derived from the topic name to avoid collisions between topics.
    """
    import dataclasses as dc

    from cyclonedds.idl import IdlStruct

    cls_name = topic_name.replace("/", "_").replace("-", "_").replace(".", "_") + "_Type"
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

    def connect(self) -> DdsParticipantInfo:
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
        if not self._connected:
            raise RuntimeError("Not connected to DDS domain")
        self._writers.clear()
        self._readers.clear()
        self._topics.clear()
        self._idl_types.clear()
        self._qos_map.clear()
        self._sample_counts.clear()
        self._participant = None
        self._connected = False

    def _require_connected(self):
        if not self._connected:
            raise RuntimeError("Not connected -- call connect() first")

    def create_topic(
        self,
        name: str,
        fields: list[str],
        qos: DdsTopicQos,
    ) -> DdsTopicInfo:
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
        self._require_connected()
        result = []
        for name in self._topics:
            idl_type = self._idl_types[name]
            fields = [f.name for f in idl_type.__dataclass_fields__.values()]
            result.append(
                DdsTopicInfo(
                    name=name,
                    fields=fields,
                    qos=self._qos_map.get(name, DdsTopicQos()),
                    sample_count=self._sample_counts.get(name, 0),
                )
            )
        return result

    def publish(self, topic_name: str, data: dict[str, Any]) -> DdsPublishResult:
        self._require_connected()
        if topic_name not in self._writers:
            raise ValueError(f"Topic '{topic_name}' not registered -- call create_topic() first")

        idl_type = self._idl_types[topic_name]
        sample = idl_type(**data)
        self._writers[topic_name].write(sample)
        self._sample_counts[topic_name] = self._sample_counts.get(topic_name, 0) + 1

        return DdsPublishResult(topic_name=topic_name, success=True, samples_written=1)

    def read(self, topic_name: str, max_samples: int) -> DdsReadResult:
        self._require_connected()
        if topic_name not in self._readers:
            raise ValueError(f"Topic '{topic_name}' not registered -- call create_topic() first")

        reader = self._readers[topic_name]
        raw_samples = reader.take(N=max_samples)

        samples = []
        for s in raw_samples:
            data = {}
            for f in s.__dataclass_fields__:
                data[f] = getattr(s, f)
            samples.append(DdsSample(topic_name=topic_name, data=data, timestamp=time.time()))

        return DdsReadResult(topic_name=topic_name, samples=samples, sample_count=len(samples))

    def get_participant_info(self) -> DdsParticipantInfo:
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

    def connect(self) -> DdsParticipantInfo:
        if self._connected:
            raise RuntimeError("Already connected to DDS domain")
        self._connected = True
        return DdsParticipantInfo(
            domain_id=self._domain_id,
            topic_count=len(self._topics),
            is_connected=True,
        )

    def disconnect(self) -> None:
        if not self._connected:
            raise RuntimeError("Not connected to DDS domain")
        self._connected = False
        self._topics.clear()
        self._topic_fields.clear()
        self._buffers.clear()
        self._sample_counts.clear()

    def _require_connected(self):
        if not self._connected:
            raise RuntimeError("Not connected -- call connect() first")

    def create_topic(
        self,
        name: str,
        fields: list[str],
        qos: DdsTopicQos,
    ) -> DdsTopicInfo:
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
        self._require_connected()
        result = []
        for name, info in self._topics.items():
            result.append(
                DdsTopicInfo(
                    name=info.name,
                    fields=info.fields,
                    qos=info.qos,
                    sample_count=self._sample_counts.get(name, 0),
                )
            )
        return result

    def publish(self, topic_name: str, data: dict[str, Any]) -> DdsPublishResult:
        self._require_connected()
        if topic_name not in self._topics:
            raise ValueError(f"Topic '{topic_name}' not registered -- call create_topic() first")

        fields = self._topic_fields[topic_name]
        for key in data:
            if key not in fields:
                raise ValueError(f"Unknown field '{key}' for topic '{topic_name}'")

        sample = DdsSample(topic_name=topic_name, data=data, timestamp=time.time())
        qos = self._topics[topic_name].qos
        buf = self._buffers[topic_name]
        buf.append(sample)
        if len(buf) > qos.history_depth:
            self._buffers[topic_name] = buf[-qos.history_depth:]
        self._sample_counts[topic_name] = self._sample_counts.get(topic_name, 0) + 1
        return DdsPublishResult(topic_name=topic_name, success=True, samples_written=1)

    def read(self, topic_name: str, max_samples: int) -> DdsReadResult:
        self._require_connected()
        if topic_name not in self._topics:
            raise ValueError(f"Topic '{topic_name}' not registered -- call create_topic() first")

        buf = self._buffers[topic_name]
        taken = buf[:max_samples]
        self._buffers[topic_name] = buf[max_samples:]
        return DdsReadResult(topic_name=topic_name, samples=taken, sample_count=len(taken))

    def get_participant_info(self) -> DdsParticipantInfo:
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
        if hasattr(super(), "__post_init__"):
            super().__post_init__()
        if self.use_mock:
            self._backend = MockDdsBackend(domain_id=self.domain_id)
        else:
            self._backend = DdsBackend(domain_id=self.domain_id)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_dds.client.DdsClient"

    def close(self):
        if self._backend._connected:
            try:
                self._backend.disconnect()
            except Exception:
                logger.warning("Failed to disconnect DDS backend", exc_info=True)
        super().close()

    def _default_qos(self) -> DdsTopicQos:
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
    async def monitor(self, topic_name: str) -> AsyncGenerator[DdsSample, None]:
        """Stream data samples from a topic as they arrive.

        Polls the topic reader periodically and yields new samples.
        """
        import asyncio

        self._backend._require_connected()
        if topic_name not in self._backend._topics:
            raise ValueError(f"Topic '{topic_name}' not registered")

        for _ in range(100):
            result = self._backend.read(topic_name, max_samples=10)
            for sample in result.samples:
                yield sample
            await asyncio.sleep(0.1)


@dataclass(kw_only=True, config=ConfigDict(arbitrary_types_allowed=True))
class MockDds(Driver):
    """Mock DDS driver for testing without real CycloneDDS.

    Wraps MockDdsBackend with the same @export interface as Dds,
    allowing full gRPC e2e testing without native dependencies.

    Accepts an optional ``backend`` parameter to inject a custom
    backend for stateful testing.
    """

    domain_id: int = 0
    backend: MockDdsBackend | None = field(default=None, repr=False)

    _internal_backend: MockDdsBackend = field(init=False, repr=False)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()
        self._internal_backend = self.backend or MockDdsBackend(domain_id=self.domain_id)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_dds.client.DdsClient"

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
        qos = DdsTopicQos()
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
    async def monitor(self, topic_name: str) -> AsyncGenerator[DdsSample, None]:
        """Stream data samples from a mock topic."""
        import asyncio

        self._internal_backend._require_connected()
        if topic_name not in self._internal_backend._topics:
            raise ValueError(f"Topic '{topic_name}' not registered")

        for _ in range(100):
            result = self._internal_backend.read(topic_name, max_samples=10)
            for sample in result.samples:
                yield sample
            await asyncio.sleep(0.1)
