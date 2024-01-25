---
title: DUTlink board
description:
date: 2017-01-04
weight: 1
---

{{% pageinfo %}}
Information about the DUTlink board, how to use it and how to connect it to your DUT.
{{% /pageinfo %}}

The dutlink-board is a test harness designed for Jumpstarter, it's a board in micro ITX
format, which allows mounting of a DUT on top, and enables the usage of standard rack or desktop
server cases.

The dutlink-board is an Open Hardware project, you can find the design files in the
[DUTlink board repository](https://github.com/jumpstarter-dev/dutlink-board), a first batch
was built by SeeedStudio using their fusion PCB service, and you can find the manufacturing files
[here](https://github.com/jumpstarter-dev/dutlink-board/tree/main/hardware/manufacturing/1.0.0).

## High level overview
A device under test (left side) is connected to the dutlink board, which is connected to a
host (right side) via USB-C. The host runs the jumpstarter software, which allows CI to
interact with the DUT, controlling the power, connection and management of a storage device
(see the pendrive in the next pictures), and communication via serial console.

{{% imgproc "jumpstarter_diagram.png" Fit 800x800 /%}}

### This is how the hardware looks

{{% imgproc "general_jumpstarter.jpg" Fit 1024x1024 %}}
Top view of the dutlink board REL-1.0.0
On the **left** area: you can see connections to the Device Under Test
On the **right**, you can see the connections to the testing
host, where the jumpstarter software runs.
{{% /imgproc %}}

{{% imgproc "general_with_visionfive2.jpg" Fit 1024x1024 %}}
Top view of the dutlink board REL-1.0.0 with a visionfive2 board attached
via USB-PD power pass-through. See more details in the [visionfive2 section](visionfive2/).
{{% /imgproc %}}

## Warnings: read before you use the board

### Barrel power connectors and USB-PD power pass-through

* **CE** and **FCC** certification is still pending (the labels on the board are still incorrect). This is a still a prototype, and it has not been certified yet.

* **Barrel power connectors have a polarity**, with the positive pin in the center, and the negative pin on the outside. If you connect the barrel power connector with the wrong polarity, you will damage the board.

{{% imgproc "barrel_connector.png" Fit 256x256 / %}}

* **USB-PD power pass-through and Barrel power should not be used at the same time**, as this will damage the board or your power adapters.

{{% imgproc "power_input.png" Fit 512x512 / %}}

### Digital signals on the I/O connector are 3.3V only
The I/O connector has digital signals, but they are 3.3V only, this is generally ok if you use 0 or HiZ outputs,
but never use a HI/H/1 signal on an output when the target device is only 1.2, 1.8 or 2.5V, as this could damage the device.

{{% imgproc "io_pins.png" Fit 256x256 / %}}

Outputs are protected with a 100 ohm resistor which would avoid damage in most cases.

The V IO pin is provided to enable the use of voltage translation circuits if necessary.


## Known issues and limitations

### USB-C connections
USB-C connections are reversable, but in the 1.0.0 version of the board, the USB-C receptacles have not been wired properly, so in some cases you may need to flip the USB-C cable to get the connection or power working, this applies to all USB-Cs on the board.

{{% imgproc "usb_host.png" Fit 256x256 / %}}

i.e.:
 * dutlink-board is not being detected by the host: try flipping the USB-C cable.
 * the device is not powered-up or charging: try flipping the power USB-C cable.

### Storage DUT Out connector is fragile

The USB storage connector used to connect to the DUT is fragile, avoid pressing up or down
from the cables or connectors once attached to the board, as this could rip the connector
from the board. Once the board is tested glue could be carefully applied to the back of the connector.

{{% imgproc "storage_dut.png" Fit 256x256 / %}}



