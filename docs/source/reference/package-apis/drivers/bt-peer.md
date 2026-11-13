# BT Peer Driver

`jumpstarter-driver-bt-peer` provides a Bluetooth peer device powered by [bumble](https://github.com/google/bumble).
It can pair, connect, and stream A2DP audio to a DUT over BR/EDR.
Transport-agnostic: works over TCP (rootcanal/netsim), USB dongle, serial
UART, or any bumble transport string.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-bt-peer
```

## Configuration

```yaml
export:
  bt_peer:
    type: jumpstarter_driver_bt_peer.driver.BtPeer
    config:
      transport: "tcp-client:127.0.0.1:7300"
```

### Config parameters

| Parameter | Description | Type | Required | Default |
| --------- | ----------- | ---- | -------- | ------- |
| transport | Bumble transport string (e.g. `tcp-client:host:port`, `usb:0`, `serial:/dev/ttyUSB0`) | str | no | `tcp-client:127.0.0.1:7300` |
| follow_transport_keys | Let PLAY/PAUSE/STOP pressed on the DUT control playback. Off by default, because a DUT that sends PAUSE on connect would otherwise silence a track that was just started. | bool | no | `false` |

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_bt_peer.client.BtPeerClient()
    :members:
```

### CLI

```console
jumpstarter ⚡ local ➤ j bt_peer
Usage: j bt_peer [OPTIONS] COMMAND [ARGS]...

  Bluetooth peer device (bumble).

Options:
  --help  Show this message and exit.

Commands:
  address             Show the peer's Bluetooth address.
  connect             Connect to a remote device (e.g. the CVD).
  connections         Show active connections.
  events              Show events since timestamp.
  pair                Authenticate and encrypt a connection.
  pause               Suspend the stream, keeping the track and its position.
  play                Stream an audio file to the DUT over A2DP.
  playback            Show what is currently streaming.
  resume              Restart a stream suspended by pause.
  start               Start the BT peer device.
  stop                Stop the BT peer device.
  stop-playback       Stop the current track and suspend the stream.
  wait-connection     Wait for an incoming connection.
  wait-disconnection  Wait for a disconnection.
```

### Playing media

Anything ffmpeg can read is transcoded locally and uploaded as SBC, so the
exporter does not need ffmpeg:

```console
jumpstarter ⚡ local ➤ j bt_peer play song.mp3
jumpstarter ⚡ local ➤ j bt_peer play jingle.wav --loop
jumpstarter ⚡ local ➤ j bt_peer playback
```

A file that is already `.sbc` is the exception: it is uploaded as-is, on the
assumption that it was encoded deliberately. The peer rejects it with an
explanation if it does not match the configuration negotiated with the DUT, so
convert an incompatible one first:

```console
$ ffmpeg -i song.mp3 -ar 44100 -ac 2 -b:a 320k -f sbc song.sbc
```

The peer advertises an A2DP source and an AVRCP target, so the DUT treats it
as a real media device and displays the track's title, artist, album and
position. See the
[package README](https://github.com/jumpstarter-dev/jumpstarter/blob/main/python/packages/jumpstarter-driver-bt-peer/README.md)
for the SBC configuration the peer pins and why.
