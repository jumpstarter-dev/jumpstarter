---
title: Command line
description: Command line and scripting reference for Jumpstarter.
weight: 1
---

{{% pageinfo %}}
**Jumpstarter** today comes as a commandline tool that allows you to interact
with the test-harness via the driver architecture.
{{% /pageinfo %}}

# Jumpstarter CLI

Most commands accept a {{< h >}}device-id{{< /h >}}. A {{< h >}}device-id{{< /h >}} can be either
the {{< h >}}serial{{< /h >}} number of the device, or the {{< h bash >}}device name{{< /h >}}.

All commands accept the following flags
{{< highlight ""  >}}
  -d, --driver string    Only devices for the specified driver
  -h, --help             help for jumpstarter
{{< / highlight >}}

## GENERAL COMMANDS

### **list-devices**
This command will list all the devices that are currently available throught the various test-harness drivers.
{{< highlight "" >}}
$ jumpstarter list-devices
Device Name	Serial Number	Driver			Version	Device		Tags
orin-agx-00	e6058a05	jumpstarter-board	0.05	/dev/ttyACM2	orin-agx, orin, 64gb
xavier-nx-00	e6058905	jumpstarter-board	0.04	/dev/ttyACM1	nvidia, xavier-nx, nvidia-xavier, arm64, 8gb
visionfive2-00	031da453	jumpstarter-board	0.04	/dev/ttyACM0	rv64gc, rv64, jh7110, visionfive2, 8gb
{{< / highlight >}}

### **list-drivers**
This command lists all the drivers that are currently available.

{{< highlight "" >}}
$ jumpstarter list-drivers
jumpstarter-board
	OpenSource HIL USB harness (https://github.com/jumpstarter-dev/jumpstarter-board)
	enables the control of Edge and Embedded devices via USB.
	It has the following capabilities: power metering, power cycling, and serial console
	access, and USB storage switching.
{{< / highlight >}}

### **run-script** script.yaml
This is probably the most useful jumpstarter command today.
It runs a jumpstarter script, which will select a device based on the selector tags,
and execute all the steps of the script. Once finished or if an error occurs the
cleanup section of the script will be run.

{{< highlight "" >}}
$ jumpstarter run-script script.yaml
{{< / highlight >}}
See the [scripting](/docs/reference/scripting/) section for a detailed guide on how to write
scripts and examples.

### **set-control** device-id
Set a control signal from the test-harness to the device. This is used to control
signals on the DUT or trigger external hardware like video generators, simulated sensors,
fault injectors, or other necessary devices.

{{< highlight "" >}}
$ jumpstarter set-control orin-agx-00 A LOW
{{< / highlight >}}

The signal names and output modes depend on the test-harness being used. See the
[jumpstarter-board](/docs/reference/jumpstarter-board/) section for more details.



## STORAGE MANAGEMENT

### **set-disk-image** device-id
Set the disk image to be used for the DUT. This is used to write the disk image
to the DUT's attacheable storage device. Images can be a raw disk image
or an ISO image.

{{< highlight "" >}}
$ jumpstarter set-disk-image orin-agx-00 my-system-image.raw
{{< / highlight >}}

{{< highlight "" >}}
Flags:
  -o, --offset-gb uint   Offset in GB to write the image to in the disk
{{< / highlight >}}


You can limit or filter the devices found by jumpstarter for image writing by
using the storage_filter config. This configuration is stored into the test
harness board and will be used by jumpstarter to filter the devices found.

i.e. for using only `/dev/disk/by-id/usb-SanDisk_Extreme_Pro_52A456790D93-0:0`

you could set:
`jumpstarter set-config device-id storage_filter Extreme_Pro_52A456790D93-0:0`

This is also useful if your USB disk exposes multiple sub-devices.

### attach-storage device-id
This command attaches the storage device to the DUT. This is normally required to boot the DUT.

{{< highlight "" >}}
$ jumpstarter attach-storage orin-agx-00
💾 Attaching storage for orin-agx-00 ... done
{{< / highlight >}}

#### **detach-storage** device-id
This command detaches the storage device to the DUT.

{{< highlight "" >}}
$ jumpstarter detach-storage orin-agx-00
💾 Detaching storage for orin-agx-00 ... done
{{< / highlight >}}

## POWER MANAGEMENT

### **power** on|off|force-on|force-off|rescue device-id [-t] [-r] [-a]
This command manipulates the power status of a device

i.e.:
```
$ jumpstarter power off orin-agx-00
🔌 Powering off orin-agx-00... done
```

The different power actions available are:

| Action      | Description    |
|-------------|----------------|
| on          | Power on the device using the power_on profile from configuration |
| off         | Power off the device using the power_off profile from configuration |
| force-on    | Power on the device using only power, ignoring the profile from configuration |
| force-off   | Power off the device using only power, ignoring the profile from configuration |
| rescue      | Power on the device using the power_rescue profile from configuration |


```
Flags:
  -a, --attach-storage   Attach storage before powering on
  -t, --console          Open console terminal after powering on
  -r, --reset            Reset device after power up
```

For information on how to configura the power profiles (power_on, power_off, power_rescue),
please see the set-config section.



### **reset** device-id
  Use the reset signal on the device to reset it, only open drain signal is supported (pulling low + high impedance) at this time.

{{< highlight "" >}}
$ jumpstarter reset orin-agx-00
⚡ Toggling reset on orin-agx-00
{{< / highlight >}}

## DEVICE CONSOLE

### **console** device-id
This command provides a serial console to the DUT, it will connect to the serial console of the DUT and allow you to interact with it.

{{< highlight "" >}}
$ jumpstarter console orin-agx-00
Looking up for out-of-band console:  TOPOD83B461B-if01
💻 Entering console: Press Ctrl-B 3 times to exit console
[0000.219] I> FUSE_OPT_PVA_DISABLE = 0x00000000
...
...
...
{{< / highlight >}}


### **create-ansible-inventory** device-id
This command interacts with the console of the DUT
which must be logged in with a user andcreates an ansible inventory file for the DUT. This ansible inventory can
be used to run ansible playbooks against the DUT.

{{< highlight "" >}}
$ jumpstarter create-ansible-inventory orin-agx-00
{{< / highlight >}}

This command accepts the following extra flags:
{{< highlight "" >}}
Flags:
  -k, --ssh-key string   The ssh key to use for the ansible inventory file
  -u, --user string      The user for the ansible inventory file (default "root")
{{< / highlight >}}

### **run** device-id command
  Sends a string via the serial console to the DUT and waits for a response which is then written to stdout.

{{< highlight "" >}}
$ ./jumpstarter/jumpstarter run orin-agx-00 "ls -la"
Looking up for out-of-band console:  TOPOD83B461B-if01
total 24
dr-xr-x---.  6 root root 168 Sep 22 14:08 .
dr-xr-xr-x. 19 root root 248 Sep 22 14:01 ..
drwx------.  3 root root  17 Sep 22 14:06 .ansible
-rw-------.  1 root root 325 Sep 22 14:09 .bash_history
-rw-r--r--.  1 root root  18 Aug 10  2021 .bash_logout
-rw-r--r--.  1 root root 141 Aug 10  2021 .bash_profile
-rw-r--r--.  1 root root 429 Aug 10  2021 .bashrc
-rw-r--r--.  1 root root 100 Aug 10  2021 .cshrc
drwx------.  3 root root  26 Sep 22 14:08 .nv
drwx------.  2 root root  29 Sep 22 14:01 .ssh
-rw-r--r--.  1 root root 129 Aug 10  2021 .tcshrc
drwxr-xr-x.  2 root root 116 Sep 22 14:09 artifacts
[root@localhost ~]#
$
{{< / highlight >}}

In the above example the system had already been logged
in via the console.

{{< highlight "" >}}
Flags:
  -w, --wait int        Wait seconds before trying to get a response (default 2)
{{< / highlight >}}

## CONFIGURATION

### **set-name** device-id name
Changes device name. This is used to set a name for the test-harness device. This
should make devices easier to identify.

{{< highlight "" >}}
$ jumpstarter set-name e6058a05 orin-agx-00
✍ Changing device name for e6058a05 to orin-agx-00 ... done
{{< / highlight >}}


### **set-tags** device-id tag1 [tag2 ...]
Changes device tags, pass one argument per tag. This is used to set tags for the test-harness device
which can be used to select specific devices from a script or some commands.

{{< highlight "" >}}
$ jumpstarter set-name orin-agx-00 orin-agx orin 64gb
✍ Changing device name for orin-agx-00 to orin-agx ... done
{{< / highlight >}}

### **get-config** device-id [key]

Shows the device configuration parameters stored on the test-harness board.
If a key is provided only the value for that key will be shown.

```
$ ./jumpstarter get-config orin-nx-00 power_on
p1,bL,w5,bZ
```

Otherwise a yaml list of all parameters will be shown.
```
$ ./jumpstarter get-config orin-nx-00
name: orin-nx-00
tags: orin-nx-00,orin,orin-nx,16gb
json:
usb_console:
power_on: p1,bL,w5,bZ
power_off: p1,bL,w5,bZ,w10,bL,w110,bZ
power_rescue: p1,bL,w1,bZ,w1,aL,rL,w1,rZ,w1,aZ
```

### **set-config** device-id key value

Changes a device configuration config parameter (see get-config).

if values is "" the config will be deleted.

#### usb_console config parameter
Changes device name for out of band USB console. Some devices expose a console only via USB
and the console is not accessible via pins. This command allows you to set a matching string
for the USB console of the device.

Jumpstarter will try to find the USB console device by matching the string provided with
this command when trying to communicate with the device via the console.

{{< highlight "" >}}
$ jumpstarter set-usb-console orin-agx-00 TOPOD83B461B-if01
✍ Changing usb_console name for orin-agx-00 to TOPOD83B461B-if01 ... done
{{< / highlight >}}

#### power_on/off/recue parameters
These parameters are used to configure the power sequences, they are a comma separated list of
commands to be executed by the test-harness board to perform the power on/off/rescue of the device.

The commands are:
* p1: power enable
* p0: power disable
* aL: pin A low
* aH: pin A high
* aZ: pin A high impedance
* bL: pin B low
* ...
* rL: pin RESET low
* rH: pin RESET high
* rZ: pin RESET high impedance
* wN: wait N*100 ms, where N is a natural number.

Some examples of configuration for orin-nx/orin-agx devkit boards:

Touch power button for 500ms:
```
power_on: p1,bL,w5,bZ
```

Touch power button for 500ms (to make sure we start from ON), then touch for 11s.
```
power_off: p1,bL,w5,bZ,w10,bL,w110,bZ
```

Touch power button for 500ms (to make sure we start from ON), then assert
A (recovery), then assert the reset signal, and wait 500ms, then deassert reset,
wait 500ms, deassert A (recovery). This should put the jetson device into recovery mode.
```
power_rescue: p1,bL,w5,bZ,w1,aL,rL,w5,rZ,w5,aZ
```

where pin A is "REC" and pin B is "PWR" on the orin-agx/orin-nx boards.
