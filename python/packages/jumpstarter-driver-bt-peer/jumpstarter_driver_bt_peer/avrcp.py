"""The AVRCP target: what the DUT displays and the keys it sends back.

The delegate never stores playback state of its own. It asks the player what
is loaded, so the metadata the DUT reads and the status the driver reports
cannot describe different tracks.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from bumble import avc
from bumble.avrcp import CharacterSetId as AvrcpCharacterSetId
from bumble.avrcp import (
    Command,
    EventId,
    GetElementAttributesCommand,
    GetElementAttributesResponse,
    GetPlayStatusResponse,
    MediaAttribute,
    MediaAttributeId,
    PduId,
    PlayStatus,
)
from bumble.avrcp import Delegate as BumbleAvrcpDelegate
from bumble.avrcp import Protocol as BumbleAvrcpProtocol

from .errors import BtPeerError


@dataclass(frozen=True)
class TrackMetadata:
    """What the DUT displays for the current track."""

    title: str = ""
    artist: str = ""
    album: str = ""
    duration_ms: int = 0

    def as_media_attributes(self, attribute_ids) -> list[MediaAttribute]:
        """Render as AVRCP media attributes, omitting the ones left empty.

        An empty request means "every attribute", per AVRCP.
        """
        values = {
            MediaAttributeId.TITLE: self.title,
            MediaAttributeId.ARTIST_NAME: self.artist,
            MediaAttributeId.ALBUM_NAME: self.album,
            MediaAttributeId.PLAYING_TIME: str(self.duration_ms) if self.duration_ms else "",
        }
        wanted = list(attribute_ids) or list(values)
        return [
            MediaAttribute(
                attribute_id=attribute_id,
                character_set_id=AvrcpCharacterSetId.UTF_8,
                attribute_value=values[attribute_id],
            )
            for attribute_id in wanted
            if values.get(attribute_id)
        ]

    def as_dict(self) -> dict[str, object]:
        return {
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "duration_ms": self.duration_ms,
        }


def _nothing_playing() -> tuple[TrackMetadata, int]:
    return TrackMetadata(), 0


class PeerDelegate(BumbleAvrcpDelegate):
    """Reports the peer's transport state to the DUT's AVRCP controller.

    Without an AVRCP target the DUT sees an A2DP source that never claims to
    be playing, so it leaves the sink inactive and renders nothing even though
    packets are arriving. Key presses are forwarded to `on_key` so the head
    unit's transport controls reach the driver.

    `now_playing` returns the current metadata and position together, in one
    call, so the two cannot come from different tracks.
    """

    def __init__(
        self,
        on_key: Callable[[object, bool], None] | None = None,
        now_playing: Callable[[], tuple[TrackMetadata, int]] | None = None,
    ):
        super().__init__(
            supported_events=[
                EventId.PLAYBACK_STATUS_CHANGED,
                EventId.TRACK_CHANGED,
            ]
        )
        self._on_key = on_key
        self._now_playing = now_playing or _nothing_playing

    async def on_key_event(self, key, pressed: bool, data: bytes) -> None:
        if self._on_key is not None:
            self._on_key(key, pressed)

    async def get_media_attributes(self, attribute_ids) -> list[MediaAttribute]:
        metadata, _position = self._now_playing()
        return metadata.as_media_attributes(attribute_ids)

    async def get_song_length_and_position(self) -> tuple[int, int]:
        metadata, position = self._now_playing()
        if not metadata.duration_ms:
            return (GetPlayStatusResponse.UNAVAILABLE, GetPlayStatusResponse.UNAVAILABLE)
        return (metadata.duration_ms, position)


class PeerProtocol(BumbleAvrcpProtocol):
    """Adds the target commands bumble leaves unimplemented.

    bumble answers GET_PLAY_STATUS with "length unavailable" and rejects
    GET_ELEMENT_ATTRIBUTES outright, so the DUT sees a player with no name,
    no track and no duration. Android shows that as an unidentified session.
    """

    @property
    def _target_delegate(self) -> PeerDelegate:
        # Narrowing `delegate` with an annotation would be an unsound override,
        # since the base class accepts any Delegate.
        return cast(PeerDelegate, self.delegate)

    def _on_command_pdu(self, pdu_id: PduId, pdu: bytes) -> None:
        if pdu_id != PduId.GET_ELEMENT_ATTRIBUTES or self.receive_command_state is None:
            super()._on_command_pdu(pdu_id, pdu)
            return

        transaction_label = self.receive_command_state.transaction_label
        command = Command.from_bytes(pdu_id, pdu)
        assert isinstance(command, GetElementAttributesCommand)

        async def get_media_attributes() -> None:
            attributes = await self._target_delegate.get_media_attributes(command.attribute_ids)
            self.send_avrcp_response(
                transaction_label,
                avc.ResponseFrame.ResponseCode.IMPLEMENTED_OR_STABLE,
                GetElementAttributesResponse(attributes=attributes),
            )

        self._delegate_command(transaction_label, command, get_media_attributes())
        self.receive_command_state = None

    def _on_get_play_status_command(self, transaction_label: int, command) -> None:
        async def get_play_status() -> None:
            play_status = await self.delegate.get_playback_status()
            song_length, song_position = await self._target_delegate.get_song_length_and_position()
            self.send_avrcp_response(
                transaction_label,
                avc.ResponseFrame.ResponseCode.IMPLEMENTED_OR_STABLE,
                GetPlayStatusResponse(
                    song_length=song_length,
                    song_position=song_position,
                    play_status=play_status,
                ),
            )

        self._delegate_command(transaction_label, command, get_play_status())


class Reporter:
    """Pushes the player's state out to the DUT's AVRCP controller.

    Only `Player` drives this, so what the head unit displays is always the
    state the driver believes it is in.
    """

    def __init__(self, delegate: PeerDelegate, protocol: BumbleAvrcpProtocol | None, logger):
        self._delegate = delegate
        self._protocol = protocol
        self._logger = logger

    def report(self, status: PlayStatus, track_changed: bool = False) -> None:
        # The delegate answers GET_PLAY_STATUS from this, so it is set even
        # when there is no controller registered for notifications.
        self._delegate.playback_status = status
        if self._protocol is None:
            return
        if track_changed:
            self._notify("track change", self._protocol.notify_track_changed, 0)
        self._notify("playback status", self._protocol.notify_playback_status_changed, status)

    def _notify(self, what: str, notify, argument) -> None:
        try:
            notify(argument)
        except Exception as exc:  # noqa: BLE001
            # Only a notification: a controller that never registered, or a
            # dropped AVCTP channel, must not fail the call that triggered it.
            self._logger.debug("could not notify %s: %s", what, exc)


def parse_metadata(metadata_json: str, default_title: str) -> TrackMetadata:
    """Build track metadata from the client, falling back to the file name."""
    try:
        raw = json.loads(metadata_json) if metadata_json else {}
    except json.JSONDecodeError as exc:
        raise BtPeerError(f"invalid metadata JSON: {exc.msg}") from exc
    if not isinstance(raw, dict):
        raise BtPeerError(f"metadata must be a JSON object, got {type(raw).__name__}")

    try:
        duration_ms = int(raw.get("duration_ms") or 0)
    except (TypeError, ValueError):
        duration_ms = 0

    return TrackMetadata(
        title=str(raw.get("title") or default_title),
        artist=str(raw.get("artist") or ""),
        album=str(raw.get("album") or ""),
        duration_ms=max(duration_ms, 0),
    )
