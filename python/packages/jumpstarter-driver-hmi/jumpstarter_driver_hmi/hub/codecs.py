"""Annex-B H.264 and HEVC helpers, and JPEG pictures: find NAL units, tell a key picture from a delta one, name
the codec from an SPS, repair a key unit that lost its parameter sets.

Pure functions on ``bytes`` (and one small cache, :class:`ParameterSets`); nothing here knows a device or Jumpstarter.
Every function takes the codec ``family`` (``h264``, the default, ``hevc`` or ``mjpeg``: the interface's codec names).
An H.264 NAL unit has a one-byte header with its type in the low five bits; an HEVC one has two bytes with its type in
bits 1 to 6 of the first. A **Motion JPEG** unit is one JPEG picture: it has no NAL units and no parameter sets, and
every picture is a key picture.

**Why the hub repairs parameter sets.** A decoder can start only at a key picture that comes with its parameter sets in
front (SPS and PPS; VPS, SPS and PPS for HEVC), and a viewer that joins mid-stream is started from the cached group of
pictures, whose first unit is that key picture. Some encoders send the parameter sets once, at the start, and none of
the later key pictures carries them; others do it only when asked. Whatever the source does, the hub keeps the last
parameter sets it has seen and puts them in front of a key unit that came without, so that every start the hub hands
out can be decoded.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import NamedTuple

#: H.264 ``nal_unit_type`` values this package cares about.
NAL_SLICE = 1
NAL_IDR = 5
NAL_SEI = 6
NAL_SPS = 7
NAL_PPS = 8
NAL_AUD = 9
#: The NAL unit types of a coded slice of a picture (data partitions A to C, and an IDR slice, included).
SLICE_TYPES = (NAL_SLICE, 2, 3, 4, NAL_IDR)
#: HEVC ``nal_unit_type`` values: the parameter sets, the delimiter, and the key pictures (BLA, IDR and CRA: 16 to 21).
HEVC_VPS = 32
HEVC_SPS = 33
HEVC_PPS = 34
HEVC_AUD = 35
HEVC_IRAP_TYPES = range(16, 22)
#: The start code this module writes.
START_CODE = b"\x00\x00\x00\x01"
#: The families (the interface's codec names) these helpers know.
FAMILIES = ("h264", "hevc", "mjpeg")


def _sets_of(family: str) -> tuple[int, ...]:
    """The NAL types of the parameter sets of a family, in the order a decoder wants them (VPS, SPS, PPS)."""
    return (HEVC_VPS, HEVC_SPS, HEVC_PPS) if family == "hevc" else (NAL_SPS, NAL_PPS)


def _aud_of(family: str) -> int:
    return HEVC_AUD if family == "hevc" else NAL_AUD


def iter_nals(data: bytes | bytearray | memoryview, family: str = "h264") -> Iterator[tuple[int, int, int]]:
    """``(nal_unit_type, start, end)`` of every NAL unit in an Annex-B buffer (none in a ``mjpeg`` one).

    ``start`` is the offset of the NAL header (just after its 3- or 4-byte start code) and ``end`` is where the unit
    ends
    (the offset of the next start code, minus trailing zero bytes that belong to a 4-byte start code).
    """
    if family == "mjpeg":
        return
    view = bytes(data)
    n = len(view)
    starts: list[int] = []
    i = 0
    while True:
        i = view.find(b"\x00\x00\x01", i)
        if i < 0 or i + 3 >= n:
            break
        starts.append(i + 3)
        i += 3
    for index, start in enumerate(starts):
        end = starts[index + 1] - 3 if index + 1 < len(starts) else n
        while end > start and view[end - 1] == 0:  # the zero of a 4-byte start code (or trailing_zero_8bits)
            end -= 1
        if end > start:
            yield (view[start] >> 1) & 0x3F if family == "hevc" else view[start] & 0x1F, start, end


def nal_types(data: bytes, family: str = "h264") -> list[int]:
    """The ``nal_unit_type`` of every NAL unit, in order."""
    return [t for t, _, _ in iter_nals(data, family)]


def is_jpeg(data: bytes) -> bool:
    """Whether the unit is one JPEG picture (it starts with the start-of-image marker)."""
    return data[:2] == b"\xff\xd8"


def _is_slice(kind: int, family: str) -> bool:
    return kind < 32 if family == "hevc" else kind in SLICE_TYPES


def _is_key(kind: int, family: str) -> bool:
    return kind in HEVC_IRAP_TYPES if family == "hevc" else kind == NAL_IDR


def has_idr(data: bytes, family: str = "h264") -> bool:
    """Whether the access unit is a picture a decoder can start at: an IDR slice (H.264), an IRAP picture (HEVC) or a
    JPEG (every one is).
    """
    if family == "mjpeg":
        return is_jpeg(data)
    return any(_is_key(t, family) for t in nal_types(data, family))


def has_sps(data: bytes, family: str = "h264") -> bool:
    """Whether the access unit carries a sequence parameter set."""
    return _sets_of(family)[-2] in nal_types(data, family)


def has_pps(data: bytes, family: str = "h264") -> bool:
    """Whether the access unit carries a picture parameter set."""
    return _sets_of(family)[-1] in nal_types(data, family)


def has_picture(data: bytes, family: str = "h264") -> bool:
    """Whether the access unit carries a coded slice (not only parameter sets), or is a JPEG."""
    if family == "mjpeg":
        return is_jpeg(data)
    return any(_is_slice(t, family) for t in nal_types(data, family))


def _reference(data: bytes, family: str) -> bool:
    if family == "mjpeg":
        return True
    if family == "hevc":
        return True  # see is_reference
    slices = [(data[start] >> 5) & 0x3 for kind, start, _ in iter_nals(data) if kind in SLICE_TYPES]
    return not slices or any(ref_idc != 0 for ref_idc in slices)


def is_reference(data: bytes, family: str = "h264") -> bool:
    """Whether a later picture may predict from this access unit.

    H.264: some coded slice of it has ``nal_ref_idc`` other than 0. An IDR picture is always a reference. An encoder
    makes every delta picture a reference unless it was asked for temporal layers (then the enhancement layer's pictures
    are not): those are what a congested viewer can drop without breaking the picture.

    HEVC: always, from the unit alone. A picture of a ``_N`` type (0, 2, 4, ... 14) is not used for reference by the
    pictures of its own temporal sub-layer, but a higher sub-layer may still predict from it, and only the stream's
    highest sub-layer is free to drop; the hub does not follow sub-layers, so a source that knows which of its pictures
    nothing predicts from says so with the ``reference`` hint of ``HubSink.media``. A JPEG is a reference too (nothing
    predicts from it, but nothing is lost by dropping it either: the hub treats every unit of a stream of key pictures
    alike).
    """
    return _reference(data, family)


def first_sps(data: bytes, family: str = "h264") -> bytes | None:
    """The first SPS NAL unit (header included), or ``None``."""
    sps = _sets_of(family)[-2]
    for kind, start, end in iter_nals(data, family):
        if kind == sps:
            return bytes(data[start:end])
    return None


def codec_string(sps: bytes) -> str:
    """The RFC 6381 codec string (``avc1.PPCCLL``) of an H.264 SPS NAL unit: profile, constraint flags and level, as
    WebCodecs wants it.

    Raises:
        ValueError: If the unit is too short to be an SPS.
    """
    if len(sps) < 4 or sps[0] & 0x1F != NAL_SPS:
        raise ValueError("not an SPS NAL unit")
    return "avc1." + sps[1:4].hex().upper()


def _unescaped(data: bytes) -> bytes:
    """``data`` without its emulation-prevention bytes (``00 00 03`` is ``00 00``)."""
    out = bytearray()
    zeros = 0
    for byte in data:
        if zeros >= 2 and byte == 3:
            zeros = 0
            continue
        out.append(byte)
        zeros = zeros + 1 if byte == 0 else 0
    return bytes(out)


def hevc_codec_string(sps: bytes) -> str:
    """The RFC 6381 codec string (``hev1.1.6.L93.B0``) of an HEVC SPS NAL unit: its profile space, profile,
    compatibility flags (bit-reversed), tier and level, and the constraint bytes (without trailing zero bytes), as
    WebCodecs wants it.

    Raises:
        ValueError: If the unit is too short to be an SPS or is not one.
    """
    if len(sps) < 15 or (sps[0] >> 1) & 0x3F != HEVC_SPS:
        raise ValueError("not an HEVC SPS NAL unit")
    ptl = _unescaped(sps[2:26])  # the VPS id byte, then profile_tier_level
    if len(ptl) < 13:
        raise ValueError("an HEVC SPS too short to name its profile")
    space, tier, profile = ptl[1] >> 6, (ptl[1] >> 5) & 1, ptl[1] & 0x1F
    compatibility = int(f"{int.from_bytes(ptl[2:6], 'big'):032b}"[::-1], 2)
    constraints = bytes(ptl[6:12]).rstrip(b"\x00")
    out = f"hev1.{'' if space == 0 else 'ABC'[space - 1]}{profile}.{compatibility:X}.{'H' if tier else 'L'}{ptl[12]}"
    return out + "".join(f".{b:02X}" for b in constraints)


def codec_string_of(unit: bytes, family: str = "h264") -> str | None:
    """The codec string a codec line carries for a key unit (``avc1.*``, ``hev1.*`` or ``mjpeg``), or ``None`` for an
    H.264 or HEVC unit that has no SPS to read it from.
    """
    if family == "mjpeg":
        return "mjpeg"
    sps = first_sps(unit, family)
    if sps is None:
        return None
    return hevc_codec_string(sps) if family == "hevc" else codec_string(sps)


# -- parameter sets
# ---------------------------------------------------------------------------------------------------------------


class UnitScan(NamedTuple):
    """What one pass over an access unit finds: the facts the hub needs about it, so that it is read once."""

    #: It is a picture a decoder can start at (an IDR slice, an IRAP picture, a JPEG).
    idr: bool
    #: It carries a coded slice (not only parameter sets), or is a JPEG.
    picture: bool
    #: A later picture may predict from it (see :func:`is_reference`).
    reference: bool
    #: The SPS NAL units in front of its first picture (header included, no start code), in order.
    sps: tuple[bytes, ...]
    #: The PPS NAL units in front of its first picture, likewise.
    pps: tuple[bytes, ...]
    #: The VPS NAL units in front of its first picture, likewise (HEVC only).
    vps: tuple[bytes, ...] = ()


def scan_unit(data: bytes, family: str = "h264") -> UnitScan:
    """Read an access unit once. A parameter set after the first slice belongs to the next unit and is not counted."""
    if family == "mjpeg":
        jpeg = is_jpeg(data)
        return UnitScan(jpeg, jpeg, True, (), ())
    idr = picture = False
    sets: dict[int, list[bytes]] = {kind: [] for kind in _sets_of(family)}
    for kind, start, end in iter_nals(data, family):
        if _is_slice(kind, family):
            picture = True
            idr = idr or _is_key(kind, family)
        elif not picture and kind in sets:
            sets[kind].append(bytes(data[start:end]))
    sps_type, pps_type = _sets_of(family)[-2:]
    vps = tuple(sets.get(HEVC_VPS, ()))
    return UnitScan(idr, picture, _reference(data, family), tuple(sets[sps_type]), tuple(sets[pps_type]), vps)


def nal_begin(data: bytes, start: int) -> int:
    """The offset of the start code of the NAL unit whose header is at ``start`` (a 3- or 4-byte start code)."""
    begin = start - 3
    return begin - 1 if begin > 0 and data[begin - 1] == 0 else begin


def _scan_sets(scan: UnitScan, family: str) -> dict[int, tuple[bytes, ...]]:
    """The parameter sets a scan found, by NAL type."""
    if family == "hevc":
        return {HEVC_VPS: scan.vps, HEVC_SPS: scan.sps, HEVC_PPS: scan.pps}
    return {NAL_SPS: scan.sps, NAL_PPS: scan.pps}


def parameter_sets(data: bytes, family: str = "h264") -> bytes:
    """The parameter sets (VPS, SPS and PPS; SPS and PPS for H.264) of an access unit in front of its first picture, in
    order, each with a 4-byte start code; ``b""`` if none."""
    found = _scan_sets(scan_unit(data, family), family)
    return b"".join(_with_start_codes(found[kind]) for kind in _sets_of(family)) if family != "mjpeg" else b""


def with_parameter_sets(data: bytes, sets: bytes, family: str = "h264") -> bytes:
    """The access unit with ``sets`` (parameter sets with their start codes) put in front of its picture: after a
    leading access unit delimiter, which a unit must begin with, and before everything else."""
    return _insert(data, _first_non_delimiter(data, family), sets)


def _with_start_codes(nals: tuple[bytes, ...]) -> bytes:
    return b"".join(START_CODE + nal for nal in nals)


def _first_non_delimiter(data: bytes, family: str = "h264") -> int:
    """Where the first NAL unit that is not an access unit delimiter begins (the end of the data if there is none)."""
    aud = _aud_of(family)
    for kind, start, _ in iter_nals(data, family):
        if kind != aud:
            return nal_begin(data, start)
    return len(data)


def _insert(data: bytes, at: int, payload: bytes) -> bytes:
    return data[:at] + payload + data[at:]


class ParameterSets:
    """The latest parameter sets a stream has carried (SPS and PPS; VPS too for HEVC): what to put in front of a key
    unit that came without them. A ``mjpeg`` stream has none and is never repaired.

    Not thread-safe by itself (the hub keeps it under its own lock). A stream whose picture parameters change
    (:meth:`reset`) starts again from nothing: the old sets would describe another picture.
    """

    def __init__(self, family: str = "h264") -> None:
        """Start with no parameter sets seen."""
        self.family = family
        self._order = () if family == "mjpeg" else _sets_of(family)
        self._seen: dict[int, tuple[bytes, ...]] = dict.fromkeys(self._order, ())

    def reset(self) -> None:
        """Forget what was seen."""
        self._seen = dict.fromkeys(self._order, ())

    @property
    def complete(self) -> bool:
        """Whether every set a decoder needs is known (always, for a stream that has none)."""
        return all(self._seen.values())

    @property
    def size(self) -> int:
        """Bytes the cached sets take in front of a unit."""
        return sum(len(_with_start_codes(nals)) for nals in self._seen.values())

    def observe(self, scan: UnitScan) -> None:
        """Remember the sets an access unit carries (any unit, key or not). A unit with only some of them updates only
        those."""
        for kind, nals in _scan_sets(scan, self.family).items():
            if nals and kind in self._seen:
                self._seen[kind] = nals

    def repair(self, data: bytes, scan: UnitScan) -> bytes | None:
        """A key unit a decoder can start at: ``data`` itself when it has every set, else ``data`` with what it lacks
        put where it belongs.

        Call :meth:`observe` with the same scan first, so that the sets the unit does carry are the ones used. ``None``
        when the unit lacks a set that has never been seen: there is nothing to repair it with, and it is no start for
        anyone.
        """
        found = _scan_sets(scan, self.family)
        missing = [kind for kind in self._order if not found[kind]]
        if not missing:
            return data
        if any(not self._seen[kind] for kind in missing):
            return None
        if len(missing) == len(self._order):  # it has none: all of them, in order, in front of the picture
            sets = b"".join(_with_start_codes(self._seen[kind]) for kind in self._order)
            return with_parameter_sets(data, sets, self.family)
        for kind in missing:  # a set goes before the first set that follows it, else after the last that precedes it
            data = self._insert_set(data, kind)
        return data

    def _insert_set(self, data: bytes, kind: int) -> bytes:
        rank = self._order.index(kind)
        spans = [(t, nal_begin(data, start)) for t, start, _ in iter_nals(data, self.family)]
        later = [begin for t, begin in spans if t in self._order and self._order.index(t) > rank]
        payload = _with_start_codes(self._seen[kind])
        if later:
            return _insert(data, min(later), payload)
        last = max(i for i, (t, _) in enumerate(spans) if t in self._order and self._order.index(t) < rank)
        return _insert(data, spans[last + 1][1] if last + 1 < len(spans) else len(data), payload)
