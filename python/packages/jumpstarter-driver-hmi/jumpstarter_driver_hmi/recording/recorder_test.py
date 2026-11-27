"""Recording the hub's stream to an MP4 on the exporter: a real Hub fed from a thread, the limits, the file's safety."""

from __future__ import annotations

import os
import stat
import threading
import time
from pathlib import Path

import pytest

from ..common import CodecLine
from ..hub.codecs import has_idr
from ..hub.hub import Hub, StreamEnded
from . import recorder as recording_module
from .mp4 import Fmp4Muxer
from .mp4_testing import (
    HAVE_FFMPEG,
    HAVE_X265,
    NO_FFMPEG,
    NO_X265,
    SPS_240x320,
    au,
    build_hevc_sps,
    decode,
    encode,
    encode_hevc,
    fragments,
    frame_sizes,
    hevc_stream,
    parse_boxes,
    pattern_stream,
    probe,
    stream,
)
from .recorder import Recording

LANDSCAPE = CodecLine("avc1.42C00C", 320, 240, 15)
PORTRAIT = CodecLine("avc1.42C00C", 240, 320, 15)
H264_PATTERN = CodecLine("avc1.42C01E", 240, 400, 15)
HEVC_PATTERN = CodecLine("hev1.1.6.L60.90", 240, 400, 15)  # the codec string of the HEVC test pattern's SPS
HEVC_LANDSCAPE = CodecLine("hev1.1.6.L60.90", 400, 240, 25)
HEVC_PORTRAIT = CodecLine("hev1.1.6.L60.90", 240, 400, 25)
MJPEG = CodecLine("mjpeg", 240, 400, 15)
JPEG = b"\xff\xd8\xff\xe0" + b"\x11" * 100 + b"\xff\xd9"


def wait_until(predicate, timeout: float = 8.0, what: str = "the condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


def feed(
    hub: Hub, items, *, line: CodecLine = LANDSCAPE, pause: float = 0.0005, at=None, close: bool = True
) -> threading.Thread:
    """Publish ``items`` (``(timestamp, access unit, is_key)``) from a thread; ``at(index)`` runs after each one."""

    def run() -> None:
        hub.publish_config(line)
        for index, (ts, unit, key) in enumerate(items):
            hub.publish_media(ts, unit, key)
            if at is not None:
                at(index)
            time.sleep(pause)
        if close:
            hub.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def samples_of(path: Path) -> list[tuple[int, int, int]]:
    return [s for f in fragments(path.read_bytes()) for s in f.samples]


def same_file(result_path: str, expected: Path) -> bool:
    return Path(result_path).resolve() == expected.resolve()


@pytest.fixture
def out(tmp_path):
    directory = tmp_path / "recordings"
    directory.mkdir()
    return directory


# ---- recording a stream ----------------------------------------------------------------------------------------------


def test_it_records_what_the_stream_carries_and_ends_by_itself_when_the_hub_closes(out):
    items = stream(count=45, gop=15, fps=15)
    hub = Hub()
    recording = Recording.start(hub, "all.mp4", root=out)
    feed(hub, items).join(10)
    wait_until(lambda: not recording.active, what="the recording to end with the stream")
    status = recording.status()
    assert status["active"] is False and status["reason"] == "stream_ended" and status["error"] is None
    result = recording.stop()
    assert same_file(result["path"], out / "all.mp4")
    assert result["reason"] == "stream_ended"
    assert (result["width"], result["height"]) == (320, 240)
    assert result["size"] == (out / "all.mp4").stat().st_size == status["bytes"]
    data = (out / "all.mp4").read_bytes()
    assert [b.kind for b in parse_boxes(data)[:2]] == ["ftyp", "moov"]
    samples = samples_of(out / "all.mp4")
    assert result["frames"] == len(samples) == status["frames"]
    assert result["frames"] == 45 and result["seconds"] == pytest.approx(3.0, abs=0.05)
    assert samples[0][2] == 0x02000000  # the file starts at a key picture
    assert hub.viewer_count == 0


@pytest.mark.parametrize(
    ("replay_bytes", "expected_first_key"), [(None, 0), (0, 15)], ids=["from the cached GOP", "from the next IDR"]
)
def test_a_recording_that_starts_in_the_middle_of_a_gop_starts_at_a_key_picture(out, replay_bytes, expected_first_key):
    items = stream(count=45, gop=15, fps=15)
    hub = Hub() if replay_bytes is None else Hub(join_replay_max_bytes=replay_bytes)
    joined, go = threading.Event(), threading.Event()

    def pause(index):
        if index == 6:
            joined.set()
            assert go.wait(8)

    thread = feed(hub, items, at=pause)
    assert joined.wait(8)
    recording = Recording.start(hub, "mid.mp4", root=out)
    go.set()
    thread.join(10)
    wait_until(lambda: not recording.active)
    result = recording.stop()
    # the hub's GOP gives the keyframe at 0; without a GOP it waits for 15
    assert result["frames"] == 45 - expected_first_key
    assert samples_of(out / "mid.mp4")[0][2] == 0x02000000


@pytest.mark.skipif(not HAVE_FFMPEG, reason=NO_FFMPEG)
def test_the_recording_of_real_h264_is_a_file_ffprobe_reads_and_ffmpeg_decodes(out, tmp_path):
    items = [(1_000_000_000 + i * 66_667, unit, has_idr(unit)) for i, unit in enumerate(encode(tmp_path / "c.h264"))]
    hub = Hub()
    recording = Recording.start(hub, "real.mp4", root=out)
    feed(hub, items).join(10)
    wait_until(lambda: not recording.active)
    result = recording.stop()
    info = probe(out / "real.mp4")
    assert info["stream"]["codec_name"] == "h264"
    assert (info["stream"]["width"], info["stream"]["height"]) == (320, 240) == (result["width"], result["height"])
    assert int(info["stream"]["nb_read_frames"]) == result["frames"] == 45
    assert float(info["format"]["duration"]) == pytest.approx(result["seconds"], abs=0.01)
    run = decode(out / "real.mp4")
    assert run.returncode == 0 and run.stderr == ""


@pytest.mark.skipif(not HAVE_FFMPEG, reason=NO_FFMPEG)
def test_the_file_of_a_recording_that_is_still_running_is_already_playable(out, tmp_path):
    """What a killed exporter leaves: the file in the middle of a recording, no length patched in, maybe a torn tail."""
    items = [(1_000_000_000 + i * 66_667, unit, has_idr(unit)) for i, unit in enumerate(encode(tmp_path / "c.h264"))]
    hub = Hub()
    recording = Recording.start(hub, "live.mp4", root=out)
    paused, go = threading.Event(), threading.Event()

    def hold(index):
        if index == 29:
            paused.set()
            assert go.wait(10)

    thread = feed(hub, items, at=hold)
    assert paused.wait(10)
    wait_until(lambda: recording.status()["frames"] >= 20, what="20 pictures in the file")
    snapshot = tmp_path / "snapshot.mp4"
    snapshot.write_bytes((out / "live.mp4").read_bytes())  # the recording goes on; this is what a crash now would leave
    taken = recording.status()["frames"]
    info = probe(snapshot)
    assert 15 <= int(info["stream"]["nb_read_frames"]) <= taken
    assert (info["stream"]["width"], info["stream"]["height"]) == (320, 240)
    assert decode(snapshot).returncode == 0
    go.set()
    thread.join(10)
    wait_until(lambda: not recording.active)
    assert recording.stop()["frames"] == 45


def test_stop_ends_a_running_recording_and_detaches_from_the_hub(out):
    items = stream(count=60, gop=10, fps=15)
    hub = Hub()
    recording = Recording.start(hub, "stopped.mp4", root=out)
    thread = feed(hub, items, pause=0.004, close=False)
    wait_until(lambda: recording.status()["frames"] >= 12, what="some pictures in the file")
    live = recording.status()
    assert (
        live["active"] is True
        and live["reason"] is None
        and live["bytes"] > 0
        and same_file(live["path"], out / "stopped.mp4")
    )
    result = recording.stop()
    assert result["reason"] == "stop" and result["error"] is None
    assert hub.viewer_count == 0
    thread.join(10)
    assert recording.stop() == result  # again: the same answer
    assert recording.status()["active"] is False
    samples = samples_of(out / "stopped.mp4")
    assert len(samples) == result["frames"] and 12 <= result["frames"] < 60
    assert (out / "stopped.mp4").stat().st_size == result["size"]
    hub.close()


def test_status_has_the_fields_a_caller_polls(out):
    hub = Hub()
    recording = Recording.start(hub, "s.mp4", root=out)
    status = recording.status()
    assert set(status) == {"active", "seconds", "frames", "bytes", "path", "reason", "error"}
    assert status["active"] is True and status["frames"] == 0 and status["bytes"] == 0 and status["seconds"] == 0.0
    result = recording.stop()
    assert result["frames"] == 0 and result["size"] == 0 and (result["width"], result["height"]) == (0, 0)
    expected = "name path size seconds frames width height reason error size_changes resyncs"
    assert set(result) == set(expected.split())
    assert result["name"] == "s.mp4"


def test_it_stops_by_itself_after_max_seconds_of_video(out):
    items = stream(count=90, gop=15, fps=15)  # six seconds of timestamps, published much faster than that
    hub = Hub()
    recording = Recording.start(hub, "limited.mp4", root=out, max_seconds=1.0)
    thread = feed(hub, items, pause=0.001, close=False)
    wait_until(lambda: not recording.active, what="the recording to reach its length")
    status = recording.status()
    assert status["reason"] == "max_seconds" and hub.viewer_count == 0
    result = recording.stop()
    assert 1.0 <= result["seconds"] < 1.2 and 15 <= result["frames"] <= 17
    thread.join(10)
    hub.close()
    assert len(samples_of(out / "limited.mp4")) == result["frames"]


def test_a_stream_that_goes_quiet_does_not_hold_a_recording_past_its_length(out, monkeypatch):
    monkeypatch.setattr(recording_module, "_WALL_GRACE_S", 0.1)
    hub = Hub()
    recording = Recording.start(hub, "quiet.mp4", root=out, max_seconds=0.3)
    feed(hub, stream(count=3, gop=3, fps=15), close=False).join(5)  # three pictures, 0.13 s of video, then nothing
    wait_until(lambda: not recording.active, timeout=5, what="the wall-clock end")
    result = recording.stop()
    assert result["reason"] == "max_seconds" and result["frames"] == 3
    hub.close()


def test_it_stops_by_itself_once_the_file_has_reached_max_bytes(out):
    items = stream(count=90, gop=15, fps=15)
    hub = Hub()
    cap = 2500
    recording = Recording.start(hub, "small.mp4", root=out, max_bytes=cap, fragment_ms=200)
    thread = feed(hub, items, pause=0.001, close=False)
    wait_until(lambda: not recording.active, what="the size limit")
    result = recording.stop()
    assert result["reason"] == "max_bytes" and result["error"] is None
    assert cap <= result["size"] < cap + 1500  # past the limit by at most the fragment that crossed it
    assert result["frames"] < 90 and len(samples_of(out / "small.mp4")) == result["frames"]
    assert hub.viewer_count == 0
    thread.join(10)
    hub.close()


def test_a_hub_that_closes_before_any_key_picture_leaves_an_empty_recording(out):
    hub = Hub()
    recording = Recording.start(hub, "none.mp4", root=out)
    hub.publish_config(LANDSCAPE)
    hub.publish_media(0, au(b"\x41" + b"\xbb" * 30), False)  # no key picture, so no start
    hub.close()
    wait_until(lambda: not recording.active)
    result = recording.stop()
    assert (
        result["reason"] == "stream_ended"
        and result["frames"] == 0
        and result["size"] == 0 == (out / "none.mp4").stat().st_size
    )


def test_a_size_change_in_the_stream_goes_on_in_the_same_file(out):
    before = stream(count=30, gop=15, fps=15)
    after = stream(count=30, gop=15, fps=15, sps=SPS_240x320, first_us=before[-1][0] + 66_667)
    hub = Hub()
    recording = Recording.start(hub, "turn.mp4", root=out)

    def run() -> None:
        hub.publish_config(LANDSCAPE)
        for ts, unit, key in before:
            hub.publish_media(ts, unit, key)
        hub.publish_config(PORTRAIT)
        for ts, unit, key in after:
            hub.publish_media(ts, unit, key)
        hub.close()

    threading.Thread(target=run, daemon=True).start()
    wait_until(lambda: not recording.active)
    result = recording.stop()
    assert result["frames"] == 60 and result["size_changes"] == 1 and result["reason"] == "stream_ended"
    assert (result["width"], result["height"]) == (320, 240)  # the header names the first picture's size
    assert len(samples_of(out / "turn.mp4")) == 60


@pytest.mark.skipif(not HAVE_FFMPEG, reason=NO_FFMPEG)
def test_a_rotation_in_real_h264_decodes_at_both_sizes(out, tmp_path):
    land = encode(tmp_path / "l.h264", size="320x240", seconds=2)
    port = encode(tmp_path / "p.h264", size="240x320", seconds=2)
    hub = Hub()
    recording = Recording.start(hub, "rot.mp4", root=out)

    def run() -> None:
        ts = 3_000_000_000
        for line, clip in ((LANDSCAPE, land), (PORTRAIT, port)):
            hub.publish_config(line)
            for unit in clip:
                hub.publish_media(ts, unit, has_idr(unit))
                ts += 66_667
                time.sleep(0.0005)
        hub.close()

    threading.Thread(target=run, daemon=True).start()
    wait_until(lambda: not recording.active)
    result = recording.stop()
    assert result["frames"] == 60 and result["size_changes"] == 1
    assert frame_sizes(out / "rot.mp4") == [(320, 240)] * 30 + [(240, 320)] * 30
    assert decode(out / "rot.mp4").returncode == 0


# ---- a slow disk -----------------------------------------------------------------------------------------------------


class SlowMuxer(Fmp4Muxer):
    """A muxer whose ``add`` takes a while: the disk of a bad day."""

    delay = 0.03

    def add(self, ts_us, access_unit, keyframe=None):
        time.sleep(self.delay)
        return super().add(ts_us, access_unit, keyframe)


def test_a_slow_disk_loses_no_pictures_because_the_recording_empties_its_viewer_at_once(out, monkeypatch):
    monkeypatch.setattr(recording_module, "Fmp4Muxer", SlowMuxer)
    # a hub that would throw the viewer's pictures away after 50 ms of lag
    # (the muxer takes 30 ms a picture: 45 pictures need 1.4 s)
    hub = Hub(max_lag_s=0.05, max_queue_bytes=2_000)
    items = stream(count=45, gop=15, fps=15)
    recording = Recording.start(hub, "slow.mp4", root=out)
    feed(hub, items, pause=0.0).join(10)
    wait_until(lambda: not recording.active, timeout=15)
    result = recording.stop()
    assert result["frames"] == 45 and result["resyncs"] == 0 and result["error"] is None


def test_a_disk_so_far_behind_that_memory_would_grow_ends_the_recording_with_an_error_and_a_valid_file(
    out, monkeypatch
):
    monkeypatch.setattr(recording_module, "Fmp4Muxer", SlowMuxer)
    monkeypatch.setattr(SlowMuxer, "delay", 0.05)
    hub = Hub()
    items = stream(count=45, gop=15, fps=15)
    recording = Recording.start(hub, "behind.mp4", root=out, max_pending_bytes=600)
    thread = feed(hub, items, pause=0.002, close=False)
    wait_until(lambda: not recording.active, timeout=15)
    result = recording.stop()
    thread.join(10)
    assert result["reason"] == "error" and "keep up" in result["error"]
    assert hub.viewer_count == 0
    data = (out / "behind.mp4").read_bytes()
    assert [b.kind for b in parse_boxes(data)][:2] == ["ftyp", "moov"]  # whatever was written is a whole file
    assert sum(len(f.samples) for f in fragments(data)) == result["frames"] >= 1
    hub.close()


def test_a_disk_error_ends_the_recording_with_the_error_and_closes_the_file(out, monkeypatch):
    hub = Hub()
    recording = Recording.start(hub, "err.mp4", root=out)

    class Failing:
        def write(self, data):
            raise OSError(28, "No space left on device")

        def flush(self):
            pass

        def seek(self, *_):
            pass

        def close(self):
            real.close()

    real = recording._file
    recording._file = Failing()  # ty: ignore[invalid-assignment]
    feed(hub, stream(count=30, gop=10, fps=15), close=False).join(5)
    wait_until(lambda: not recording.active)
    result = recording.stop()
    assert result["reason"] == "error" and "No space left" in result["error"]
    assert real.closed
    hub.close()


# ---- where it writes -------------------------------------------------------------------------------------------------


def test_the_file_is_private_and_new(out):
    old = os.umask(0)  # whatever the exporter's umask is, the file is 0600
    try:
        hub = Hub()
        recording = Recording.start(hub, "private.mp4", root=out)
        recording.stop()
    finally:
        os.umask(old)
    assert stat.S_IMODE((out / "private.mp4").stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        Recording.start(hub, "private.mp4", root=out)
    assert (out / "private.mp4").stat().st_size == 0  # and what was there was not touched


def test_it_never_writes_outside_the_directory_it_was_given(tmp_path, out):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "target").write_bytes(b"precious")
    (out / "dir-link").symlink_to(outside, target_is_directory=True)
    (out / "file-link.mp4").symlink_to(outside / "target")
    hub = Hub()
    for bad in ("../evil.mp4", outside / "evil.mp4", "dir-link/evil.mp4", "sub/../../evil.mp4", "..", ".", "", "a/.."):
        with pytest.raises((ValueError, FileNotFoundError)):
            Recording.start(hub, bad, root=out)
    with pytest.raises(FileExistsError):  # a symlink at the name is not followed
        Recording.start(hub, "file-link.mp4", root=out)
    with pytest.raises(FileNotFoundError):
        Recording.start(hub, "missing-dir/x.mp4", root=out)
    assert sorted(p.name for p in outside.iterdir()) == ["target"]
    assert (outside / "target").read_bytes() == b"precious"
    assert hub.viewer_count == 0
    assert sorted(p.name for p in out.iterdir()) == ["dir-link", "file-link.mp4"]


def test_a_path_inside_the_directory_may_be_absolute_or_in_a_subdirectory(out):
    (out / "sub").mkdir()
    hub = Hub()
    first = Recording.start(hub, out / "sub" / "a.mp4", root=out)
    second = Recording.start(hub, "sub/b.mp4", root=out)
    assert same_file(first.stop()["path"], out / "sub" / "a.mp4") and same_file(
        second.stop()["path"], out / "sub" / "b.mp4"
    )


def test_without_a_root_the_directory_must_exist_and_the_name_must_be_new(tmp_path):
    hub = Hub()
    recording = Recording.start(hub, tmp_path / "x.mp4")
    assert recording.stop()["frames"] == 0
    with pytest.raises(FileNotFoundError):
        Recording.start(hub, tmp_path / "nowhere" / "x.mp4")


def test_a_limit_must_be_positive_and_a_closed_hub_takes_no_recording_and_leaves_no_file(out):
    hub = Hub()
    for bad in ({"max_seconds": 0}, {"max_seconds": -1}, {"max_bytes": 0}):
        with pytest.raises(ValueError, match="positive"):
            Recording.start(hub, "x.mp4", root=out, **bad)
    hub.close()
    with pytest.raises(StreamEnded):
        Recording.start(hub, "x.mp4", root=out)
    assert list(out.iterdir()) == []


def test_it_is_a_context_manager_that_stops_on_exit(out):
    hub = Hub()
    with Recording.start(hub, "ctx.mp4", root=out) as recording:
        assert recording.active
        feed(hub, stream(count=20, gop=10, fps=15), close=False).join(5)
        wait_until(lambda: recording.status()["frames"] >= 5)
    assert not recording.active and hub.viewer_count == 0
    hub.close()


def test_joining_asks_the_hub_for_nothing_it_does_not_ask_everyone_for(out):
    asked = []
    hub = Hub(on_need_idr=lambda: asked.append(1))
    hub.publish_config(LANDSCAPE)
    items = stream(count=20, gop=10, fps=15)
    for ts, unit, key in items[:5]:
        hub.publish_media(ts, unit, key)
    # it is served the cached GOP: no key picture is asked of the device
    recording = Recording.start(hub, "gop.mp4", root=out)
    result = recording.stop()
    assert asked == [] and result["frames"] == 5


# ---- HEVC (and the H.264 pattern, through the same checks) -----------------------------------------------------------


@pytest.mark.skipif(not HAVE_FFMPEG, reason=NO_FFMPEG)
@pytest.mark.parametrize(
    ("family", "line", "codec_name", "tag"),
    [("hevc", HEVC_PATTERN, "hevc", "hev1"), ("h264", H264_PATTERN, "h264", "avc1")],
)
def test_the_recording_of_the_real_pattern_is_a_file_ffprobe_reads_in_its_codec_and_ffmpeg_decodes(
    out, family, line, codec_name, tag
):
    items = pattern_stream(family)
    hub = Hub()
    recording = Recording.start(hub, "pattern.mp4", root=out)
    feed(hub, items, line=line).join(10)
    wait_until(lambda: not recording.active)
    result = recording.stop()
    assert result["reason"] == "stream_ended" and result["error"] is None and result["frames"] == 60
    assert (result["width"], result["height"]) == (240, 400) and result["seconds"] == pytest.approx(4.0, abs=0.01)
    info = probe(out / "pattern.mp4")
    assert (info["stream"]["codec_name"], info["stream"]["codec_tag_string"]) == (codec_name, tag)
    assert (info["stream"]["width"], info["stream"]["height"]) == (240, 400)
    assert int(info["stream"]["nb_read_frames"]) == 60
    assert float(info["format"]["duration"]) == pytest.approx(result["seconds"], abs=0.01)
    run = decode(out / "pattern.mp4")
    assert run.returncode == 0 and run.stderr == ""


def test_an_hevc_recording_starts_at_a_key_picture_from_the_cached_gop_and_keeps_every_picture_after_it(out):
    items = pattern_stream("hevc")
    hub = Hub()
    hub.publish_config(HEVC_PATTERN)
    for ts, unit, key in items[:20]:  # the cached group starts at picture 15
        hub.publish_media(ts, unit, key)
    recording = Recording.start(hub, "mid.mp4", root=out)
    for ts, unit, key in items[20:]:
        hub.publish_media(ts, unit, key)
    hub.close()
    wait_until(lambda: not recording.active)
    result = recording.stop()
    assert result["frames"] == 60 - 15 and result["reason"] == "stream_ended"
    samples = samples_of(out / "mid.mp4")
    assert samples[0][2] == 0x02000000 and [flag for _, _, flag in samples].count(0x02000000) == 3


def test_an_hevc_recording_has_an_hev1_sample_entry(out):
    hub = Hub()
    recording = Recording.start(hub, "hevc.mp4", root=out)
    feed(hub, stream_of_hevc(), line=HEVC_LANDSCAPE).join(5)
    wait_until(lambda: not recording.active)
    recording.stop()
    sample_entry = parse_boxes((out / "hevc.mp4").read_bytes())[1].find("trak", "mdia", "minf", "stbl", "stsd")
    assert [c.kind for c in sample_entry.children] == ["hev1"]


def stream_of_hevc(count=30, gop=10, fps=15, **options):
    return hevc_stream(count=count, gop=gop, fps=fps, **options)


def test_a_size_change_in_an_hevc_stream_goes_on_in_the_same_file(out):
    sps_landscape = build_hevc_sps(400, 240)
    before = stream_of_hevc(sps=sps_landscape)
    after = stream_of_hevc(first_us=before[-1][0] + 66_667)  # the pattern's own 240x400 SPS
    hub = Hub()
    recording = Recording.start(hub, "turn.mp4", root=out)

    def run() -> None:
        hub.publish_config(HEVC_LANDSCAPE)
        for ts, unit, key in before:
            hub.publish_media(ts, unit, key)
        hub.publish_config(HEVC_PORTRAIT)  # the same codec family: one file
        for ts, unit, key in after:
            hub.publish_media(ts, unit, key)
        hub.close()

    threading.Thread(target=run, daemon=True).start()
    wait_until(lambda: not recording.active)
    result = recording.stop()
    assert result["frames"] == 60 and result["size_changes"] == 1 and result["reason"] == "stream_ended"
    assert (result["width"], result["height"]) == (400, 240)  # the header names the first picture's size


@pytest.mark.skipif(not HAVE_X265, reason=NO_X265)
def test_a_rotation_in_real_hevc_decodes_at_both_sizes(out, tmp_path):
    land = encode_hevc(tmp_path / "l.h265", size="320x240")
    port = encode_hevc(tmp_path / "p.h265", size="240x320")
    hub = Hub()
    recording = Recording.start(hub, "rot.mp4", root=out)

    def run() -> None:
        ts = 3_000_000_000
        for line, clip in (
            (CodecLine("hev1.1.6.L60.90", 320, 240, 15), land),
            (CodecLine("hev1.1.6.L60.90", 240, 320, 15), port),
        ):
            hub.publish_config(line)
            for unit in clip:
                hub.publish_media(ts, unit, has_idr(unit, "hevc"))
                ts += 66_667
                time.sleep(0.0005)
        hub.close()

    threading.Thread(target=run, daemon=True).start()
    wait_until(lambda: not recording.active)
    result = recording.stop()
    assert result["frames"] == 60 and result["size_changes"] == 1
    assert frame_sizes(out / "rot.mp4") == [(320, 240)] * 30 + [(240, 320)] * 30
    assert decode(out / "rot.mp4").returncode == 0


# ---- what is not recorded, and what ends a recording

NOT_RECORDED = "an MJPEG stream is not recorded"


def test_an_mjpeg_stream_is_not_recorded_and_no_file_and_no_viewer_is_left_behind(out):
    hub = Hub()
    hub.publish_config(MJPEG)
    with pytest.raises(RuntimeError, match=NOT_RECORDED):
        Recording.start(hub, "mjpeg.mp4", root=out)
    assert list(out.iterdir()) == [] and hub.viewer_count == 0


def test_the_refusal_names_what_to_use_instead_and_comes_after_the_checks_of_the_arguments(out):
    hub = Hub()
    hub.publish_config(MJPEG)
    with pytest.raises(RuntimeError, match="h264 or hevc"):
        Recording.start(hub, "x.mp4", root=out)
    with pytest.raises(ValueError, match="positive"):
        Recording.start(hub, "x.mp4", root=out, max_seconds=0)
    with pytest.raises(FileNotFoundError):
        Recording.start(hub, "missing/x.mp4", root=out)


def test_a_recording_started_before_the_stream_says_its_codec_ends_with_an_error_when_it_is_mjpeg(out):
    hub = Hub()
    recording = Recording.start(hub, "late.mp4", root=out)  # the hub knows no codec line yet
    hub.publish_config(MJPEG)
    hub.publish_media(1000, JPEG)
    wait_until(lambda: not recording.active)
    result = recording.stop()
    assert result["reason"] == "error" and NOT_RECORDED in result["error"] and result["frames"] == 0
    assert hub.viewer_count == 0 and (out / "late.mp4").stat().st_size == 0
    hub.close()


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("h264", HEVC_PATTERN),
        ("hevc", H264_PATTERN),
        ("h264", MJPEG),
        ("hevc", MJPEG),
    ],
    ids=["h264 to hevc", "hevc to h264", "h264 to mjpeg", "hevc to mjpeg"],
)
def test_a_change_of_codec_family_ends_the_recording_with_that_reason_and_a_whole_file_of_what_came_before(
    out, before, after
):
    first_line = H264_PATTERN if before == "h264" else HEVC_PATTERN
    items = pattern_stream(before)[:30]
    hub = Hub()
    recording = Recording.start(hub, "family.mp4", root=out)
    feed(hub, items, line=first_line, close=False).join(5)
    wait_until(lambda: recording.status()["frames"] >= 28, what="the pictures before the change")
    hub.publish_config(after)  # the HMI's codec was changed: what follows is another kind of picture
    wait_until(lambda: not recording.active, what="the recording to end at the change")
    result = recording.stop()
    assert result["reason"] == "codec_changed" and result["error"] is None and result["frames"] == 30
    assert hub.viewer_count == 0
    data = (out / "family.mp4").read_bytes()
    assert [b.kind for b in parse_boxes(data)][:2] == ["ftyp", "moov"]
    assert sum(len(f.samples) for f in fragments(data)) == 30
    assert result["size"] == len(data)
    if HAVE_FFMPEG:
        assert int(probe(out / "family.mp4")["stream"]["nb_read_frames"]) == 30
        assert decode(out / "family.mp4").returncode == 0
    hub.close()


def test_a_change_of_codec_string_inside_a_family_does_not_end_an_hevc_recording(out):
    hub = Hub()
    recording = Recording.start(hub, "same.mp4", root=out)
    items = stream_of_hevc(count=20, gop=10)

    def run() -> None:
        hub.publish_config(HEVC_PATTERN)
        for ts, unit, key in items[:10]:
            hub.publish_media(ts, unit, key)
        hub.publish_config(CodecLine("hev1.1.6.L93.B0", 240, 400, 15))  # another level: a new SPS, the same family
        for ts, unit, key in items[10:]:
            hub.publish_media(ts, unit, key)
        hub.close()

    threading.Thread(target=run, daemon=True).start()
    wait_until(lambda: not recording.active)
    result = recording.stop()
    assert result["reason"] == "stream_ended" and result["frames"] == 20 and result["error"] is None


def test_the_codec_of_a_recording_is_the_one_of_the_line_in_force_when_its_first_picture_came(out):
    hub = Hub()
    recording = Recording.start(hub, "late-line.mp4", root=out)
    hub.publish_config(H264_PATTERN)  # a line before any picture is not a codec change: no file has begun
    hub.publish_config(HEVC_PATTERN)
    feed(hub, pattern_stream("hevc")[:20], line=HEVC_PATTERN).join(5)
    wait_until(lambda: not recording.active)
    result = recording.stop()
    assert result["frames"] == 20 and result["reason"] == "stream_ended" and result["error"] is None
    stsd = parse_boxes((out / "late-line.mp4").read_bytes())[1].find("trak", "mdia", "minf", "stbl", "stsd")
    assert [c.kind for c in stsd.children] == ["hev1"]
