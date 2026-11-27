# HMI Streaming

Jumpstarter gives targets with a display one **HMI interface**: an **HMI** (human-machine interface) is a remote
display with declared input. The device's picture goes out as a video stream (H.264 by default; HEVC and MJPEG are
optional), and whatever the device takes back goes in: a pointer (touch, mouse or pen), a keyboard, hard keys, rotary
knobs and typed text. The word means the device's interface, not an HMI application that runs on it. The interface covers **embedded
touch screens and mobile devices** today, and it is shaped for remote desktops (see the
{doc}`vnc <../../reference/package-apis/drivers/vnc>` driver for what exists now) and KVM devices later.

A driver that implements the interface behaves the same for every device: a client, a script or a browser page can watch
and drive an Android phone, an iPhone or any other display without knowing how the picture is made, and a UI shows
only the input the device declares.

## How it works

The device (or a program next to it) captures and encodes the picture. The **exporter** receives it and does everything
else: it keeps the current group of pictures so a new viewer starts at once, shares one encode between any number of
viewers, drops pictures for a viewer that falls behind without slowing the others, follows the network between the
client and the exporter by lowering and raising the quality (where the device lets it), records to MP4, and turns the
client's pointer, keyboard, key and text events into the device's own protocol. The client, and a browser behind it,
only ever see the generic stream; the device's protocol never reaches them.

Device drivers add an `hmi` child when the exporter configures one, the way `appium` is added for UI automation:

| Target | Driver | Configure |
| --- | --- | --- |
| No device (synthetic pictures, for tests and demos) | {doc}`hmi <../../reference/package-apis/drivers/hmi>` | `MockHmi`, with `pattern: true` for a picture a browser can decode |

## What a device declares

Each device says what it takes in `hmi_info()["input"]`, and nothing else is ever sent to it:

| | Declares |
| --- | --- |
| Pointer | The kinds (`touch`, `mouse`, `pen`), whether it takes absolute and/or relative positions, buttons, a wheel, and how many contacts it follows. |
| Keyboard | The W3C `KeyboardEvent.code` names it takes (`"all"` or a list), whether it types text, and the layout of that text. |
| Hard keys | The named buttons (`HOME`, `BACK`, ...) a UI draws, and the presses each takes. |
| Rotaries | Rotary knobs or encoders (turned by detents, optionally pushed), as embedded and automotive HMIs have them. |
| Codecs | Which of H.264, HEVC and MJPEG it can produce; the codec line names the one in use. |

An HMI that declares no input is view-only. The package README has the table of what each implementation declares.

## Try it without a device

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata:
  namespace: default
  name: hmi-demo
endpoint: ""
token: ""
export:
  hmi:
    type: jumpstarter_driver_hmi.driver.MockHmi
    config:
      pattern: true
```

```console
$ jmp shell --exporter-config hmi-demo.yaml -- j hmi view
HMI view: http://127.0.0.1:41873/?token=...  (Ctrl-C to stop)
```

`j <hmi> view` serves a small web page on `127.0.0.1` and opens it. The page decodes the stream with WebCodecs (H.264
and HEVC, in a browser that has them; it is tested with Chrome) or as pictures (MJPEG), shows the input the device
declares (the pointer, the wheel, the keyboard, the device's hard keys as buttons — hold one for a long press —, a dial for
each rotary and typed text) and reports its lag. It needs no network. The address carries a random token that the page's stream and input
need, so another program on the same machine cannot drive the device, and the page is never served beyond `127.0.0.1`.
Try a mouse and keyboard HMI, or another codec, with `config: {pattern: true, mode: mouse, codec: mjpeg}`.

## Use it from a script

```python
from jumpstarter.common.utils import env

with env() as root:
    hmi = root.hmi
    print(hmi.hmi_info()["input"]["pointer"])

    with hmi.video() as video:
        print(video.header)  # codec, size, rotation
        for frame in video:  # a str is a codec line, bytes is 8-byte timestamp + one access unit
            if isinstance(frame, bytes):
                break

    with hmi.input() as ui:
        ui.tap(120, 200)  # pixels of the picture the viewer is shown
        ui.key("HOME")  # a hard key
        ui.text("hello")
        ui.press("KeyA")  # a key by its KeyboardEvent.code, if the device declares keyboard codes
```

An event names the size of the picture it was aimed at (`size=(w, h)`); one aimed at a picture that has since been
rotated or rescaled is refused instead of landing somewhere else. Contacts beyond what the device follows, and pointer
kinds and buttons it does not declare, are ignored; a wheel, relative motion, a key code or text the device does not
take is answered with an error line. The input stream never waits for the client to read it.

## From the command line

| Usage | Description |
| --- | --- |
| `j <hmi> status` | State, picture, measured frame rate and bit rate, viewers and what each lost |
| `j <hmi> info` | What the HMI is and takes: size, codecs, the declared input, quality levels (`--json` for the raw answer) |
| `j <hmi> start` / `stop` | Start or stop the stream (joining it also starts it); `start --codec hevc` chooses the codec |
| `j <hmi> quality LEVEL` | `auto` follows the network; `minimal`, `low`, `medium` and `high` pin a level |
| `j <hmi> tap X Y` / `swipe X1 Y1 X2 Y2` / `click X Y` / `scroll DX DY` | A touch, a drag, a mouse click, the wheel (positive `DY` scrolls down) |
| `j <hmi> key NAME` / `press CODE` / `text TEXT` | Press a hard key, press a key by its code, type text |
| `j <hmi> turn ID DELTA` / `push ID` | Turn a rotary by detents (positive is clockwise), push it |
| `j <hmi> recording -o FILE` | Record the stream on the exporter to an MP4 (muxed there, no transcode; H.264 and HEVC) and fetch it |
| `j <hmi> record -o FILE` | Write the raw stream (Annex-B for H.264 and HEVC, JPEGs for MJPEG) |
| `j <hmi> view` | Open the local web page |

## Hard keys

`hmi_info()` lists the keys an HMI accepts and, as `hard_keys`, the buttons a UI should draw. Every implementation has a
default list, and the exporter can replace it:

```yaml
hmi:
  hard_keys:
    - HOME
    - {key: VOLUME_UP, label: Louder, presses: [short]}
```

The power and lock buttons are never in a default list and are not accepted unless the exporter lists them: pressing one
can lock the user out of a device whose only way in is its screen. `hmi: {control: false}` makes an HMI view-only.

## Implement it for your own device

Subclass `HmiDriver` and return an `HmiSource` that produces access units, declares what the device takes and takes
that input; everything above is done for it. The package's README describes the contract, and
`jumpstarter_driver_hmi.testing` has an `HmiConformance` suite to run against a fake of the device, with no hardware.
