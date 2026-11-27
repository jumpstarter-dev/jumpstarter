"""One encode, many viewers: what each is promised (see the hub's docstring). Pure code, a clock of the test's own.

Nothing here sleeps: time is a clock the test advances, and the hub's callbacks are lists the test reads.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import replace

import pytest

from ..common import KIND_CONFIG, KIND_MEDIA, CodecLine, FrameParser, split_media
from ..testing.pattern import pattern_units
from . import hub as hub_module
from .codecs import codec_string_of, iter_nals
from .hub import LAG_GRACE_S, LAG_HARD_S, LAG_SOFT_S, STALL_OVERFLOWS, Hub, StreamEnded

START = b"\x00\x00\x00\x01"
SPS = START + bytes(
    [0x67, 0x64, 0x00, 0x1F, 0xAC, 0xD9, 0x40, 0x50, 0x05, 0xBB, 0x01, 0x10, 0x00, 0x00, 0x03, 0x00, 0x10, 0x80]
)
PPS = START + bytes([0x68, 0xEB, 0xE3, 0xCB, 0x22, 0xC0])
PARAMS = SPS + PPS
OTHER_PARAMS = (
    START + bytes([0x67, 0x42, 0xC0, 0x1E, 0xDA, 0x05, 0x82, 0x4C, 0x80]) + START + bytes([0x68, 0xCE, 0x3C, 0x80])
)
LINE = CodecLine("avc1.64001F", 540, 1200, 30)
OTHER_LINE = CodecLine("avc1.64001F", 1200, 540, 30)  # another coded size: a new SPS
ROTATED = replace(LINE, rotation=90, point_scale=2)


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def idr_unit(n, params=PARAMS, pad=100):
    return params + START + b"\x65\x88\x84" + f"K{n:08d}".encode() + b"\xaf" * pad


def delta_unit(n, ref=True, pad=20):
    return START + (b"\x41\x9a" if ref else b"\x01\x9a") + f"D{n:08d}".encode() + b"\x11" * pad


class Rig:
    """A hub on a clock of its own, the key pictures it asked its source for, and a way to publish numbered units."""

    def __init__(self, cost="cheap", **options):
        self.clock = Clock()
        self.asked = []
        self.congested = []
        self.stalled = []
        options.setdefault("on_need_idr", lambda: self.asked.append(self.clock()))
        options.setdefault("on_congestion", self.congested.append)
        options.setdefault("on_stalled", self.stalled.append)
        self.hub = Hub(keyframe_cost=cost, clock=self.clock, **options)
        self.n = 0

    def config(self, line=LINE):
        return self.hub.publish_config(line)

    def idr(self, params=None, pad=100):
        """A key unit with its parameter sets in front (``params``: ``b""`` for none); returns its number."""
        self.n += 1
        self.hub.publish_media(self.n * 1000, idr_unit(self.n, PARAMS if params is None else params, pad))
        return self.n

    def delta(self, count=1, *, ref=True, pad=20):
        for _ in range(count):
            self.n += 1
            self.hub.publish_media(self.n * 1000, delta_unit(self.n, ref, pad))
        return self.n


def drain(viewer):
    out = []
    while (frame := viewer.pop()) is not None:
        out.append(frame)
    return out


def unit_of(frame):
    """The access unit inside a media frame's wire bytes."""
    kind, body = FrameParser().feed(frame.wire)[0]
    assert kind == KIND_MEDIA
    return split_media(body)[1]


def line_of(frame):
    kind, body = FrameParser().feed(frame.wire)[0]
    assert kind == KIND_CONFIG
    return json.loads(body)


def describe(frames):
    """``config`` for a codec line; ``idr:N``, ``delta:N`` or ``disposable:N`` for a picture (N: its number)."""
    out = []
    for frame in frames:
        if frame.kind == KIND_CONFIG:
            out.append("config")
        else:
            found = re.search(rb"[KD](\d{8})", unit_of(frame))
            assert found is not None
            number = int(found.group(1))
            out.append(f"{'idr' if frame.idr else 'delta' if frame.reference else 'disposable'}:{number}")
    return out


# ---- a joiner


@pytest.mark.parametrize("cost", ["cheap", "restart", "none"])
def test_a_joining_viewer_starts_at_once_from_the_cached_gop_and_the_source_is_not_asked(cost):
    rig = Rig(cost)
    rig.config()
    rig.idr()
    rig.delta(3)
    rig.clock.advance(0.5)
    viewer = rig.hub.add_viewer()
    # the line, then a picture it can start from
    assert describe(drain(viewer)) == ["config", "idr:1", "delta:2", "delta:3", "delta:4"]
    rig.delta()
    assert describe(drain(viewer)) == ["delta:5"]  # and the live stream goes on from there
    assert rig.asked == [] and rig.hub.joins_replayed == 1 and rig.hub.joins_waited == 0
    assert viewer.waiting_idr is False


def test_the_first_unit_a_joiner_gets_is_a_key_unit_with_the_parameter_sets_in_front():
    rig = Rig()
    rig.config()
    rig.idr()
    rig.delta()
    frames = drain(rig.hub.add_viewer())
    assert frames[1].idr and unit_of(frames[1]).startswith(PARAMS)


@pytest.mark.parametrize("cost", ["cheap", "restart", "none"])
def test_a_viewer_at_the_very_start_waits_for_the_first_idr_without_asking_for_one(cost):
    rig = Rig(cost)
    viewer = rig.hub.add_viewer()
    rig.config()
    rig.delta()  # before any IDR: nothing a decoder could use (the hub itself ignores it)
    rig.idr()
    assert describe(drain(viewer)) == ["config", "idr:2"]
    assert rig.asked == [] and rig.hub.keyframe_requests == 0


@pytest.mark.parametrize("cost", ["restart", "none"])
def test_a_gop_too_large_to_replay_makes_the_joiner_wait_and_the_source_is_asked(cost):
    rig = Rig(cost, join_replay_max_bytes=500)
    rig.config()
    rig.idr(pad=600)
    viewer = rig.hub.add_viewer()
    assert describe(drain(viewer)) == ["config"] and len(rig.asked) == 1 and rig.hub.joins_waited == 1
    rig.delta()
    assert drain(viewer) == []  # nothing until an IDR
    rig.idr()
    assert describe(drain(viewer)) == ["idr:3"]


def test_a_gop_too_large_to_replay_makes_a_cheap_source_joiner_wait_however_recent_it_is():
    rig = Rig(join_replay_max_bytes=500)
    rig.config()
    rig.idr()
    rig.delta(20)  # the group is now over 500 bytes
    viewer = rig.hub.add_viewer()
    assert describe(drain(viewer)) == ["config"] and viewer.waiting_idr
    assert len(rig.asked) == 1 and rig.hub.gop()["bytes"] > 500


def test_a_gop_that_grows_past_the_most_the_hub_keeps_is_dropped_and_joiners_wait():
    rig = Rig(max_gop_bytes=600, join_replay_max_bytes=10_000)
    rig.config()
    rig.idr()
    rig.delta(20)
    assert rig.hub.gop() == {"frames": 0, "bytes": 0, "age_s": None, "parameter_set_bytes": len(PARAMS)}
    viewer = rig.hub.add_viewer()
    assert viewer.waiting_idr and len(rig.asked) == 1
    rig.idr()
    assert describe(drain(viewer))[-1] == "idr:22"


@pytest.mark.parametrize("cost", ["cheap", "restart"])
def test_a_joiner_that_waited_join_wait_s_for_an_idr_is_given_the_large_gop_anyway(cost):
    rig = Rig(cost, join_replay_max_bytes=100, join_wait_s=1.0)
    rig.config()
    rig.idr(pad=200)
    rig.delta()
    viewer = rig.hub.add_viewer()
    assert describe(drain(viewer)) == ["config"] and rig.hub.joins_waited == 1
    rig.clock.advance(0.5)
    rig.delta()
    assert drain(viewer) == []  # not yet
    rig.clock.advance(0.7)
    rig.delta()  # the next picture of the stream serves it
    assert describe(drain(viewer)) == ["idr:1", "delta:2", "delta:3", "delta:4"] and rig.hub.joins_forced == 1
    rig.delta()
    assert describe(drain(viewer)) == ["delta:5"]  # and it is a live viewer from there


def test_a_cheap_joiner_that_finds_the_key_unit_old_waits_for_a_new_one_and_is_served_the_old_group_if_none_comes():
    rig = Rig(join_replay_max_age_s=1.5, join_wait_s=1.0, keyframe_min_interval_s=2.0)
    rig.config()
    rig.idr()
    rig.delta(2)
    rig.clock.advance(2.0)
    viewer = rig.hub.add_viewer()
    assert describe(drain(viewer)) == ["config"] and len(rig.asked) == 1  # waits, and a fresh one is asked for
    rig.clock.advance(1.1)  # the source never answered: after join_wait_s the old group is better than a blank picture
    assert rig.hub.release_waiting_joins() == 1
    assert describe(drain(viewer)) == ["idr:1", "delta:2", "delta:3"] and rig.hub.joins_forced == 1


def test_a_hmi_that_sends_nothing_serves_the_waiting_joiner_from_the_watchdogs_tick():
    rig = Rig("restart", join_replay_max_bytes=100, join_wait_s=1.0)
    rig.config()
    rig.idr(pad=200)
    viewer = rig.hub.add_viewer()
    drain(viewer)
    assert rig.hub.release_waiting_joins() == 0
    rig.clock.advance(1.5)
    assert rig.hub.release_waiting_joins() == 1 and describe(drain(viewer)) == ["idr:1"]
    assert rig.hub.release_waiting_joins() == 0  # once


def test_a_natural_idr_before_the_wait_is_over_serves_the_joiner_and_nothing_is_replayed_after_it():
    rig = Rig("restart", join_replay_max_bytes=100, join_wait_s=1.0)
    rig.config()
    rig.idr(pad=200)
    viewer = rig.hub.add_viewer()
    drain(viewer)
    rig.clock.advance(0.4)
    rig.idr()
    assert describe(drain(viewer)) == ["idr:2"]
    rig.clock.advance(2.6)
    rig.delta()
    assert describe(drain(viewer)) == ["delta:3"] and rig.hub.joins_forced == 0


def test_with_join_wait_s_zero_the_large_gop_is_replayed_at_the_next_picture():
    rig = Rig("restart", join_replay_max_bytes=100, join_wait_s=0.0)
    rig.config()
    rig.idr(pad=200)
    viewer = rig.hub.add_viewer()
    drain(viewer)
    rig.delta()
    assert describe(drain(viewer)) == ["idr:1", "delta:2"]


def test_the_cached_gop_starts_again_at_every_idr():
    rig = Rig()
    rig.config()
    rig.idr()
    rig.delta()
    rig.idr()
    rig.delta()
    assert rig.hub.gop()["frames"] == 2
    assert [f.ts_us for f in drain(rig.hub.add_viewer()) if f.kind == KIND_MEDIA] == [3000, 4000]


# ---- age-gated join (a source that can be asked cheaply)


def test_a_cheap_joiner_finding_an_old_key_unit_waits_for_a_new_one_and_one_is_asked_for():
    rig = Rig(join_replay_max_age_s=1.5)
    rig.config()
    rig.idr()
    rig.delta(5)
    rig.clock.advance(1.6)  # the group is older than a joiner should be replayed
    rig.delta()
    viewer = rig.hub.add_viewer()
    assert describe(drain(viewer)) == ["config"]
    assert rig.asked == [rig.clock()] and viewer.waiting_idr
    rig.delta(2)  # deltas it cannot decode are skipped, not queued
    assert drain(viewer) == [] and viewer.dropped_in_resync == 2
    rig.idr()
    rig.delta()
    assert describe(drain(viewer)) == ["idr:10", "delta:11"]
    assert rig.hub.joins_waited == 1 and rig.hub.joins_replayed == 0


def test_the_age_is_counted_from_the_key_unit_not_from_the_last_picture():
    rig = Rig(join_replay_max_age_s=1.5)
    rig.config()
    rig.idr()
    for _ in range(4):  # a stream that goes on: the group grows, the key unit gets older
        rig.clock.advance(0.5)
        rig.delta()
    viewer = rig.hub.add_viewer()  # 2.0 s since the key unit
    assert viewer.waiting_idr and len(rig.asked) == 1


def test_a_group_exactly_as_old_as_the_limit_is_still_replayed():
    rig = Rig(join_replay_max_age_s=1.5)
    rig.config()
    rig.idr()
    rig.delta()
    rig.clock.advance(1.5)
    viewer = rig.hub.add_viewer()
    assert not viewer.waiting_idr and rig.asked == []
    assert rig.hub.gop()["age_s"] == 1.5


@pytest.mark.parametrize("cost", ["restart", "none"])
def test_a_source_that_cannot_be_asked_cheaply_is_replayed_the_old_group_whatever_its_age(cost):
    rig = Rig(cost, join_replay_max_age_s=1.5)
    rig.config()
    rig.idr()
    rig.delta()
    # a still picture: the next key unit is far off, and the group is still what the viewer shows
    rig.clock.advance(30.0)
    viewer = rig.hub.add_viewer()
    assert describe(drain(viewer)) == ["config", "idr:1", "delta:2"] and rig.asked == []


def test_the_default_age_limit_is_one_and_a_half_seconds_and_the_byte_limit_three_megabytes():
    hub = Hub()
    assert hub.join_replay_max_age_s == 1.5 and hub.join_replay_max_bytes == 3_000_000 and hub.keyframe_cost == "none"


def test_the_replay_a_joiner_is_given_may_be_made_of_many_units():
    rig = Rig(max_queue_bytes=10_000_000)
    rig.config()
    rig.idr()
    rig.delta(20)
    viewer = rig.hub.add_viewer()
    assert viewer.waiting_idr is False and viewer.resyncs == 0
    rig.delta(3)
    assert len(describe(drain(viewer))) == 1 + 21 + 3


# ---- the codec line


def test_each_viewers_codec_line_carries_its_own_id_so_that_a_client_can_report_by_it():
    rig = Rig()
    rig.config()
    first, second = rig.hub.add_viewer(), rig.hub.add_viewer()
    one, two = line_of(first.pop()), line_of(second.pop())
    assert one["viewer"] == first.id and two["viewer"] == second.id and one["viewer"] != two["viewer"]
    assert one["codec"] == "avc1.64001F" and one["width"] == 540 and one["v"] == 1
    rig.config(OTHER_LINE)  # a new line: each viewer has its own id again
    assert line_of(first.pop())["viewer"] == first.id and line_of(second.pop())["viewer"] == second.id


def test_the_codec_line_that_was_published_before_a_viewer_joined_comes_first():
    rig = Rig()
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.idr()
    assert describe(drain(viewer)) == ["config", "idr:1"]


def test_a_config_that_did_not_change_is_not_sent_again_and_one_that_did_is_sent_to_every_viewer():
    rig = Rig()
    assert rig.config() is True
    viewer = rig.hub.add_viewer()
    rig.idr()
    drain(viewer)
    assert rig.config() is False and drain(viewer) == []
    assert rig.config(replace(LINE)) is False  # an equal line, however it was built, is nothing
    assert rig.config(OTHER_LINE) is True
    assert describe(drain(viewer)) == ["config"]


def test_a_new_picture_size_drops_the_cached_group_and_every_viewer_waits_for_the_key_unit_that_carries_it():
    rig = Rig()
    rig.config()
    first = rig.hub.add_viewer()
    rig.idr()
    rig.delta(2)
    drain(first)
    rig.config(OTHER_LINE)
    assert rig.hub.gop()["frames"] == 0
    rig.delta()  # no start for the new picture: nothing is queued
    assert first.waiting_idr and describe(drain(first)) == ["config"]
    late = rig.hub.add_viewer()  # a joiner now has no group to start from
    assert late.waiting_idr
    rig.idr(params=OTHER_PARAMS)
    assert describe(drain(first)) == ["idr:5"] and describe(drain(late)) == ["config", "idr:5"]
    assert not first.waiting_idr and not late.waiting_idr


def test_a_change_of_rotation_alone_is_a_change_and_every_viewer_gets_the_new_line():
    rig = Rig()
    assert rig.config(ROTATED) is True
    first, second = rig.hub.add_viewer(), rig.hub.add_viewer()
    one, two = line_of(first.pop()), line_of(second.pop())
    assert one["rotation"] == 90 and two["point_scale"] == 2
    assert rig.config(ROTATED) is False  # the same line again is nothing
    assert rig.config(replace(ROTATED, rotation=270)) is True
    assert line_of(first.pop())["rotation"] == 270 and line_of(second.pop())["rotation"] == 270
    assert rig.config(replace(ROTATED, rotation=270, point_scale=3)) is True  # so is the point scale alone
    assert line_of(first.pop())["point_scale"] == 3
    assert rig.config(LINE) is True  # and leaving them out: the fields go
    assert "rotation" not in line_of(first.pop())


@pytest.mark.parametrize("cost", ["cheap", "restart", "none"])
def test_a_change_of_rotation_does_not_stop_the_picture_or_drop_the_group_and_no_key_unit_is_asked_for(cost):
    rig = Rig(cost)
    rig.config(ROTATED)
    viewer = rig.hub.add_viewer()
    rig.idr()
    rig.delta(2)
    drain(viewer)
    group_before = rig.hub.gop()["frames"]
    rig.config(replace(ROTATED, rotation=180))  # the SPS did not change: the source sends no key unit for this
    assert viewer.waiting_idr is False
    rig.delta(2)
    assert describe(drain(viewer)) == ["config", "delta:4", "delta:5"]  # the new line, and the units go on from it
    assert rig.hub.gop()["frames"] == group_before + 2 and rig.asked == [] and rig.hub.keyframe_requests == 0


@pytest.mark.parametrize("change", [{"fps": 60}, {"point_scale": 3}, {"input": "stream"}, {"rotation": 270}])
def test_only_a_change_of_codec_or_coded_size_holds_the_viewers_anything_else_is_just_a_new_line(change):
    rig = Rig()
    rig.config(LINE)
    viewer = rig.hub.add_viewer()
    rig.idr()
    drain(viewer)
    assert rig.config(replace(LINE, **change)) is True
    rig.delta()
    assert describe(drain(viewer)) == ["config", "delta:2"] and not viewer.waiting_idr
    assert rig.hub.gop()["frames"] == 2


def test_a_change_of_the_visible_size_with_the_same_coded_size_leaves_the_decode_alone_too():
    rig = Rig()
    rig.config(replace(LINE, coded_width=544, coded_height=1216))
    viewer = rig.hub.add_viewer()
    rig.idr()
    drain(viewer)
    # the same SPS, a different crop
    rig.config(replace(LINE, width=500, height=1100, coded_width=544, coded_height=1216))
    rig.delta()
    assert describe(drain(viewer)) == ["config", "delta:2"] and not viewer.waiting_idr


def test_a_change_of_the_codec_string_restarts_the_decode_like_a_new_size_does():
    rig = Rig()
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.idr()
    drain(viewer)
    rig.config(replace(LINE, codec="avc1.640028"))
    rig.delta()
    assert viewer.waiting_idr and describe(drain(viewer)) == ["config"] and rig.hub.gop()["frames"] == 0


def test_a_change_of_the_coded_size_still_waits_for_the_key_unit_that_carries_the_new_parameter_sets():
    rig = Rig()
    rig.config(ROTATED)
    viewer = rig.hub.add_viewer()
    rig.idr()
    drain(viewer)
    rig.config(replace(ROTATED, coded_width=1232, rotation=0))
    rig.delta()
    assert viewer.waiting_idr and describe(drain(viewer)) == ["config"] and rig.hub.gop()["frames"] == 0
    assert rig.asked == []  # the new key unit is on its way with the new picture: not asked for


def test_a_joiner_is_given_the_newest_line_and_the_group_it_can_start_from():
    rig = Rig()
    rig.config(ROTATED)
    rig.idr()
    rig.delta(2)
    rig.config(replace(ROTATED, rotation=270))
    rig.delta()
    viewer = rig.hub.add_viewer()
    frames = drain(viewer)
    assert line_of(frames[0])["rotation"] == 270 and line_of(frames[0])["viewer"] == viewer.id
    assert describe(frames) == ["config", "idr:1", "delta:2", "delta:3", "delta:4"]  # the group was kept


def test_a_closed_hub_takes_no_new_line():
    rig = Rig()
    rig.hub.close()
    assert rig.config() is False and rig.hub.line is None


# ---- the stream and its time


def test_the_frames_are_the_viewers_wire_format_with_the_timestamp_the_source_gave():
    rig = Rig()
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.idr()
    rig.delta()
    frames = drain(viewer)
    parsed = FrameParser().feed(b"".join(f.wire for f in frames))
    assert [kind for kind, _ in parsed] == [KIND_CONFIG, KIND_MEDIA, KIND_MEDIA]
    assert [split_media(body)[0] for kind, body in parsed if kind == KIND_MEDIA] == [1000, 2000]
    assert frames[1].ts_us == 1000


def test_timestamps_are_the_sources_own_microseconds_and_the_hub_adds_nothing_to_them():
    rig = Rig()
    rig.config()
    viewer = rig.hub.add_viewer()
    since_boot_us = 987_654_321_012  # whatever monotonic clock the source has: not the epoch, and not this hub's clock
    stamps = [since_boot_us, since_boot_us + 33_333, since_boot_us + 66_667]
    for i, ts in enumerate(stamps):
        rig.hub.publish_media(ts, idr_unit(i) if i == 0 else delta_unit(i))
    frames = [f for f in drain(viewer) if f.kind == KIND_MEDIA]
    assert [f.ts_us for f in frames] == stamps
    assert [split_media(FrameParser().feed(f.wire)[0][1])[0] for f in frames] == stamps
    replayed = [f.ts_us for f in drain(rig.hub.add_viewer()) if f.kind == KIND_MEDIA]
    assert replayed == stamps  # a replay carries them as made


def test_a_repaired_key_unit_keeps_the_timestamp_it_was_given():
    rig = Rig()
    rig.config()
    rig.idr()
    viewer = rig.hub.add_viewer()
    drain(viewer)
    rig.hub.publish_media(777_000_123, idr_unit(9, params=b""))
    (frame,) = drain(viewer)
    assert frame.ts_us == 777_000_123 and split_media(FrameParser().feed(frame.wire)[0][1])[0] == 777_000_123


def test_the_hub_never_reads_the_wall_clock(monkeypatch):
    def forbidden(*_a, **_k):
        raise AssertionError("the hub read the wall clock")

    monkeypatch.setattr(time, "time", forbidden)
    monkeypatch.setattr(time, "time_ns", forbidden)
    hub = Hub(keyframe_cost="cheap")  # the default clock is the monotonic one
    hub.publish_config(LINE)
    viewer = hub.add_viewer()
    hub.publish_media(5, idr_unit(1))
    hub.publish_media(6, delta_unit(2))
    hub.report_lag(viewer.id, 0.1)
    hub.measure()
    hub.stats()
    hub.poll()
    assert hub.line == LINE and len(drain(viewer)) == 3


def test_the_hubs_own_clock_is_the_monotonic_one_unless_told_otherwise():
    assert Hub()._clock is time.monotonic


def test_a_viewer_is_told_when_there_is_something_to_read():
    woken = []
    rig = Rig()
    rig.config()
    viewer = rig.hub.add_viewer(notify=lambda: woken.append(1))
    rig.idr()
    assert woken
    count = len(woken)
    rig.hub.remove_viewer(viewer)
    assert len(woken) == count + 1  # closing wakes it too


def test_closing_the_hub_ends_every_viewer_after_what_it_has_queued_and_takes_no_new_one():
    rig = Rig()
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.idr()
    rig.hub.close()
    assert viewer.closed and viewer.close_reason == "ended"
    assert describe(drain(viewer)) == ["config", "idr:1"]  # what was queued can still be read
    rig.delta()  # nothing more is pushed
    assert viewer.pop() is None
    with pytest.raises(StreamEnded):
        rig.hub.add_viewer()
    assert rig.hub.closed and rig.hub.viewer_count == 0
    rig.hub.remove_viewer(viewer)  # idempotent
    assert rig.hub.request_keyframe("late") is False and rig.hub.poll() is False


def test_a_removed_viewer_is_closed_and_nothing_more_is_queued_for_it():
    rig = Rig()
    a, b = rig.hub.add_viewer(), rig.hub.add_viewer()
    rig.hub.remove_viewer(a)
    rig.config()
    rig.idr()
    assert a.closed and a.close_reason == "removed" and drain(a) == []
    assert describe(drain(b)) == ["config", "idr:1"] and rig.hub.viewer_count == 1


def test_measure_counts_pictures_in_the_window():
    rig = Rig()
    for i in range(30):
        rig.hub.publish_media(i, idr_unit(i, pad=1000) if i == 0 else delta_unit(i, pad=1000))
        rig.clock.advance(0.1)
    rate = rig.hub.measure(window_s=3.0)
    assert abs(rate["fps"] - 10.0) <= 0.5 and rate["kbit_s"] > 0


def test_the_hub_says_when_its_last_unit_came():
    rig = Rig()
    rig.config()
    rig.hub.add_viewer()
    assert rig.hub.last_unit_at is None
    rig.idr()
    assert rig.hub.last_unit_at == rig.clock()
    rig.clock.advance(3)
    rig.hub.add_viewer()  # a joiner is not a unit
    assert rig.hub.last_unit_at == rig.clock() - 3
    rig.delta()
    assert rig.hub.last_unit_at == rig.clock()


def test_a_viewer_reports_what_it_was_sent_and_what_it_lost():
    rig = Rig()
    rig.config()
    rig.idr()
    viewer = rig.hub.add_viewer()
    drain(viewer)
    stats = viewer.stats()
    assert (
        stats["sent"] == 1 and stats["sent_bytes"] > 0 and stats["resyncs"] == 0 and stats["waiting_for_idr"] is False
    )
    assert stats["close_reason"] is None and stats["idle_skips"] == 0


def test_stats_say_what_the_hub_has_seen():
    rig = Rig()
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.idr()
    rig.delta(3)
    stats = rig.hub.stats()
    assert stats["frames"] == 4 and stats["idrs"] == 1 and stats["bytes"] > 0 and stats["last_idr_age_s"] == 0.0
    assert (
        stats["gop"]["frames"] == 4
        and stats["gop"]["parameter_set_bytes"] == len(PARAMS)
        and stats["gop"]["age_s"] == 0.0
    )
    assert stats["viewers"][0]["id"] == viewer.id and stats["stalled"] == 0 and stats["keyframe_cost"] == "cheap"
    assert stats["keyframe_requests"]["requested"] == 0 and stats["joins"] == {"replayed": 0, "waited": 1, "forced": 0}
    rig.clock.advance(1)
    assert rig.hub.stats()["last_idr_age_s"] == 1.0
    assert rig.hub.measure(3.0)["fps"] == round(4 / 3.0, 1)


def test_the_cost_of_a_key_picture_is_one_of_three_and_anything_else_is_refused():
    with pytest.raises(ValueError, match="keyframe_cost"):
        Hub(keyframe_cost="free")
    hub = Hub()
    with pytest.raises(ValueError, match="keyframe_cost"):
        hub.set_source(None, "free")
    hub.set_source(None, "cheap")
    assert hub.keyframe_cost == "cheap"


# ---- a slow viewer: what the hub does for it, and what it tells the adaptation


def test_non_reference_pictures_are_dropped_first_and_the_picture_stays_clean():
    rig = Rig("none", max_lag_s=0.5)
    rig.config()
    rig.idr()
    viewer = rig.hub.add_viewer()
    drain(viewer)
    for i in range(1, 7):  # alternate reference and disposable pictures
        rig.clock.advance(0.1)
        rig.delta(ref=bool(i % 2))
    rig.clock.advance(0.2)  # the oldest queued picture has now waited 0.7 s: behind, but not 2.5 times behind
    rig.delta()
    got = drain(viewer)
    assert viewer.dropped_non_reference == 3 and viewer.resyncs == 0
    assert all(f.reference for f in got if f.kind == KIND_MEDIA)


def test_a_viewer_far_behind_with_only_reference_pictures_skips_to_the_next_idr_and_the_hub_is_told():
    rig = Rig("none", max_lag_s=0.5)
    rig.config()
    slow, quick = rig.hub.add_viewer(), rig.hub.add_viewer()
    rig.idr()
    for _ in range(3):
        rig.clock.advance(0.1)
        rig.delta()
        drain(quick)  # the quick one keeps up
    rig.clock.advance(1.5)  # the slow one's oldest queued picture is now 1.8 s old
    rig.delta()
    assert slow.resyncs == 1 and rig.congested == [slow] and quick.resyncs == 0
    assert describe(drain(quick)) == ["delta:5"]
    rig.delta()
    assert describe(drain(slow)) == ["config"]  # nothing for the slow one until the IDR
    rig.idr()
    assert describe(drain(slow)) == ["idr:7"]


def test_an_idr_is_a_way_out_of_lag():
    rig = Rig("none", max_lag_s=0.5)
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.idr()
    rig.delta()
    rig.clock.advance(2.0)
    rig.idr()
    assert [f.ts_us for f in drain(viewer) if f.kind == KIND_MEDIA] == [3000]
    assert viewer.dropped_for_idr == 2


def test_a_queue_that_has_waited_exactly_the_limit_is_no_overflow_and_one_that_waited_longer_is():
    rig = Rig("none", max_lag_s=1.0)
    rig.config()
    rig.idr()
    viewer = rig.hub.add_viewer()
    drain(viewer)
    rig.delta()
    rig.clock.advance(1.0)
    rig.delta()
    assert viewer.dropped_non_reference == 0 and viewer.path(10).events == 0
    rig.clock.advance(0.01)
    rig.delta(ref=False)  # behind by a hair: only what nothing depends on goes
    assert viewer.path(10).events == 1 and viewer.dropped_non_reference == 1 and viewer.resyncs == 0


_STEP = 0.05


def overflow(rig, readers=(), slow=(), *, cycles=1, big=800, pause=0.4):
    """An IDR and enough big delta pictures to overflow a viewer that does not read.

    The viewers in ``readers`` pop everything as it comes; the ones in ``slow`` pop a single frame at the start of each
    cycle (they are alive, and fall behind all the same); any other viewer reads nothing.
    """
    for _ in range(cycles):
        rig.idr(pad=100)
        for viewer in slow:
            viewer.pop()
        for _ in range(5):
            rig.clock.advance(_STEP)
            rig.delta(pad=big)
            for viewer in readers:
                drain(viewer)
        rig.clock.advance(pause)


def test_a_viewer_that_keeps_skipping_gets_key_pictures_only_and_the_viewer_that_reads_is_untouched():
    rig = Rig("none", max_lag_s=1.0, max_queue_bytes=1000)
    rig.config()
    reading, slow = rig.hub.add_viewer(), rig.hub.add_viewer()
    for _ in range(4):
        overflow(rig, [reading], [slow])
    assert slow.keyframes_only is True and slow.resyncs >= 3
    # it lost nothing
    assert reading.keyframes_only is False and reading.resyncs == 0 and reading.dropped_in_resync == 0
    # on key pictures only: a delta is dropped and counted, an IDR is queued
    before = slow.dropped_keyframes_only
    rig.delta(pad=50)
    assert slow.dropped_keyframes_only == before + 1
    rig.idr(pad=50)
    assert [f.idr for f in drain(slow) if f.kind == KIND_MEDIA][-1] is True
    assert slow.stats()["keyframes_only"] is True and slow.stats()["dropped_keyframes_only"] > 0


def test_a_viewer_on_key_pictures_is_tried_on_the_whole_stream_again_after_a_wait_which_doubles_if_it_does_not_hold():
    rig = Rig("none", max_lag_s=1.0, max_queue_bytes=1000)
    rig.config()
    viewer = rig.hub.add_viewer()
    for _ in range(4):
        overflow(rig, slow=[viewer])
    assert viewer.keyframes_only is True and viewer._retry_s == 8.0
    drain(viewer)
    rig.clock.advance(9.0)  # the wait is over: the next key picture takes it back to the whole stream
    rig.idr(pad=100)
    assert viewer.keyframes_only is False
    rig.delta(pad=50)
    assert [f.kind for f in drain(viewer)][-1] == KIND_MEDIA
    for _ in range(4):  # it does not hold: the whole stream overflows its queue again
        overflow(rig, slow=[viewer])
    assert viewer.keyframes_only is True and viewer._retry_s == 16.0


def test_the_path_says_how_often_the_queue_overflowed_and_how_full_it_got():
    rig = Rig("none", max_lag_s=1.0, max_queue_bytes=1000)
    rig.config()
    viewer = rig.hub.add_viewer()
    calm = rig.hub.path_samples(10)[0]
    assert calm.events == 0 and calm.peak == 0 and calm.keyframes_only is False
    overflow(rig, slow=[viewer], cycles=2)
    busy = rig.hub.path_samples(10)[0]
    assert busy.events >= 2 and busy.peak >= 1.0
    assert viewer.path(0.001).events == 0  # a window that has nothing in it
    rig.clock.advance(400)  # long ago
    assert rig.hub.path_samples(10)[0].events == 0


def test_a_slow_viewer_drops_to_the_next_key_unit_and_a_fast_one_is_never_slowed():
    rig = Rig(max_lag_s=100.0, max_queue_bytes=1000, keyframe_min_interval_s=0)
    rig.config()
    fast, slow = rig.hub.add_viewer(), rig.hub.add_viewer()
    rig.idr()
    assert describe(drain(fast)) == ["config", "idr:1"]
    slow.pop()  # it reads the line, so it is alive
    for _ in range(30):
        rig.delta(pad=400)
        assert len(describe(drain(fast))) == 1  # the fast one reads every unit as it comes
    # the slow one never read on: its queue overflowed, was discarded, and it now waits for a key unit
    assert slow.resyncs == 1 and slow.waiting_idr
    assert fast.resyncs == 0 and fast.waiting_idr is False and fast.dropped_in_resync == 0
    assert len(rig.asked) == 1  # a key unit was asked for on its behalf
    assert describe(drain(slow)) == []  # and nothing it could not decode is queued
    rig.idr()
    rig.delta()
    assert describe(drain(slow)) == ["idr:32", "delta:33"]  # back on the stream at the next key unit
    assert describe(drain(fast)) == ["idr:32", "delta:33"]
    assert rig.hub.viewer_count == 2


def test_a_viewer_is_skipped_to_the_next_idr_only_once_its_backlog_passes_two_and_a_half_times_the_limit():
    rig = Rig(max_lag_s=100.0, max_queue_bytes=1000, keyframe_min_interval_s=0)
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.idr(pad=100)
    drain(viewer)
    one_wire_size = len(unit_of_size(500)) + 13  # a delta of 500 padding bytes: header, timestamp and unit
    assert one_wire_size * 4 < 2.5 * 1000 < one_wire_size * 5
    rig.delta(5, pad=500)  # the head, and four behind it: behind (1000 over) but not far behind
    assert viewer.resyncs == 0 and viewer.path(10).events == 1
    rig.delta(pad=500)  # a fifth behind the head
    assert viewer.resyncs == 1 and viewer.waiting_idr


def unit_of_size(pad):
    return delta_unit(0, pad=pad)


# ---- what a viewer says of its own lag


def started_viewer(rig):
    """A viewer that joined before any picture existed (never replayed one) and has read the first key unit."""
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.idr()
    drain(viewer)
    return viewer


def replayed_viewer(rig):
    rig.config()
    rig.idr()
    rig.delta()
    viewer = rig.hub.add_viewer()  # replayed the GOP
    drain(viewer)
    return viewer


def test_a_lag_past_the_soft_limit_counts_as_an_overflow_and_past_the_hard_limit_the_viewer_skips_to_a_key_picture():
    rig = Rig("none")
    viewer = started_viewer(rig)
    assert rig.hub.report_lag(viewer.id, LAG_SOFT_S / 2) is True
    sample = viewer.path(10)
    # a lag well inside the limit is no trouble, and is counted in the peak
    assert sample.events == 0 and 0.4 < sample.peak < 0.6
    rig.hub.report_lag(viewer.id, LAG_SOFT_S * 1.5)
    # an overflow: the viewer is slow, nothing is thrown away yet
    assert viewer.path(10).events == 1 and viewer.resyncs == 0
    rig.delta()
    rig.hub.report_lag(viewer.id, LAG_HARD_S * 1.2)
    assert viewer.resyncs == 1 and viewer.waiting_idr is True  # it skips what is queued and waits for a key picture
    assert viewer.pop() is None
    rig.hub.report_lag(viewer.id, LAG_HARD_S * 1.2)
    assert viewer.resyncs == 1  # not again at once: what is in flight goes on reporting the lag for a while
    rig.clock.advance(LAG_HARD_S + 0.1)
    rig.hub.report_lag(viewer.id, LAG_HARD_S * 1.2)
    assert viewer.resyncs == 1  # nor while it is held for a key picture: it has nothing left to skip
    rig.idr()  # its key picture came
    rig.hub.report_lag(viewer.id, LAG_HARD_S * 1.2)
    assert viewer.resyncs == 2


def test_a_viewer_that_keeps_reporting_a_hard_lag_ends_up_on_key_pictures_only():
    rig = Rig("none")
    viewer = started_viewer(rig)
    for _ in range(4):
        rig.idr()
        drain(viewer)  # it reads, and says it is far behind
        rig.hub.report_lag(viewer.id, LAG_HARD_S * 2)
        rig.clock.advance(LAG_HARD_S + 0.1)
    assert viewer.keyframes_only is True and not viewer.closed


def test_a_report_for_a_viewer_that_is_gone_is_refused_and_a_lag_inside_the_limit_changes_nothing_but_the_peak():
    rig = Rig()
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.hub.remove_viewer(viewer)
    assert rig.hub.report_lag(viewer.id, 5.0) is False
    assert rig.hub.report_lag(99, 5.0) is False
    assert viewer.report_lag(5.0) is False  # and a viewer that was closed takes no report from its own stream either


def test_what_a_viewer_says_just_after_a_replay_is_not_counted_and_counts_again_once_the_grace_is_over():
    rig = Rig("none")
    viewer = replayed_viewer(rig)
    rig.clock.advance(1.0)
    assert rig.hub.report_lag(viewer.id, LAG_HARD_S * 2) is True  # the report is taken...
    assert viewer.resyncs == 0 and viewer.waiting_idr is False and viewer.path(10).events == 0  # ...and does nothing
    assert viewer.stats()["lag_ignored"] == 1
    rig.clock.advance(LAG_GRACE_S)
    rig.hub.report_lag(viewer.id, LAG_SOFT_S * 1.5)
    assert viewer.path(10).events == 1 and viewer.stats()["lag_ignored"] == 1


def test_each_replay_starts_the_grace_again_and_a_viewer_that_was_never_replayed_has_none():
    rig = Rig("none")
    viewer = replayed_viewer(rig)
    rig.clock.advance(LAG_GRACE_S + 0.1)
    rig.hub.report_lag(viewer.id, LAG_SOFT_S * 1.5)
    assert viewer.path(10).events == 1
    rig.hub.resync(viewer)  # served from the cached GOP again
    rig.clock.advance(1.0)
    rig.hub.report_lag(viewer.id, LAG_SOFT_S * 1.5)
    assert viewer.path(10).events == 1 and viewer.stats()["lag_ignored"] == 1
    fresh = Rig("none")
    fresh.config()
    other = fresh.hub.add_viewer()  # no GOP yet: nothing replayed
    fresh.hub.report_lag(other.id, LAG_SOFT_S * 1.5)
    assert other.path(10).events == 1 and other.stats()["lag_ignored"] == 0


def test_one_viewers_replay_does_not_silence_another():
    rig = Rig("none")
    old = replayed_viewer(rig)
    rig.clock.advance(LAG_GRACE_S + 0.1)
    new = rig.hub.add_viewer()
    drain(new)
    rig.hub.report_lag(old.id, LAG_SOFT_S * 1.5)
    rig.hub.report_lag(new.id, LAG_SOFT_S * 1.5)
    assert old.path(10).events == 1 and new.path(10).events == 0


def test_the_grace_after_a_replay_is_the_hubs_constant_and_can_be_changed(monkeypatch):
    monkeypatch.setattr(hub_module, "LAG_GRACE_S", 0.0)
    rig = Rig("none")
    viewer = replayed_viewer(rig)
    rig.hub.report_lag(viewer.id, LAG_SOFT_S * 2)
    assert viewer.path(10).events == 1 and viewer.stats()["lag_ignored"] == 0


def test_a_viewers_stats_keep_its_last_few_reports_with_their_age_and_whether_they_counted():
    rig = Rig("none")
    viewer = replayed_viewer(rig)
    assert viewer.stats()["recent_lags"] == []
    rig.hub.report_lag(viewer.id, 2.45)  # inside the grace
    rig.clock.advance(LAG_GRACE_S)
    rig.hub.report_lag(viewer.id, 0.3)
    rig.clock.advance(2.0)
    assert viewer.stats()["recent_lags"] == [
        {"s": 2.45, "age_s": LAG_GRACE_S + 2.0, "counted": False},
        {"s": 0.3, "age_s": 2.0, "counted": True},
    ]
    for lag in (0.1, 0.2, 0.3, 0.4, 0.5):
        rig.hub.report_lag(viewer.id, lag)
    assert [r["s"] for r in viewer.stats()["recent_lags"]] == [0.1, 0.2, 0.3, 0.4, 0.5]  # only the last few


def test_a_lag_report_is_tied_to_the_viewer_it_is_made_on_and_the_driver_can_make_it_on_the_viewer_itself():
    rig = Rig("none")
    rig.config()
    first, second = rig.hub.add_viewer(), rig.hub.add_viewer()
    rig.idr()
    drain(first)
    drain(second)
    assert first.report_lag(LAG_HARD_S * 2) is True  # the viewer's own stream says it: the driver holds the viewer
    assert first.resyncs == 1 and first.waiting_idr
    assert second.resyncs == 0 and not second.waiting_idr and second.path(10).events == 0
    rig.hub.report_lag(second.id, LAG_SOFT_S * 2)  # and through the hub, by id: it lands on the one it names
    assert second.path(10).events == 1 and first.path(10).events == 1


def test_a_hard_lag_said_on_the_viewer_itself_asks_a_cheap_source_for_a_key_picture_for_that_viewer_only():
    rig = Rig(keyframe_min_interval_s=0)
    first = started_viewer(rig)
    second = rig.hub.add_viewer()
    drain(second)
    assert first.report_lag(LAG_HARD_S * 2) is True
    assert len(rig.asked) == 1 and rig.hub.last_keyframe_reason == "lag"
    assert rig.hub.stats()["keyframe_requests"]["requested"] == 1
    assert second.report_lag(LAG_SOFT_S * 2) is False  # a soft lag asks nothing
    assert len(rig.asked) == 1


def test_report_lag_counts_an_event_past_the_soft_limit_and_a_cheap_source_is_asked_for_the_skip_past_the_hard_one():
    rig = Rig(keyframe_min_interval_s=0)
    viewer = started_viewer(rig)
    assert rig.hub.report_lag(viewer.id, LAG_SOFT_S / 2) is True
    assert viewer.path(10).events == 0 and rig.asked == []
    rig.hub.report_lag(viewer.id, LAG_SOFT_S * 1.5)
    assert viewer.path(10).events == 1 and viewer.resyncs == 0  # slow, and nothing is thrown away yet
    rig.delta(3)
    rig.hub.report_lag(viewer.id, LAG_HARD_S * 1.2)
    assert viewer.resyncs == 1 and viewer.waiting_idr and viewer.pop() is None  # what was queued is gone
    assert len(rig.asked) == 1  # a key unit is asked for
    rig.hub.report_lag(viewer.id, LAG_HARD_S * 1.2)
    assert viewer.resyncs == 1  # not again at once: what is in flight keeps reporting the lag for a while
    rig.idr()
    drain(viewer)  # it reads
    rig.clock.advance(LAG_HARD_S + 0.1)
    rig.hub.report_lag(viewer.id, LAG_HARD_S * 1.2)
    assert viewer.resyncs == 2 and len(rig.asked) == 2


def test_a_viewer_that_stays_far_behind_is_treated_like_one_whose_queue_overflowed():
    rig = Rig(keyframe_min_interval_s=0)
    rig.config()
    behind, fine = rig.hub.add_viewer(), rig.hub.add_viewer()
    rig.idr()
    drain(behind)
    rig.hub.report_lag(behind.id, 6.0)
    rig.delta(2)
    assert behind.waiting_idr and describe(drain(behind)) == []  # deltas skipped until a key unit
    assert describe(drain(fine))[-2:] == ["delta:2", "delta:3"]
    rig.idr()
    assert describe(drain(behind)) == ["idr:4"]
    assert behind.stats()["resyncs"] == 1


# ---- asking a source that can be asked cheaply for a key picture


def requests(rig):
    return rig.hub.stats()["keyframe_requests"]


def test_joiners_while_a_key_unit_is_on_its_way_take_that_one_and_it_is_asked_again_if_it_never_comes():
    rig = Rig(keyframe_min_interval_s=2.0)
    rig.config()
    rig.idr()
    rig.delta()
    rig.clock.advance(5)  # the group is old: every joiner has to wait
    first = rig.hub.add_viewer()
    assert rig.asked == [105.0]  # asked at once
    rig.clock.advance(0.5)
    second = rig.hub.add_viewer()
    rig.clock.advance(0.5)
    third = rig.hub.add_viewer()
    assert rig.asked == [105.0]  # the key unit on its way serves them too: not asked for again
    assert requests(rig)["shared"] == 2 and requests(rig)["coalesced"] == 0
    rig.clock.advance(0.9)
    assert rig.hub.poll() is False  # the window is not over
    rig.clock.advance(0.2)
    assert rig.hub.poll() is True  # and it never came: asked again, for all of them
    assert rig.asked == [105.0, pytest.approx(107.1)] and requests(rig)["retries"] == 1
    assert rig.hub.poll() is False  # once in a window
    assert all(v.waiting_idr for v in (first, second, third))
    rig.idr()  # it comes now
    assert not any(v.waiting_idr for v in (first, second, third))
    rig.clock.advance(5)
    assert rig.hub.poll() is False and len(rig.asked) == 2  # nobody waits: nothing is asked


def test_the_same_viewer_asking_again_inside_the_window_is_that_request_repeated_and_waits_for_the_window_to_end():
    rig = Rig(keyframe_min_interval_s=2.0)
    rig.config()
    rig.idr()
    rig.clock.advance(5)
    viewer = rig.hub.add_viewer()  # asked at 105
    rig.idr()  # answered
    drain(viewer)  # it reads
    rig.clock.advance(0.5)
    rig.hub.report_lag(viewer.id, LAG_HARD_S * 2)  # it falls behind: it needs a key unit again
    assert viewer.waiting_idr and len(rig.asked) == 1  # the same viewer inside the window: coalesced
    assert requests(rig)["coalesced"] == 1 and requests(rig)["forwarded"] == 0
    rig.clock.advance(1.0)
    assert rig.hub.poll() is False  # not yet
    rig.clock.advance(0.6)
    assert rig.hub.poll() is True and len(rig.asked) == 2  # sent when the window ended


def test_a_coalesced_request_is_dropped_when_a_key_unit_came_meanwhile_and_nobody_waits_any_more():
    rig = Rig(keyframe_min_interval_s=2.0)
    rig.config()
    rig.idr()
    rig.clock.advance(5)
    viewer = rig.hub.add_viewer()  # asked
    rig.idr()  # answered
    drain(viewer)
    rig.clock.advance(0.5)
    rig.hub.report_lag(viewer.id, LAG_HARD_S * 2)  # coalesced
    assert len(rig.asked) == 1 and requests(rig)["coalesced"] == 1
    rig.idr()  # a key unit comes meanwhile, whoever asked for it
    rig.clock.advance(1.6)
    assert rig.hub.poll() is False and len(rig.asked) == 1
    assert requests(rig)["cancelled"] == 1


def test_a_viewer_that_joins_after_the_key_unit_asked_for_another_has_gone_by_is_asked_for_at_once():
    rig = Rig(keyframe_min_interval_s=2.0, join_replay_max_age_s=0.2)
    rig.config()
    rig.idr()
    rig.clock.advance(5)
    first = rig.hub.add_viewer()  # asked at 105 ...
    rig.idr()  # ... and answered: the key unit went by
    assert rig.asked == [105.0] and not first.waiting_idr
    rig.clock.advance(0.5)  # inside the window; the cached key unit is too old to start a joiner from
    second = rig.hub.add_viewer()
    assert second.waiting_idr
    assert rig.asked == [105.0, 105.5]  # not left to wait for the window to end: it is not the same request
    assert requests(rig)["forwarded"] == 1 and requests(rig)["coalesced"] == 0
    rig.idr()
    assert not second.waiting_idr and describe(drain(second))[-1].startswith("idr:")


def test_a_viewer_that_joins_inside_the_window_is_started_from_the_cached_key_unit_when_it_is_recent():
    rig = Rig(keyframe_min_interval_s=2.0)
    rig.config()
    rig.idr()
    rig.clock.advance(5)
    first = rig.hub.add_viewer()  # asked at 105, answered
    rig.idr()
    rig.delta(2)
    rig.clock.advance(0.5)
    second = rig.hub.add_viewer()  # the group is 0.5 s old: replayed, nothing to ask
    assert not second.waiting_idr and describe(drain(second)) == ["config", "idr:2", "delta:3", "delta:4"]
    assert rig.asked == [105.0] and rig.hub.joins_replayed == 1
    assert not first.waiting_idr


def test_a_request_that_went_unanswered_is_made_again_by_the_next_unit_after_the_window():
    rig = Rig(keyframe_min_interval_s=2.0, join_wait_s=60.0)  # (the joiners are not given the old group meanwhile)
    rig.config()
    rig.idr()
    rig.clock.advance(5)
    rig.hub.add_viewer()
    rig.hub.add_viewer()  # the key unit asked for the first is on its way: it serves this one too
    assert len(rig.asked) == 1
    rig.clock.advance(2.1)
    rig.delta()  # it did not come, and a unit arrives after the window: the next unit asks again
    assert len(rig.asked) == 2


def test_a_viewer_whose_queue_overflowed_is_asked_for_at_once_when_the_last_request_was_for_another_viewer():
    rig = Rig(keyframe_min_interval_s=2.0, max_lag_s=100.0, max_queue_bytes=1000)
    rig.config()
    rig.idr()
    rig.clock.advance(5)
    first = rig.hub.add_viewer()  # asked at 105 ...
    rig.idr()  # ... and answered
    second = rig.hub.add_viewer()  # started from the group
    drain(second)  # it reads the replay
    rig.clock.advance(0.1)
    for _ in range(8):  # the first reads, the second does not
        rig.delta(pad=400)
        drain(first)
    assert second.waiting_idr and not first.waiting_idr
    assert rig.asked == [105.0, pytest.approx(105.1)]  # the second's own request, not left waiting for the window
    assert requests(rig)["forwarded"] == 1


def test_a_resync_gives_a_clean_start_from_the_group_and_asks_a_cheap_source_for_a_key_unit_too():
    rig = Rig(keyframe_min_interval_s=0)
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.idr()
    rig.delta(2)
    drain(viewer)
    rig.hub.resync(viewer)
    assert describe(drain(viewer)) == ["idr:1", "delta:2", "delta:3"]  # from the recent group
    assert len(rig.asked) == 1  # and a new key unit is asked for
    rig.clock.advance(5)  # the group is old now
    rig.hub.resync(viewer)
    assert viewer.waiting_idr and len(rig.asked) == 2
    rig.hub.remove_viewer(viewer)
    rig.hub.resync(viewer)  # but not for one that has left
    assert len(rig.asked) == 2


@pytest.mark.parametrize("cost", ["restart", "none"])
def test_a_resync_serves_a_clean_start_from_the_gop_without_asking_a_source_that_is_costly_to_ask(cost):
    rig = Rig(cost)
    rig.config()
    rig.idr()
    viewer = rig.hub.add_viewer()
    drain(viewer)
    rig.delta()
    rig.hub.resync(viewer)
    assert describe(drain(viewer)) == ["idr:1", "delta:2"] and rig.asked == []


@pytest.mark.parametrize("cost", ["restart", "none"])
def test_a_resync_with_no_gop_to_give_asks_the_source_directly_without_the_rate_limit(cost):
    rig = Rig(cost, join_replay_max_bytes=100)
    rig.config()
    rig.idr(pad=300)
    viewer = rig.hub.add_viewer()  # too large to replay: asked once
    rig.hub.resync(viewer)  # and again: the gate is for a source that can be asked cheaply
    assert len(rig.asked) == 2 and viewer.waiting_idr


def test_pictures_lost_before_the_hub_leave_no_start_in_the_cache_and_a_cheap_source_is_asked_for_one():
    rig = Rig()
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.idr()
    rig.delta(2)
    drain(viewer)
    rig.hub.discontinuity()
    assert rig.hub.gop()["frames"] == 0 and rig.hub.discontinuities == 1
    assert viewer.waiting_idr and len(rig.asked) == 1 and rig.hub.last_keyframe_reason == "discontinuity"
    rig.delta()
    assert drain(viewer) == []  # nothing until an IDR
    late = rig.hub.add_viewer()
    assert late.waiting_idr
    rig.idr()
    assert describe(drain(viewer)) == ["idr:5"] and describe(drain(late)) == ["config", "idr:5"]


@pytest.mark.parametrize("cost", ["restart", "none"])
def test_a_discontinuity_holds_every_viewer_until_an_idr_and_drops_the_gop(cost):
    rig = Rig(cost)
    rig.config()
    rig.idr()
    viewer = rig.hub.add_viewer()
    drain(viewer)
    rig.hub.discontinuity()
    rig.delta()
    assert drain(viewer) == [] and rig.hub.gop()["frames"] == 0 and rig.asked == []  # waits for the periodic IDR
    rig.idr()
    assert describe(drain(viewer)) == ["idr:3"]


def test_a_discontinuity_with_nobody_attached_asks_nothing():
    rig = Rig()
    rig.config()
    rig.idr()
    rig.hub.discontinuity()
    assert rig.asked == []


def test_a_request_the_driver_makes_itself_is_rate_limited_for_a_cheap_source_and_direct_for_any_other():
    cheap = Rig(keyframe_min_interval_s=2.0)
    assert cheap.hub.request_keyframe("stall") is True
    assert cheap.hub.request_keyframe("stall") is False and len(cheap.asked) == 1  # a request is on its way
    other = Rig("restart")
    assert other.hub.request_keyframe("stall") is True and other.hub.request_keyframe("stall") is True
    assert len(other.asked) == 2 and other.hub.last_keyframe_reason == "stall"
    assert other.hub.poll() is False  # nothing is retried for a source that is not asked cheaply


def test_a_key_unit_nobody_could_decode_is_no_start_and_a_cheap_source_is_asked_for_another():
    rig = Rig()
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.idr(params=b"")  # no parameter sets seen yet, none in the unit
    assert describe(drain(viewer)) == ["config"] and viewer.waiting_idr
    assert rig.hub.keys_without_params == 1 and len(rig.asked) == 1 and rig.hub.last_keyframe_reason == "params"
    assert rig.hub.gop()["frames"] == 0
    rig.idr()
    assert describe(drain(viewer)) == ["idr:2"]
    assert rig.hub.stats()["keys_without_params"] == 1


@pytest.mark.parametrize("cost", ["restart", "none"])
def test_a_key_unit_nobody_could_decode_is_counted_and_no_other_source_is_asked_for_another(cost):
    rig = Rig(cost)
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.idr(params=b"")
    assert describe(drain(viewer)) == ["config"] and rig.hub.keys_without_params == 1 and rig.asked == []


# ---- a stuck viewer must not cost the source a key picture every few seconds, forever


def squeezed(cost="cheap", **options):
    """A hub whose viewers overflow after a few big pictures, and that does not rate-limit (nothing hides a request)."""
    options.setdefault("max_lag_s", 1.0)
    options.setdefault("max_queue_bytes", 1000)
    options.setdefault("keyframe_min_interval_s", 0)
    return Rig(cost, **options)


def test_a_stuck_viewer_does_not_request_a_key_picture_forever():
    rig = squeezed()
    rig.config()
    reading, stuck = rig.hub.add_viewer(), rig.hub.add_viewer()
    overflow(rig, [reading])  # the first overflow of a viewer that has read nothing since it joined
    # it is not asked for: a key unit would only bury it again
    assert stuck.resyncs == 1 and stuck.waiting_idr and rig.asked == []
    for _ in range(STALL_OVERFLOWS - 1):
        overflow(rig, [reading])
    assert stuck.closed and stuck.close_reason == "stalled" and stuck.resyncs == STALL_OVERFLOWS
    assert rig.hub.stalled == 1 and rig.hub.stats()["stalled"] == 1 and rig.stalled == [stuck]
    assert rig.hub.viewers() == [reading]  # it costs nothing from here on
    before = len(rig.asked)
    for _ in range(25):  # and it goes on like that for as long as the stream does: not one more request
        overflow(rig, [reading])
    assert len(rig.asked) == before == 0 and reading.resyncs == 0 and rig.hub.keyframe_requests == 0


@pytest.mark.parametrize("cost", ["restart", "none"])
def test_a_stuck_viewer_is_let_go_whatever_the_source_and_the_source_is_never_asked(cost):
    rig = squeezed(cost)
    rig.config()
    reading, stuck = rig.hub.add_viewer(), rig.hub.add_viewer()
    for _ in range(STALL_OVERFLOWS):
        overflow(rig, [reading])
    assert stuck.close_reason == "stalled" and rig.hub.stalled == 1 and rig.asked == []
    assert rig.hub.viewer_count == 1 and reading.close_reason is None


def test_a_viewer_that_reads_between_overflows_is_asked_for_each_time_and_is_never_let_go():
    rig = squeezed()
    rig.config()
    reading, slow = rig.hub.add_viewer(), rig.hub.add_viewer()
    for _ in range(6):
        overflow(rig, [reading], [slow])
    assert slow.close_reason is None and rig.hub.stalled == 0 and not slow.closed
    assert slow.resyncs >= 1 and len(rig.asked) == slow.resyncs  # one request for each time it fell behind having read
    assert rig.hub.last_keyframe_reason == "overflow"


def test_reading_once_in_a_while_resets_the_count_of_overflows_without_progress():
    rig = squeezed()
    rig.config()
    reading, stuck = rig.hub.add_viewer(), rig.hub.add_viewer()
    # far enough apart that the viewer is not moved to key pictures only, which stops it overflowing
    spread = {"pause": 11.0}
    overflow(rig, [reading], **spread)
    overflow(rig, [reading], **spread)
    assert stuck.stats()["idle_skips"] == 2 and not stuck.closed and rig.asked == []
    drain(stuck)  # it reads something
    overflow(rig, [reading], **spread)  # and falls behind again: that is a viewer that was alive
    assert stuck.stats()["idle_skips"] == 0 and not stuck.closed and len(rig.asked) == 1
    overflow(rig, [reading], **spread)
    overflow(rig, [reading], **spread)
    assert not stuck.closed  # two since it read
    overflow(rig, [reading], **spread)
    assert stuck.close_reason == "stalled" and len(rig.asked) == 1


def test_a_viewer_that_stopped_reading_is_asked_for_once_it_reads_again_and_not_before():
    rig = squeezed()
    rig.config()
    reading, stuck = rig.hub.add_viewer(), rig.hub.add_viewer()
    overflow(rig, [reading])
    assert stuck.owes_key and requests(rig)["withheld"] == 1 and rig.asked == []
    assert rig.hub.poll() is False  # it is still not reading
    drain(stuck)  # it reads again
    assert rig.hub.poll() is True and len(rig.asked) == 1 and rig.hub.last_keyframe_reason == "resumed"
    assert not stuck.owes_key and rig.hub.poll() is False  # once


def test_the_next_picture_after_the_viewer_reads_again_asks_for_what_it_is_owed():
    rig = squeezed()
    rig.config()
    reading, stuck = rig.hub.add_viewer(), rig.hub.add_viewer()
    overflow(rig, [reading])
    assert rig.asked == []
    drain(stuck)
    rig.delta()  # no watchdog tick needed: every picture looks
    assert len(rig.asked) == 1


def test_a_viewer_that_got_its_key_picture_some_other_way_is_owed_nothing():
    rig = squeezed()
    rig.config()
    reading, stuck = rig.hub.add_viewer(), rig.hub.add_viewer()
    overflow(rig, [reading])
    assert stuck.owes_key
    rig.idr()  # the periodic one: it needed no asking
    assert not stuck.owes_key and not stuck.waiting_idr
    drain(stuck)
    assert rig.hub.poll() is False and rig.asked == []


def test_a_viewer_that_stopped_reading_does_not_keep_a_whole_window_of_retries_going():
    rig = squeezed(keyframe_min_interval_s=2.0)
    rig.config()
    stuck = rig.hub.add_viewer()
    overflow(rig)
    assert stuck.owes_key
    for _ in range(10):  # nobody else waits for a key unit and the one stuck viewer is not reading
        rig.clock.advance(5.0)
        assert rig.hub.poll() is False
    assert rig.asked == []


def test_a_viewer_that_keeps_saying_it_is_far_behind_without_reading_is_let_go_too():
    rig = squeezed()
    rig.config()
    viewer = rig.hub.add_viewer()
    for _ in range(STALL_OVERFLOWS):
        rig.idr()  # queued, never read
        viewer.report_lag(LAG_HARD_S * 2)
        rig.clock.advance(LAG_HARD_S + 0.1)
    assert viewer.close_reason == "stalled" and rig.stalled == [viewer] and rig.asked == []
    assert rig.hub.report_lag(viewer.id, LAG_HARD_S * 2) is False  # it is gone from the hub
    assert viewer.report_lag(LAG_HARD_S * 2) is False  # and takes no more of its own


def test_a_viewer_let_go_is_told_and_woken_and_leaves_the_adaptation_alone():
    woken = []
    rig = squeezed("none")
    rig.config()
    reading = rig.hub.add_viewer()
    stuck = rig.hub.add_viewer(notify=lambda: woken.append(1))
    for _ in range(STALL_OVERFLOWS):
        overflow(rig, [reading])
    assert woken and stuck.closed
    assert [sample.viewer for sample in rig.hub.path_samples(30)] == [reading.id]  # its trouble is not the stream's
    # the adaptation heard of the first overflows, not of the one that closed it
    assert rig.congested == [stuck] * (STALL_OVERFLOWS - 1)
    assert stuck.stats()["close_reason"] == "stalled"
    rig.hub.remove_viewer(stuck)  # the driver removes it too when it ends the stream: idempotent, and the reason stays
    assert stuck.close_reason == "stalled"


def test_the_stall_limit_is_three_overflows_in_a_row():
    assert STALL_OVERFLOWS == 3


# ---- parameter sets: a key unit that lost them is repaired


def test_a_key_unit_without_parameter_sets_is_given_the_latest_ones():
    rig = Rig()
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.idr()
    drain(viewer)
    rig.idr(params=b"")  # the source did not repeat them
    (frame,) = drain(viewer)
    assert (
        frame.idr
        and unit_of(frame).startswith(PARAMS)
        and rig.hub.keys_without_params == 0
        and rig.hub.keys_repaired == 1
    )
    assert unit_of(frame) == PARAMS + idr_unit(2, params=b"")  # in front of the picture, which is untouched


def test_a_key_unit_that_has_its_parameter_sets_is_not_touched():
    rig = Rig()
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.idr()
    (_, frame) = drain(viewer)
    assert unit_of(frame) == idr_unit(1) and rig.hub.keys_repaired == 0


def test_the_cached_group_starts_with_the_repaired_key_unit_so_that_a_joiner_can_decode_it():
    rig = Rig()
    rig.config()
    rig.idr()
    rig.idr(params=b"")
    rig.delta()
    frames = drain(rig.hub.add_viewer())
    assert describe(frames) == ["config", "idr:2", "delta:3"] and unit_of(frames[1]).startswith(PARAMS)
    assert rig.hub.gop()["parameter_set_bytes"] == len(PARAMS)


@pytest.mark.parametrize(("carried", "missing"), [(SPS, "pps"), (PPS, "sps")])
def test_a_key_unit_with_only_one_of_the_two_sets_gets_the_other_where_it_belongs(carried, missing):
    rig = Rig()
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.idr()
    drain(viewer)
    rig.idr(params=carried)
    (frame,) = drain(viewer)
    assert unit_of(frame) == PARAMS + idr_unit(2, params=b""), missing  # the sps first, then the pps, then the picture
    assert rig.hub.keys_repaired == 1


def test_the_sets_any_unit_carries_are_remembered_a_delta_unit_too():
    rig = Rig()
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.hub.publish_media(1000, idr_unit(1, params=OTHER_PARAMS))
    rig.hub.publish_media(2000, PARAMS + delta_unit(2))  # a delta unit that happens to repeat them
    drain(viewer)
    rig.hub.publish_media(3000, idr_unit(3, params=b""))
    (frame,) = drain(viewer)
    assert unit_of(frame).startswith(PARAMS) and not unit_of(frame).startswith(OTHER_PARAMS)


def test_a_new_set_a_unit_carries_replaces_the_old_one_for_the_repairs_after_it():
    rig = Rig()
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.idr()
    rig.idr(params=OTHER_PARAMS)
    drain(viewer)
    rig.idr(params=b"")
    (frame,) = drain(viewer)
    assert unit_of(frame).startswith(OTHER_PARAMS)


def test_a_delta_unit_is_never_given_parameter_sets():
    rig = Rig()
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.idr()
    rig.delta()
    (_, _, frame) = drain(viewer)
    assert unit_of(frame) == delta_unit(2) and rig.hub.keys_repaired == 0


def test_a_change_of_the_coded_size_resets_the_parameter_sets_and_a_key_unit_without_new_ones_is_no_start():
    rig = Rig()
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.idr()
    drain(viewer)
    rig.config(OTHER_LINE)  # another SPS is due: the old sets describe another picture
    assert rig.hub.gop()["parameter_set_bytes"] == 0
    rig.idr(params=b"")
    assert describe(drain(viewer)) == ["config"] and rig.hub.keys_without_params == 1 and viewer.waiting_idr
    rig.idr(params=OTHER_PARAMS)
    assert describe(drain(viewer)) == ["idr:3"] and rig.hub.gop()["parameter_set_bytes"] == len(OTHER_PARAMS)
    rig.idr(params=b"")  # and from now on it is the new ones that repair
    assert unit_of(drain(viewer)[-1]).startswith(OTHER_PARAMS)


def test_a_change_of_the_codec_string_resets_the_parameter_sets_too():
    rig = Rig()
    rig.config()
    rig.idr()
    rig.config(replace(LINE, codec="avc1.640028"))
    assert rig.hub.gop()["parameter_set_bytes"] == 0


def test_a_change_of_the_rotation_keeps_the_parameter_sets():
    rig = Rig()
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.idr()
    drain(viewer)
    rig.config(ROTATED)
    assert rig.hub.gop()["parameter_set_bytes"] == len(PARAMS)
    rig.idr(params=b"")
    assert unit_of(drain(viewer)[-1]).startswith(PARAMS)


def test_a_new_source_forgets_the_parameter_sets_of_the_old_one():
    rig = Rig()
    rig.config()
    rig.idr()
    rig.hub.reset_source()
    assert rig.hub.gop()["parameter_set_bytes"] == 0
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.idr(params=b"")
    assert describe(drain(viewer)) == ["config"] and rig.hub.keys_without_params == 1


def test_what_the_source_says_about_a_unit_wins_over_what_the_hub_finds_in_it():
    rig = Rig()
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.hub.publish_media(1000, idr_unit(1), idr=False)  # the source says it is not a key unit
    assert describe(drain(viewer)) == ["config"]  # and a stream does not start at it
    rig.hub.publish_media(2000, idr_unit(2), idr=True, reference=True)
    rig.hub.publish_media(3000, delta_unit(3, ref=True), reference=False)  # the source says nothing depends on it
    assert describe(drain(viewer)) == ["idr:2", "disposable:3"]


def test_a_unit_that_is_not_flagged_is_read_for_what_it_is_so_a_source_that_does_not_know_need_not_say():
    rig = Rig()
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.hub.publish_media(1000, delta_unit(1, ref=False))  # no start yet
    rig.hub.publish_media(2000, idr_unit(2))
    rig.hub.publish_media(3000, delta_unit(3, ref=False))
    rig.hub.publish_media(4000, delta_unit(4, ref=True))
    assert describe(drain(viewer)) == ["config", "idr:2", "disposable:3", "delta:4"]


def test_a_key_unit_is_always_a_reference():
    rig = Rig()
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.hub.publish_media(1000, idr_unit(1), idr=True, reference=False)
    assert drain(viewer)[-1].reference is True


# ---- a replaced source


def test_a_replaced_source_keeps_its_viewers_which_wait_for_the_new_stream_and_are_sent_its_codec_line():
    rig = Rig()
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.idr()
    rig.delta(2)
    drain(viewer)
    asked_of_new_source = []
    rig.hub.reset_source()
    rig.hub.set_source(lambda: asked_of_new_source.append(rig.clock()))
    assert (
        viewer.waiting_idr
        and rig.hub.line is None
        and rig.hub.last_unit_at is None
        and rig.hub.seconds_since_idr() is None
    )
    assert rig.hub.gop()["frames"] == 0 and rig.hub.viewer_count == 1 and rig.hub.resets == 1
    rig.delta()  # nothing before the new stream's own start counts
    assert rig.hub.frames == 3 and drain(viewer) == []
    assert rig.config() is True  # the new source says the same picture: the hub does not know it, so it is told
    (line,) = drain(viewer)
    assert line_of(line)["viewer"] == viewer.id and line_of(line)["codec"] == "avc1.64001F"  # the same viewer
    rig.delta()
    assert drain(viewer) == []  # still no start
    rig.idr()
    assert describe(drain(viewer)) == ["idr:6"] and not viewer.waiting_idr
    joiner = rig.hub.add_viewer()  # and the cache is the new stream's own
    assert describe(drain(joiner)) == ["config", "idr:6"]
    rig.clock.advance(5)
    third = rig.hub.add_viewer()
    assert third.waiting_idr and asked_of_new_source and rig.asked == []  # requests go to the new source only


def test_a_replaced_source_forgets_what_was_asked_of_the_old_one():
    rig = Rig(keyframe_min_interval_s=2.0)
    rig.config()
    rig.idr()
    rig.clock.advance(5)
    rig.hub.add_viewer()  # asked, and not answered
    assert len(rig.asked) == 1
    rig.hub.reset_source()
    rig.config()
    rig.idr()
    rig.clock.advance(5)
    rig.hub.add_viewer()
    assert len(rig.asked) == 2  # not held back by the old source's window or its unanswered request


def test_a_viewer_joining_a_new_source_before_its_first_key_unit_is_not_asked_for_one():
    rig = Rig("restart")
    rig.config()
    rig.idr()
    rig.hub.reset_source()
    rig.config()
    viewer = rig.hub.add_viewer()  # the new capture's first key unit is on its way
    assert viewer.waiting_idr and rig.asked == []


def test_a_new_source_can_change_what_a_key_unit_costs():
    rig = Rig("none")
    rig.config()
    rig.idr()
    rig.hub.reset_source()
    rig.hub.set_source(lambda: rig.asked.append(1), "cheap")
    assert rig.hub.keyframe_cost == "cheap"
    rig.hub.request_keyframe("x")
    rig.hub.request_keyframe("x")
    assert rig.asked == [1]  # now rate limited


def test_a_viewer_can_join_while_the_source_is_being_replaced_and_the_owed_key_units_are_forgotten():
    rig = squeezed()
    rig.config()
    reading, stuck = rig.hub.add_viewer(), rig.hub.add_viewer()
    overflow(rig, [reading])
    assert stuck.owes_key
    rig.hub.reset_source()
    drain(stuck)
    assert rig.hub.poll() is False and rig.asked == []  # the debt was to the old source


# ---- threads


def consume(viewer, wakeup, out):
    """What a viewer's own thread does: sleep until woken, read what there is, and stop once the viewer is closed."""
    while True:
        wakeup.wait(timeout=10)
        wakeup.clear()
        closed = viewer.closed  # (read before the queue: what was queued before it closed is read after)
        while (frame := viewer.pop()) is not None:
            if frame.kind == KIND_MEDIA:
                out.append(frame.ts_us)
        if closed:
            return


def test_a_reader_thread_publishes_while_viewer_threads_drain_and_each_gets_every_picture_in_order():
    hub = Hub(max_lag_s=1e9, max_queue_bytes=1 << 40)  # nothing overflows: this is about order and wake-ups
    hub.publish_config(LINE)
    wakeups = [threading.Event() for _ in range(3)]
    viewers = [hub.add_viewer(notify=event.set) for event in wakeups]
    seen: list[list[int]] = [[] for _ in viewers]
    threads = [
        threading.Thread(target=consume, args=args, daemon=True) for args in zip(viewers, wakeups, seen, strict=True)
    ]
    for thread in threads:
        thread.start()
    hub.publish_media(0, idr_unit(0))
    for n in range(1, 300):
        hub.publish_media(n, delta_unit(n))
    hub.close()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert seen == [list(range(300))] * 3


# ---- the package ----------------------------------------------------------------------------------------------------


def test_the_package_offers_the_hub_the_policy_the_model_and_the_helpers_under_one_roof():
    from jumpstarter_driver_hmi import hub as package

    assert all(hasattr(package, name) for name in package.__all__)
    wanted = {
        "Hub", "Viewer", "Frame", "StreamEnded", "KeyframeGate", "PathSample", "Adapter", "Decision", "Rung", "LEVELS",
        "PRESETS", "ladder", "level_rung", "top_rung", "auto_bit_rate", "scaled", "iter_nals", "nal_types", "has_idr",
        "has_sps", "has_picture", "is_reference", "first_sps", "codec_string", "ParameterSets", "scan_unit",
    }  # fmt: skip
    assert wanted <= set(package.__all__)
    assert package.Hub is Hub and package.Frame.__module__.endswith("hub.hub")


def test_the_hub_is_pure_code_it_imports_no_jumpstarter_no_sockets_and_no_event_loop():
    import ast
    import pathlib

    banned = {"jumpstarter", "socket", "asyncio", "anyio", "subprocess", "select", "selectors"}
    for path in pathlib.Path(__file__).parent.glob("*.py"):
        if path.name.endswith("_test.py"):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            assert not {name.split(".")[0] for name in names} & banned, (path.name, names)


def test_a_lag_report_for_a_viewer_already_held_for_a_key_picture_changes_nothing():
    """Pictures in flight read as a lag, not as skips: a viewer that reads everything it is given is never closed."""
    rig = squeezed("none")
    rig.config()
    rig.idr()
    viewer = rig.hub.add_viewer()
    drain(viewer)
    viewer.wait_for_idr()  # held until the next key picture, which is not due for a while
    for _ in range(STALL_OVERFLOWS * 3):
        assert viewer.report_lag(LAG_HARD_S * 2) is False
        rig.clock.advance(LAG_HARD_S + 0.1)
        drain(viewer)
    assert viewer.close_reason is None and rig.stalled == []
    assert viewer.stats()["resyncs"] == 0


def test_the_measured_rate_remembers_a_bounded_time_even_when_nobody_asks_for_it():
    rig = Rig()
    rig.config()
    rig.idr()
    for _ in range(2000):
        rig.clock.advance(0.1)
        rig.delta()
    assert len(rig.hub._recent) <= int(hub_module.MEASURE_MEMORY_S / 0.1) + 2


def test_an_internal_viewer_is_not_a_path_for_the_adaptation():
    rig = Rig()
    rig.config()
    rig.idr()
    client = rig.hub.add_viewer()
    recorder = rig.hub.add_viewer(internal=True)
    assert [sample.viewer for sample in rig.hub.path_samples(10.0)] == [client.id]
    assert rig.hub.viewer_count == 2  # it still keeps the stream alive
    assert recorder.internal is True


# ---- HEVC: the same hub, reading the units as the codec family the line names

HEVC_LINE = CodecLine("hev1.1.6.L93.B0", 540, 1200, 30)
OTHER_HEVC_LINE = CodecLine("hev1.1.6.L93.B0", 1200, 540, 30)  # another coded size: another SPS
# Only the two-byte NAL headers matter to the hub (the type is in bits 1 to 6 of the first byte); the rest is made up
# without a ``00 00 01`` in it.
H_AUD = START + bytes([0x46, 0x01, 0x50])
H_VPS = START + bytes([0x40, 0x01, 0x0C, 0x01, 0xFF, 0xFF, 0x01, 0x60, 0x00, 0x00, 0x03, 0x00, 0x90])
H_SPS = START + bytes([0x42, 0x01, 0x01, 0x01, 0x60, 0x00, 0x00, 0x03, 0x00, 0x90, 0x00, 0x00, 0x03, 0x00, 0x3C, 0xA0])
H_PPS = START + bytes([0x44, 0x01, 0xC1, 0x72, 0xB4, 0x62, 0x40])
H_PARAMS = H_VPS + H_SPS + H_PPS
H_OTHER_PARAMS = (
    START
    + bytes([0x40, 0x01, 0x0C, 0x02, 0xFF, 0xFF])
    + START
    + bytes([0x42, 0x01, 0x01, 0x02, 0x60, 0x11])
    + START
    + bytes([0x44, 0x01, 0xC2, 0x73])
)
#: ``nal_unit_type`` << 1: IDR_W_RADL (19), the other key pictures (16 to 21) and the first reserved one (22)
H_IDR, H_CRA, H_RESERVED_IRAP = 0x26, 0x2A, 0x2C


def hevc_idr_unit(n, params=H_PARAMS, pad=100, kind=H_IDR):
    return H_AUD + params + START + bytes([kind, 0x01, 0xAF, 0x08]) + f"K{n:08d}".encode() + b"\xaf" * pad


def hevc_delta_unit(n, ref=True, pad=20, kind=None):
    kind = (0x02 if ref else 0x00) if kind is None else kind  # TRAIL_R (1) or TRAIL_N (0)
    return H_AUD + START + bytes([kind, 0x01, 0x9A, 0x08]) + f"D{n:08d}".encode() + b"\x11" * pad


class HevcRig(Rig):
    """The rig for an HEVC stream: its codec line, and key and delta units of the family."""

    def config(self, line=HEVC_LINE):
        return super().config(line)

    def idr(self, params=None, pad=100, kind=H_IDR):
        self.n += 1
        self.hub.publish_media(self.n * 1000, hevc_idr_unit(self.n, H_PARAMS if params is None else params, pad, kind))
        return self.n

    def delta(self, count=1, *, ref=True, pad=20, kind=None):
        for _ in range(count):
            self.n += 1
            # the unit alone never says an HEVC picture is free to drop: the source does, with the hint
            unit = hevc_delta_unit(self.n, ref, pad, kind)
            self.hub.publish_media(self.n * 1000, unit, reference=None if ref else False)
        return self.n


@pytest.mark.parametrize("cost", ["cheap", "restart", "none"])
def test_an_hevc_joiner_starts_at_once_from_the_cached_gop_with_its_parameter_sets_in_front(cost):
    rig = HevcRig(cost)
    rig.config()
    rig.idr()
    rig.delta(3)
    rig.clock.advance(0.5)
    viewer = rig.hub.add_viewer()
    frames = drain(viewer)
    assert describe(frames) == ["config", "idr:1", "delta:2", "delta:3", "delta:4"]
    assert line_of(frames[0])["codec"] == HEVC_LINE.codec and line_of(frames[0])["format"] == "annexb"
    assert unit_of(frames[1]) == hevc_idr_unit(1) and unit_of(frames[1]).startswith(H_AUD + H_PARAMS)
    rig.delta()
    assert describe(drain(viewer)) == ["delta:5"]
    assert rig.asked == [] and rig.hub.joins_replayed == 1 and rig.hub.joins_waited == 0


def test_an_hevc_key_unit_without_parameter_sets_is_given_the_latest_vps_sps_and_pps_after_its_delimiter():
    rig = HevcRig()
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.idr()
    drain(viewer)
    rig.idr(params=b"")  # the source did not repeat them
    (frame,) = drain(viewer)
    assert frame.idr and rig.hub.keys_repaired == 1 and rig.hub.keys_without_params == 0
    assert unit_of(frame) == hevc_idr_unit(2) == H_AUD + H_PARAMS + unit_of(frame)[len(H_AUD + H_PARAMS) :]
    assert rig.hub.gop()["parameter_set_bytes"] == len(H_PARAMS)
    assert unit_of(drain(rig.hub.add_viewer())[1]) == hevc_idr_unit(2)  # the cached group starts with the repaired unit


@pytest.mark.parametrize(
    "carried",
    [H_VPS, H_SPS, H_PPS, H_VPS + H_SPS, H_VPS + H_PPS, H_SPS + H_PPS],
    ids=["vps", "sps", "pps", "vps+sps", "vps+pps", "sps+pps"],
)
def test_an_hevc_key_unit_with_some_of_its_sets_gets_the_others_where_they_belong(carried):
    rig = HevcRig()
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.idr()
    drain(viewer)
    rig.idr(params=carried)
    (frame,) = drain(viewer)
    assert unit_of(frame) == hevc_idr_unit(2) and rig.hub.keys_repaired == 1  # always VPS, SPS, PPS, picture


def test_an_hevc_key_unit_that_has_all_its_sets_is_not_touched_and_a_delta_unit_never_gets_any():
    rig = HevcRig()
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.idr()
    rig.delta()
    (_, key, delta) = drain(viewer)
    assert unit_of(key) == hevc_idr_unit(1) and unit_of(delta) == hevc_delta_unit(2) and rig.hub.keys_repaired == 0


def test_an_hevc_key_unit_without_a_vps_is_no_start_until_one_has_been_seen():
    rig = HevcRig()
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.idr(params=H_SPS + H_PPS)  # the pair H.264 needs is not what HEVC needs
    assert describe(drain(viewer)) == ["config"] and viewer.waiting_idr
    assert rig.hub.keys_without_params == 1 and len(rig.asked) == 1 and rig.hub.last_keyframe_reason == "params"
    rig.idr()
    assert describe(drain(viewer)) == ["idr:2"]
    rig.idr(params=H_SPS + H_PPS)  # now the VPS is known: it is put back
    assert unit_of(drain(viewer)[0]) == hevc_idr_unit(3)


@pytest.mark.parametrize(
    ("kind", "key"),
    [(0x20, True), (0x22, True), (0x24, True), (0x26, True), (0x28, True), (0x2A, True), (0x2C, False), (0x02, False)],
    ids=["BLA_W_LP", "BLA_W_RADL", "BLA_N_LP", "IDR_W_RADL", "IDR_N_LP", "CRA", "reserved IRAP", "TRAIL_R"],
)
def test_the_key_pictures_of_hevc_are_the_irap_types_16_to_21(kind, key):
    rig = HevcRig()
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.idr(kind=kind)
    assert describe(drain(viewer)) == (["config", "idr:1"] if key else ["config"])


@pytest.mark.parametrize("kind", [0, 2, 4, 6, 8, 10, 12, 14, 1, 3, 5, 7, 9, 11, 13, 15])
def test_an_hevc_picture_is_a_reference_whatever_its_type_when_the_source_does_not_say(kind):
    rig = HevcRig()
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.idr()
    rig.delta(kind=kind << 1)
    assert drain(viewer)[-1].reference is True  # a higher sub-layer may predict from a ``_N`` picture


def test_an_hevc_picture_the_source_says_nothing_depends_on_is_not_a_reference():
    rig = HevcRig()
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.idr()
    rig.delta(ref=False)
    assert drain(viewer)[-1].reference is False


def test_an_hevc_picture_with_a_reference_slice_among_its_slices_is_a_reference():
    rig = HevcRig()
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.idr()
    drain(viewer)
    two_slices = hevc_delta_unit(2, ref=False) + START + bytes([0x02, 0x01, 0x9A, 0x08, 0x31])
    rig.hub.publish_media(2000, two_slices)
    assert drain(viewer)[0].reference is True


def test_hevc_joiners_share_the_key_picture_already_asked_for_and_it_is_asked_again_if_it_never_comes():
    rig = HevcRig(keyframe_min_interval_s=2.0)
    rig.config()
    rig.idr()
    rig.delta()
    rig.clock.advance(5)  # the group is old: every joiner has to wait
    first = rig.hub.add_viewer()
    assert rig.asked == [105.0]
    rig.clock.advance(0.5)
    second = rig.hub.add_viewer()
    assert rig.asked == [105.0] and requests(rig)["shared"] == 1  # the one on its way serves this one too
    rig.clock.advance(1.7)
    assert rig.hub.poll() is True and requests(rig)["retries"] == 1  # and it never came: asked again, for both
    rig.idr(kind=H_CRA)  # a CRA is a key picture too
    assert describe(drain(first)) == ["config", "idr:3"] and describe(drain(second)) == ["config", "idr:3"]
    rig.clock.advance(5)
    assert rig.hub.poll() is False and len(rig.asked) == 2


def test_an_hevc_slow_viewer_is_dropped_the_pictures_nothing_depends_on_first_and_the_picture_stays_clean():
    rig = HevcRig("none", max_lag_s=0.5)
    rig.config()
    rig.idr()
    viewer = rig.hub.add_viewer()
    drain(viewer)
    for i in range(1, 7):  # alternate reference and non-reference pictures
        rig.clock.advance(0.1)
        rig.delta(ref=bool(i % 2))
    rig.clock.advance(0.2)  # the oldest queued picture has now waited 0.7 s: behind, but not 2.5 times behind
    rig.delta()
    got = drain(viewer)
    assert viewer.dropped_non_reference == 3 and viewer.resyncs == 0
    assert all(f.reference for f in got if f.kind == KIND_MEDIA)
    assert all(hevc_nal_type(unit_of(f)) == 1 for f in got if f.kind == KIND_MEDIA and not f.idr)  # TRAIL_R only


def hevc_nal_type(unit):
    return next(t for t, _, _ in iter_nals(unit, "hevc") if t < 32)


def test_an_hevc_viewer_far_behind_with_only_reference_pictures_skips_to_the_next_key_picture_and_the_hub_is_told():
    rig = HevcRig("none", max_lag_s=0.5)
    rig.config()
    slow, quick = rig.hub.add_viewer(), rig.hub.add_viewer()
    rig.idr()
    for _ in range(3):
        rig.clock.advance(0.1)
        rig.delta()
        drain(quick)
    rig.clock.advance(1.5)
    rig.delta()
    assert slow.resyncs == 1 and rig.congested == [slow] and quick.resyncs == 0
    rig.delta()
    assert describe(drain(slow)) == ["config"]  # nothing for the slow one until a key picture
    rig.idr(kind=H_CRA)
    assert describe(drain(slow)) == ["idr:7"]


def test_a_new_hevc_sps_forgets_the_old_parameter_sets_and_the_new_ones_repair_from_then_on():
    rig = HevcRig()
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.idr()
    drain(viewer)
    rig.config(OTHER_HEVC_LINE)
    assert rig.hub.gop()["parameter_set_bytes"] == 0 and viewer.waiting_idr
    rig.idr(params=b"")  # nothing to repair it with: no start
    assert describe(drain(viewer)) == ["config"] and rig.hub.keys_without_params == 1
    rig.idr(params=H_OTHER_PARAMS)
    assert describe(drain(viewer)) == ["idr:3"] and rig.hub.gop()["parameter_set_bytes"] == len(H_OTHER_PARAMS)
    rig.idr(params=b"")
    assert unit_of(drain(viewer)[-1]) == hevc_idr_unit(4, params=H_OTHER_PARAMS)


def test_hevc_stats_name_the_family_and_count_key_pictures_and_parameter_set_bytes():
    rig = HevcRig()
    rig.config()
    rig.hub.add_viewer()
    rig.idr()
    rig.delta(3)
    stats = rig.hub.stats()
    assert stats["family"] == "hevc" and stats["frames"] == 4 and stats["idrs"] == 1 and stats["invalid_units"] == 0
    assert stats["gop"]["frames"] == 4 and stats["gop"]["parameter_set_bytes"] == len(H_PARAMS)


def strip_parameter_sets(unit):
    """The unit without its VPS, SPS and PPS."""
    return b"".join(START + unit[s:e] for t, s, e in iter_nals(unit, "hevc") if t not in (32, 33, 34))


def nals_of(unit):
    return [(t, unit[s:e]) for t, s, e in iter_nals(unit, "hevc")]


def test_the_real_hevc_pattern_goes_through_the_hub_as_it_is_and_a_joiner_starts_at_its_last_key_picture():
    units = pattern_units("hevc")
    line = CodecLine(codec_string_of(units[0], "hevc") or "", 240, 400, 15)
    assert line.codec == "hev1.1.6.L60.90" and line.family == "hevc"
    rig = Rig("none")
    rig.config(line)
    viewer = rig.hub.add_viewer()
    seen = []
    for i, unit in enumerate(units[:50]):
        rig.hub.publish_media(i * 66_667, unit)  # no hints: the hub reads the NAL units
        rig.clock.advance(1 / 15)
        seen += [unit_of(f) for f in drain(viewer) if f.kind == KIND_MEDIA]
    assert seen == list(units[:50])  # every key picture carried its sets: nothing was repaired
    assert rig.hub.idrs == 4 and rig.hub.keys_repaired == 0 and rig.hub.keys_without_params == 0
    joiner = drain(rig.hub.add_viewer())
    assert [unit_of(f) for f in joiner if f.kind == KIND_MEDIA] == list(units[45:50])
    assert joiner[1].idr and not any(f.idr for f in joiner[2:])
    assert rig.hub.gop()["parameter_set_bytes"] == len(
        b"".join(START + n for t, n in nals_of(units[0]) if t in (32, 33, 34))
    )


def test_a_real_hevc_key_picture_that_lost_its_sets_gets_the_real_ones_back_in_front_of_its_picture():
    units = pattern_units("hevc")
    line = CodecLine(codec_string_of(units[0], "hevc") or "", 240, 400, 15)
    rig = Rig("none")
    rig.config(line)
    viewer = rig.hub.add_viewer()
    rig.hub.publish_media(0, units[0])
    (first,) = [unit_of(f) for f in drain(viewer) if f.kind == KIND_MEDIA]
    rig.hub.publish_media(1, strip_parameter_sets(units[15]))  # the encoder said its sets once, at the start
    (second,) = [unit_of(f) for f in drain(viewer) if f.kind == KIND_MEDIA]
    assert second != units[15] and nals_of(second) == nals_of(units[15])  # the same units, in the same order
    assert [t for t, _ in nals_of(second)] == [35, 32, 33, 34, 20] and rig.hub.keys_repaired == 1
    assert nals_of(first) == nals_of(units[0])


# ---- Motion JPEG: every unit is a key picture

MJPEG_LINE = CodecLine("mjpeg", 540, 1200, 30)


def jpeg_unit(n, pad=200):
    return b"\xff\xd8\xff\xe0" + f"K{n:08d}".encode() + b"\x11" * pad + b"\xff\xd9"


class MjpegRig(Rig):
    """The rig for a Motion JPEG stream: its codec line and numbered pictures."""

    def config(self, line=MJPEG_LINE):
        return super().config(line)

    def picture(self, count=1, pad=200, **hints):
        for _ in range(count):
            self.n += 1
            self.hub.publish_media(self.n * 1000, jpeg_unit(self.n, pad), **hints)
        return self.n


@pytest.mark.parametrize("cost", ["none", "restart", "cheap"])
def test_an_mjpeg_joiner_is_given_the_latest_picture_at_once_and_waits_for_nothing(cost):
    rig = MjpegRig(cost)
    rig.config()
    rig.picture(3)
    rig.clock.advance(0.5)
    viewer = rig.hub.add_viewer()
    frames = drain(viewer)
    assert describe(frames) == ["config", "idr:3"] and line_of(frames[0])["format"] == "jpeg"
    assert unit_of(frames[1]) == jpeg_unit(3)
    rig.picture()
    assert describe(drain(viewer)) == ["idr:4"]
    assert rig.asked == [] and rig.hub.joins_replayed == 1 and rig.hub.joins_waited == 0 and not viewer.waiting_idr


@pytest.mark.parametrize("cost", ["none", "restart", "cheap"])
def test_a_picture_larger_than_the_replay_limit_is_still_given_to_an_mjpeg_joiner(cost):
    rig = MjpegRig(cost, join_replay_max_bytes=100)  # the limit is for a burst of many pictures
    rig.config()
    rig.picture(pad=5000)
    viewer = rig.hub.add_viewer()
    assert describe(drain(viewer)) == ["config", "idr:1"] and rig.asked == [] and rig.hub.joins_waited == 0


@pytest.mark.parametrize("cost", ["none", "restart"])
def test_a_still_mjpeg_picture_is_given_to_a_joiner_however_old_it_is_unless_the_source_is_cheap_to_ask(cost):
    rig = MjpegRig(cost, join_replay_max_age_s=1.5)
    rig.config()
    rig.picture()
    rig.clock.advance(60)  # a still screen sends nothing
    assert describe(drain(rig.hub.add_viewer())) == ["config", "idr:1"] and rig.asked == []


def test_an_old_mjpeg_picture_is_held_to_the_age_limit_like_any_key_picture_by_a_source_cheap_to_ask():
    rig = MjpegRig("cheap", join_replay_max_age_s=1.5, join_wait_s=1.0)
    rig.config()
    rig.picture()
    rig.clock.advance(2.0)
    viewer = rig.hub.add_viewer()
    assert describe(drain(viewer)) == ["config"] and viewer.waiting_idr and len(rig.asked) == 1
    rig.picture()  # the next picture is a start at once
    assert describe(drain(viewer)) == ["idr:2"] and not viewer.waiting_idr


@pytest.mark.parametrize("cost", ["none", "restart", "cheap"])
def test_an_mjpeg_viewer_at_the_very_start_waits_for_the_first_picture_without_asking_for_one(cost):
    rig = MjpegRig(cost)
    viewer = rig.hub.add_viewer()
    rig.config()
    assert describe(drain(viewer)) == ["config"] and viewer.waiting_idr
    rig.picture()
    assert describe(drain(viewer)) == ["idr:1"] and not viewer.waiting_idr
    assert rig.asked == [] and rig.hub.keyframe_requests == 0


def test_a_viewer_that_does_not_read_is_left_with_the_latest_mjpeg_picture_only_and_is_never_resynced():
    rig = MjpegRig("none")
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.picture(40, pad=3000)  # 120 kB: far past the viewer's queue limit if they were kept
    assert describe(drain(viewer)) == ["config", "idr:40"]
    assert viewer.dropped_for_idr == 39 and viewer.resyncs == 0 and viewer.dropped_in_resync == 0
    assert not viewer.waiting_idr and viewer.keyframes_only is False and rig.congested == []


def test_a_slow_mjpeg_viewer_shows_it_to_the_adaptation_though_its_queue_never_grows():
    rig = MjpegRig("none", max_lag_s=0.5)
    rig.config()
    slow, quick = rig.hub.add_viewer(), rig.hub.add_viewer()
    rig.picture()
    drain(slow)
    drain(quick)
    for _ in range(20):  # two seconds at ten pictures a second: the slow one reads nothing
        rig.clock.advance(0.1)
        rig.picture()
        drain(quick)
    samples = {sample.viewer: sample for sample in rig.hub.path_samples(10)}
    assert samples[quick.id].events == 0 and samples[quick.id].peak == 0.0
    assert samples[slow.id].events >= 3 and samples[slow.id].peak > 2.0  # about 0.5 s apart; 2 s is 4 times the limit
    assert slow.resyncs == 0 and slow.keyframes_only is False and rig.congested == []
    assert describe(drain(slow)) == ["idr:21"]  # and what it gets is the latest picture, whole


def test_an_mjpeg_viewer_that_reads_every_other_picture_is_no_slower_than_the_limit_and_shows_nothing():
    rig = MjpegRig("none", max_lag_s=0.5)
    rig.config()
    viewer = rig.hub.add_viewer()
    for i in range(40):
        rig.clock.advance(0.1)
        rig.picture()
        if i % 2:
            drain(viewer)  # every 0.2 s
    sample = rig.hub.path_samples(10)[0]
    assert sample.events == 0 and sample.peak < 1.0
    assert viewer.dropped_for_idr >= 15 and viewer.resyncs == 0


def test_a_replay_is_no_lag_for_an_mjpeg_viewer_that_has_not_read_it_yet():
    rig = MjpegRig("none", max_lag_s=0.5)
    rig.config()
    rig.picture()
    viewer = rig.hub.add_viewer()  # primed with the latest picture, which it does not read
    rig.clock.advance(3.0)
    rig.picture()
    assert viewer.path(10).events == 0 and describe(drain(viewer)) == ["config", "idr:2"]


@pytest.mark.parametrize("cost", ["none", "restart"])
def test_an_mjpeg_resync_is_served_the_latest_picture_at_once_and_nobody_is_asked(cost):
    rig = MjpegRig(cost)
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.picture(2)
    drain(viewer)
    rig.hub.resync(viewer)
    assert describe(drain(viewer)) == ["idr:2"] and rig.asked == [] and not viewer.waiting_idr


def test_an_mjpeg_viewer_that_says_it_is_far_behind_is_back_on_the_stream_at_the_next_picture():
    rig = MjpegRig("none")
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.picture()
    drain(viewer)
    assert rig.hub.report_lag(viewer.id, LAG_HARD_S * 2) is True and viewer.waiting_idr
    rig.picture()  # there is no key picture to wait for: this one is one
    assert describe(drain(viewer)) == ["idr:2"] and not viewer.waiting_idr and rig.asked == []


@pytest.mark.parametrize("cost", ["cheap", "restart", "none"])
def test_pictures_lost_from_an_mjpeg_stream_leave_no_hole_so_nothing_is_dropped_held_or_asked(cost):
    rig = MjpegRig(cost)
    rig.config()
    rig.picture(2)
    viewer = rig.hub.add_viewer()
    drain(viewer)
    rig.hub.discontinuity()
    assert rig.hub.discontinuities == 1 and rig.hub.gop()["frames"] == 1 and not viewer.waiting_idr
    assert rig.asked == [] and rig.hub.keyframe_requests == 0
    rig.picture()
    assert describe(drain(viewer)) == ["idr:3"]
    assert describe(drain(rig.hub.add_viewer())) == ["config", "idr:3"]


def test_what_a_source_says_of_an_mjpeg_picture_is_not_believed_it_is_a_key_picture_and_a_reference_by_what_it_is():
    rig = MjpegRig("none")
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.picture(idr=False, reference=False)  # a driver that reads units as H.264 says so of every one of them
    (_, frame) = drain(viewer)
    assert frame.idr and frame.reference and rig.hub.gop()["frames"] == 1


def test_a_unit_that_is_not_a_jpeg_is_dropped_and_counted_and_nobody_is_sent_it():
    rig = MjpegRig("none")
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.hub.publish_media(1, START + b"\x65" + b"\x88" * 20)  # H.264 in an MJPEG stream
    rig.hub.publish_media(2, b"")
    assert describe(drain(viewer)) == ["config"]  # the line, and no picture
    assert rig.hub.invalid_units == 2 and rig.hub.frames == 0 and rig.hub.gop()["frames"] == 0
    rig.picture()
    assert describe(drain(viewer))[-1] == "idr:1" and rig.hub.stats()["invalid_units"] == 2


def test_mjpeg_stats_count_every_picture_as_a_key_picture_and_the_cache_is_the_latest_one():
    rig = MjpegRig()
    rig.config()
    rig.hub.add_viewer()
    rig.picture(5, pad=300)
    stats = rig.hub.stats()
    assert stats["family"] == "mjpeg" and stats["frames"] == 5 and stats["idrs"] == 5 and stats["last_idr_age_s"] == 0.0
    assert stats["gop"]["frames"] == 1 and stats["gop"]["parameter_set_bytes"] == 0
    assert stats["gop"]["bytes"] == len(jpeg_unit(5, 300)) + 13 and stats["gop"]["age_s"] == 0.0
    assert stats["keys_without_params"] == 0 and stats["keys_repaired"] == 0 and stats["invalid_units"] == 0
    assert stats["keyframe_requests"]["requested"] == 0 and stats["viewers"][0]["waiting_for_idr"] is False
    rig.clock.advance(1)
    assert rig.hub.stats()["last_idr_age_s"] == 1.0 and rig.hub.gop()["age_s"] == 1.0


def test_the_real_mjpeg_pattern_goes_through_the_hub_whole_and_a_joiner_is_given_the_latest_picture():
    units = pattern_units("mjpeg")
    rig = Rig("none")
    rig.config(CodecLine("mjpeg", 240, 400, 15))
    viewer = rig.hub.add_viewer()
    seen = []
    for i, unit in enumerate(units):
        rig.hub.publish_media(i * 66_667, unit)
        rig.clock.advance(1 / 15)
        seen += [unit_of(f) for f in drain(viewer) if f.kind == KIND_MEDIA]
    assert seen == list(units) and rig.hub.idrs == len(units) == 15
    joiner = drain(rig.hub.add_viewer())
    assert [unit_of(f) for f in joiner if f.kind == KIND_MEDIA] == [units[-1]]


# ---- a change of codec family

#: ``(codec line, a key unit of picture n with or without its parameter sets, a delta unit of picture n)``, by family
FAMILIES = {
    "h264": (LINE, lambda n, sets=True: idr_unit(n, PARAMS if sets else b""), delta_unit),
    "hevc": (HEVC_LINE, lambda n, sets=True: hevc_idr_unit(n, H_PARAMS if sets else b""), hevc_delta_unit),
    "mjpeg": (MJPEG_LINE, lambda n, sets=True: jpeg_unit(n), jpeg_unit),
}
SWITCHES = [
    ("h264", "hevc"),
    ("hevc", "h264"),
    ("h264", "mjpeg"),
    ("mjpeg", "h264"),
    ("hevc", "mjpeg"),
    ("mjpeg", "hevc"),
]


@pytest.mark.parametrize(("before", "after"), SWITCHES)
def test_a_switch_of_codec_family_drops_the_cache_and_viewers_get_the_new_line_then_a_key_picture(before, after):
    rig = Rig("none")
    old_line, old_key, old_delta = FAMILIES[before]
    new_line, new_key, new_delta = FAMILIES[after]
    rig.config(old_line)
    viewer = rig.hub.add_viewer()
    rig.hub.publish_media(1000, old_key(1))
    rig.hub.publish_media(2000, old_delta(2))
    drain(viewer)
    assert rig.hub.stats()["family"] == before and rig.hub.gop()["frames"] > 0
    assert rig.config(new_line) is True
    # the old group is no start for the new stream, and neither are the old parameter sets
    assert rig.hub.stats()["family"] == after and rig.hub.gop()["frames"] == 0
    assert rig.hub.gop()["parameter_set_bytes"] == 0 and viewer.waiting_idr and viewer.all_key == (after == "mjpeg")
    frames = drain(viewer)
    assert describe(frames) == ["config"] and line_of(frames[0])["codec"] == new_line.codec
    late = rig.hub.add_viewer()  # a joiner has nothing to start from either
    assert late.waiting_idr and describe(drain(late)) == ["config"]
    if after != "mjpeg":
        rig.hub.publish_media(3000, new_key(3, sets=False))  # no sets seen yet for this family: no start
        assert describe(drain(viewer)) == [] and rig.hub.keys_without_params == 1
    rig.hub.publish_media(4000, new_key(4))
    assert describe(drain(viewer)) == ["idr:4"] and describe(drain(late)) == ["idr:4"]
    assert not viewer.waiting_idr and rig.hub.gop()["frames"] == 1
    rig.hub.publish_media(5000, new_delta(5))
    assert describe(drain(viewer))[0] in ("delta:5", "idr:5")  # and the stream goes on in the new family


def test_a_switch_through_all_three_families_and_back_leaves_every_viewer_on_the_latest_stream():
    rig = Rig("none")
    viewer = rig.hub.add_viewer()
    for number, family in enumerate(("hevc", "mjpeg", "h264", "hevc"), start=1):
        line, key, _ = FAMILIES[family]
        rig.config(line)
        rig.hub.publish_media(number * 1000, key(number))
        frames = drain(viewer)
        assert describe(frames) == ["config", f"idr:{number}"] and line_of(frames[0])["codec"] == line.codec
    assert rig.hub.stats()["family"] == "hevc" and rig.hub.keys_without_params == 0


def test_a_change_of_picture_size_inside_a_family_keeps_that_family_and_mjpeg_has_nothing_to_forget():
    rig = MjpegRig()
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.picture()
    drain(viewer)
    rig.config(replace(MJPEG_LINE, width=1200, height=540))  # another size: a new line, the decoder starts again
    assert viewer.waiting_idr and rig.hub.stats()["family"] == "mjpeg" and viewer.all_key
    rig.picture()
    assert describe(drain(viewer)) == ["config", "idr:2"]


def test_a_new_source_starts_again_as_h264_until_it_says_what_it_sends():
    rig = MjpegRig()
    rig.config()
    viewer = rig.hub.add_viewer()
    rig.picture()
    assert viewer.all_key is True
    rig.hub.reset_source()
    assert rig.hub.stats()["family"] == "h264" and rig.hub.gop()["frames"] == 0 and viewer.all_key is False
    rig.config(HEVC_LINE)
    rig.hub.publish_media(2000, hevc_idr_unit(2))
    assert describe(drain(viewer))[-2:] == ["config", "idr:2"] and rig.hub.stats()["family"] == "hevc"
    assert viewer.all_key is False
