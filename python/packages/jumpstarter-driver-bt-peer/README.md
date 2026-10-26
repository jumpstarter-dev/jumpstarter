# BtPeer Driver

`jumpstarter-driver-bt-peer` provides a Bluetooth peer device powered by
[bumble](https://github.com/google/bumble). It can pair, connect, and stream
A2DP audio to a DUT over BR/EDR.

## Installation

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-bt-peer
```

## Configuration

Example configuration:

```yaml
export:
  bt_peer:
    type: jumpstarter_driver_bt_peer.driver.BtPeer
    config:
      transport: "tcp-client:127.0.0.1:7300"  # bumble transport string
```

## Usage

Start the peer, pair with a DUT, and verify the connection:

```bash
j bt_peer start '{"name": "Bumble-Phone"}'
j bt_peer address
j bt_peer wait-connection --timeout 60
j bt_peer connections
j bt_peer pair --handle 0
j bt_peer stop
```

The `transport` config accepts any bumble transport string:
- `tcp-client:host:port` - rootcanal / netsim
- `usb:0` — USB HCI dongle
- `serial:/dev/ttyUSB0` - serial UART

## Playing media

Once the DUT has connected and opened an A2DP stream, send it audio:

```bash
j bt_peer play song.mp3
j bt_peer play jingle.wav --loop
j bt_peer pause
j bt_peer resume
j bt_peer playback
j bt_peer stop-playback
```

`play` starts the stream itself rather than waiting to be asked: in A2DP the
source drives it, and the DUT opens the signalling channel and then waits. A
track loaded before the DUT's AVDTP channel exists is started as soon as it
arrives, so the two can race without the track being left silent.

`pause` and `stop-playback` send AVDTP SUSPEND, which leaves the stream OPEN
and resumable rather than tearing it down; `resume` and `play` start it again.
Pause keeps the track's position. While a stream is started with no track set
the peer sends filler, so a track can be swapped in without renegotiating.

Anything ffmpeg can read is accepted; the client transcodes locally and
uploads the encoded SBC, so the exporter does not need ffmpeg. Install it with
`brew install ffmpeg` (or your distribution's package). A file that is already
`.sbc` is uploaded as-is.

The peer advertises exactly one SBC configuration — 44100 Hz, stereo, 8
subbands, 16 blocks, loudness allocation, bitpool 2-53 — because it is the
AVDTP acceptor and the DUT's sink picks from whatever is offered. Pinning it
means a mismatched file is rejected with an explanation instead of arriving as
noise.

The mode is stereo rather than joint stereo because ffmpeg's SBC encoder
chooses the channel mode from the bitrate and offers no way to override it: it
emits joint stereo below roughly 192 kbps and plain stereo above. Pre-encoded
`.sbc` input must match; to produce a compatible file by hand:

```bash
ffmpeg -i song.mp3 -ar 44100 -ac 2 -b:a 320k -f sbc song.sbc
```

Tracks are buffered in the exporter's memory, up to 256 MiB.

The peer also advertises an AVRCP target and reports playing/paused/stopped
through it. A DUT needs this to treat the peer as a media device at all: with
only the A2DP record advertised, Android connects its sink, decodes the
incoming audio and discards it, leaving `A2dpSinkService` with no active
device.

Track details come from the source file's tags, read locally with `ffprobe`,
and are shown by the DUT along with a duration and progress position. Override
them per track when a file is untagged:

```bash
j bt_peer play untagged.mp3 --title "Chime" --artist "Test Fixture"
```

Without tags or an override the DUT shows the file name. bumble's AVRCP target
implements neither `GET_ELEMENT_ATTRIBUTES` nor song length in
`GET_PLAY_STATUS`, so the driver answers both itself.

PLAY/PAUSE/STOP coming *from* the DUT are recorded as `avrcp_key` events but
ignored unless the driver is configured with `follow_transport_keys: true`.
Android sends PAUSE as soon as it connects when it cannot resolve an addressed
player, which would otherwise silence a track that was just started.

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_bt_peer.driver.BtPeer()
```
