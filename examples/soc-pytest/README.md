# Jumpstarter SOC testing with pytest example

This example aims to demonstrate Jumpstarter in an a simple SOC testing scenario
using pytest.

The following drivers will be utilized:
- DUTLink: for power, storage and console control of the target
- UStreamer: with an hdmi capture card plus a webcam for video snapshits

This example requires the following hardware:
- 1x Raspberry Pi 4
- 1x dutlink  (DUTLink could be replaced by a composite set of power, storage
  mux and serial port interface)
- 1x HDMI Capture card
- 1x Webcam

# Running the example (distributed env)

1) Setup an environment with the required hardware, and customize the
   exporter.yaml
2) Setup the exporter to be run from a container (TODO: link)
3) Label the exporter in k8s with the `board=rpi4` label
4) Prepare the images by running `make` in the `image` directory
5) Run the tests in this directory by running:
```shell
$ cd jumpstarter_example_soc_pytest
$ uv run pytest -s
================================================================== test session starts ===================================================================
platform linux -- Python 3.12.3, pytest-8.3.3, pluggy-1.5.0
rootdir: /home/majopela/jumpstarter/examples/soc-pytest
configfile: pyproject.toml
plugins: anyio-4.6.2.post1, cov-5.0.0
collected 6 items

test_on_rpi4.py::TestResource::test_setup_device
--------------------------------------------------------------------- live log setup ---------------------------------------------------------------------
INFO     jumpstarter.client.lease:lease.py:35 Leasing Exporter matching labels {'board': 'rpi4'} for seconds: 1800

INFO     jumpstarter.client.lease:lease.py:42 Lease c33b74ff-ad92-42a6-aa88-2c8a944a297c created
INFO     jumpstarter.client.lease:lease.py:46 Polling Lease c33b74ff-ad92-42a6-aa88-2c8a944a297c
INFO     jumpstarter.client.lease:lease.py:51 Lease c33b74ff-ad92-42a6-aa88-2c8a944a297c acquired
INFO     jumpstarter.client.lease:lease.py:73 Connecting to Lease with name c33b74ff-ad92-42a6-aa88-2c8a944a297c
--------------------------------------------------------------------- live log call ----------------------------------------------------------------------
INFO     /home/majopela/jumpstarter/examples/soc-pytest/jumpstarter_example_soc_pytest/test_on_rpi4.py:test_on_rpi4.py:51 Setting up device
read: 2.45GB [00:49, 52.8MB/s]
INFO     jumpstarter.testing.utils:utils.py:15 Waiting for login prompt

RPi: BOOTLOADER release VERSION:817717 DATE: 2023/01/11 TIME: 17:40:52
BOOTMODE: 0x06 partition 1 build-ts BUILD_TIMESTAMP=1673458852 serial c3656a7d boardrev d03114 stc 608563
..
Starting start4.elf @ 0xfeb00200 partition 1
+
[    0.000000] Booting Linux on physical CPU 0x0000000000 [0x410fd083]
...
...

Raspbian GNU/Linux 12 rpitest ttyS0

rpitest login: root
Password:
Linux rpitest 6.6.31+rpt-rpi-v8 #1 SMP PREEMPT Debian 1:6.6.31-1+rpt1 (2024-05-29) aarch64

The programs included with the Debian GNU/Linux system are free software;
the exact distribution terms for each program are described in the
individual files in /usr/share/doc/*/copyright.

Debian GNU/Linux comes with ABSOLUTELY NO WARRANTY, to the extent
permitted by applicable law.
root@rpitest:~#INFO     jumpstarter.testing.utils:utils.py:21 Logged in
INFO     /home/majopela/jumpstarter/examples/soc-pytest/jumpstarter_example_soc_pytest/test_on_rpi4.py:test_on_rpi4.py:112 Attempting a soft power off
 poweroff
root@rpitest:~#          Stopping session-1.scope - Session 1 of User root...
...
[   28.964752] reboot: Power down
PASSED
test_on_rpi4.py::TestResource::test_tpm2_device
--------------------------------------------------------------------- live log setup ---------------------------------------------------------------------
INFO     jumpstarter.testing.utils:utils.py:15 Waiting for login prompt
INFO     jumpstarter.testing.utils:utils.py:21 Logged in
--------------------------------------------------------------------- live log call ----------------------------------------------------------------------
INFO     /home/majopela/jumpstarter/examples/soc-pytest/jumpstarter_example_soc_pytest/test_on_rpi4.py:test_on_rpi4.py:78 Running command: apt-get install -y tpm2-tools
apt-get install -y tpm2-tools
...
root@rpitest:~# INFO     /home/majopela/jumpstarter/examples/soc-pytest/jumpstarter_example_soc_pytest/test_on_rpi4.py:test_on_rpi4.py:78 Running command: tpm2_createprimary -C e -c primary.ctx
tpm2_createprimary -C e -c primary.ctx
name-alg:
  value: sha256
  raw: 0xb
attributes:
  value: fixedtpm|fixedparent|sensitivedataorigin|userwithauth|restricted|decrypt
  raw: 0x30072
type:
  value: rsa
  raw: 0x1
exponent: 65537
bits: 2048
scheme:
  value: null
  raw: 0x10
scheme-halg:
  value: (null)
  raw: 0x0
sym-alg:
  value: aes
  raw: 0x6
sym-mode:
  value: cfb
  raw: 0x43
sym-keybits: 128
rsa: efe8d8387679d50d7cea501f4302834eebd4c4b3ec7f7b6a40128c63f3e9fb6e9203429dba4e1221d4d40039ff757dc3cbec638c79e11fe5cb4cc159a5e15a3d785b179f3081ada24f6370bad9b81ad2ddcba2e137bb62a454069d37da7cd1e3a06cb7fe03fc8386b055746b5396ee3b44aa1e40dae4e6257c763a53f7eb60a29df18ee14bce38d376434d89e9c95a79d1563833a48db8016c130f6246f24e023b8874e6f2f8bb1fbfe8ad9a1a0ef71b7fc0ed412056a40a225b6f352ea32aa9564c56bef09df7107b871db136aa530ae479b0b09256373479716416bc18fc7544df8c5de99383c37193f5e016bca7ab39231a69c6d4255d93aed66527bb261d
root@rpitest:~# INFO     /home/majopela/jumpstarter/examples/soc-pytest/jumpstarter_example_soc_pytest/test_on_rpi4.py:test_on_rpi4.py:78 Running command: tpm2_create -G rsa -u key.pub -r key.priv -C primary.ctx
tpm2_create -G rsa -u key.pub -r key.priv -C primary.ctx
name-alg:
  value: sha256
  raw: 0xb
attributes:
  value: fixedtpm|fixedparent|sensitivedataorigin|userwithauth|decrypt|sign
  raw: 0x60072
type:
  value: rsa
  raw: 0x1
exponent: 65537
bits: 2048
scheme:
  value: null
  raw: 0x10
scheme-halg:
  value: (null)
  raw: 0x0
sym-alg:
  value: null
  raw: 0x10
sym-mode:
  value: (null)
  raw: 0x0
sym-keybits: 0
rsa: c8cebe46344bbed17c39a497c3e5c53406be142ce741697641d940b77a835b3956c4ce0c5949688ff44a5d8ef847097e1870589ff4afcd401d2b7814b9a57ecc1f750b8a759b4e4f59915d8dda68c5463c8392870a59e21a02481e4d9b8d7ad27dd915850a587b6ff1a87fa98c578a0188e74c2731e39456c4e2e7f3158a878a294f82105a6ead9e397c15cd80c8b587c9a3f47513680cbe5f5fb5a0a41830566e5b70f312fa5e28fc780f45e72d4c8aa42fc2ea9d19e1068815493e2acda90cd6f7dabede223b494f916bd0c67682d4d5b4073b80954c0bab0ac612ae243f92c1d85ab3a7840d1d4aa7390f6155edb3341f229fbc015a8637d16230da03920f
root@rpitest:~# INFO     /home/majopela/jumpstarter/examples/soc-pytest/jumpstarter_example_soc_pytest/test_on_rpi4.py:test_on_rpi4.py:78 Running command: tpm2_load -C primary.ctx -u key.pub -r key.priv -c key.ctx
tpm2_load -C primary.ctx -u key.pub -r key.priv -c key.ctx
name: 000b0395380f392a3ef0773853ed245ed1a2ba94d26261d846268146f2f4de148cf0
root@rpitest:~# INFO     /home/majopela/jumpstarter/examples/soc-pytest/jumpstarter_example_soc_pytest/test_on_rpi4.py:test_on_rpi4.py:78 Running command: echo my message > message.dat
echo my message > message.dat
root@rpitest:~# INFO     /home/majopela/jumpstarter/examples/soc-pytest/jumpstarter_example_soc_pytest/test_on_rpi4.py:test_on_rpi4.py:78 Running command: tpm2_sign -c key.ctx -g sha256 -o sig.rssa message.dat
tpm2_sign -c key.ctx -g sha256 -o sig.rssa message.dat
root@rpitest:~# INFO     /home/majopela/jumpstarter/examples/soc-pytest/jumpstarter_example_soc_pytest/test_on_rpi4.py:test_on_rpi4.py:78 Running command: tpm2_verifysignature -c key.ctx -g sha256 -s sig.rssa -m message.dat
.dat_verifysignature -c key.ctx -g sha256 -s sig.rssa -m message
root@rpitest:~# echo result: $?
result: 0
PASSED
------------------------------------------------------------------- live log teardown --------------------------------------------------------------------
INFO     /home/majopela/jumpstarter/examples/soc-pytest/jumpstarter_example_soc_pytest/test_on_rpi4.py:test_on_rpi4.py:112 Attempting a soft power off
poweroff
root@rpitest:~# poweroff
...
[   80.068761] reboot: Power down

test_on_rpi4.py::TestResource::test_power_off_camera PASSED
test_on_rpi4.py::TestResource::test_power_on_camera PASSED
test_on_rpi4.py::TestResource::test_power_on_hdmi
--------------------------------------------------------------------- live log call ----------------------------------------------------------------------
INFO     imagehash:imagehash.py:79 video comparing snapshot test_booting_empty_ok.jpeg: snapshot f0f0f0f0f0f0f0f0, ref f0f0f0f0f0f0f0f0, diff: 0
INFO     imagehash:imagehash.py:79 video comparing snapshot test_booting_rainbow_ok.jpeg: snapshot 3c3c3c1c1c1c1c1c, ref 3c3c3c1c1c1c1c1c, diff: 0
INFO     imagehash:imagehash.py:79 video comparing snapshot test_booting_raspberries_ok.jpeg: snapshot c000000000000000, ref c000000000000000, diff: 0
PASSED
test_on_rpi4.py::TestResource::test_login_console_hdmi
--------------------------------------------------------------------- live log setup ---------------------------------------------------------------------
INFO     jumpstarter.testing.utils:utils.py:15 Waiting for login prompt
INFO     jumpstarter.testing.utils:utils.py:21 Logged in
--------------------------------------------------------------------- live log call ----------------------------------------------------------------------
INFO     imagehash:imagehash.py:79 video comparing snapshot test_booted_ok.jpeg: snapshot c0c0000000000000, ref c0c0000000000000, diff: 0
PASSED
------------------------------------------------------------------- live log teardown --------------------------------------------------------------------
INFO     /home/majopela/jumpstarter/examples/soc-pytest/jumpstarter_example_soc_pytest/test_on_rpi4.py:test_on_rpi4.py:112 Attempting a soft power off
INFO     jumpstarter.client.lease:lease.py:63 Releasing Lease c33b74ff-ad92-42a6-aa88-2c8a944a297c


============================================================= 6 passed in 303.59s (0:05:03) ==============================================================
```
