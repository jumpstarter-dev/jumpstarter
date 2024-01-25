---
title: Connector reference
weight: 1
date: 2023-09-26
description: Connector reference for te dutlink-board

---

{{% imgproc "image.png" Fit 1024x1024 / %}}

## Device Under Test connections
### Power
<table class="table">
  <thead>
    <tr>
      <th scope="col">Connector</th>
      <th scope="col">Definition</th>
    </tr>
  </thead>
  <tbody>
   <tr>
      <th scope="row">J3</th>
      <td>J3 is a barrel jack connector that provides power output to the DUT. The source
            of the power comes from <b>J1</b>.<br/><br/>
        <b>Warning</b>: Do not use at the same time as J5 or J2.</td>
    </tr>
    <tr>
      <th scope="row">J5</th>
      <td>J5 is a USB-C connector that provides power output to the DUT. The source of the
power comes from <b>J2</b>, USB-PD negotiation is connected to the power adapter on J2. USB-PD is a standard that allows negotiation of the voltage and current. <br/><br/>
       <b>Warning:</b> Do not use at the same time as J3 or J1.</td>
    </tr>
  </tbody>
</table>

### Storage
<table class="table">
  <thead>
    <tr>
      <th scope="col">Connector</th>
      <th scope="col">Definition</th>
    </tr>
  </thead>
  <tbody>
   <tr>
      <th scope="row">J8</th>
      <td>J8 is a USB3 micro B connector for storage. This connector provides access to
          the storage device that is connected to <b>J9</b>.
            One possible cable you can use in this connector is this one: <a href="https://www.amazon.com/CableCreation-External-Compatible-Galaxy-Camera/dp/B074V3GD2S/">link</a></td>
    </tr>
  </tbody>
</table>

### I/O pins
The I/O pins connector provides a 3.3V digital interface to the DUT, or any surrounding
test hardware (like video signal generators, sensor emulators, or other elements).

Never use a HI/H/1 signal on an output when the target device is only 1.2, 1.8 or 2.5V, as this could damage the device.

Outputs are protected with a 100 ohm resistor which would avoid damage in most cases.

The V IO pin is provided to enable the use of voltage translation circuits if necessary.


<table class="table">
  <thead>
    <tr>
      <th scope="col">Pin</th>
      <th scope="col">Definition</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <th scope="row">V IO</th>
      <td>Provides a voltage reference for a voltage translation circuit</td>
    </tr>
    <tr>
      <th scope="row">TX</th>
      <td>Provides an output UART to the DUT, this is connected to the jumpstarter console.</td>
    </tr>
    <tr>
      <th scope="row">RX</th>
      <td>Provides an input UART from the DUT, this is connected to the jumpstarter console.</td>
    </tr>
    <tr>
      <th scope="row">CTL_A</th>
      <td>Provides a digital input/output which can be controlled by the jumpstarter software.
          The current convention is to use this pin for forcing devices into flashing mode,
          but it can be used for any purpose.</td>
    </tr>
    <tr>
      <th scope="row">CTL_B</th>
      <td>Provides a digital input/output which can be controlled by the jumpstarter software.</td>
    </tr>
    <tr>
      <th scope="row">CTL_C</th>
      <td>Provides a digital input/output which can be controlled by the jumpstarter software.</td>
    </tr>
    <tr>
      <th scope="row">CTL_D</th>
      <td>Provides a digital input/output which can be controlled by the jumpstarter software.</td>
    </tr>
    <tr>
      <th scope="row">/RESET</th>
      <td>Provides a reset output signal which can be controlled from the jumpstarter
              software. It's active low open collector output, this means that it will output
              a '0' when the reset is asserted, and it will be in HiZ when the reset is not.</td>
    </tr>
  </tbody>
</table>

## Power input connections
<table class="table">
  <thead>
    <tr>
      <th scope="col">Connector</th>
      <th scope="col">Definition</th>
    </tr>
  </thead>
  <tbody>
   <tr>
      <th scope="row">J1</th>
      <td>J1 is a barrel jack connector where the power adaptor for the DUT must be connected. The
            destination of this power is <b>J3</b>.<br/><br/>
        <b>Warning</b>: Do not use at the same time as J5 or J2. And please note that the center
                        pin is the possitive connection, power cannot be inverted.</td>
    </tr>
    <tr>
      <th scope="row">J2</th>
      <td>J2 is a USB-C connector that receives power for the DUT from the power adapter. The
      destination is <b>J5</b>, USB-PD negotiation is connected between J2 and J5. Please note that
      there is a bug in the 1.0.0 version of the board, and the USB-C receptacle is not wired
        properly, so in some cases you may need to flip the USB-C cable to get the connection or
        power working, this applies to all USB-Cs on the board.<br/><br/>
       <b>Warning:</b> Do not use at the same time as J3 or J1.</td>
    </tr>
  </tbody>
</table>

## Host connections
<table class="table">
  <thead>
    <tr>
      <th scope="col">Connector</th>
      <th scope="col">Definition</th>
    </tr>
  </thead>
  <tbody>
   <tr>
      <th scope="row">P1</th>
      <td>P1 is a USB-C connector that provides power and control to the Jumpstarter microcontroller,
      once this is connected to the host jumpstarter must be detected by the kernel and a ttyACM device
      must be detected.</td>
    </tr>
    <tr>
      <th scope="row">J7</th>
      <td>J7 is a USB3 B connector. This connector must be connected to the host, and it provides
      access to the storage device connected to <b>J9</b>. One possible cable you can use in this
        connector is this one: <a href="https://www.amazon.com/AkoaDa-Durable-Compatible-Printers-External/dp/B08HRSP9NY">link</a></td>
    </tr>
    <tr>
      <th scope="row">Other/notes</th>
      <td>While not technically a part of the dutlink board, some DUTs need USB host
          access to allow flashing from the host, i.e. NVIDIA Jetson boards. In some
          cases multiple USB connections.</a></td>
    </tr>
  </tbody>
</table>

## Storage Device
<table class="table">
  <thead>
    <tr>
      <th scope="col">Connector</th>
      <th scope="col">Definition</th>
    </tr>
  </thead>
  <tbody>
   <tr>
      <th scope="row">J9</th>
      <td>J9 is a USB3 A connector, in this connector a storage device must be connected,
          this device will be multiplexed between the host and the DUT. The HOST can flash
          it, and the DUT can boot or install from this device.
          Flashing speed will hugely depend on the storage device used, and while the
          dutlink-board has been designed for 10Gbps USB3.2 Gen2, the speed will
            depend on the storage device used as well as the cables connected to J8 and J7.
          <b>To stay on the safe side 5Gbps devices are recommended.</b> One possible device
          you can use for this purpose is: <a href="https://www.amazon.com/SanDisk-128GB-Extreme-Solid-State/dp/B08GYM5F8G">link</a>
      </td>
    </tr>
  </tbody>
</table>

## Connection examples
* [VisionFive2](../visionfive2/)
* [NVIDIA Orin AGX](../orin-agx/)
* [NVIDIA Orin NX](../orin-nx/)
* [Raspberry Pi 4](../rpi4/)
