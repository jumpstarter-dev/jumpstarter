# HMI driver

An **HMI** (human-machine interface) is a **remote display with declared input**: a picture, and whatever the device takes back, which it declares: a pointer (touch, mouse, pen), a keyboard, hard keys, rotary knobs and typed text. The word means the device's interface, not an HMI application that runs on it. A device that declares no input is a valid, view-only HMI.

`jumpstarter-driver-hmi` is the **interface**, not a device driver. It holds the abstract `HmiInterface`, the client, the shared hub that fans one encode out to any number of viewers, the recorder, the wire format, a small web view, and a mock HMI for tests. It covers embedded touch screens and mobile devices today and is made for more: remote desktops (VNC) and KVM devices are future sources of the same interface (see [What each implementation declares](#what-each-implementation-declares)). The implementations live in the device packages that already know the device, each as an optional `hmi:` child of the device driver, the way `appium:` is. Anyone can implement the interface in their own driver (see [Implementing an HMI](#implementing-an-hmi)).

* `jumpstarter-driver-adb` (`jumpstarter-driver-adb[hmi]`): Android, through scrcpy.
* `jumpstarter-driver-iosdevice` (`jumpstarter-driver-iosdevice[hmi]`): iPhone and iPad.

## Installation

```shell
pip install jumpstarter-driver-hmi
```

## The idea: the receiver always runs on the exporter

Whatever the device speaks, the **exporter** receives it and turns it into one HMI interface: a codec stream (H.264 by default), one framing, one shared hub. The client, and any browser behind it, only ever sees the generic HMI. Input from the client is translated on the exporter into the device's own protocol, and only what the device declares is ever sent to it. Device protocols never reach the client. The source captures and encodes; the exporter does everything else (group-of-pictures cache, fan-out, adaptation to the network, key-picture requests, recording).

## What an HMI exports

| Export | |
| --- | --- |
| `video()` | The picture as framed access units (see [Wire format](#wire-format)). Joining it starts the capture. The client may write `{"resync": true}` and `{"lag": seconds}` lines back. |
| `input()` | Pointer, keyboard, key and text events as JSON lines; errors come back as `{"error": ...}` lines that the exporter never waits on. |
| `hmi_info()` | What the HMI is and takes: size, codecs and the codec in use, and the declared input (pointer, keyboard, hard keys, clipboard), quality levels. |
| `status()` | State, picture, measured fps and bit rate, viewers and what each lost, quality, recording, and the device's own facts under `device`. |
| `start()` / `stop()` / `set_quality()` | `stop` is bounded in time. `start(codec=...)` chooses the stream's codec. `set_quality` raises `ValueError` if the HMI is not adjustable. |
| `start_recording()` / `recording_status()` / `stop_recording()` / `recording_download` | A fragmented MP4 muxed on the exporter from the stream's own access units (no transcode, no ffmpeg), with limits in time and bytes. H.264 and HEVC are recorded; MJPEG is not. |
| `report_lag()` / `report_path()` | Deprecated unary forms of the `{"lag"}` line, kept for one release. |

## What an HMI declares

`hmi_info()["input"]` is the only thing a UI needs in order to show the right controls: it shows what is declared and nothing else, and an HMI that declares nothing gets no input handling at all.

```json
{
  "interface": "hmi", "v": 1,
  "codecs": ["h264", "hevc"], "codec": "h264",
  "width": 1206, "height": 2622, "rotation": 0,
  "input": {
    "pointer":  {"kinds": ["touch"], "absolute": true, "relative": false, "buttons": false, "wheel": false, "max_contacts": 1},
    "keyboard": {"codes": ["KeyA", "KeyB", "..."], "text": true, "layout": "us"},
    "keys": ["HOME", "APP_SWITCH", "VOLUME_UP", "VOLUME_DOWN"],
    "hard_keys": [{"key": "HOME", "label": "Home", "presses": ["short", "long"]}],
    "rotaries": [],
    "clipboard": false
  },
  "quality": {"adjustable": false, "auto": false, "levels": []},
  "recording": true
}
```

| Field | Meaning |
| --- | --- |
| `pointer.kinds` | Which pointers the device takes: `touch`, `mouse`, `pen` (empty: no pointer). |
| `pointer.absolute` / `relative` | Whether it is told where in the picture, or by how far it moved (or both). |
| `pointer.buttons` / `wheel` | Whether it takes buttons beyond the primary one, and a scroll wheel. |
| `pointer.max_contacts` | How many fingers it follows at once. |
| `keyboard.codes` | The W3C `KeyboardEvent.code` names it takes: `"all"`, a list, or `[]` (no key events). |
| `keyboard.text` / `layout` | Whether it types text, and in which layout (`us` unless it says). |
| `keys` / `hard_keys` | The named hard keys it accepts, and the buttons a UI draws, each with the presses it takes (see [Hard keys](#hard-keys)). |
| `rotaries` | The rotary knobs or encoders it has, each with an `id`, a `label` and, if it can be pushed, the hard key that is its `push` button. Embedded and automotive HMIs have them; the implementations here declare none. |
| `clipboard` | Reserved: whether the clipboard can be set and read through the HMI. No implementation here declares it. |

### What each implementation declares

| Implementation | Codecs | Pointer | Keyboard | Hard keys |
| --- | --- | --- | --- | --- |
| `MockHmi` (`mode: touch`, the default) | H.264, HEVC, MJPEG | touch, 1 contact | text | Home, Back, App switch, Volume up |
| `MockHmi` (`mode: mouse`) | H.264, HEVC, MJPEG | mouse: absolute, buttons, wheel (relative with `relative: true`), 1 contact | every code, text | none |
| Android, through `adb` (scrcpy) | H.264, HEVC | touch, 1 contact | a table of codes with an Android key code, text | Back, Home, Recents, Volume up and down (Power and Lock only when listed) |
| iPhone and iPad, through `iosdevice` (scrios) | H.264, HEVC | touch, 1 contact | the codes that map to a USB HID usage, text | Home, App Switcher, Volume up and down, Mute, Siri (Lock and Power only when listed) |
| **Future** | | | | |
| VNC (remote desktops) | whatever the server speaks, re-encoded or MJPEG | mouse: absolute, buttons, wheel | every code, text | none |
| KVM devices | MJPEG or H.264 | mouse: absolute and relative, buttons, wheel | every code | none |

A device takes the subset it can genuinely do and says so; the exporter never sends it anything else (an event it does not take is answered with an error line, or ignored for a kind, a button or a contact it does not declare). The table lists what exists; the last two rows are what the interface is shaped for, not something implemented here.

## Codecs

One stream has **one** codec at a time, chosen by the `codec` option of the exporter's `hmi:` section or, for one lease, by `start(codec=...)` (a running stream restarts with it: viewers stay attached and get the new codec line). The default is **H.264**, which every browser and every device supports. **HEVC** and **MJPEG** are optional and declared in `hmi_info()["codecs"]`; the codec line (and `hmi_info()["codec"]`) names the one in use.

| Codec | `codec` | Codec line | Notes |
| --- | --- | --- | --- |
| H.264 | `h264` | `avc1.*` | The default. Annex-B access units, SPS and PPS in front of every key picture. |
| HEVC | `hevc` | `hev1.*` | Annex-B access units, VPS, SPS and PPS in front of every key picture. The web view needs a browser that decodes it (typically one with a hardware decoder for it, such as a recent Chrome, Edge or Safari); it says so when it cannot. |
| MJPEG | `mjpeg` | `mjpeg` | Every unit is one complete JPEG and a key picture. Large, but it decodes anywhere and joins instantly. **Not recorded** (the recorder muxes H.264 and HEVC only; `hmi_info()["recording"]` is `false`). |

## Wire format

Frames: `u32 BE N | u8 kind | body`.

* Kind 0 is the **codec line**, one JSON line. Required: `codec` (`avc1.*`, `hev1.*`, `hvc1.*` or `mjpeg`), `width`, `height`, `fps`, `format` (`annexb`, or `jpeg` for MJPEG), `timestamp` (`us`) and `v` (1). Optional: `coded_width`/`coded_height`, `rotation`, `point_scale`, `input` and `viewer`. It is sent first and again whenever it changes; only a change of codec string or coded size needs a new key picture.
* Kind 1 is an 8-byte big-endian microsecond timestamp followed by one access unit: Annex-B with the parameter sets in front of every key picture (H.264, HEVC) or one JPEG (MJPEG).
* Kinds 2 to 15 are reserved; a reader skips them.

Input lines, in the order sent:

```json
{"pointer": {"type": "down", "kind": "mouse", "x": 540, "y": 1200, "id": 0, "buttons": 1, "w": 1206, "h": 2622}}
{"pointer": {"type": "wheel", "kind": "mouse", "x": 540, "y": 1200, "dx": 0, "dy": 120, "w": 1206, "h": 2622}}
{"touch": {"type": "down", "x": 540, "y": 1200, "id": 0, "w": 1206, "h": 2622}}
{"keyboard": {"code": "KeyA", "type": "down"}}
{"key": {"key": "HOME", "press": "short"}}
{"rotary": {"id": "volume", "delta": -2}}
{"text": "hello"}
```

* **pointer**: `type` is `down`, `move`, `up`, `cancel` (read as `up`) or `wheel`; `kind` is `touch`, `mouse` or `pen`; `{"touch": ...}` is shorthand for a pointer of kind `touch`. `x` and `y` are pixels of the **shown** picture (the codec line's size turned by its `rotation`); `w` and `h` (both or neither) name the picture the event was aimed at, and one aimed at another size is refused. `id` tells the client's contacts apart (a whole number such as `PointerEvent.pointerId`, default 0); the device is told small numbers, 0 to `max_contacts` - 1, in the order the contacts come down. `buttons` is the `PointerEvent.buttons` bitmask (1 primary, 2 secondary, 4 auxiliary, 8 back, 16 forward, 32 eraser); a `down` without it has the primary button, a `move` keeps the buttons that are down (and one that says a declared button is down while no contact is, as a browser reports a second button, is a press), and it is the state **after** the event, as in a browser (`down` when the first button goes down, `move` when others change, `up` when the last is let go). `dx` and `dy` are pixels: the deltas of a `wheel` (the sign of `WheelEvent.deltaX` and `deltaY`: positive is right and down) and the relative motion of a `move` that has no `x` and `y` (for a device that declares `relative`). Contacts beyond `max_contacts`, kinds and buttons the device does not declare are ignored; a `wheel` or relative motion it has none of is refused with an error line. A contact still down when the stream ends is lifted.
* **keyboard**: `code` is a W3C `KeyboardEvent.code` (`KeyA`, `Digit1`, `ArrowLeft`, `ShiftLeft`: the physical key, whatever the layout) and `type` is `down` or `up`. Modifiers are keys like any other. A `down` of a key that is already down is a repeat and is dropped (the device repeats by itself); keys still down when the stream ends are released. Only the codes the device declares are taken.
* **key**: a named hard key (`HOME`, `BACK`, `APP_SWITCH`, `VOLUME_UP`...): `press` is `short`, `long`, `down` or `up`, and the key takes the presses it declares in `hard_keys` (`short` and `long` unless it says): any other press is refused with an error line.
* **rotary**: a rotary the device declares in `rotaries`: `id` names it and `delta` is the number of detents it is turned, positive clockwise (a whole number, not 0, at most 100 either way: a fast spin is several lines). Pushing it is the hard key its `push` names.
* **text**: characters to type, in the layout the device declares (`us` by default). A device that can only type text takes this and no `keyboard` events.

## Hard keys

`input.keys` lists every key name the HMI accepts (Android's names without `KEYCODE_`, compared without case, `-` read as `_`; `APP_SWITCHER` and `RECENTS` are `APP_SWITCH`). `input.hard_keys` is the ordered list of buttons a UI draws, each with a `label` and the `presses` it takes (`short`, `long`, `down`, `up`; `short` and `long` unless the key says): the exporter refuses a press a key does not declare. Every implementation has a default; the exporter can override it. The power and lock buttons are **not** in any default and are not accepted unless the exporter lists them (a source says which keys it can send but does not offer, as `optional_keys`): pressing one can lock the user out of a device whose only way in is its screen.

```yaml
hmi:
  hard_keys:
    - HOME
    - {key: POWER, label: Sleep, presses: [short]}
```

## Configuration

`HmiDriver` options, common to every implementation (an implementation adds its own):

| Option | Default | |
| --- | --- | --- |
| `codec` | the source's first (H.264) | The stream's codec: `h264`, `hevc` or `mjpeg`; the implementation says which it produces. |
| `control` | `true` | `false` makes the HMI view-only: it declares no input and `input()` is refused. |
| `hard_keys` | the source's | Replaces the buttons a UI draws. |
| `max_size`, `bit_rate`, `max_fps` | none | Optional **ceilings**. Set none and the stream starts at the device's own size and the bit rate that suits it, then follows the network. Only for sources whose quality can be adjusted. |
| `idle_stop_s` | `60` | Stop the capture after this long with no viewer and no input stream (0: never). |
| `start_timeout_s` / `stop_deadline_s` | `25` / `6` | How long the source gets to connect, and the longest `stop` and the end of a lease wait for it to let go. |
| `max_lag_s`, `max_queue_bytes` | `0.6`, `4000000` | A viewer further behind than this loses its queued pictures and waits for a key picture. |
| `join_replay_max_bytes`, `join_replay_max_age_s`, `join_wait_s` | `3000000`, `1.5`, `1.0` | How a joining viewer is started from the cached group of pictures. |
| `adaptive`, `adapt_min_interval_s`, `adapt_quiet_s` | `true`, `30`, `60` | Follow the network: one rung down when most viewers cannot keep up, one up after a quiet period. |
| `max_recording_s`, `max_recording_bytes` | `600`, 256 MiB | Limits of one recording. |
| `stall_s` | `15` | Silence, with a viewer attached, after which the source is asked what to do (wait, restart, end). |

## Local web view

`j <hmi> view` serves a page on `127.0.0.1` (never beyond it) and opens the browser at an address with a random per-session token, which the page needs for its stream and its input (a local process that does not have the address cannot drive the device). The page decodes the stream with WebCodecs (H.264 and HEVC) or as pictures (MJPEG), shows only the input the HMI declares (pointer, wheel, keyboard, hard keys as buttons, text), and reports its lag. It needs no network.

```shell
j hmi view               # any free port
j hmi view --port 8080 --no-open
j hmi view --codec mjpeg # choose the stream's codec first
```

## Implementing an HMI

An implementation is an `HmiSource` (the device-specific half) and an `HmiDriver` subclass (the exporter-facing half, which does the rest):

```python
@dataclass(kw_only=True)
class MyHmi(HmiDriver):
    device: MyDevice | None = None

    def make_source(self) -> HmiSource:
        return MySource(...)
```

A source never sees a viewer, a queue, a frame or a JSON line:

| Hook | You do |
| --- | --- |
| `start(sink, rung, timeout_s, codec=...)` | Start capturing in the codec asked for and return once connected. Call `sink.config(CodecLine(...))` before the first picture and whenever the picture's parameters change, and `sink.media(pts_us, access_unit)` for each access unit (a key picture with its parameter sets first). |
| `stop(deadline_s)` | End the capture within the deadline; idempotent. For a child process: ask, then SIGTERM, then SIGKILL (see `jumpstarter_driver_hmi.proc`). |
| `capabilities()` | Say honestly what the HMI is: `codecs`, `pointer` (`PointerCaps`), `keyboard` (`KeyboardCaps`), `keys`, `hard_keys`, `rotaries`, `quality`, `keyframe_cost` (`cheap`, `restart` or `none`; read again once `start` has returned, for a source that only knows its encoder then). Declare nothing for what the device cannot do. |
| `input_sink()` | Return an `InputSink`, or `None` for a view-only HMI. Override only what you declared: `pointer(event, size)` (by default a single touch, passed to `touch(kind, x, y, size)`), `keyboard(code, down)`, `key(key, press)`, `rotary(id, delta)`, `text(text)` and `release()`; they only **enqueue** (never block: `jumpstarter_driver_hmi.proc.CommandWriter` is the queue to use) and refuse (`InputError`) what they do not take. The exporter has already checked the event against what you declared, so coordinates are clamped integers in the shown picture. It is asked for on every event, so it may differ after a restart; it refuses while the capture is not running. |
| `request_keyframe()` | Ask the device for a key picture, without blocking. Only called for a `keyframe_cost` of `cheap`; where it restarts the capture, the hub waits for the periodic key picture instead. |
| `on_stall(silent_s, attempt)` | Say what to do about silence: `wait`, `restart` or `end`. |

`jumpstarter_driver_hmi.testing` has a `FakeSource` and a `HmiConformance` suite: subclass it in your tests, build your driver around a fake of your device, and the suite checks the codec line, the key picture with its parameter sets, joins, slow viewers, `resync` and `lag`, the input the HMI declares (pointer, keyboard, text, keys and their presses, rotaries, and that what it does not declare is refused), the lifted contacts, `hmi_info()`, the codecs, the recording and `stop`.

## MockHmi

`MockHmi` serves synthetic pictures and takes input into a log; it is for tests and demos. With `pattern: true` it serves a real test pattern (a 240x400 moving test chart, in a loop, in the chosen codec) that a browser can decode, which is what `j <hmi> view` needs to be tried without a device:

```yaml
export:
  hmi:
    type: jumpstarter_driver_hmi.driver.MockHmi
    config: {pattern: true, mode: mouse, codec: hevc}
```

`mode` is `touch` (the default: one touch, hard keys, text) or `mouse` (a mouse with buttons and a wheel, every key code, text); `relative: true` adds relative motion to the mouse; `rotaries` adds rotary knobs (`[{id: volume, label: Volume, push: VOLUME_PUSH}]`); `codecs` lists what it offers (all three by default).

## Limits of version 1

Hard keys are declared per implementation; text and key codes work only where the device supports them. Pen pressure and tilt, the clipboard and audio are not part of the wire format yet (`clipboard` is reserved in `hmi_info()`).

## API Reference

```{autoclass} jumpstarter_driver_hmi.client.HmiClient
:members: start, stop, status, hmi_info, set_quality, video, input, record, start_recording, recording_status, stop_recording
```
