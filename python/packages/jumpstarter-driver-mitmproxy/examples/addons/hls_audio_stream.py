"""
Custom addon: HLS audio stream simulation.

Generates fake HLS (HTTP Live Streaming) playlists and serves
audio segment files from disk, simulating a live audio stream
like internet radio on the DUT.

Mock config entry::

    "GET /streaming/audio/channel/*": {
        "addon": "hls_audio_stream",
        "addon_config": {
            "segments_dir": "audio/segments",
            "segment_duration_s": 6,
            "channels": {
                "ch101": {"name": "Classic Rock", "bitrate": 128000},
                "ch202": {"name": "Jazz", "bitrate": 256000}
            }
        }
    }

File layout::

    mock-files/
    └── audio/
        └── segments/
            ├── silence_6s_128k.aac   ← default fallback segment
            ├── tone_6s_128k.aac      ← test tone segment
            ├── ch101_001.aac         ← real content segments
            ├── ch101_002.aac
            └── ...

If real segment files aren't available, the addon generates a
minimal silent AAC segment so the client's audio stack still
exercises its full decode/buffer/playback path.

NOTE: This addon references a configurable files directory for loading
real audio segments. If unavailable, it falls back to generated silence.

"""

from __future__ import annotations

import time
from pathlib import Path

from mitmproxy import ctx, http

# Minimal valid AAC-LC frame: 1024 samples of silence at 44100Hz
# This is enough to keep an AAC decoder happy without real content.
SILENT_AAC_FRAME = (
    b"\xff\xf1"          # ADTS sync word + MPEG-4, Layer 0
    b"\x50"              # AAC-LC, 44100 Hz (idx 4)
    b"\x80"              # Channel config: 2 (stereo)
    b"\x00\x1f"          # Frame length (header + padding)
    b"\xfc"              # Buffer fullness (VBR)
    + b"\x00" * 24       # Silent spectral data
)


def _generate_silent_segment(duration_s: float = 6.0) -> bytes:
    """Generate a silent AAC segment of approximately the given duration.

    Each AAC-LC frame at 44100Hz covers ~23.2ms (1024 samples).
    """
    frames_needed = int(duration_s / 0.0232)
    return SILENT_AAC_FRAME * frames_needed


class Handler:
    """HLS audio stream mock handler.

    Serves:
    - Master playlist: /streaming/audio/channel/{id}/master.m3u8
    - Media playlist:  /streaming/audio/channel/{id}/media.m3u8
    - Segments:        /streaming/audio/channel/{id}/seg_{n}.aac
    """

    def __init__(self):
        self._sequence_counters: dict[str, int] = {}

    def handle(self, flow: http.HTTPFlow, config: dict) -> bool:
        """Route HLS requests to the appropriate handler."""
        path = flow.request.path
        files_dir = Path(
            config.get("files_dir", "/opt/jumpstarter/mitmproxy/mock-files")
        )
        segments_dir = config.get("segments_dir", "audio/segments")
        segment_duration = config.get("segment_duration_s", 6)
        channels = config.get("channels", {
            "default": {"name": "Test Channel", "bitrate": 128000},
        })

        # Parse channel ID from path
        # Expected: /streaming/audio/channel/{channel_id}/...
        parts = path.rstrip("/").split("/")
        if len(parts) < 5:
            return False

        channel_id = parts[4]
        resource = parts[5] if len(parts) > 5 else "master.m3u8"

        channel = channels.get(channel_id, {
            "name": f"Channel {channel_id}",
            "bitrate": 128000,
        })

        if resource == "master.m3u8":
            self._serve_master_playlist(
                flow, channel_id, channel,
            )
        elif resource == "media.m3u8":
            self._serve_media_playlist(
                flow, channel_id, channel, segment_duration,
            )
        elif resource.startswith("seg_") and resource.endswith(".aac"):
            self._serve_segment(
                flow, channel_id, resource, files_dir,
                segments_dir, segment_duration,
            )
        else:
            return False

        return True

    def _serve_master_playlist(
        self,
        flow: http.HTTPFlow,
        channel_id: str,
        channel: dict,
    ):
        """Serve the HLS master playlist (points to media playlist)."""
        bitrate = channel.get("bitrate", 128000)
        base = f"/streaming/audio/channel/{channel_id}"

        playlist = (
            "#EXTM3U\n"
            "#EXT-X-VERSION:3\n"
            f"#EXT-X-STREAM-INF:BANDWIDTH={bitrate},"
            f"CODECS=\"mp4a.40.2\",NAME=\"{channel.get('name', channel_id)}\"\n"
            f"{base}/media.m3u8\n"
        )

        flow.response = http.Response.make(
            200,
            playlist.encode(),
            {
                "Content-Type": "application/vnd.apple.mpegurl",
                "Cache-Control": "no-cache",
            },
        )
        ctx.log.info(f"HLS master playlist: {channel_id}")

    def _serve_media_playlist(
        self,
        flow: http.HTTPFlow,
        channel_id: str,
        channel: dict,
        segment_duration: float,
    ):
        """Serve a live-style media playlist with a sliding window.

        Generates a playlist with 3 segments, advancing the sequence
        number based on wall-clock time to simulate a live stream.
        """
        # Calculate current sequence number from wall clock
        # This gives a continuously advancing "live" stream
        current_time = int(time.time())
        seq_base = current_time // int(segment_duration)
        base = f"/streaming/audio/channel/{channel_id}"

        lines = [
            "#EXTM3U",
            "#EXT-X-VERSION:3",
            f"#EXT-X-TARGETDURATION:{int(segment_duration)}",
            f"#EXT-X-MEDIA-SEQUENCE:{seq_base}",
            # No #EXT-X-ENDLIST → live stream
        ]

        # Sliding window of 3 segments
        for i in range(3):
            seq = seq_base + i
            lines.append(f"#EXTINF:{segment_duration:.1f},")
            lines.append(f"{base}/seg_{seq}.aac")

        playlist = "\n".join(lines) + "\n"

        flow.response = http.Response.make(
            200,
            playlist.encode(),
            {
                "Content-Type": "application/vnd.apple.mpegurl",
                "Cache-Control": "no-cache, no-store",
            },
        )
        ctx.log.info(
            f"HLS media playlist: {channel_id} (seq {seq_base})"
        )

    def _serve_segment(
        self,
        flow: http.HTTPFlow,
        channel_id: str,
        resource: str,
        files_dir: Path,
        segments_dir: str,
        segment_duration: float,
    ):
        """Serve an audio segment file.

        Tries to find a real segment file on disk. Falls back to
        generated silence if no file exists. This lets you test with
        real audio when available, but always have a working stream.
        """

        # Try channel-specific segment
        seg_path = files_dir / segments_dir / f"{channel_id}_{resource}"
        if not seg_path.exists():
            # Try generic segment
            seg_path = files_dir / segments_dir / resource
        if not seg_path.exists():
            # Try any segment for the channel
            channel_dir = files_dir / segments_dir
            if channel_dir.exists():
                candidates = sorted(channel_dir.glob(f"{channel_id}_*.aac"))
                if candidates:
                    # Rotate through available segments
                    idx = hash(resource) % len(candidates)
                    seg_path = candidates[idx]

        if seg_path.exists():
            body = seg_path.read_bytes()
            ctx.log.debug(f"HLS segment (file): {resource}")
        else:
            # Generate silent segment as fallback
            body = _generate_silent_segment(segment_duration)
            ctx.log.debug(f"HLS segment (silence): {resource}")

        flow.response = http.Response.make(
            200,
            body,
            {
                "Content-Type": "audio/aac",
                "Cache-Control": "max-age=3600",
            },
        )
