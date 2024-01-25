---
title: Concepts
weight: 2
description: >
  In this section you can find a more detailed explanation of the concepts used in the Jumpstarter project.
---


<table class="table">
  <thead>
    <tr>
      <th scope="col">Concept</th>
      <th scope="col">Definition</th>
    </tr>
  </thead>
  <tbody>
   <tr>
      <th scope="row">Testing Harness</th>
      <td>This is the physical device used to allow Jumpstarter interfacing into your hardware, one example of this is the <b>dutlink-board</b>
          which is an Open Hardware reference design for Jumpstarter. But other Testing Harnesses can exist.</td>
    </tr>
    <tr>
      <th scope="row">DUT</th>
      <td>Device Under Test: This is the device that you connect to your</td>
    </tr>
    <tr>
      <th scope="row">Serial console</th>
      <td>Embedded devices and most servers have one or multiple serial consoles. A serial console allows you to transmit and receive bytes via
          a TX and RX line (plus Ground), sometimes in RS-232 physical voltage levels, sometimes in digital voltage levels (i.e. 3.3v, 1.8v, etc..),
          most bootloaders, UEFI bios, and the kernel can communicate through a serial console. i.e. the kernel accepts the {{< h bash >}}console{{< /h >}} parameter
          to let you direct the main kernel output and console, for example using the kernel parameter {{< h bash >}}console=ttyS0,115200{{< /h >}}. In jumpstarter
          we use the console as a the main communication method to Edge devices, with the purpose of monitoring and automation</td>
    </tr>
  </tbody>
</table>
