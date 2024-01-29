---
title: Jumpstarter scripts
weight: 4
date: 2023-09-25
description: See your project in action!
---

{{% pageinfo %}}
Jumpstarter scripts come in yaml form. They are used to describe the steps to be taken to deploy a
project into an Edge system and perform console interactions with it.
{{% /pageinfo %}}
## Using a script

Scripts can be executed using the `run-script` command,
a script has a selector to pickup an available device connected
to the jumpstarter host. The selector
specifies all the tags the device must have.

{{< highlight "" >}}
$ jumpstarter run-script script.yaml
{{< / highlight >}}


Jumpstarter will fail if there are no available devices with the specified tags.

i.e.:

```
$ jumpstarter list-devices
Device Name	Serial Number	Driver			Version	Device		Tags
orin-nx-00	e605c805	dutlink-board	0.05	/dev/ttyACM2	orin-nx, orin, 16gb
xavier-nx-00	e6058905	dutlink-board	0.04	/dev/ttyACM1	nvidia, xavier-nx, nvidia-xavier, arm64, 8gb
visionfive2-00	031da453	dutlink-board	0.04	/dev/ttyACM0	rv64gc, rv64, jh7110, visionfive2, 8gb
```

if we run [one of the examples](https://github.com/jumpstarter-dev/jumpstarter/blob/main/script-examples/orin-agx.yaml)
available in the jumpstarter repository we should see:

```
$ sudo jumpstarter run-script script-examples/orin-agx.yaml
⚙ Using device "orin-nx-00" with tags [orin-nx orin 16gb]

➤ Powering off and writing the image to disk
➤ Step ➤ power: "off"
[✓] done

➤ Step ➤ set-disk-image
🔍 Detecting USB storage device and connecting to host: done
📋 rhel-guest-image.raw -> /dev/disk/by-id/usb-SanDisk_Extreme_Pro_52A456790D93-0:0 offset 0x0:
💾 1280 MB copied 289.22 MB/s

...

```

Please note the call via **sudo**. Jumpstarter needs access to block storage devices and serial ports. While
serial port access can be granted adding the user to the `dialout` group, block storage access requires
root privileges.

This command is typically used from CI scripts, storing an image building and a jumpstarter script along your software project.

## Script structure
```yaml
name: "Name of your script"
selector:
  - tag

timeout: 1800

expect-timeout: 60

steps:
  - ....

cleanup:
  - ....

```

A script has a name, a selector, and a timeout and a expect-timeout as main fields:

| Field             | Description |
| -----------       | ----------- |
| name              | Just a descriptive name for the script       |
| selector          | A list of tags to find a compatible board from those available on the host        |
| timeout           | This is the selection timeout, when waiting for a valid device based on the selector tags to become available |
| expect-timeout    | This is the default timeout for expect steps        |

## Script step commands

### - comment

This is the simplest, will print a comment into the console during execution.

```yaml
steps:
  - comment: "Powering off and writing the image to disk"
```

results in:
```
➤ Powering off and writing the image to disk
```

### - pause

This command pauses execution for the specified amount of seconds.

```yaml
steps:
  - pause: 5
```

results in:
```
➤ Step ➤ pause: 5
[✓] done

```

### - power

Enables power control of the device, accepted orders are:
* **on**
* **off**
* **cycle** : power off and on again

```yaml
steps:
  - power: "on"
```

results in:
```
➤ Step ➤ power: "on"
[✓] done

```

### - reset

Toggles the /RESET line of the dutlink-board, this will reset the DUT.

```yaml
steps:
  - reset:
      time_ms: 500
```
results in:
```
➤ Step ➤ reset
Resetting device...
[✓] done
```

### - set-disk-image

Writes a disk image into the storage device attached to jumpstarter in [connector J9](/docs/testharness/dutlinkboard/connector-reference/).

It accepts multiple parameters:

| Parameter         | Description |
| -----------       | ----------- |
| image             | The image .iso/.raw that must be in a bootable format for the DUT       |
| attach_storage    | true/false bool, if we want to attach the storage right away            |
| offset_gb         | if we want to store the image at an specific offset of the disk (in GB)           |

i.e.:
```yaml
steps:
  - set-disk-image:
      image: "rhel-image.raw"
```

results in:
```
➤ Step ➤ set-disk-image
🔍 Detecting USB storage device and connecting to host: done
📋 rhel-image.raw -> /dev/disk/by-id/usb-SanDisk_Extreme_Pro_52A456790D93-0:0 offset 0x0:
💾 10240 MB copied 287.80 MB/s
[✓] done
```

### - storage

Allows attaching or detaching the USB storage from the DUT.

Accepted orders are:
* attach
* detach

i.e.:
```yaml
steps:
  - storage: attach
```

results in:
```
➤ Step ➤ storage: "attach"
[✓] done
```

### - expect

Waits for a string to be received before continuing to next steps. It accepts multiple parameters:


| Parameter         | Description |
| -----------       | ----------- |
| this              | The string we are expecting on the console before we can continue       |
| echo              | true/false bool, if we want to echo the received data, useful for debugging and logging        |
| timeout           | seconds to wait for the expected string before failure, it defaults to expect-timeout from the script yaml      |
| debug_escapes     | true/false bool, transforms ESC terminal control sequences into text to avoid terminal manipulation      |

i.e.:
```yaml
steps:
  - expect:
      this: "login: "
      debug_escapes: true
      timeout: 300
      echo: true
```

could result in:
```
➤ Step ➤ expect: "login: "
....
....
<ESC>[0;1;39mRotate log files
<ESC>[0m.
[   44.674563] block sda: the capability attribute has been deprecated.
[   44.678058] WARNING! power/level is deprecated; use power/control instead

Red Hat Enterprise Linux 9.3 (Plow)
Kernel 5.14.0-362.8.1.el9_3.aarch64 on an aarch64

Activate the web console with: systemctl enable --now cockpit.socket

localhost login:
➤ Step ➤ ...
```

### - send

Sends a list of strings to the device one after another with a delay between them. It accepts multiple parameters:


| Parameter         | Description |
| -----------       | ----------- |
| this              | The list of strings to be sent to the device |
| delay_ms          | millisecond delay between strings. Defaults to 100ms      |
| echo              | true/false bool, if we want to echo the received data, useful for debugging and logging; but consumes output that could be needed in a later expect command (bug)   |
| debug_escapes     | true/false bool, transforms ESC terminal control sequences into text to avoid terminal manipulation      |

i.e.:
```yaml
steps:
  - send:
      this:
        - "root\n"
        - "password\n"
      echo: true
```

could result in:
```
➤ Step ➤ send


sent: root

root
Password:

sent: password

➤ Step ...
```

strings can contain any of the following sequences and they will be converted
into the corresponding control characters:
`<ESC>`, `<F1>`, `<F2>`, `<F3>`, `<F4>`, `<F5>`, `<F6>`, `<F7>`, `<F8>`, `<F9>`, `<F10>`, `<F11>`,
 `<UP>`, `<DOWN>`, `<LEFT>`, `<RIGHT>`, `<ENTER>`, `<TAB>`, `<BACKSPACE>`, `<DELETE>`,
 `<CTRL-A>`, `<CTRL-B>`, `<CTRL-C>`, `<CTRL-D>`, `<CTRL-E>`


### - write-ansible-inventory
This action assumes that the serial console is past login and it is ready to be used.
It will create an ansible inventory file for the DUT. This ansible inventory can
be used to run ansible playbooks against the DUT, as long as the DUT is connected
to a shared network with the jumpstarter host.

It accepts multiple parameters:

| Parameter         | Description |
| -----------       | ----------- |
| filename          | The filename for the ansible inventory file, defaults to `inventory`       |
| ssh_key           | The ssh key to use for the ansible inventory file, defaults to `~/.ssh/id_rsa`       |
| user              | The user for the ansible inventory file, defaults to `root`       |

i.e.:
```yaml
  - write-ansible-inventory:
      filename: "inventory.yaml"
      ssh_key: ~/.ssh/id_rsa
```

would look like:

```
➤ Step ➤ write-ansible-inventory

written : inventory.yaml
```

and the inventory file would look like:

```yaml
---
boards:
  hosts:
    orin-nx-00:
      ansible_host: 192.168.1.138
      ansible_user: root
      ansible_become: yes
      ansible_ssh_common_args: '-o StrictHostKeyChecking=no'
      ansible_ssh_private_key_file: ~/.ssh/id_rsa
```

### - local-shell
This command runs scripts on the local shell where jumpstarter is running.

i.e.:
```yaml
  - local-shell:
      script: |
        ansible -m ping -i inventory.yaml all
```

would look like:
```
➤ Step ➤ local-shell
+ ansible -m ping -i inventory.yaml all
orin-nx-00 | SUCCESS => {
    "ansible_facts": {
        "discovered_interpreter_python": "/usr/bin/python3"
    },
    "changed": false,
    "ping": "pong"
}
```

If you had previously generated an inventory with write-ansible-inventory.
