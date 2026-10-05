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

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_bt_peer.driver.BtPeer()
```
