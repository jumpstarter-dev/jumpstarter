---
title: Getting Started
description: What do you need to get started with Jumpstarter?
#categories: [Examples, Placeholders]
tags: [test, docs]
weight: 3
---

To start using Jumpstarter you need a [test-harness](../testharness/) (i.e., a [jumpstarter-board](../testharness/jumpstarterboard/)) and a device under test (DUT).

You will also need an image to flash into your DUT, it could be an .iso or a raw device image. In the case of the jumpstarter-board, this image
is flashed on a usb storage device which then can be attached to the DUT.

## Installation

{{< highlight bash  >}}

# install jumpstarter binary
wget https://github.com/jumpstarter-dev/jumpstarter/releases/download/${JUMPSTARTER_VERSION}/jumpstarter-${JUMPSTARTER_VERSION}-linux-amd64.tar.gz
tar -xzf jumpstarter-${JUMPSTARTER_VERSION}-linux-amd64.tar.gz
export PATH=${PATH}:$(pwd)

{{< / highlight >}}

## Setup

If you will be using a test harness that works through USB (like the jumpstarter-board), it is recommended to use a
physical host. QEMU is still possible but you will need to pass through the whole USB host controller to the VM, this
is recommended because the USB devices will come and go as devices are manipulated through the test-harness.

## Hello World
You can use the {{< h bash >}}jumpstarter list-devices{{< /h >}} command to get a list of detected devices from your drivers.

<img src="../../jumpstarter-console2.png"/>
