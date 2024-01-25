---
title: Test harness
description: A test harness is a device that enables jumpstarter to manage a DUT via a jumpstarter-driver.
date: 2023-09-26
weight: 7
---

{{% pageinfo %}}
Jumpstarter provides a driver architecture to enable the easy contribution of additional test harnesses,
but today only the dutlink-board is supported.
{{% /pageinfo %}}

We recognize that the dutlink-board could help test many general edge devices, but complex
devices may require a custom test harness, and we also understand that customers may have
already their own harnesses in place. **One of our design goals was to make it very easy
to add new test harnes drivers** to Jumpstarter.



