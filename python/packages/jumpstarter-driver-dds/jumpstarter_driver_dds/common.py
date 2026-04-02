from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class DdsReliability(str, Enum):
    """DDS reliability QoS."""

    BEST_EFFORT = "BEST_EFFORT"
    RELIABLE = "RELIABLE"


class DdsDurability(str, Enum):
    """DDS durability QoS."""

    VOLATILE = "VOLATILE"
    TRANSIENT_LOCAL = "TRANSIENT_LOCAL"
    TRANSIENT = "TRANSIENT"
    PERSISTENT = "PERSISTENT"


class DdsTopicQos(BaseModel):
    """Quality of Service settings for a DDS topic."""

    reliability: DdsReliability = DdsReliability.RELIABLE
    durability: DdsDurability = DdsDurability.VOLATILE
    history_depth: int = Field(10, ge=1)


class DdsParticipantInfo(BaseModel):
    """Information about the DDS domain participant."""

    domain_id: int
    topic_count: int = 0
    is_connected: bool = False


class DdsTopicInfo(BaseModel):
    """Information about a registered DDS topic."""

    name: str
    fields: list[str] = []
    qos: DdsTopicQos = DdsTopicQos()
    sample_count: int = 0


class DdsSample(BaseModel):
    """A single DDS data sample."""

    topic_name: str
    data: dict[str, Any]
    timestamp: float = 0.0


class DdsPublishResult(BaseModel):
    """Result of a publish operation.

    Publish failures always raise exceptions; this model is only
    returned on success.
    """

    topic_name: str
    samples_written: int = 0


class DdsReadResult(BaseModel):
    """Result of a read/take operation."""

    topic_name: str
    samples: list[DdsSample] = []
    sample_count: int = 0

    @model_validator(mode="after")
    def _validate_sample_count(self) -> DdsReadResult:
        """Ensure sample_count matches the actual number of samples."""
        if self.sample_count != len(self.samples):
            raise ValueError(f"sample_count ({self.sample_count}) does not match len(samples) ({len(self.samples)})")
        return self
