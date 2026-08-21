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
  address           Show the peer's Bluetooth address.
  connect           Connect to a remote device.
  connections       Show active connections.
  events            Show events since timestamp.
  pair              Authenticate and encrypt a connection.
  start             Start the BT peer device.
  stop              Stop the BT peer device.
  wait-connection   Wait for an incoming connection.
  wait-disconnection Wait for a disconnection.
```
