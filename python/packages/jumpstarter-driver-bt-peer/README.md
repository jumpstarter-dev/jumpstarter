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
j bt_peer playback
j bt_peer stop-playback
```

While no track is playing the peer streams silence, so the A2DP stream stays
open and a track can be started or swapped without renegotiating it.

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

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_bt_peer.driver.BtPeer()
```
