import logging
from unittest.mock import MagicMock, patch

import pytest
from bumble.avrcp import (
    EventId,
    GetElementAttributesCommand,
    GetElementAttributesResponse,
    GetPlayStatusResponse,
    MediaAttributeId,
    PduId,
    PlayStatus,
    RegisterNotificationCommand,
    TrackChangedEvent,
)
from bumble.avrcp import Protocol as BumbleAvrcpProtocol

from .avrcp import PeerDelegate, PeerProtocol, Reporter, TrackMetadata, parse_metadata
from .driver import BtPeerError

# -- metadata ------------------------------------------------------------


def test_track_metadata_renders_requested_attributes():
    metadata = TrackMetadata(title="Fun Fun Music", artist="gr0za", album="Test", duration_ms=128040)
    attributes = metadata.as_media_attributes([MediaAttributeId.TITLE, MediaAttributeId.ARTIST_NAME])

    assert [(a.attribute_id, a.attribute_value) for a in attributes] == [
        (MediaAttributeId.TITLE, "Fun Fun Music"),
        (MediaAttributeId.ARTIST_NAME, "gr0za"),
    ]


def test_track_metadata_empty_request_means_everything():
    metadata = TrackMetadata(title="T", artist="A", album="B", duration_ms=1000)
    ids = [a.attribute_id for a in metadata.as_media_attributes([])]

    assert set(ids) == {
        MediaAttributeId.TITLE,
        MediaAttributeId.ARTIST_NAME,
        MediaAttributeId.ALBUM_NAME,
        MediaAttributeId.PLAYING_TIME,
    }


def test_track_metadata_omits_unset_fields():
    """An attribute with an empty value is left out rather than sent blank."""
    attributes = TrackMetadata(title="Only A Title").as_media_attributes([])

    assert [a.attribute_id for a in attributes] == [MediaAttributeId.TITLE]


def test_media_attributes_survive_bumble_encoding():
    """The response has to serialize; a bad character set id fails only here."""
    metadata = TrackMetadata(title="Fun Fun Music", artist="gr0za", album="Test", duration_ms=128040)
    response = GetElementAttributesResponse(attributes=metadata.as_media_attributes([]))

    encoded = bytes(response)
    assert b"Fun Fun Music" in encoded
    assert b"gr0za" in encoded


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"title": "T", "artist": "A", "album": "B", "duration_ms": 1500}', ("T", "A", "B", 1500)),
        ("{}", ("fallback.mp3", "", "", 0)),
        ('{"title": ""}', ("fallback.mp3", "", "", 0)),
        ('{"duration_ms": "2000"}', ("fallback.mp3", "", "", 2000)),
        ('{"duration_ms": "not a number"}', ("fallback.mp3", "", "", 0)),
        ('{"duration_ms": -5}', ("fallback.mp3", "", "", 0)),
    ],
)
def test_parse_metadata(raw, expected):
    metadata = parse_metadata(raw, default_title="fallback.mp3")
    assert (metadata.title, metadata.artist, metadata.album, metadata.duration_ms) == expected


def test_parse_metadata_rejects_malformed_json():
    with pytest.raises(BtPeerError, match="invalid metadata JSON"):
        parse_metadata("{not json", default_title="x")
    with pytest.raises(BtPeerError, match="must be a JSON object"):
        parse_metadata("[1, 2]", default_title="x")


# -- the delegate --------------------------------------------------------


def _delegate(on_key=None, now_playing=None) -> PeerDelegate:
    """A delegate whose driver callbacks are stubs unless a test cares."""
    return PeerDelegate(
        on_key=on_key or (lambda key, pressed: None),
        now_playing=now_playing or (lambda: (TrackMetadata(), 0)),
    )


@pytest.mark.anyio
async def test_delegate_reads_metadata_from_the_player():
    """The delegate holds no state of its own, so it cannot go stale."""
    now_playing = MagicMock(return_value=(TrackMetadata(title="First"), 0))
    delegate = _delegate(now_playing=now_playing)

    attributes = await delegate.get_media_attributes([MediaAttributeId.TITLE])
    assert attributes[0].attribute_value == "First"

    now_playing.return_value = (TrackMetadata(title="Second"), 0)
    attributes = await delegate.get_media_attributes([MediaAttributeId.TITLE])
    assert attributes[0].attribute_value == "Second"


@pytest.mark.anyio
async def test_play_status_is_unavailable_without_a_duration():
    """A file with no duration must not claim a length of zero."""
    delegate = _delegate()
    length, position = await delegate.get_song_length_and_position()

    assert length == GetPlayStatusResponse.UNAVAILABLE
    assert position == GetPlayStatusResponse.UNAVAILABLE


@pytest.mark.anyio
async def test_play_status_reports_length_and_position():
    delegate = _delegate(now_playing=lambda: (TrackMetadata(duration_ms=128040), 4000))

    assert await delegate.get_song_length_and_position() == (128040, 4000)


@pytest.mark.anyio
async def test_key_events_reach_the_driver():
    seen = []
    delegate = _delegate(on_key=lambda key, pressed: seen.append((key, pressed)))

    await delegate.on_key_event("PLAY", True, b"")

    assert seen == [("PLAY", True)]


# -- the protocol --------------------------------------------------------


@pytest.mark.anyio
async def test_get_element_attributes_is_dispatched():
    """bumble rejects this PDU by default; the subclass has to answer it."""
    protocol = PeerProtocol.__new__(PeerProtocol)
    protocol.delegate = _delegate(now_playing=lambda: (TrackMetadata(title="Fun Fun Music"), 0))
    protocol.receive_command_state = MagicMock(transaction_label=7)
    command = GetElementAttributesCommand(identifier=0, attribute_ids=[MediaAttributeId.TITLE])

    with (
        patch.object(PeerProtocol, "send_avrcp_response") as send,
        patch.object(PeerProtocol, "_delegate_command") as delegate_command,
    ):
        protocol._on_command_pdu(PduId.GET_ELEMENT_ATTRIBUTES, bytes(command))
        # Await the coroutine the dispatcher would have spawned.
        await delegate_command.call_args.args[2]

    assert protocol.receive_command_state is None
    label, _code, response = send.call_args.args
    assert label == 7
    assert [a.attribute_value for a in response.attributes] == ["Fun Fun Music"]


@pytest.mark.anyio
async def test_other_pdus_still_reach_the_base_class():
    protocol = PeerProtocol.__new__(PeerProtocol)
    protocol.receive_command_state = MagicMock(transaction_label=1)

    with patch.object(BumbleAvrcpProtocol, "_on_command_pdu") as base:
        protocol._on_command_pdu(PduId.GET_PLAY_STATUS, b"")

    base.assert_called_once()


# -- the reporter --------------------------------------------------------


def _reporter(protocol=None):
    delegate = _delegate()
    return Reporter(delegate, protocol or MagicMock(), logging.getLogger("test")), delegate


def test_reporter_sets_the_status_the_delegate_answers_with():
    """GET_PLAY_STATUS is answered from the delegate, notifications or not."""
    reporter, delegate = _reporter()

    reporter.report(PlayStatus.PLAYING)

    assert delegate.playback_status == PlayStatus.PLAYING


def test_reporter_notifies_a_track_change_only_when_asked():
    protocol = MagicMock()
    reporter, _delegate = _reporter(protocol)

    reporter.report(PlayStatus.PLAYING, track_changed=True)
    protocol.notify_track_changed.assert_called_once()
    protocol.notify_playback_status_changed.assert_called_once_with(PlayStatus.PLAYING)

    reporter.report(PlayStatus.PAUSED)
    protocol.notify_track_changed.assert_called_once()


def test_a_failed_notification_is_not_fatal():
    """A controller that never registered must not fail the call behind it."""
    protocol = MagicMock()
    protocol.notify_playback_status_changed.side_effect = RuntimeError("no listener")
    protocol.notify_track_changed.side_effect = RuntimeError("no listener")
    reporter, delegate = _reporter(protocol)

    reporter.report(PlayStatus.PLAYING, track_changed=True)

    assert delegate.playback_status == PlayStatus.PLAYING


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "uid"),
    [(PlayStatus.PLAYING, 0), (PlayStatus.PAUSED, 0), (PlayStatus.STOPPED, TrackChangedEvent.NO_TRACK)],
)
async def test_a_track_changed_registration_is_answered_with_the_selected_track(status, uid):
    """A controller registers again after every notification, so this is the answer it keeps getting."""
    reporter, delegate = _reporter()
    reporter.report(status, track_changed=True)
    protocol = PeerProtocol.__new__(PeerProtocol)
    protocol.delegate = delegate
    command = RegisterNotificationCommand(event_id=EventId.TRACK_CHANGED, playback_interval=0)

    with (
        patch.object(PeerProtocol, "send_avrcp_response") as send,
        patch.object(PeerProtocol, "_delegate_command") as delegate_command,
        patch.object(PeerProtocol, "_register_notification_listener"),
    ):
        protocol._on_register_notification_command(7, command)
        # Await the coroutine the dispatcher would have spawned.
        await delegate_command.call_args.args[2]

    _label, _code, response = send.call_args.args
    assert response.event.uid == uid
