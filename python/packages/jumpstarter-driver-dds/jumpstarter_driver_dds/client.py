from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from typing import Any

import click

from .common import (
    DdsParticipantInfo,
    DdsPublishResult,
    DdsReadResult,
    DdsSample,
    DdsTopicInfo,
)
from jumpstarter.client import DriverClient
from jumpstarter.client.decorators import driver_click_group


@dataclass(kw_only=True)
class DdsClient(DriverClient):
    """Client interface for DDS (Data Distribution Service).

    Provides methods to manage DDS domain participation, create topics,
    publish and subscribe to data, and monitor topic streams via the
    Jumpstarter remoting layer.
    """

    def connect(self) -> DdsParticipantInfo:
        """Connect to the DDS domain and create a domain participant."""
        return DdsParticipantInfo.model_validate(self.call("connect"))

    def disconnect(self) -> None:
        """Disconnect from the DDS domain."""
        self.call("disconnect")

    def create_topic(
        self,
        name: str,
        fields: list[str],
        reliability: str | None = None,
        durability: str | None = None,
        history_depth: int | None = None,
    ) -> DdsTopicInfo:
        """Create a DDS topic with the given schema and QoS."""
        return DdsTopicInfo.model_validate(
            self.call("create_topic", name, fields, reliability, durability, history_depth)
        )

    def list_topics(self) -> list[DdsTopicInfo]:
        """List all registered topics."""
        raw = self.call("list_topics")
        if not isinstance(raw, list):
            raise TypeError(f"Expected list from list_topics(), got {type(raw).__name__}")
        return [DdsTopicInfo.model_validate(t) for t in raw]

    def publish(self, topic_name: str, data: dict[str, Any]) -> DdsPublishResult:
        """Publish a data sample to a DDS topic."""
        return DdsPublishResult.model_validate(self.call("publish", topic_name, data))

    def read(self, topic_name: str, max_samples: int = 10) -> DdsReadResult:
        """Read (take) samples from a DDS topic."""
        return DdsReadResult.model_validate(self.call("read", topic_name, max_samples))

    def get_participant_info(self) -> DdsParticipantInfo:
        """Get information about the DDS domain participant."""
        return DdsParticipantInfo.model_validate(self.call("get_participant_info"))

    def monitor(self, topic_name: str) -> Generator[DdsSample, None, None]:
        """Stream data samples from a topic as they arrive."""
        for v in self.streamingcall("monitor", topic_name):
            yield DdsSample.model_validate(v)

    def _register_lifecycle_commands(self, base):
        """Register connect, disconnect, topics, and info CLI commands."""
        @base.command(name="connect")
        def connect_cmd():
            """Connect to DDS domain"""
            info = self.connect()
            click.echo(f"Connected to DDS domain {info.domain_id}")

        @base.command(name="disconnect")
        def disconnect_cmd():
            """Disconnect from DDS domain"""
            self.disconnect()
            click.echo("Disconnected from DDS domain")

        @base.command()
        def topics():
            """List registered topics"""
            topic_list = self.list_topics()
            if not topic_list:
                click.echo("No topics registered")
                return
            for t in topic_list:
                click.echo(
                    f"  {t.name}: fields={t.fields} "
                    f"reliability={t.qos.reliability.value} "
                    f"samples={t.sample_count}"
                )

        @base.command()
        def info():
            """Show DDS participant info"""
            pinfo = self.get_participant_info()
            click.echo(f"Domain ID:    {pinfo.domain_id}")
            click.echo(f"Connected:    {pinfo.is_connected}")
            click.echo(f"Topic count:  {pinfo.topic_count}")

    def _register_data_commands(self, base):
        """Register read and monitor CLI commands."""
        @base.command(name="read")
        @click.argument("topic_name")
        @click.option("--max-samples", "-n", default=10, help="Max samples to read")
        def read_cmd(topic_name, max_samples):
            """Read samples from a topic"""
            result = self.read(topic_name, max_samples)
            click.echo(f"Read {result.sample_count} samples from {topic_name}:")
            for s in result.samples:
                click.echo(f"  {s.data}")

        @base.command(name="monitor")
        @click.argument("topic_name")
        @click.option("--count", "-n", default=10, help="Number of events")
        def monitor_cmd(topic_name, count):
            """Monitor samples from a topic"""
            for i, sample in enumerate(self.monitor(topic_name)):
                click.echo(f"[{sample.topic_name}] {sample.data}")
                if i + 1 >= count:
                    break

    def cli(self):
        """Build and return the Click command group for this driver."""
        @driver_click_group(self)
        def base():
            """DDS pub/sub communication"""
            pass

        self._register_lifecycle_commands(base)
        self._register_data_commands(base)
        return base
