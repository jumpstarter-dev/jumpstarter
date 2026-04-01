from __future__ import annotations

import pytest

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
from .driver import Dds, MockDds, MockDdsBackend
from jumpstarter.client.core import DriverError
from jumpstarter.common.utils import serve

# =============================================================================
# Level 1: Unit Tests (Pydantic models, backend logic)
# =============================================================================


class TestPydanticModels:
    """1a. Pydantic model validation."""

    def test_topic_qos_defaults(self):
        qos = DdsTopicQos()
        assert qos.reliability == DdsReliability.RELIABLE
        assert qos.durability == DdsDurability.VOLATILE
        assert qos.history_depth == 10

    def test_topic_qos_custom(self):
        qos = DdsTopicQos(
            reliability=DdsReliability.BEST_EFFORT,
            durability=DdsDurability.TRANSIENT_LOCAL,
            history_depth=50,
        )
        assert qos.reliability == DdsReliability.BEST_EFFORT
        assert qos.durability == DdsDurability.TRANSIENT_LOCAL
        assert qos.history_depth == 50

    def test_participant_info(self):
        info = DdsParticipantInfo(domain_id=42, topic_count=3, is_connected=True)
        assert info.domain_id == 42
        assert info.topic_count == 3
        assert info.is_connected is True

    def test_topic_info(self):
        info = DdsTopicInfo(name="sensor/temp", fields=["value", "unit"])
        assert info.name == "sensor/temp"
        assert info.fields == ["value", "unit"]
        assert info.sample_count == 0

    def test_dds_sample(self):
        sample = DdsSample(
            topic_name="test",
            data={"x": "1", "y": "2"},
            timestamp=1234567.0,
        )
        assert sample.topic_name == "test"
        assert sample.data["x"] == "1"

    def test_publish_result(self):
        result = DdsPublishResult(topic_name="test", samples_written=1)
        assert result.samples_written == 1

    def test_read_result_empty(self):
        result = DdsReadResult(topic_name="test", samples=[], sample_count=0)
        assert result.sample_count == 0

    def test_read_result_count_mismatch_raises(self):
        with pytest.raises(ValueError, match="sample_count"):
            DdsReadResult(
                topic_name="test",
                samples=[DdsSample(topic_name="test", data={"k": "v"})],
                sample_count=0,
            )

    def test_reliability_enum(self):
        assert DdsReliability("RELIABLE") == DdsReliability.RELIABLE
        assert DdsReliability("BEST_EFFORT") == DdsReliability.BEST_EFFORT
        with pytest.raises(ValueError):
            DdsReliability("INVALID")

    def test_durability_enum(self):
        assert DdsDurability("VOLATILE") == DdsDurability.VOLATILE
        assert DdsDurability("TRANSIENT_LOCAL") == DdsDurability.TRANSIENT_LOCAL
        assert DdsDurability("TRANSIENT") == DdsDurability.TRANSIENT
        assert DdsDurability("PERSISTENT") == DdsDurability.PERSISTENT
        with pytest.raises(ValueError):
            DdsDurability("INVALID")


class TestMockDdsBackendUnit:
    """1b. MockDdsBackend unit tests (no gRPC)."""

    def test_connect_disconnect(self):
        backend = MockDdsBackend(domain_id=5)
        info = backend.connect()
        assert info.domain_id == 5
        assert info.is_connected is True
        backend.disconnect()
        assert backend._connected is False

    def test_double_connect_raises(self):
        backend = MockDdsBackend()
        backend.connect()
        with pytest.raises(RuntimeError, match="Already connected"):
            backend.connect()

    def test_disconnect_without_connect_raises(self):
        backend = MockDdsBackend()
        with pytest.raises(RuntimeError, match="Not connected"):
            backend.disconnect()

    def test_create_topic(self):
        backend = MockDdsBackend()
        backend.connect()
        qos = DdsTopicQos()
        info = backend.create_topic("test_topic", ["field1", "field2"], qos)
        assert info.name == "test_topic"
        assert info.fields == ["field1", "field2"]

    def test_create_duplicate_topic_raises(self):
        backend = MockDdsBackend()
        backend.connect()
        qos = DdsTopicQos()
        backend.create_topic("test_topic", ["field1"], qos)
        with pytest.raises(ValueError, match="already exists"):
            backend.create_topic("test_topic", ["field2"], qos)

    def test_publish_and_read(self):
        backend = MockDdsBackend()
        backend.connect()
        backend.create_topic("t", ["val"], DdsTopicQos())
        result = backend.publish("t", {"val": "42"})
        assert result.samples_written == 1
        read_result = backend.read("t", 10)
        assert read_result.sample_count == 1
        assert read_result.samples[0].data["val"] == "42"

    def test_publish_partial_fills_defaults(self):
        """Missing fields are filled with empty string, matching real backend."""
        backend = MockDdsBackend()
        backend.connect()
        backend.create_topic("t", ["x", "y"], DdsTopicQos())
        backend.publish("t", {"x": "10"})
        result = backend.read("t", 10)
        assert result.samples[0].data == {"x": "10", "y": ""}

    def test_publish_unknown_field_raises(self):
        backend = MockDdsBackend()
        backend.connect()
        backend.create_topic("t", ["x"], DdsTopicQos())
        with pytest.raises(ValueError, match="Unknown field"):
            backend.publish("t", {"x": "1", "z": "bad"})

    def test_read_empty_topic(self):
        backend = MockDdsBackend()
        backend.connect()
        backend.create_topic("t", ["val"], DdsTopicQos())
        read_result = backend.read("t", 10)
        assert read_result.sample_count == 0

    def test_read_consumes_samples(self):
        backend = MockDdsBackend()
        backend.connect()
        backend.create_topic("t", ["val"], DdsTopicQos())
        backend.publish("t", {"val": "1"})
        backend.publish("t", {"val": "2"})
        first = backend.read("t", 10)
        assert first.sample_count == 2
        second = backend.read("t", 10)
        assert second.sample_count == 0

    def test_history_depth_enforcement(self):
        backend = MockDdsBackend()
        backend.connect()
        backend.create_topic("t", ["val"], DdsTopicQos(history_depth=3))
        for i in range(5):
            backend.publish("t", {"val": str(i)})
        result = backend.read("t", 10)
        assert result.sample_count == 3
        assert result.samples[0].data["val"] == "2"

    def test_publish_to_nonexistent_topic_raises(self):
        backend = MockDdsBackend()
        backend.connect()
        with pytest.raises(ValueError, match="not registered"):
            backend.publish("nope", {"val": "1"})

    def test_read_from_nonexistent_topic_raises(self):
        backend = MockDdsBackend()
        backend.connect()
        with pytest.raises(ValueError, match="not registered"):
            backend.read("nope", 10)

    def test_operations_before_connect_raise(self):
        backend = MockDdsBackend()
        with pytest.raises(RuntimeError, match="Not connected"):
            backend.create_topic("t", ["f"], DdsTopicQos())
        with pytest.raises(RuntimeError, match="Not connected"):
            backend.publish("t", {})
        with pytest.raises(RuntimeError, match="Not connected"):
            backend.read("t", 10)
        with pytest.raises(RuntimeError, match="Not connected"):
            backend.list_topics()

    def test_list_topics(self):
        backend = MockDdsBackend()
        backend.connect()
        assert backend.list_topics() == []
        backend.create_topic("a", ["x"], DdsTopicQos())
        backend.create_topic("b", ["y", "z"], DdsTopicQos())
        topics = backend.list_topics()
        assert len(topics) == 2
        names = {t.name for t in topics}
        assert names == {"a", "b"}

    def test_disconnect_clears_state(self):
        backend = MockDdsBackend()
        backend.connect()
        backend.create_topic("t", ["f"], DdsTopicQos())
        backend.publish("t", {"f": "v"})
        backend.disconnect()
        backend.connect()
        assert backend.list_topics() == []

    def test_get_participant_info(self):
        backend = MockDdsBackend()
        backend.connect()
        backend.create_topic("t1", ["a"], DdsTopicQos())
        backend.create_topic("t2", ["b"], DdsTopicQos())
        info = backend.get_participant_info()
        assert info.domain_id == 0
        assert info.topic_count == 2
        assert info.is_connected is True


# =============================================================================
# Level 2: E2E Tests with MockDds (gRPC boundary, always run)
# =============================================================================


class TestMockDdsE2E:
    """2a. Full e2e tests through gRPC with MockDds driver."""

    def test_connect_disconnect(self):
        with serve(MockDds()) as client:
            info = client.connect()
            assert info.is_connected is True
            assert info.domain_id == 0
            client.disconnect()

    def test_create_topic_and_list(self):
        with serve(MockDds()) as client:
            client.connect()
            topic = client.create_topic("sensor/temp", ["value", "unit"])
            assert topic.name == "sensor/temp"
            assert topic.fields == ["value", "unit"]

            topics = client.list_topics()
            assert len(topics) == 1
            assert topics[0].name == "sensor/temp"
            client.disconnect()

    def test_publish_and_read(self):
        with serve(MockDds()) as client:
            client.connect()
            client.create_topic("data", ["x", "y"])
            pub_result = client.publish("data", {"x": "10", "y": "20"})
            assert pub_result.samples_written == 1

            read_result = client.read("data")
            assert read_result.sample_count == 1
            assert read_result.samples[0].data["x"] == "10"
            assert read_result.samples[0].data["y"] == "20"
            client.disconnect()

    def test_publish_partial_fills_defaults(self):
        """Partial publish fills missing fields with empty string."""
        with serve(MockDds()) as client:
            client.connect()
            client.create_topic("data", ["x", "y"])
            client.publish("data", {"x": "10"})
            result = client.read("data")
            assert result.samples[0].data == {"x": "10", "y": ""}
            client.disconnect()

    def test_multiple_topics(self):
        with serve(MockDds()) as client:
            client.connect()
            client.create_topic("a", ["f1"])
            client.create_topic("b", ["f2"])
            client.publish("a", {"f1": "A"})
            client.publish("b", {"f2": "B"})

            ra = client.read("a")
            rb = client.read("b")
            assert ra.samples[0].data["f1"] == "A"
            assert rb.samples[0].data["f2"] == "B"
            client.disconnect()

    def test_custom_qos(self):
        with serve(MockDds()) as client:
            client.connect()
            topic = client.create_topic(
                "reliable_topic",
                ["val"],
                reliability="RELIABLE",
                durability="TRANSIENT_LOCAL",
                history_depth=5,
            )
            assert topic.qos.reliability == DdsReliability.RELIABLE
            assert topic.qos.durability == DdsDurability.TRANSIENT_LOCAL
            assert topic.qos.history_depth == 5
            client.disconnect()

    def test_get_participant_info(self):
        with serve(MockDds()) as client:
            client.connect()
            client.create_topic("t1", ["a"])
            info = client.get_participant_info()
            assert info.domain_id == 0
            assert info.topic_count == 1
            assert info.is_connected is True
            client.disconnect()

    def test_streaming_monitor(self):
        with serve(MockDds()) as client:
            client.connect()
            client.create_topic("stream", ["val"])
            client.publish("stream", {"val": "hello"})
            client.publish("stream", {"val": "world"})

            events = []
            for sample in client.monitor("stream"):
                events.append(sample)
                if len(events) >= 2:
                    break
            assert len(events) == 2
            assert events[0].data["val"] == "hello"
            assert events[1].data["val"] == "world"
            client.disconnect()

    def test_read_multiple_samples(self):
        with serve(MockDds()) as client:
            client.connect()
            client.create_topic("multi", ["v"])
            for i in range(5):
                client.publish("multi", {"v": str(i)})
            result = client.read("multi", max_samples=3)
            assert result.sample_count == 3
            client.disconnect()


class TestMockDdsErrorPaths:
    """2b. Error path tests through gRPC."""

    def test_operations_before_connect(self):
        with serve(MockDds()) as client:
            with pytest.raises(DriverError, match="Not connected"):
                client.create_topic("t", ["f"])
            with pytest.raises(DriverError, match="Not connected"):
                client.publish("t", {"f": "v"})
            with pytest.raises(DriverError, match="Not connected"):
                client.read("t")
            with pytest.raises(DriverError, match="Not connected"):
                client.list_topics()

    def test_double_connect(self):
        with serve(MockDds()) as client:
            client.connect()
            with pytest.raises(DriverError, match="Already connected"):
                client.connect()

    def test_disconnect_without_connect(self):
        with serve(MockDds()) as client:
            with pytest.raises(DriverError, match="Not connected"):
                client.disconnect()

    def test_publish_nonexistent_topic(self):
        with serve(MockDds()) as client:
            client.connect()
            with pytest.raises(DriverError, match="not registered"):
                client.publish("nope", {"f": "v"})

    def test_read_nonexistent_topic(self):
        with serve(MockDds()) as client:
            client.connect()
            with pytest.raises(DriverError, match="not registered"):
                client.read("nope")

    def test_duplicate_topic_creation(self):
        with serve(MockDds()) as client:
            client.connect()
            client.create_topic("t", ["f"])
            with pytest.raises(DriverError, match="already exists"):
                client.create_topic("t", ["f"])

    def test_publish_unknown_field(self):
        with serve(MockDds()) as client:
            client.connect()
            client.create_topic("t", ["x"])
            with pytest.raises(DriverError, match="Unknown field"):
                client.publish("t", {"x": "1", "z": "bad"})


class TestClientCli:
    """2c. Client CLI interface tests."""

    def test_cli_interface(self):
        with serve(MockDds()) as client:
            cli = client.cli()
            assert hasattr(cli, "commands")
            expected_commands = {
                "connect",
                "disconnect",
                "topics",
                "info",
                "create-topic",
                "publish",
                "read",
                "monitor",
            }
            assert expected_commands.issubset(set(cli.commands)), (
                f"Missing CLI commands: {expected_commands - set(cli.commands)}"
            )


class TestDdsUseMock:
    """2d. Test Dds(use_mock=True) path."""

    def test_use_mock_flag(self):
        driver = Dds(use_mock=True)
        with serve(driver) as client:
            info = client.connect()
            assert info.is_connected is True
            client.create_topic("t", ["f"])
            client.publish("t", {"f": "v"})
            result = client.read("t")
            assert result.sample_count == 1
            client.disconnect()

    def test_use_mock_custom_qos(self):
        driver = Dds(
            use_mock=True,
            default_reliability=DdsReliability.BEST_EFFORT,
            default_history_depth=5,
        )
        with serve(driver) as client:
            client.connect()
            topic = client.create_topic("t", ["v"])
            assert topic.qos.reliability == DdsReliability.BEST_EFFORT
            assert topic.qos.history_depth == 5
            client.disconnect()


# =============================================================================
# Level 2.5: Stateful Tests (DDS lifecycle enforcement)
# =============================================================================


class TestStatefulConnectionLifecycle:
    """2.5a. Connection lifecycle with stateful backend."""

    def test_stateful_connect_disconnect(self, stateful_client):
        client, backend = stateful_client
        info = client.connect()
        assert info.is_connected is True
        assert backend._connected is True

        client.disconnect()
        assert backend._connected is False

    def test_stateful_double_connect_raises(self, stateful_client):
        client, _backend = stateful_client
        client.connect()
        with pytest.raises(DriverError, match="Already connected"):
            client.connect()

    def test_stateful_disconnect_without_connect_raises(self, stateful_client):
        client, _backend = stateful_client
        with pytest.raises(DriverError, match="Not connected"):
            client.disconnect()

    def test_stateful_operations_before_connect_raise(self, stateful_client):
        client, _backend = stateful_client
        with pytest.raises(DriverError):
            client.create_topic("t", ["f"])
        with pytest.raises(DriverError):
            client.publish("t", {"f": "v"})
        with pytest.raises(DriverError):
            client.read("t")


class TestStatefulTopicManagement:
    """2.5b. Topic creation and schema enforcement."""

    def test_stateful_create_topic(self, stateful_client):
        client, backend = stateful_client
        client.connect()
        topic = client.create_topic("sensor", ["temp", "humidity"])
        assert topic.name == "sensor"
        assert topic.fields == ["temp", "humidity"]
        assert "sensor" in backend._topics

    def test_stateful_duplicate_topic_rejected(self, stateful_client):
        client, _backend = stateful_client
        client.connect()
        client.create_topic("sensor", ["temp"])
        with pytest.raises(DriverError, match="already exists"):
            client.create_topic("sensor", ["humidity"])

    def test_stateful_multiple_topics_independent(self, stateful_client):
        client, backend = stateful_client
        client.connect()
        client.create_topic("a", ["x"])
        client.create_topic("b", ["y"])
        assert len(backend._topics) == 2

        client.publish("a", {"x": "1"})
        client.publish("b", {"y": "2"})

        ra = client.read("a")
        rb = client.read("b")
        assert ra.samples[0].data["x"] == "1"
        assert rb.samples[0].data["y"] == "2"


class TestStatefulPublishSubscribe:
    """2.5c. Publish and subscribe with schema validation."""

    def test_stateful_publish_valid_fields(self, stateful_client):
        client, backend = stateful_client
        client.connect()
        client.create_topic("data", ["x", "y"])
        result = client.publish("data", {"x": "10", "y": "20"})
        assert result.samples_written == 1
        assert backend._total_published == 1

    def test_stateful_publish_partial_fills_defaults(self, stateful_client):
        """Partial publish fills missing fields with empty string."""
        client, _backend = stateful_client
        client.connect()
        client.create_topic("data", ["x", "y"])
        client.publish("data", {"x": "10"})
        result = client.read("data")
        assert result.samples[0].data == {"x": "10", "y": ""}

    def test_stateful_publish_invalid_field_rejected(self, stateful_client):
        client, _backend = stateful_client
        client.connect()
        client.create_topic("data", ["x", "y"])
        with pytest.raises(DriverError, match="Unknown field"):
            client.publish("data", {"x": "10", "z": "bad"})

    def test_stateful_read_consumes_samples(self, stateful_client):
        client, _backend = stateful_client
        client.connect()
        client.create_topic("t", ["v"])
        client.publish("t", {"v": "1"})
        client.publish("t", {"v": "2"})

        first = client.read("t")
        assert first.sample_count == 2

        second = client.read("t")
        assert second.sample_count == 0

    def test_stateful_history_depth_enforcement(self, stateful_client):
        client, _backend = stateful_client
        client.connect()
        client.create_topic("t", ["v"], history_depth=3)
        for i in range(5):
            client.publish("t", {"v": str(i)})

        result = client.read("t")
        assert result.sample_count == 3
        assert result.samples[0].data["v"] == "2"
        assert result.samples[2].data["v"] == "4"


class TestStatefulReconnect:
    """2.5d. Reconnect resets state."""

    def test_stateful_reconnect_clears_topics(self, stateful_client):
        client, backend = stateful_client
        client.connect()
        client.create_topic("t", ["f"])
        client.publish("t", {"f": "v"})
        client.disconnect()

        client.connect()
        topics = client.list_topics()
        assert len(topics) == 0
        assert backend._total_published == 0

    def test_stateful_reconnect_allows_same_topic(self, stateful_client):
        client, _backend = stateful_client
        client.connect()
        client.create_topic("t", ["f"])
        client.disconnect()

        client.connect()
        topic = client.create_topic("t", ["f"])
        assert topic.name == "t"


class TestStatefulCallLog:
    """2.5e. Call log / audit trail."""

    def test_stateful_call_log_records_operations(self, stateful_client):
        client, backend = stateful_client
        client.connect()
        client.create_topic("t", ["f"])
        client.publish("t", {"f": "v"})
        client.read("t")
        client.disconnect()
        assert backend._call_log == [
            "connect",
            "create_topic(t)",
            "publish(t)",
            "read(t)",
            "disconnect",
        ]

    def test_stateful_counters(self, stateful_client):
        client, backend = stateful_client
        client.connect()
        client.create_topic("t", ["f"])
        client.publish("t", {"f": "a"})
        client.publish("t", {"f": "b"})
        client.read("t")
        assert backend._total_published == 2
        assert backend._total_read == 2


class TestStatefulFullWorkflow:
    """2.5f. End-to-end workflow tests."""

    def test_stateful_sensor_data_workflow(self, stateful_client):
        """Simulate a typical sensor data collection workflow."""
        client, backend = stateful_client

        client.connect()
        client.create_topic("sensor/temperature", ["value", "unit", "location"])
        client.create_topic("sensor/humidity", ["value", "unit"])

        client.publish("sensor/temperature", {"value": "22.5", "unit": "C", "location": "lab1"})
        client.publish("sensor/temperature", {"value": "23.1", "unit": "C", "location": "lab2"})
        client.publish("sensor/humidity", {"value": "45", "unit": "%"})

        info = client.get_participant_info()
        assert info.topic_count == 2

        temp_data = client.read("sensor/temperature")
        assert temp_data.sample_count == 2

        humid_data = client.read("sensor/humidity")
        assert humid_data.sample_count == 1
        assert humid_data.samples[0].data["value"] == "45"

        assert backend._total_published == 3
        assert backend._total_read == 3

        client.disconnect()

    def test_stateful_high_frequency_pubsub(self, stateful_client):
        """Publish many samples and verify history depth trimming."""
        client, backend = stateful_client
        client.connect()
        client.create_topic("fast", ["seq"], history_depth=5)

        for i in range(100):
            client.publish("fast", {"seq": str(i)})

        assert backend._total_published == 100

        result = client.read("fast")
        assert result.sample_count == 5
        assert result.samples[0].data["seq"] == "95"
        assert result.samples[4].data["seq"] == "99"

        client.disconnect()

    def test_stateful_qos_combinations(self, stateful_client):
        """Test different QoS combinations on separate topics."""
        client, _backend = stateful_client
        client.connect()

        t1 = client.create_topic("reliable", ["v"], reliability="RELIABLE", durability="VOLATILE")
        assert t1.qos.reliability == DdsReliability.RELIABLE
        assert t1.qos.durability == DdsDurability.VOLATILE

        t2 = client.create_topic("best_effort", ["v"], reliability="BEST_EFFORT", durability="TRANSIENT_LOCAL")
        assert t2.qos.reliability == DdsReliability.BEST_EFFORT
        assert t2.qos.durability == DdsDurability.TRANSIENT_LOCAL

        client.disconnect()
