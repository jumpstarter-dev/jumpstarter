# CAN driver

`jumpstarter-driver-can` provides functionality for interacting with CAN bus
connections based on the [python-can](https://python-can.readthedocs.io/en/stable/index.html)
library.

## Installation

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-can
```

## `jumpstarter_driver_can.Can`

A generic CAN bus driver.

Available on any platform, supports many different CAN interfaces through the `python-can` library.

### Configuration

Example configuration:

```yaml
export:
  can:
    type: jumpstarter_driver_can.Can
    config:
      channel: 1
      interface: "virtual"
```

| Parameter     | Description | Type | Required | Default |
| --------------| ----------- | ---- | -------- | ------- |
| interface     | Refer to the [python-can](https://python-can.readthedocs.io/en/stable/interfaces.html) list of interfaces | str  | yes      |         |
| channel       | channel to be used, refer to the interface documentation | int or str | yes |     |

### API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_can.client.CanClient()
    :members:
```

## `jumpstarter_driver_can.IsoTpPython`
 
A Pure python ISO-TP socket driver

Available on any platform (does not require Linux ISO-TP kernel module), moderate
performance and reliability, wide support for non-standard hardware interfaces

### Configuration

Example configuration:

```yaml
export:
  can:
    type: jumpstarter_driver_can.IsoTpPython
    config:
      channel: 0
      interface: "virtual"
      address:
        rxid: 1
        txid: 2
      params:
        max_frame_size: 2048
        blocking_send: false
        can_fd: true

```

| Parameter     | Description | Type | Required | Default |
| --------------| ----------- | ---- | -------- | ------- |
| interface     | Refer to the [python-can](https://python-can.readthedocs.io/en/stable/interfaces.html) list of interfaces | `str` | no |  |
| channel       | channel to be used, refer to the interface documentation | `int` or `str` | no |  |
| address       | Refer to the [isotp.Address](https://can-isotp.readthedocs.io/en/latest/isotp/addressing.html#isotp.Address) documentation | `isotp.Address` | yes |  |
| params        | IsoTp parameters, refer to the [IsoTpParams](#isotpparams) section table | `IsoTpParams` | no | see table |
| read_timeout  | Read timeout for the bus in seconds | `float` | no | 0.05 |

### API Reference
```{eval-rst}
.. autoclass:: jumpstarter_driver_can.client.IsoTpClient()
    :members:
```

## `jumpstarter_driver_can.IsoTpSocket`

Pure python ISO-TP socket driver

Available on any platform, moderate performance and reliability, wide support for non-standard hardware interfaces

### Configuration

Example configuration:

```yaml
export:
  can:
    type: jumpstarter_driver_can.IsoTpSocket
    config:
      channel: "vcan0"
      address:
        rxid: 1
        txid: 2
      params:
        max_frame_size: 2048
        blocking_send: false
        can_fd: true

```

| Parameter     | Description | Type | Required | Default |
| --------------| ----------- | ---- | -------- | ------- |
| channel       | CAN bus to be used i.e. `vcan0`, `vcan1`, etc.. |  `str` | yes |  |
| address       | Refer to the [isotp.Address](https://can-isotp.readthedocs.io/en/latest/isotp/addressing.html#isotp.Address) documentation | isotp.Address | yes | |
| params        | IsoTp parameters, refer to the [IsoTpParams](#isotpparams) section table | `IsoTpParams` | no | see table |

### API Reference
```{eval-rst}
.. autoclass:: jumpstarter_driver_can.client.IsoTpClient()
    :noindex:
    :members:
```

## IsoTpParams
| Parameter                   | Description                                                                                           | Type             | Required | Default    |
|-----------------------------|-------------------------------------------------------------------------------------------------------|------------------|----------|------------|
| `stmin`                     | Minimum Separation Time minimum in milliseconds between consecutive frames.                           | `int`            | No       | `0`        |
| `blocksize`                 | Number of consecutive frames that can be sent before waiting for a flow control frame.                | `int`            | No       | `8`        |
| `tx_data_length`            | Default length of data in a transmitted CAN frame (CAN 2.0) or initial frame (CAN FD).                | `int`            | No       | `8`        |
| `tx_data_min_length`        | Minimum length of data in a transmitted CAN frame; pads with `tx_padding` if shorter.                 | `int` \| `None`  | No       | `None`     |
| `override_receiver_stmin`   | Override the STmin value (in seconds) received from the receiver; `None` means do not override.       | `float` \| `None`| No       | `None`     |
| `rx_flowcontrol_timeout`    | Timeout in milliseconds for receiving a flow control frame after sending a first frame or a block.    | `int`            | No       | `1000`     |
| `rx_consecutive_frame_timeout`| Timeout in milliseconds for receiving a consecutive frame in a multi-frame message.                 | `int`            | No       | `1000`     |
| `tx_padding`                | Byte value used for padding if the data length is less than `tx_data_min_length` or for CAN FD.       | `int` \| `None`  | No       | `None`     |
| `wftmax`                    | Maximum number of Wait Frame Transmissions (WFTMax) allowed before aborting. `0` means WFTs are not used.| `int`         | No       | `0`        |
| `max_frame_size`            | Maximum size of a single ISO-TP frame that can be processed.                                          | `int`            | No       | `4095`     |
| `can_fd`                    | If `True`, enables CAN FD (Flexible Data-Rate) specific ISO-TP handling.                              | `bool`           | No       | `False`    |
| `bitrate_switch`            | If `True` and `can_fd` is `True`, enables bitrate switching for CAN FD frames.                        | `bool`           | No       | `False`    |
| `default_target_address_type` | Default target address type: `0` for Physical (1-to-1), `1` for Functional (1-to-n).                | `int`            | No       | `0`        |
| `rate_limit_enable`         | If `True`, enables rate limiting for outgoing frames.                                                 | `bool`           | No       | `False`    |
| `rate_limit_max_bitrate`    | Maximum bitrate in bits per second for rate limiting if enabled.                                      | `int`            | No       | `10000000` |
| `rate_limit_window_size`    | Time window in seconds over which the rate limit is calculated.                                       | `float`          | No       | `0.2`      |
| `listen_mode`               | If `True`, the stack operates in listen-only mode (does not send any frames).                         | `bool`           | No       | `False`    |
| `blocking_send`             | If `True`, send operations will block until the message is fully transmitted or an error occurs.      | `bool`           | No       | `False`    |