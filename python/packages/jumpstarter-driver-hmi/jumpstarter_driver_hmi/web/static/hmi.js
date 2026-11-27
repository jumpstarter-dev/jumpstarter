// The local HMI view: decodes the exporter's picture stream (H.264 or HEVC with WebCodecs, or Motion JPEG as plain pictures) and
// sends back only the input the HMI declares: a pointer (touch, mouse, pen), a keyboard, hard keys, rotary knobs and typed text.
//
// A plain ES module: no build step, no dependencies, and no network but this page's own /info and /ws.
//
//   1. Pure functions and small classes (no DOM, no network). The tests run these under node.
//   2. Player: the decoder (or the picture decoder), and drawing onto the canvas.
//   3. The page: connection, what /info says is offered, pointer, keyboard, hard keys, dials, text, lag report and status.
//
// Wire (the exporter's frames, one to one): a TEXT frame from the server is the codec line (JSON) or {"error": "..."};
// a BINARY frame is an 8-byte big-endian microsecond timestamp followed by one access unit (Annex-B for H.264 and HEVC, one
// JPEG for MJPEG). To the server, TEXT frames: {"resync": true}, {"lag": seconds}, and the input events of the interface:
// {"pointer": {...}}, {"keyboard": {...}}, {"key": {...}}, {"rotary": {...}}, {"text": "..."}.
//
// The address of this page carries a secret (?token=...), which /info and /ws want: the page passes it on.

// ---------------------------------------------------------------------------------------------------------------------
// 1. Pure parts
// ---------------------------------------------------------------------------------------------------------------------

export const HOLD_MS = 600; // a hard key held this long is a long press
export const MAX_TEXT_CHARS = 8000; // characters per text message (the exporter takes 16384)
export const LAG_REPORT_S = 1; // at most one lag report a second
export const LAG_THRESHOLD_S = 0.25; // below this the viewer is keeping up
export const LINE_PX = 40; // pixels of a wheel "line" (WheelEvent.deltaMode 1)
export const WHEEL_DETENT_PX = 100; // pixels of wheel that turn a dial by one detent
export const MAX_ROTARY_DELTA = 100; // detents in one `rotary` line (the exporter takes no more)
export const DETENT_DEGREES = 15; // how far a dial's notch turns for a detent (what the page shows, not what the HMI does)

const POINTER_KINDS = ["touch", "mouse", "pen"];
const ROTARY_ID = /^[A-Za-z][A-Za-z0-9_-]{0,31}$/;
const FAMILY_NAMES = { h264: "H.264", hevc: "HEVC", mjpeg: "MJPEG" };

const wholeSize = (v) => Number.isInteger(v) && v > 0 && v <= 16384;

// The family of a codec line's codec string: "h264" (avc1.*), "hevc" (hev1.*, hvc1.*) or "mjpeg", or null for a codec this page
// does not know.
export function codecFamily(codec) {
  const text = typeof codec === "string" ? codec.toLowerCase() : "";
  if (text.startsWith("avc1.") || text.startsWith("avc3.")) return "h264";
  if (text.startsWith("hev1.") || text.startsWith("hvc1.")) return "hevc";
  return text === "mjpeg" ? "mjpeg" : null;
}

// The codec line as the page uses it, or null when it is not one.
export function parseCodecLine(text) {
  let o;
  try {
    o = JSON.parse(text);
  } catch {
    return null;
  }
  if (o === null || typeof o !== "object" || typeof o.codec !== "string" || o.codec === "") return null;
  if (!wholeSize(o.width) || !wholeSize(o.height)) return null;
  return {
    codec: o.codec,
    family: codecFamily(o.codec),
    width: o.width,
    height: o.height,
    codedWidth: wholeSize(o.coded_width) ? o.coded_width : o.width,
    codedHeight: wholeSize(o.coded_height) ? o.coded_height : o.height,
    rotation: [90, 180, 270].includes(o.rotation) ? o.rotation : 0,
    fps: Number.isFinite(o.fps) ? o.fps : 0,
  };
}

// What a TEXT frame from the server is: {kind: "error", message}, {kind: "line", line}, or null for anything else.
export function parseServerText(text) {
  let o;
  try {
    o = JSON.parse(text);
  } catch {
    return null;
  }
  if (o !== null && typeof o === "object" && "error" in o) return { kind: "error", message: String(o.error) };
  const line = parseCodecLine(text);
  return line === null ? null : { kind: "line", line };
}

// The size of the picture as shown: the visible size turned by the rotation (90 and 270 swap the two). Pointers are aimed at this.
export function shownSize(line) {
  return line.rotation === 90 || line.rotation === 270 ? [line.height, line.width] : [line.width, line.height];
}

// Whether a viewer must start again from a key picture after this line: the codec string (so also the family) or the coded size
// changed. A rotation or a new frame rate does not.
export function restartsDecoder(previous, line) {
  return (
    previous === null ||
    previous.codec !== line.codec ||
    previous.codedWidth !== line.codedWidth ||
    previous.codedHeight !== line.codedHeight
  );
}

// The VideoDecoder configuration for an H.264 or HEVC codec line: the chunks are Annex-B access units, with their parameter sets in
// front of every key picture, so there is no `description`.
export function decoderConfig(line) {
  const config = {
    codec: line.codec,
    codedWidth: line.codedWidth,
    codedHeight: line.codedHeight,
    optimizeForLatency: true,
  };
  if (line.family === "h264") config.avc = { format: "annexb" };
  else if (line.family === "hevc") config.hevc = { format: "annexb" };
  return config;
}

// What to tell the user when the browser cannot decode the codec a line names. HEVC says what to ask for instead; nothing falls back.
export function unsupportedMessage(line) {
  if (line.family === null) return `This page does not know the codec ${line.codec}.`;
  const name = FAMILY_NAMES[line.family];
  if (line.family === "hevc") return `This browser cannot decode HEVC (${line.codec}). Ask the HMI for H.264 instead: --codec h264.`;
  if (line.family === "mjpeg") return `This browser cannot show ${name} pictures (${line.codec}): it has no createImageBitmap.`;
  return `This browser cannot decode ${name} (${line.codec}). Use a current Chrome, Edge, Safari or Firefox, at http://127.0.0.1 or http://localhost.`;
}

// How the (unrotated) picture is drawn onto a canvas of the shown size: turn about the middle by `angle`, draw srcW x srcH centered.
export function canvasPlan(line) {
  const [width, height] = shownSize(line);
  return { width, height, angle: (line.rotation * Math.PI) / 180, srcW: line.width, srcH: line.height };
}

// A BINARY frame: {ptsUs, data} (data is the access unit, a view onto the same bytes), or null if it is too short.
export function splitMedia(buffer) {
  if (!(buffer instanceof ArrayBuffer) || buffer.byteLength <= 8) return null;
  const view = new DataView(buffer);
  // 64 bits as two halves: microseconds since the epoch are far below 2**53, so a Number holds them exactly.
  return { ptsUs: view.getUint32(0) * 4294967296 + view.getUint32(4), data: new Uint8Array(buffer, 8) };
}

// The nal_unit_type of every NAL unit of an Annex-B buffer, in order: the low five bits of the header byte for H.264, bits 1 to 6 of
// the first of the two header bytes for HEVC.
export function nalTypes(data, family = "h264") {
  const types = [];
  const n = data.length;
  let i = 0;
  while (i + 3 < n) {
    if (data[i + 2] > 1) {
      i += 3; // no start code can begin at i, i + 1 or i + 2
    } else if (data[i] === 0 && data[i + 1] === 0 && data[i + 2] === 1) {
      types.push(family === "hevc" ? (data[i + 3] >> 1) & 0x3f : data[i + 3] & 0x1f);
      i += 4;
    } else {
      i += 1;
    }
  }
  return types;
}

// Whether the access unit is a picture a decoder can start at: an IDR slice (H.264), a BLA, IDR or CRA picture (HEVC, types 16 to
// 21) or any JPEG. Stops at the first coded slice it meets.
export function isKeyUnit(data, family = "h264") {
  if (family === "mjpeg") return true;
  for (const type of nalTypes(data, family)) {
    if (family === "hevc") {
      if (type >= 16 && type <= 21) return true;
      if (type < 32) return false;
    } else {
      if (type === 5) return true;
      if (type >= 1 && type <= 4) return false;
    }
  }
  return false;
}

export const hasIdr = (data) => isKeyUnit(data, "h264");

// Pixels of the shown picture (width x height) for a pointer at clientX, clientY over a canvas drawn at `rect`; null if it has no size.
export function mapPoint(clientX, clientY, rect, width, height) {
  if (!(rect.width > 0 && rect.height > 0)) return null;
  const clamp = (v, hi) => Math.min(Math.max(Math.round(v), 0), hi - 1);
  return {
    x: clamp(((clientX - rect.left) / rect.width) * width, width),
    y: clamp(((clientY - rect.top) / rect.height) * height, height),
  };
}

export function keyMessage(key, press) {
  return { key: { key, press } };
}

// -- what /info says the HMI takes ------------------------------------------------------------------------------------------

const KEY_CODE = /^[A-Za-z][A-Za-z0-9]{0,31}$/;
const asObject = (v) => (v !== null && typeof v === "object" && !Array.isArray(v) ? v : {});

// The input the page offers for an /info answer (`hmi_info()`), and nothing it does not declare:
//   pointer:  null, or {kinds, absolute, relative, buttons, wheel, maxContacts}
//   keyboard: null, or {codes: "all" | [KeyboardEvent.code...], text, layout}
//   keys:     the names of the hard keys the HMI takes, upper case
//   hardKeys: the buttons to draw (see hardKeyButtons), without the push buttons of the rotaries (those are drawn with their dial)
//   rotaries: the dials to draw (see rotaryDials)
//   viewOnly: the HMI takes nothing this page can send
//   relativeOnly: there is a pointer but it takes relative motion only, which this page does not send
export function inputPlan(info) {
  const input = asObject(asObject(info).input);
  const p = asObject(input.pointer);
  const kinds = Array.isArray(p.kinds) ? POINTER_KINDS.filter((kind) => p.kinds.includes(kind)) : [];
  const contacts = Number.isInteger(p.max_contacts) && p.max_contacts >= 1 ? Math.min(p.max_contacts, 32) : 1;
  const pointer =
    kinds.length === 0
      ? null
      : {
          kinds,
          absolute: p.absolute === true,
          relative: p.relative === true,
          buttons: p.buttons === true,
          wheel: p.wheel === true,
          maxContacts: contacts,
        };
  const k = asObject(input.keyboard);
  const codes = k.codes === "all" ? "all" : Array.isArray(k.codes) ? k.codes.filter((c) => KEY_CODE.test(c)) : [];
  const text = k.text === true;
  const keyboard =
    codes === "all" || codes.length > 0 || text
      ? { codes, text, layout: typeof k.layout === "string" ? k.layout : "us" }
      : null;
  const keys = Array.isArray(input.keys) ? input.keys.filter((name) => typeof name === "string").map((name) => name.toUpperCase()) : [];
  const rotaries = rotaryDials(input.rotaries, keys, input.hard_keys);
  const pushed = new Set(rotaries.filter((dial) => dial.pushKey !== null).map((dial) => dial.pushKey));
  const hardKeys = hardKeyButtons(input.hard_keys).filter((button) => !pushed.has(button.key.toUpperCase()));
  return {
    pointer,
    keyboard,
    keys,
    hardKeys,
    rotaries: rotaries.map(({ id, label, push }) => ({ id, label, push })),
    viewOnly: pointer === null && keyboard === null && hardKeys.length === 0 && rotaries.length === 0,
    relativeOnly: pointer !== null && !pointer.absolute,
  };
}

// The kind of pointer to send for what the browser's pointer is (`pointerType`): its own kind if the HMI declares it, else the first
// kind the HMI declares (a mouse on a touch-only HMI is a touch; a touch on a mouse-only HMI is a mouse).
export function pointerKind(pointerType, kinds) {
  return kinds.includes(pointerType) ? pointerType : kinds[0];
}

// The `buttons` to send for an event of `type`: PointerEvent.buttons as it is AFTER the event. An HMI without buttons has the primary
// one only; a press has at least that.
export function pointerButtons(type, buttons, caps) {
  if (type === "up" || type === "cancel") return 0;
  const masked = (Number.isInteger(buttons) ? buttons : 1) & (caps.buttons ? 63 : 1);
  return type === "down" ? masked || 1 : masked;
}

// What an event of a browser's pointer is to the HMI. The HMI has a contact down while one of the buttons it declares is (all of
// them if it declares `buttons`, else the primary one alone), whatever the other buttons do: a browser reports a second button as a
// `pointermove` whose `buttons` changed, never as a `pointerdown` or a `pointerup`, so the page follows the buttons and not the events.
//   `type`    the browser's event: "pointerdown", "pointermove" or "pointerup" (the last button is up: no contact is left)
//   `buttons` its PointerEvent.buttons, the state after the event
//   `down`    whether the page has a contact down for this pointer
// "down" when a declared button just went down, "move" while one is down and was, "up" when the last one just went up (though other
// buttons are still held), "hover" for a pointer that moves with none down, and null for anything else (a button the HMI does not
// declare going down or up, a pointer that is not down going up).
export function pointerStep(type, buttons, down, caps) {
  const state = Number.isInteger(buttons) ? buttons : type === "pointerdown" ? 1 : 0;
  const pressed = type !== "pointerup" && (state & (caps.buttons ? 63 : 1)) !== 0;
  if (pressed) return down ? "move" : "down";
  if (down) return "up";
  return type === "pointermove" ? "hover" : null;
}

export function pointerMessage(type, kind, point, shown, id, buttons) {
  return { pointer: { type, kind, x: point.x, y: point.y, id, buttons, w: shown[0], h: shown[1] } };
}

// A wheel's deltas in pixels, with the sign of WheelEvent.deltaX and deltaY: lines count LINE_PX each, pages the shown size.
export function wheelDeltas(event, shown) {
  const scale = event.deltaMode === 1 ? [LINE_PX, LINE_PX] : event.deltaMode === 2 ? shown : [1, 1];
  return { dx: (event.deltaX || 0) * scale[0], dy: (event.deltaY || 0) * scale[1] };
}

export function wheelMessage(kind, point, shown, deltas) {
  return { pointer: { type: "wheel", kind, x: point.x, y: point.y, id: 0, dx: deltas.dx, dy: deltas.dy, w: shown[0], h: shown[1] } };
}

// Which of the browser's pointers hold which contact (0 to max - 1) of the HMI: the lowest free one; none when all are held.
export class Contacts {
  constructor(max) {
    this.max = max;
    this.slots = new Map(); // pointerId -> contact
  }

  get size() {
    return this.slots.size;
  }

  has(pointerId) {
    return this.slots.has(pointerId);
  }

  // The contact for this pointer (the one it holds, or a new one), or -1 when the HMI follows no more fingers.
  acquire(pointerId) {
    if (this.slots.has(pointerId)) return this.slots.get(pointerId);
    const used = new Set(this.slots.values());
    for (let slot = 0; slot < this.max; slot++) {
      if (!used.has(slot)) {
        this.slots.set(pointerId, slot);
        return slot;
      }
    }
    return -1;
  }

  release(pointerId) {
    this.slots.delete(pointerId);
  }

  clear() {
    this.slots.clear();
  }
}

// -- the keyboard ---------------------------------------------------------------------------------------------------------

const MODIFIER_CODES = new Set([
  "ShiftLeft", "ShiftRight", "ControlLeft", "ControlRight", "AltLeft", "AltRight", "MetaLeft", "MetaRight", "OSLeft", "OSRight",
]);

// What the keyboard's Enter, Backspace, Escape and Tab are called as the HMI's named keys (Android's names, as `input.keys` has them):
// the way into an HMI that takes text and named keys but no key codes.
const KEYBOARD_KEYS = new Map([
  ["Enter", "ENTER"],
  ["Backspace", "DEL"],
  ["Escape", "ESCAPE"],
  ["Tab", "TAB"],
]);

// Whether the HMI takes the key with this KeyboardEvent.code.
export function codeTaken(keyboard, code) {
  if (keyboard === null || typeof code !== "string" || !KEY_CODE.test(code)) return false;
  return keyboard.codes === "all" || keyboard.codes.includes(code);
}

// What a keydown on the focused picture sends: {kind: "code" | "text" | "key", message}, or null when it sends nothing (and the browser
// keeps the key). A key whose code the HMI takes is a {"keyboard": {code, type: "down"}} line, whatever the layout. Else, when the HMI
// takes text and the key is one printable character, it is typed as text; else, a named key the HMI declares (Enter, Backspace,
// Escape, Tab). Anything held with Ctrl, Meta or Alt is the browser's (copy, paste, reload: not captured), a key that is part of an
// IME composition is the IME's, and Shift+Tab is left to the browser so that focus can always leave. `keys` are the names in /info's
// `input.keys`, upper case.
export function keyboardAction(event, keyboard, keys) {
  if (keyboard === null || event.isComposing) return null;
  const code = event.code;
  if (code === "Tab" && event.shiftKey) return null;
  const altGraph = typeof event.getModifierState === "function" && event.getModifierState("AltGraph");
  const chord = (event.ctrlKey || event.metaKey || event.altKey) && !altGraph;
  if (chord && !MODIFIER_CODES.has(code)) return null;
  if (codeTaken(keyboard, code)) return { kind: "code", code, message: { keyboard: { code, type: "down" } } };
  const key = event.key;
  if (typeof key !== "string") return null;
  if (key.length === 1) return keyboard.text ? { kind: "text", message: { text: key } } : null;
  const name = KEYBOARD_KEYS.get(key);
  if (name === undefined || !keys.includes(name)) return null;
  return { kind: "key", message: keyMessage(name, "short") };
}

export function keyUpMessage(code) {
  return { keyboard: { code, type: "up" } };
}

// The path of /info or /ws with the page's token (`search` is location.search): the server refuses either without it.
// null when the page has no token.
export function endpoint(path, search) {
  const token = new URLSearchParams(search).get("token");
  return token === null || token === "" ? null : `${path}?token=${encodeURIComponent(token)}`;
}

// The text messages for some typed or pasted text: cut at code points, so that no character is split.
export function textMessages(text, size = MAX_TEXT_CHARS) {
  const chars = Array.from(text);
  const out = [];
  for (let i = 0; i < chars.length; i += size) out.push({ text: chars.slice(i, i + size).join("") });
  return out;
}

// The button for one hard key of /info: {key, label, short, long}, or null for a key that takes neither a short nor a long press.
function keyButton(k) {
  if (k === null || typeof k !== "object" || typeof k.key !== "string" || k.key === "") return null;
  const presses = Array.isArray(k.presses) ? k.presses : ["short", "long"];
  const short = presses.includes("short");
  const long = presses.includes("long");
  if (!short && !long) return null;
  return { key: k.key, label: typeof k.label === "string" && k.label !== "" ? k.label : k.key, short, long };
}

// The buttons for the hard keys of /info: {key, label, short, long}. A key that takes neither a short nor a long press has no button.
export function hardKeyButtons(list) {
  return Array.isArray(list) ? list.map(keyButton).filter((button) => button !== null) : [];
}

// The dials for the rotaries of /info (`rotaries`; `keys` are the names of its `keys`, upper case; `hardKeys` its `hard_keys`):
// {id, label, push, pushKey}, in the order declared. `push` is the dial's push button, the same {key, label, short, long} as a hard
// key's (labeled "Push": the dial has the name), or null; `pushKey` is the upper case name of the key the rotary declares as its
// push button, or null. A push button exists when that key is one the HMI takes and takes a short or a long press (the presses of
// its entry in `hard_keys`, else both). A rotary with an id the exporter would refuse, or one already seen, is left out.
export function rotaryDials(list, keys, hardKeys) {
  if (!Array.isArray(list)) return [];
  const entries = Array.isArray(hardKeys) ? hardKeys : [];
  const dials = [];
  const seen = new Set();
  for (const r of list) {
    if (r === null || typeof r !== "object" || typeof r.id !== "string" || !ROTARY_ID.test(r.id) || seen.has(r.id)) continue;
    seen.add(r.id);
    const pushKey = typeof r.push === "string" && r.push !== "" ? r.push.toUpperCase() : null;
    let push = null;
    if (pushKey !== null && keys.includes(pushKey)) {
      const entry = entries.find((k) => k !== null && typeof k === "object" && typeof k.key === "string" && k.key.toUpperCase() === pushKey);
      const button = keyButton(entry === undefined ? { key: pushKey } : entry);
      push = button === null ? null : { ...button, label: "Push" };
    }
    dials.push({ id: r.id, label: typeof r.label === "string" && r.label.trim() !== "" ? r.label : r.id, push, pushKey });
  }
  return dials;
}

// The `rotary` lines for a turn of `detents` whole detents (positive is clockwise): one line, or several when it is more than the
// exporter takes in one. Nothing for no turn.
export function rotaryMessages(id, detents) {
  const messages = [];
  let left = Number.isFinite(detents) ? Math.trunc(detents) : 0;
  while (left !== 0) {
    const delta = Math.sign(left) * Math.min(Math.abs(left), MAX_ROTARY_DELTA);
    messages.push({ rotary: { id, delta } });
    left -= delta;
  }
  return messages;
}

// Turns a stream of wheel pixels into whole detents, one per WHEEL_DETENT_PX, and keeps what is left over for the next turn.
export class Detents {
  constructor(size = WHEEL_DETENT_PX) {
    this.size = size;
    this.rest = 0;
  }

  // `pixels` (positive is clockwise) more: the whole detents that makes now (with the sign of `pixels`), 0 if not yet one.
  take(pixels) {
    this.rest += Number.isFinite(pixels) ? pixels : 0;
    const whole = Math.trunc(this.rest / this.size) || 0; // never -0
    this.rest -= whole * this.size;
    return whole;
  }

  clear() {
    this.rest = 0;
  }
}

// The detents for a wheel event over a dial, taken from `detents` (see Detents): scrolling up turns it clockwise. Only the vertical
// delta counts (a trackpad's sideways drift does not); lines and pages are turned into pixels as for a pointer's wheel (`page` is
// the size of a page).
export function wheelTurn(detents, event, page) {
  return detents.take(-wheelDeltas(event, page).dy);
}

const ARROW_TURNS = new Map([
  ["ArrowRight", 1],
  ["ArrowUp", 1],
  ["ArrowLeft", -1],
  ["ArrowDown", -1],
]);

// The detents a keydown on a dial turns it by: an arrow key is one detent (right and up clockwise, left and down not), and a held key
// repeats. 0 for anything else, and for an arrow with a modifier (those are the browser's).
export function arrowTurn(event) {
  if (event.ctrlKey || event.metaKey || event.altKey || event.shiftKey || event.isComposing) return 0;
  return typeof event.key === "string" ? (ARROW_TURNS.get(event.key) ?? 0) : 0;
}

// Seconds to wait before the nth try (0, 1, ...) of reconnecting: 0.5, 1, 2, 4, then 8 at most.
export function backoffMs(attempt) {
  return Math.min(8000, 500 * 2 ** Math.min(attempt, 10));
}

// What a closed socket means: whether to try again, and what to tell the user. 4403 is the exporter's refusal: do not retry.
export function closeAdvice(code, reason) {
  const why = reason ? `: ${reason}` : "";
  if (code === 4403) return { retry: false, message: `The exporter refused the stream${why}` };
  if (code === 1000) return { retry: true, message: `The stream ended${why}` };
  if (code === 1011) return { retry: true, message: `The stream failed${why}` };
  return { retry: true, message: `Disconnected${why}` };
}

// How far behind real time this viewer is, from the pictures' own timestamps (the exporter's LagMeter, ported): the delay of a
// picture (arrival time less its timestamp) less the least delay of the last minute, which is the stream's own latency and the
// offset between the two clocks. Not measured: the first WARM_UP_S after a codec line (a joiner is replayed old pictures at once),
// a picture older than one already seen (a replay), and a delay over QUIET_S until it has lasted CONFIRM_S.
export class LagMeter {
  static WARM_UP_S = 3;
  static WINDOW_S = 60;
  static QUIET_S = 0.25;
  static CONFIRM_S = 1;
  static CLAMP_S = 30;

  constructor() {
    this.restart();
  }

  restart() {
    this.lagS = 0;
    this.since = null;
    this.minima = []; // [time, delay] with the delays increasing: the front is the least of the window
    this.newest = -Infinity;
    this.behindSince = null;
  }

  // `now` in seconds on any steady clock, `ptsUs` the picture's timestamp. The lag it shows, or null while warming up or for a replay.
  take(now, ptsUs) {
    if (ptsUs < this.newest) return null;
    this.newest = ptsUs;
    if (this.since === null) this.since = now;
    const delay = now - ptsUs / 1e6;
    while (this.minima.length > 0 && this.minima[this.minima.length - 1][1] >= delay) this.minima.pop();
    this.minima.push([now, delay]);
    while (this.minima.length > 0 && now - this.minima[0][0] > LagMeter.WINDOW_S) this.minima.shift();
    if (now - this.since < LagMeter.WARM_UP_S) return null;
    const behind = Math.max(0, delay - this.minima[0][1]);
    if (behind < LagMeter.QUIET_S) {
      this.behindSince = null;
      this.lagS = behind;
    } else {
      if (this.behindSince === null) this.behindSince = now;
      this.lagS = now - this.behindSince >= LagMeter.CONFIRM_S ? Math.min(behind, LagMeter.CLAMP_S) : 0;
    }
    return this.lagS;
  }
}

// When to say the lag: at most once a second while it is over the threshold, and once more when it is gone.
export class LagReporter {
  constructor() {
    this.reset();
  }

  reset() {
    this.over = false;
    this.lastAt = -Infinity;
  }

  // The number to send now, or null.
  offer(now, lagS) {
    const rounded = Math.round(lagS * 1000) / 1000;
    if (lagS > LAG_THRESHOLD_S) {
      if (now - this.lastAt < LAG_REPORT_S) return null;
      this.over = true;
      this.lastAt = now;
      return rounded;
    }
    if (!this.over) return null;
    this.over = false;
    this.lastAt = now;
    return rounded;
  }
}

// ---------------------------------------------------------------------------------------------------------------------
// 2. The decoder and the canvas
// ---------------------------------------------------------------------------------------------------------------------

const MAX_DECODE_QUEUE = 30; // chunks waiting for the decoder before it is restarted at the next key picture
const MAX_FAILURES = 5; // decoder errors in a row (with no picture between) before the page gives up
const MAX_BACKLOG = 240; // units kept while the browser is asked whether it can decode the codec

// Whether this browser can decode what `config` says (an H.264 or HEVC VideoDecoder configuration).
async function canDecode(config) {
  if (
    typeof VideoDecoder === "undefined" ||
    typeof VideoDecoder.isConfigSupported !== "function" ||
    typeof EncodedVideoChunk === "undefined"
  ) {
    return false;
  }
  try {
    return (await VideoDecoder.isConfigSupported(config)).supported === true;
  } catch {
    return false;
  }
}

// What a codec line starts: "decoder" (H.264 or HEVC through a VideoDecoder), "bitmap" (MJPEG through createImageBitmap), or
// "unsupported". While the browser is asked about a codec ("checking") the units are kept from the last key picture on, so that the
// first picture is not lost to the wait.
class Player {
  // hooks: onPicture(), onResync() (ask the server for a key picture), onProblem(message), onUnsupported(message)
  constructor(canvas, hooks) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d", { alpha: false, desynchronized: true });
    this.hooks = hooks;
    this.line = null;
    this.mode = "idle"; // idle (no codec line yet), checking, decoder, bitmap, unsupported
    this.generation = 0; // changes whenever a start is abandoned: an answer that took longer than that is not wanted
    this.decoder = null;
    this.needKey = true; // after (re)configure the first chunk must be a key chunk: deltas are dropped until then
    this.failures = 0;
    this.gaveUp = false;
    this.backlog = [];
    this.newestJpeg = null; // the picture waiting for the one being decoded: only the newest is kept
    this.decodingJpeg = false;
  }

  shown() {
    return this.line === null ? null : shownSize(this.line);
  }

  // A new connection: the next codec line starts again.
  forget() {
    this.line = null;
    this.abandon();
    this.mode = "idle";
  }

  abandon() {
    this.generation += 1;
    this.backlog = [];
    this.newestJpeg = null;
  }

  // A codec line arrived (the first of a connection, or a change).
  setLine(line) {
    const previous = this.line;
    this.line = line;
    const [width, height] = shownSize(line);
    if (this.canvas.width !== width) this.canvas.width = width;
    if (this.canvas.height !== height) this.canvas.height = height;
    const stuck = this.mode === "decoder" && !this.gaveUp && (this.decoder === null || this.decoder.state !== "configured");
    if (restartsDecoder(previous, line) || this.mode === "idle" || stuck) this.start();
    else if (this.mode !== "unsupported") this.canvas.hidden = false;
  }

  start() {
    const line = this.line;
    this.abandon();
    this.failures = 0;
    this.gaveUp = false;
    this.canvas.hidden = false;
    if (line.family === "mjpeg") {
      this.closeDecoder();
      if (typeof createImageBitmap === "function") this.mode = "bitmap";
      else this.unsupported(line);
      return;
    }
    if (line.family === null) {
      this.closeDecoder();
      this.unsupported(line);
      return;
    }
    this.mode = "checking";
    const generation = this.generation;
    canDecode(decoderConfig(line)).then((supported) => {
      if (generation !== this.generation) return;
      if (!supported) {
        this.closeDecoder();
        this.unsupported(line);
        return;
      }
      this.configure();
      const held = this.backlog;
      this.backlog = [];
      for (const [ptsUs, data] of held) this.feedDecoder(ptsUs, data);
    });
  }

  unsupported(line) {
    this.mode = "unsupported";
    this.canvas.hidden = true;
    this.hooks.onUnsupported(unsupportedMessage(line));
  }

  closeDecoder() {
    const decoder = this.decoder;
    this.decoder = null;
    if (decoder !== null && decoder.state !== "closed") {
      try {
        decoder.close();
      } catch {
        // already gone
      }
    }
  }

  configure() {
    if (this.line === null) return; // a decoder that errors after the connection was dropped has nothing to start again on
    this.mode = "decoder";
    try {
      if (this.decoder === null || this.decoder.state === "closed") {
        this.decoder = new VideoDecoder({ output: (frame) => this.draw(frame), error: (e) => this.fail(e) });
      } else {
        this.decoder.reset();
      }
      this.decoder.configure(decoderConfig(this.line));
      this.needKey = true;
    } catch (e) {
      this.decoder = null;
      this.gaveUp = true;
      this.hooks.onProblem(`The browser could not start the decoder: ${e.message}`);
    }
  }

  // One access unit.
  feed(ptsUs, data) {
    if (this.line === null) return;
    if (this.mode === "bitmap") this.feedJpeg(data);
    else if (this.mode === "decoder") this.feedDecoder(ptsUs, data);
    else if (this.mode === "checking") this.holdBack(ptsUs, data);
  }

  holdBack(ptsUs, data) {
    if (isKeyUnit(data, this.line.family)) this.backlog = [];
    else if (this.backlog.length === 0) return; // a delta picture with no key picture before it is no use
    if (this.backlog.length >= MAX_BACKLOG) return;
    this.backlog.push([ptsUs, data]);
  }

  feedDecoder(ptsUs, data) {
    if (this.gaveUp || this.decoder === null || this.decoder.state !== "configured") return;
    const key = isKeyUnit(data, this.line.family);
    if (this.needKey && !key) return;
    if (this.decoder.decodeQueueSize > MAX_DECODE_QUEUE) {
      this.configure(); // the decoder is behind: start again at a key picture, and have the server send one
      this.hooks.onResync();
      if (!key) return;
    }
    if (key) this.needKey = false;
    try {
      this.decoder.decode(new EncodedVideoChunk({ type: key ? "key" : "delta", timestamp: ptsUs, data }));
    } catch (e) {
      this.fail(e);
    }
  }

  // Motion JPEG: one picture at a time is decoded, in the order they came; those that arrive meanwhile are replaced by the newest, so
  // a browser that is behind drops the stale pictures instead of showing them late.
  feedJpeg(data) {
    this.newestJpeg = data;
    if (!this.decodingJpeg) this.decodeJpegs();
  }

  async decodeJpegs() {
    this.decodingJpeg = true;
    try {
      while (this.newestJpeg !== null) {
        const data = this.newestJpeg;
        this.newestJpeg = null;
        const generation = this.generation;
        let bitmap;
        try {
          bitmap = await createImageBitmap(new Blob([data], { type: "image/jpeg" }));
        } catch (e) {
          this.failures += 1;
          if (this.failures === 1) this.hooks.onProblem(`A picture could not be decoded: ${e.message}`);
          continue;
        }
        if (generation !== this.generation || this.line === null) {
          bitmap.close();
          continue;
        }
        this.draw(bitmap);
      }
    } finally {
      this.decodingJpeg = false;
    }
  }

  fail(e) {
    if (this.line === null) return;
    this.failures += 1;
    this.needKey = true;
    if (this.failures > MAX_FAILURES) {
      this.gaveUp = true;
      this.hooks.onProblem(`This stream cannot be decoded: ${e.message}`);
      return;
    }
    this.hooks.onProblem(`Decoder error: ${e.message}`);
    this.configure(); // an errored decoder is closed: this makes a new one
    this.hooks.onResync();
  }

  // A decoded VideoFrame or ImageBitmap.
  draw(picture) {
    try {
      if (this.line === null) return;
      const plan = canvasPlan(this.line);
      const ctx = this.ctx;
      ctx.save();
      ctx.translate(plan.width / 2, plan.height / 2);
      ctx.rotate(plan.angle);
      ctx.drawImage(picture, -plan.srcW / 2, -plan.srcH / 2, plan.srcW, plan.srcH);
      ctx.restore();
      this.failures = 0;
      this.hooks.onPicture();
    } finally {
      picture.close();
    }
  }
}

// ---------------------------------------------------------------------------------------------------------------------
// 3. The page
// ---------------------------------------------------------------------------------------------------------------------

const WHEEL_PAGE = [800, 800]; // what a wheel "page" is worth in pixels (WheelEvent.deltaMode 2) before the picture has a size

const ERROR_SHOW_MS = 6000;
const ERROR_FADE_MS = 1500;
const MAX_ERRORS = 3;

function startPage() {
  const $ = (id) => document.getElementById(id);
  const canvas = $("hmi");
  const statusEl = $("status");
  const noteEl = $("note");
  const hintEl = $("hint");
  const errorsEl = $("errors");
  const keysEl = $("keys");
  const dialsEl = $("dials");
  const controlsEl = $("controls");
  const textForm = $("text-form");
  const textInput = $("text-input");
  const reconnectButton = $("reconnect");

  const view = { phase: "connecting", note: "", fps: 0, frames: 0 };

  function renderStatus() {
    const shown = player.shown();
    let text;
    if (view.phase === "connecting") text = "Connecting…";
    else if (view.phase === "waiting") text = "Connected. Waiting for the first picture…";
    else if (view.phase === "live" && shown !== null) {
      const turned = player.line.rotation ? `, turned ${player.line.rotation}°` : "";
      text = `Live: ${shown[0]}×${shown[1]}${turned}, ${view.fps} fps, ${player.line.codec}`;
    } else text = view.note;
    if (statusEl.textContent !== text) statusEl.textContent = text;
  }

  function showError(message) {
    const item = document.createElement("li");
    item.textContent = message;
    errorsEl.append(item);
    while (errorsEl.children.length > MAX_ERRORS) errorsEl.firstElementChild.remove();
    setTimeout(() => item.classList.add("fading"), ERROR_SHOW_MS);
    setTimeout(() => item.remove(), ERROR_SHOW_MS + ERROR_FADE_MS);
  }

  const infoPath = endpoint("/info", location.search);
  const socketPath = endpoint("/ws", location.search);
  if (infoPath === null || socketPath === null) {
    statusEl.textContent =
      "This address has no token. Open the address that `j <device> hmi view` printed: it ends with ?token=...";
    return;
  }

  // -- the decoder and the lag ------------------------------------------------------------------------------------------------

  const meter = new LagMeter();
  const reporter = new LagReporter();
  const player = new Player(canvas, {
    onPicture: () => {
      view.frames += 1;
      if (view.phase === "waiting") {
        view.phase = "live";
        renderStatus();
      }
    },
    onResync: () => connection.send({ resync: true }),
    onProblem: showError,
    onUnsupported: (message) => {
      view.phase = "unsupported";
      view.note = message;
      renderStatus();
    },
  });

  setInterval(() => {
    view.fps = view.frames;
    view.frames = 0;
    renderStatus();
  }, 1000);

  // -- the socket -------------------------------------------------------------------------------------------------------------

  const connection = {
    ws: null,
    attempt: 0,
    timer: null,
    gotFrame: false,

    connect() {
      clearTimeout(this.timer);
      this.timer = null;
      reconnectButton.hidden = true;
      view.phase = "connecting";
      renderStatus();
      const scheme = location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(`${scheme}//${location.host}${socketPath}`);
      ws.binaryType = "arraybuffer";
      this.ws = ws;
      this.gotFrame = false;
      player.forget();
      meter.restart();
      reporter.reset();
      ws.onopen = () => {
        view.phase = "waiting";
        renderStatus();
      };
      ws.onmessage = (event) => this.onMessage(ws, event);
      ws.onclose = (event) => this.onClose(ws, event);
    },

    send(object) {
      if (this.ws === null || this.ws.readyState !== WebSocket.OPEN) return false;
      this.ws.send(JSON.stringify(object));
      return true;
    },

    firstFrame() {
      if (this.gotFrame) return;
      this.gotFrame = true;
      this.attempt = 0;
      refreshInfo();
    },

    onMessage(ws, event) {
      if (ws !== this.ws) return;
      if (typeof event.data === "string") {
        const text = parseServerText(event.data);
        if (text === null) return;
        if (text.kind === "error") {
          showError(text.message);
          return;
        }
        this.firstFrame();
        meter.restart();
        reporter.reset();
        // a new codec or size starts the decoder again: the status waits for its first picture
        const restarts = player.line === null || restartsDecoder(player.line, text.line);
        player.setLine(text.line);
        if (player.mode !== "unsupported" && (restarts || view.phase === "unsupported")) view.phase = "waiting";
        renderStatus();
        return;
      }
      const media = splitMedia(event.data);
      if (media === null) return;
      const now = performance.now() / 1000;
      const lag = meter.take(now, media.ptsUs);
      if (lag !== null) {
        const say = reporter.offer(now, lag);
        if (say !== null) this.send({ lag: say });
      }
      player.feed(media.ptsUs, media.data);
    },

    onClose(ws, event) {
      if (ws !== this.ws) return;
      this.ws = null;
      forgetInput(); // the exporter lets go of what was held when the stream ends: there is nobody to tell
      view.fps = 0;
      const advice = closeAdvice(event.code, event.reason);
      if (!advice.retry) {
        view.phase = "refused";
        view.note = advice.message;
        reconnectButton.hidden = false;
        renderStatus();
        return;
      }
      const delay = backoffMs(this.attempt);
      this.attempt += 1;
      view.phase = "closed";
      view.note = `${advice.message}. Reconnecting in ${Math.ceil(delay / 1000)} s…`;
      reconnectButton.hidden = false;
      renderStatus();
      this.timer = setTimeout(() => this.connect(), delay);
    },
  };

  reconnectButton.addEventListener("click", () => {
    if (connection.ws !== null) connection.ws.close();
    connection.attempt = 0;
    connection.connect();
  });

  // -- what the HMI takes (/info) ---------------------------------------------------------------------------------------------

  let shownPlan = "";

  async function refreshInfo() {
    let info;
    try {
      const response = await fetch(infoPath, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      info = await response.json();
    } catch (e) {
      showError(`Could not read what the HMI takes: ${e.message}`); // what is offered stays as it was
      return;
    }
    const next = inputPlan(info);
    const signature = JSON.stringify(next);
    if (signature === shownPlan) return;
    shownPlan = signature;
    offer(next);
  }

  // What the page is offering now, and what listens for it. A new plan replaces the old one: whatever was held is let go first.
  let plan = inputPlan(null);
  let listeners = null; // an AbortController: ends every listener of the plan

  function offer(next) {
    releaseAll();
    if (listeners !== null) listeners.abort();
    listeners = new AbortController();
    plan = next;
    const { signal } = listeners;
    keysEl.replaceChildren();
    dialsEl.replaceChildren();
    const hasText = plan.keyboard !== null && plan.keyboard.text;
    controlsEl.hidden = plan.hardKeys.length === 0 && plan.rotaries.length === 0 && !hasText;
    textForm.hidden = !hasText;
    for (const key of plan.hardKeys) keysEl.append(makeKeyButton(key));
    keysEl.hidden = plan.hardKeys.length === 0;
    dialsEl.hidden = plan.rotaries.length === 0;
    for (const dial of plan.rotaries) dialsEl.append(makeDial(dial, signal));
    const pointing = plan.pointer !== null && plan.pointer.absolute;
    canvas.style.cursor = pointing && plan.pointer.kinds[0] === "touch" ? "crosshair" : "default";
    canvas.tabIndex = plan.keyboard !== null ? 0 : -1;
    noteEl.hidden = !(plan.viewOnly || plan.relativeOnly);
    noteEl.textContent = plan.viewOnly
      ? "View only: this HMI takes no input."
      : plan.relativeOnly
        ? "This HMI takes relative pointer motion only, which this page does not send: there is no pointer here."
        : "";
    hintEl.hidden = plan.keyboard === null;
    hintEl.textContent =
      plan.keyboard === null
        ? ""
        : "Click the picture to type into it: keys go to the device while it has focus, and Shift+Tab leaves it.";
    if (plan.viewOnly) return; // no input handling at all: nothing listens
    if (pointing) listenToPointer(signal);
    if (plan.keyboard !== null) listenToKeyboard(signal);
    // whatever the page holds is let go when it is left
    window.addEventListener("blur", releaseAll, { signal });
    window.addEventListener("pagehide", releaseAll, { signal });
    document.addEventListener("visibilitychange", () => document.hidden && releaseAll(), { signal });
    if (plan.keyboard !== null && plan.keyboard.text) {
      textForm.addEventListener("submit", onTextSubmit, { signal });
      document.addEventListener("paste", onPaste, { signal });
    }
  }

  function releaseAll() {
    cancelContacts();
    releaseKeys();
  }

  function forgetInput() {
    active.clear();
    refused.clear();
    contacts.clear();
    pendingMoves.clear();
    pendingHover = null;
    held.clear();
  }

  // A button for a hard key: a click is a short press; held for HOLD_MS it is a long press instead. A key that takes only one of
  // the two does only that one. The keyboard (Enter or Space) cannot hold, so it sends the short press (the long one if that is all).
  function makeKeyButton(key) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = key.label;
    if (key.short && key.long) button.title = `${key.label}: click, or hold for a long press`;
    else if (key.long) button.title = `${key.label}: hold for a long press`;
    let timer = null;
    let heldDown = false;
    let down = false;
    const press = (kind) => connection.send(keyMessage(key.key, kind));
    const end = (release) => {
      clearTimeout(timer);
      timer = null;
      if (release && down && !heldDown && key.short) press("short");
      down = false;
    };
    button.addEventListener("pointerdown", (e) => {
      if (!e.isPrimary || (e.pointerType === "mouse" && e.button !== 0)) return;
      down = true;
      heldDown = false;
      if (key.long) {
        timer = setTimeout(() => {
          timer = null;
          heldDown = true;
          press("long");
        }, HOLD_MS);
      }
    });
    button.addEventListener("pointerup", () => end(true));
    button.addEventListener("pointercancel", () => end(false));
    button.addEventListener("pointerleave", () => end(false));
    button.addEventListener("contextmenu", (e) => e.preventDefault());
    button.addEventListener("click", (e) => {
      if (e.detail === 0) press(key.short ? "short" : "long"); // from the keyboard: a pointer's click has a count
    });
    return button;
  }

  // A dial for a rotary: a knob that the mouse wheel turns (over any part of the dial) and the arrow keys when it has focus, a button
  // for each way round (a detent a click), and, when the HMI declares one, the push button, which is a hard key's button. A turn
  // is a `rotary` line of whole detents, positive clockwise; what the wheel turns by less than a detent is kept for the next turn.
  function makeDial(dial, signal) {
    const root = document.createElement("div");
    root.className = "dial";
    root.setAttribute("role", "group");
    root.setAttribute("aria-label", dial.label);
    const knob = document.createElement("div");
    knob.className = "knob";
    knob.tabIndex = 0;
    knob.setAttribute("role", "application");
    knob.setAttribute("aria-roledescription", "rotary knob");
    knob.setAttribute("aria-label", dial.label);
    knob.title = `${dial.label}: turn it with the mouse wheel, or with the arrow keys when it has focus`;
    const notch = document.createElement("span");
    notch.className = "notch";
    knob.append(notch);
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = dial.label;
    name.setAttribute("aria-hidden", "true");
    const detents = new Detents();
    let angle = 0; // where the notch is drawn: feedback that a turn was sent, nothing the HMI knows
    function turn(count) {
      let sent = false;
      for (const message of rotaryMessages(dial.id, count)) sent = connection.send(message) || sent;
      if (!sent) return;
      angle = (angle + DETENT_DEGREES * count) % 360;
      knob.style.transform = `rotate(${angle}deg)`;
    }
    function stepButton(className, symbol, way, count) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = className;
      button.textContent = symbol;
      button.setAttribute("aria-label", `${dial.label}: ${way}`);
      button.title = `${dial.label}: ${way}, a detent a click`;
      button.addEventListener("click", () => turn(count), { signal });
      return button;
    }
    root.addEventListener(
      "wheel",
      (e) => {
        e.preventDefault(); // the page does not scroll: the dial turns
        turn(wheelTurn(detents, e, player.shown() ?? WHEEL_PAGE));
      },
      { passive: false, signal },
    );
    root.addEventListener(
      "keydown",
      (e) => {
        const count = arrowTurn(e);
        if (count === 0) return;
        e.preventDefault(); // the arrows turn the dial, they do not scroll the page
        turn(count);
      },
      { signal },
    );
    root.append(knob, stepButton("ccw", "↺", "counter-clockwise", -1), stepButton("cw", "↻", "clockwise", 1), name);
    if (dial.push !== null) {
      const push = makeKeyButton(dial.push);
      push.className = "push";
      push.setAttribute("aria-label", `${dial.label}: push`);
      root.append(push);
    }
    return root;
  }

  // -- the pointer ------------------------------------------------------------------------------------------------------------

  // Each pointer of the browser that has a declared button down (see pointerStep) holds a contact of the HMI (0 to max_contacts - 1):
  // `active` has, for each, its kind and where it was last; `contacts` hands out the numbers. Coordinates are pixels of the shown
  // picture, `w` and `h` its size.
  let contacts = new Contacts(1);
  const active = new Map(); // pointerId -> {slot, kind, last: {point, shown}}
  const refused = new Set(); // pointerId of a pointer held down that the HMI has no contact for: ignored until its buttons are up
  const pendingMoves = new Map(); // pointerId -> {x, y, buttons}: only the newest move of a frame is sent
  let pendingHover = null; // {x, y, kind}: a mouse or a pen moving with no button down, at most one a frame
  let moveFrame = false;

  function aim(clientX, clientY) {
    const shown = player.shown();
    if (shown === null) return null;
    const point = mapPoint(clientX, clientY, canvas.getBoundingClientRect(), shown[0], shown[1]);
    return point === null ? null : { point, shown };
  }

  function sendAt(type, state, pointerId, clientX, clientY, buttons) {
    const aimed = aim(clientX, clientY);
    if (aimed === null) return false;
    state.last = aimed;
    return connection.send(
      pointerMessage(type, state.kind, aimed.point, aimed.shown, state.slot, pointerButtons(type, buttons, plan.pointer)),
    );
  }

  function scheduleFlush() {
    if (moveFrame) return;
    moveFrame = true;
    requestAnimationFrame(flushMoves);
  }

  function flushMoves() {
    moveFrame = false;
    const moves = [...pendingMoves];
    pendingMoves.clear();
    for (const [pointerId, move] of moves) {
      const state = active.get(pointerId);
      if (state !== undefined) sendAt("move", state, pointerId, move.x, move.y, move.buttons);
    }
    const hover = pendingHover;
    pendingHover = null;
    if (hover !== null && active.size === 0) {
      sendAt("move", { slot: 0, kind: hover.kind }, null, hover.x, hover.y, 0);
    }
  }

  // Every pointer that is down is cancelled (the page lost the focus, was hidden, or what it offers changed).
  function cancelContacts() {
    for (const pointerId of [...active.keys()]) cancelContact(pointerId);
  }

  function cancelContact(pointerId) {
    refused.delete(pointerId);
    const state = active.get(pointerId);
    if (state === undefined) return;
    active.delete(pointerId);
    contacts.release(pointerId);
    pendingMoves.delete(pointerId);
    if (state.last !== undefined) {
      connection.send(pointerMessage("cancel", state.kind, state.last.point, state.last.shown, state.slot, 0));
    }
  }

  function listenToPointer(signal) {
    const caps = plan.pointer;
    contacts = new Contacts(caps.maxContacts);
    const on = (type, handler, options = {}) => canvas.addEventListener(type, handler, { ...options, signal });

    // One event of the browser's pointer, whichever it is: what it is to the HMI depends on the buttons it says are down.
    function follow(type, e) {
      const state = active.get(e.pointerId);
      const step = pointerStep(type, e.buttons, state !== undefined, caps);
      if (step !== "down" && step !== "move") refused.delete(e.pointerId); // no declared button is down: the next press is a new one
      if (step === "move") {
        pendingMoves.set(e.pointerId, { x: e.clientX, y: e.clientY, buttons: e.buttons });
        scheduleFlush();
      } else if (step === "up") {
        active.delete(e.pointerId);
        contacts.release(e.pointerId);
        pendingMoves.delete(e.pointerId);
        sendAt("up", state, e.pointerId, e.clientX, e.clientY, 0);
      } else if (step === "down") {
        if (!refused.has(e.pointerId)) press(type, e);
      } else if (step === "hover") {
        // not down: a mouse or a pen hovers, if the HMI has one; a finger does not
        const kind = e.pointerType === "touch" ? undefined : pointerKind(e.pointerType, caps.kinds);
        if (kind === undefined || kind === "touch" || active.size > 0) return;
        pendingHover = { x: e.clientX, y: e.clientY, kind };
        scheduleFlush();
      }
    }

    // A declared button went down on a pointer that had none: it takes a contact, if the HMI follows one more and the picture is known.
    function press(type, e) {
      const slot = contacts.acquire(e.pointerId);
      if (slot < 0 || aim(e.clientX, e.clientY) === null) {
        contacts.release(e.pointerId); // the HMI follows no more contacts (or there is nothing to aim at): this pointer is left alone
        refused.add(e.pointerId);
        return;
      }
      const state = { slot, kind: pointerKind(e.pointerType, caps.kinds) };
      if (type === "pointerdown") e.preventDefault();
      active.set(e.pointerId, state);
      pendingHover = null;
      try {
        canvas.setPointerCapture(e.pointerId);
      } catch {
        // a pointer that is no longer there: its up and cancel are still handled
      }
      canvas.focus({ preventScroll: true });
      sendAt("down", state, e.pointerId, e.clientX, e.clientY, e.buttons);
    }

    on("pointerdown", (e) => follow("pointerdown", e));
    on("pointermove", (e) => follow("pointermove", e));
    on("pointerup", (e) => follow("pointerup", e));
    on("pointercancel", (e) => cancelContact(e.pointerId));
    on("lostpointercapture", (e) => cancelContact(e.pointerId)); // after a pointerup the pointer is no longer active: nothing to cancel
    on("contextmenu", (e) => e.preventDefault());
    if (caps.wheel) {
      on(
        "wheel",
        (e) => {
          const aimed = aim(e.clientX, e.clientY);
          if (aimed === null) return;
          e.preventDefault(); // the page does not scroll: the HMI does
          const deltas = wheelDeltas(e, aimed.shown);
          if (deltas.dx === 0 && deltas.dy === 0) return;
          connection.send(wheelMessage(pointerKind("mouse", caps.kinds), aimed.point, aimed.shown, deltas));
        },
        { passive: false },
      );
    }
  }

  // -- the keyboard -----------------------------------------------------------------------------------------------------------

  // The codes of the keys that went down on the focused picture and have not come up: each is let go on blur and when the page is hidden.
  const held = new Set();

  function releaseKeys() {
    for (const code of [...held]) {
      held.delete(code);
      connection.send(keyUpMessage(code));
    }
  }

  function listenToKeyboard(signal) {
    const keyboard = plan.keyboard;
    const on = (type, handler) => canvas.addEventListener(type, handler, { signal });
    on("keydown", (e) => {
      const action = keyboardAction(e, keyboard, plan.keys);
      if (action === null) return;
      e.preventDefault();
      if (action.kind === "code") {
        if (e.repeat || held.has(action.code)) return; // the device repeats by itself
        if (connection.send(action.message)) held.add(action.code);
        return;
      }
      connection.send(action.message);
    });
    on("keyup", (e) => {
      if (!held.has(e.code)) return;
      e.preventDefault();
      held.delete(e.code);
      connection.send(keyUpMessage(e.code));
    });
    on("blur", releaseKeys);
  }

  // -- text -------------------------------------------------------------------------------------------------------------------

  function sendText(text) {
    if (text === "" || textForm.hidden) return false;
    for (const message of textMessages(text)) connection.send(message);
    return true;
  }

  function onTextSubmit(e) {
    e.preventDefault();
    if (sendText(textInput.value)) textInput.value = "";
  }

  function onPaste(e) {
    if (document.activeElement !== canvas) return; // pasting into the text box is the box's own business
    if (sendText(e.clipboardData ? e.clipboardData.getData("text/plain") : "")) e.preventDefault();
  }

  // -- go ---------------------------------------------------------------------------------------------------------------------

  refreshInfo(); // nothing is offered until /info says what the HMI takes
  connection.connect();
}

// The page runs only in a browser: under node (the tests) this file is just its pure functions.
if (typeof document !== "undefined") startPage();
