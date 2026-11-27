"""Annex-B H.264: NAL units, key and delta pictures, the codec string, and the parameter sets a key unit may lose."""

from __future__ import annotations

import pytest

from ..testing.pattern import pattern_units
from .codecs import (
    ParameterSets,
    codec_string,
    codec_string_of,
    first_sps,
    has_idr,
    has_picture,
    has_pps,
    has_sps,
    hevc_codec_string,
    is_reference,
    iter_nals,
    nal_begin,
    nal_types,
    parameter_sets,
    scan_unit,
    with_parameter_sets,
)

START = b"\x00\x00\x00\x01"
START3 = b"\x00\x00\x01"
#: A real-looking SPS (High profile, level 3.1), PPS, IDR slice, delta slice and delimiter. Only headers are read.
SPS_NAL = bytes(
    [0x67, 0x64, 0x00, 0x1F, 0xAC, 0xD9, 0x40, 0x50, 0x05, 0xBB, 0x01, 0x10, 0x00, 0x00, 0x03, 0x00, 0x10, 0x80]
)
PPS_NAL = bytes([0x68, 0xEB, 0xE3, 0xCB, 0x22, 0xC0])
OTHER_SPS_NAL = bytes([0x67, 0x42, 0xC0, 0x1E, 0xDA, 0x05, 0x82, 0x4C, 0x80])
SPS, PPS, OTHER_SPS = START + SPS_NAL, START + PPS_NAL, START + OTHER_SPS_NAL
IDR = START + bytes([0x65, 0x88, 0x84, 0x00, 0x2F, 0xFF]) + b"\x11" * 200
DELTA = START + bytes([0x41, 0x9A, 0x10, 0x30]) + b"\x22" * 40
DISPOSABLE = START + bytes([0x01, 0x9A, 0x10]) + b"\x33" * 10
AUD = START + bytes([0x09, 0xF0])
SEI = START + bytes([0x06, 0x05, 0x01, 0x80])


def test_nal_types_and_idr():
    assert nal_types(SPS + PPS + IDR) == [7, 8, 5]
    assert has_idr(SPS + PPS + IDR) and not has_idr(DELTA)
    assert has_sps(SPS + PPS) and not has_sps(IDR)
    assert has_pps(SPS + PPS) and not has_pps(SPS + IDR)
    assert has_picture(DELTA) and not has_picture(SPS + PPS)


def test_three_byte_start_codes_and_trailing_zeros():
    data = b"\x00\x00\x01\x67\x64\x00\x1f\x00" + b"\x00\x00\x00\x01\x65\x88\x84"
    assert nal_types(data) == [7, 5]
    # the zero before a 4-byte start code is not part of the previous NAL unit
    (_, start, end), *_ = iter_nals(data)
    assert data[start:end] == b"\x67\x64\x00\x1f"


def test_a_buffer_of_any_kind_is_read_the_same():
    data = SPS + PPS + IDR
    assert list(iter_nals(bytearray(data))) == list(iter_nals(data)) == list(iter_nals(memoryview(data)))
    # a start code with nothing after it is no unit
    assert list(iter_nals(b"")) == [] and list(iter_nals(b"\x00\x00\x01")) == []


def test_the_codec_string_is_named_from_the_sps():
    sps = first_sps(SPS + PPS)
    assert sps is not None
    assert codec_string(sps) == "avc1.64001F"
    assert codec_string(SPS_NAL) == "avc1.64001F"
    assert first_sps(PPS + IDR) is None
    with pytest.raises(ValueError, match="SPS"):
        codec_string(b"\x68\x00")
    with pytest.raises(ValueError, match="SPS"):
        codec_string(b"\x67\x64")  # too short to name anything


def test_a_picture_is_a_reference_unless_all_its_slices_say_otherwise():
    assert is_reference(IDR) and is_reference(DELTA)  # 0x41: nal_ref_idc 2
    assert not is_reference(DISPOSABLE)  # a slice with nal_ref_idc 0: nothing predicts from it
    # one slice that is a reference makes it one
    assert is_reference(START + bytes([0x21, 0x9A]) + START + bytes([0x01, 0x00]))
    assert is_reference(SPS + PPS)  # nothing to drop in parameter sets alone


def test_the_scan_finds_what_the_hub_needs_in_one_pass():
    scan = scan_unit(SPS + PPS + IDR)
    assert (scan.idr, scan.picture, scan.reference) == (True, True, True)
    assert scan.sps == (SPS_NAL,) and scan.pps == (PPS_NAL,)
    scan = scan_unit(DISPOSABLE)
    assert (scan.idr, scan.picture, scan.reference, scan.sps, scan.pps) == (False, True, False, (), ())
    scan = scan_unit(SPS + PPS)  # parameter sets alone: no picture, and nothing to drop
    assert (scan.idr, scan.picture, scan.reference) == (False, False, True)
    assert scan_unit(b"garbage") == (False, False, True, (), (), ())


def test_a_parameter_set_after_the_first_slice_belongs_to_the_next_unit_and_is_not_counted():
    scan = scan_unit(SPS + PPS + IDR + OTHER_SPS)
    assert scan.sps == (SPS_NAL,)
    assert scan_unit(IDR + SPS + PPS).sps == ()


def test_the_scan_keeps_every_set_a_unit_carries_in_order():
    scan = scan_unit(SPS + OTHER_SPS + PPS + IDR)
    assert scan.sps == (SPS_NAL, OTHER_SPS_NAL)


def test_the_parameter_sets_of_a_unit_come_out_with_four_byte_start_codes():
    assert parameter_sets(SPS + PPS + IDR) == SPS + PPS
    assert parameter_sets(START3 + SPS_NAL + START3 + PPS_NAL + START3 + bytes([0x65, 0x88])) == SPS + PPS
    assert parameter_sets(IDR) == b""
    assert parameter_sets(AUD + SPS + PPS + IDR) == SPS + PPS


def test_the_parameter_sets_go_in_front_of_the_picture_but_after_a_delimiter():
    assert with_parameter_sets(IDR, SPS + PPS) == SPS + PPS + IDR
    assert with_parameter_sets(AUD + IDR, SPS + PPS) == AUD + SPS + PPS + IDR  # a unit begins with its delimiter
    assert with_parameter_sets(AUD + SEI + IDR, SPS + PPS) == AUD + SPS + PPS + SEI + IDR
    assert with_parameter_sets(AUD, SPS + PPS) == AUD + SPS + PPS  # nothing but a delimiter
    assert with_parameter_sets(START3 + bytes([0x65, 0x88, 0x84]), SPS) == SPS + START3 + bytes([0x65, 0x88, 0x84])


def test_a_nal_unit_begins_at_its_start_code_of_either_length():
    data = START3 + b"\x67\x01" + START + b"\x68\x02"
    spans = [nal_begin(data, start) for _, start, _ in iter_nals(data)]
    assert spans == [0, 5]


# ---- the cache that repairs a key unit


def seen(*units: bytes) -> ParameterSets:
    sets = ParameterSets()
    for unit in units:
        sets.observe(scan_unit(unit))
    return sets


def repaired(sets: ParameterSets, unit: bytes) -> bytes | None:
    scan = scan_unit(unit)
    sets.observe(scan)
    return sets.repair(unit, scan)


def test_a_key_unit_that_has_its_sets_is_handed_back_untouched():
    sets = ParameterSets()
    unit = SPS + PPS + IDR
    assert repaired(sets, unit) is unit


def test_a_key_unit_without_sets_gets_the_latest_ones_in_front():
    sets = seen(SPS + PPS + IDR)
    assert repaired(sets, IDR) == SPS + PPS + IDR
    assert sets.complete and sets.size == len(SPS + PPS)


def test_the_sets_go_after_a_leading_delimiter():
    sets = seen(SPS + PPS + IDR)
    assert repaired(sets, AUD + SEI + IDR) == AUD + SPS + PPS + SEI + IDR


def test_a_key_unit_with_only_an_sps_gets_the_pps_right_after_it():
    sets = seen(SPS + PPS + IDR)
    assert repaired(sets, SPS + IDR) == SPS + PPS + IDR
    assert repaired(sets, AUD + SPS + SEI + IDR) == AUD + SPS + PPS + SEI + IDR
    assert repaired(sets, SPS) == SPS + PPS  # (nothing follows the sps)


def test_a_key_unit_with_only_a_pps_gets_the_sps_right_before_it():
    sets = seen(SPS + PPS + IDR)
    assert repaired(sets, PPS + IDR) == SPS + PPS + IDR
    assert repaired(sets, AUD + PPS + IDR) == AUD + SPS + PPS + IDR


def test_a_new_sps_a_unit_carries_is_used_for_the_repair_of_what_it_lacks():
    sets = seen(SPS + PPS + IDR)
    assert repaired(sets, OTHER_SPS + IDR) == OTHER_SPS + PPS + IDR  # its own sps, the cached pps
    assert repaired(sets, IDR) == OTHER_SPS + PPS + IDR  # and it is the latest from then on


def test_the_sets_any_unit_carries_are_remembered_a_delta_unit_too():
    sets = seen(SPS + PPS + DELTA)
    assert repaired(sets, IDR) == SPS + PPS + IDR


def test_every_set_of_the_latest_unit_that_had_them_is_kept():
    sets = seen(SPS + OTHER_SPS + PPS + IDR)
    assert repaired(sets, IDR) == SPS + OTHER_SPS + PPS + IDR


def test_a_unit_that_lacks_what_was_never_seen_cannot_be_repaired():
    sets = ParameterSets()
    assert repaired(sets, IDR) is None
    sets = seen(SPS + IDR)  # an sps but never a pps
    assert not sets.complete and repaired(sets, IDR) is None


def test_a_reset_forgets_every_set():
    sets = seen(SPS + PPS + IDR)
    sets.reset()
    assert not sets.complete and sets.size == 0 and repaired(sets, IDR) is None


def test_the_repair_leaves_the_unit_it_was_given_alone():
    sets = seen(SPS + PPS + IDR)
    unit = bytes(IDR)
    out = repaired(sets, unit)
    assert unit == IDR and out is not unit


# -- HEVC and MJPEG, on the real test patterns -------------------------------------------------------------------------


def hevc_nal(kind: int, payload: bytes = b"\x01\x02") -> bytes:
    """An HEVC NAL unit of ``kind`` (two header bytes: the type in bits 1-6 of the first, temporal id plus 1)."""
    return START + bytes([kind << 1, 0x01]) + payload


HEVC_VPS_NAL, HEVC_SPS_NAL, HEVC_PPS_NAL = hevc_nal(32), hevc_nal(33), hevc_nal(34)
HEVC_IDR = hevc_nal(20, b"\x11" * 100)
HEVC_CRA = hevc_nal(21, b"\x11" * 100)
HEVC_TRAIL_R = hevc_nal(1, b"\x22" * 30)
HEVC_TRAIL_N = hevc_nal(0, b"\x22" * 30)
HEVC_AUD_NAL = hevc_nal(35, b"\x50")


def test_hevc_nal_types_and_key_pictures():
    unit = HEVC_AUD_NAL + HEVC_VPS_NAL + HEVC_SPS_NAL + HEVC_PPS_NAL + HEVC_IDR
    assert nal_types(unit, "hevc") == [35, 32, 33, 34, 20]
    assert has_idr(unit, "hevc") and has_idr(HEVC_CRA, "hevc") and not has_idr(HEVC_TRAIL_R, "hevc")
    assert has_sps(unit, "hevc") and has_pps(unit, "hevc") and not has_sps(HEVC_IDR, "hevc")
    assert has_picture(HEVC_TRAIL_R, "hevc") and not has_picture(HEVC_VPS_NAL + HEVC_SPS_NAL, "hevc")
    assert first_sps(unit, "hevc") == bytes([33 << 1, 1, 1, 2])


def test_an_hevc_picture_is_a_reference_whatever_its_type_unless_the_source_says_otherwise():
    # a ``_N`` picture is not used by its own sub-layer, but a higher one may predict from it: the unit alone cannot say
    assert is_reference(HEVC_TRAIL_R, "hevc") and is_reference(HEVC_IDR, "hevc")
    assert is_reference(HEVC_TRAIL_N, "hevc")
    assert is_reference(HEVC_VPS_NAL + HEVC_SPS_NAL, "hevc")  # nothing to drop in parameter sets alone


def test_the_hevc_scan_finds_the_sets_in_front_of_the_picture():
    scan = scan_unit(HEVC_AUD_NAL + HEVC_VPS_NAL + HEVC_SPS_NAL + HEVC_PPS_NAL + HEVC_IDR, "hevc")
    assert (scan.idr, scan.picture, scan.reference) == (True, True, True)
    assert (len(scan.vps), len(scan.sps), len(scan.pps)) == (1, 1, 1)
    assert scan_unit(HEVC_IDR + HEVC_SPS_NAL, "hevc").sps == ()  # after the first slice: the next unit's


def test_the_codec_string_of_a_real_hevc_stream():
    first = pattern_units("hevc")[0]
    assert codec_string_of(first, "hevc") == "hev1.1.6.L60.90"  # Main profile, level 2.0, progressive and frame-only
    assert codec_string_of(HEVC_IDR, "hevc") is None  # no SPS to read it from
    with pytest.raises(ValueError):
        hevc_codec_string(b"\x42\x01")


def test_a_real_hevc_pattern_is_found_key_and_delta():
    units = pattern_units("hevc")
    assert len(units) == 60
    assert [i for i, unit in enumerate(units) if has_idr(unit, "hevc")] == [0, 15, 30, 45]
    scan = scan_unit(units[15], "hevc")
    assert scan.idr and len(scan.vps) == len(scan.sps) == len(scan.pps) == 1
    assert not has_sps(units[1], "hevc") and is_reference(units[1], "hevc")


def test_a_real_mjpeg_pattern_is_all_key_pictures_with_no_parameter_sets():
    units = pattern_units("mjpeg")
    assert len(units) == 15
    assert all(has_idr(unit, "mjpeg") and has_picture(unit, "mjpeg") and is_reference(unit, "mjpeg") for unit in units)
    assert all(unit[:2] == b"\xff\xd8" and unit[-2:] == b"\xff\xd9" for unit in units)
    assert scan_unit(units[0], "mjpeg") == (True, True, True, (), (), ())
    assert not has_idr(b"not a jpeg", "mjpeg")
    assert nal_types(units[0], "mjpeg") == [] and codec_string_of(units[0], "mjpeg") == "mjpeg"
    sets = ParameterSets("mjpeg")
    sets.observe(scan_unit(units[0], "mjpeg"))
    assert sets.complete and sets.size == 0 and sets.repair(units[0], scan_unit(units[0], "mjpeg")) == units[0]


def test_an_hevc_key_unit_that_lost_its_sets_gets_them_back():
    units = pattern_units("hevc")
    sets = ParameterSets("hevc")
    sets.observe(scan_unit(units[0], "hevc"))
    assert sets.complete and sets.size > 0
    keyed = units[15]
    # drop the three sets from the key unit with a real HEVC layout: AUD, then the picture
    nals = list(iter_nals(keyed, "hevc"))
    stripped = b"".join(START + keyed[start:end] for kind, start, end in nals if kind not in (32, 33, 34))
    assert not has_sps(stripped, "hevc")
    scan = scan_unit(stripped, "hevc")
    sets.observe(scan)
    repaired_unit = sets.repair(stripped, scan)
    assert repaired_unit is not None
    assert nal_types(repaired_unit, "hevc") == [35, 32, 33, 34, 20]
    assert has_idr(repaired_unit, "hevc")
    # a set that is missing alone goes back between its neighbors
    for dropped in (32, 33, 34):
        partial = b"".join(START + keyed[start:end] for kind, start, end in nals if kind != dropped)
        fixed = sets.repair(partial, scan_unit(partial, "hevc"))
        assert fixed is not None
        assert nal_types(fixed, "hevc") == [35, 32, 33, 34, 20]


def test_an_hevc_unit_that_lacks_a_set_never_seen_is_no_start():
    sets = ParameterSets("hevc")
    stripped = HEVC_AUD_NAL + HEVC_IDR
    scan = scan_unit(stripped, "hevc")
    sets.observe(scan)
    assert not sets.complete and sets.repair(stripped, scan) is None


def test_hevc_three_byte_start_codes_and_trailing_zeros():
    data = b"\x00\x00\x01\x42\x01\x01\x02\x00" + b"\x00\x00\x00\x01\x26\x01\xaf"
    assert nal_types(data, "hevc") == [33, 19]
    (_, start, end), *_ = iter_nals(data, "hevc")
    assert data[start:end] == b"\x42\x01\x01\x02"  # the zero before a 4-byte start code is not part of the unit
    assert nal_types(data, "mjpeg") == []  # a Motion JPEG unit has no NAL units, whatever its bytes


def _escaped(rbsp: bytes) -> bytes:
    """``rbsp`` with the emulation-prevention bytes a NAL unit needs."""
    out = bytearray()
    zeros = 0
    for byte in rbsp:
        if zeros >= 2 and byte <= 3:
            out.append(3)
            zeros = 0
        out.append(byte)
        zeros = zeros + 1 if byte == 0 else 0
    return bytes(out)


def test_the_hevc_codec_string_has_the_profile_space_tier_reversed_flags_and_the_constraint_bytes_unescaped():
    # profile space 1, high tier, profile 2, compatibility flag 29 (bit-reversed: 0x20000000), constraint byte 0xB0 and
    # five zero ones, level 3.1; the zero runs of the profile_tier_level are escaped in the NAL unit
    ptl = bytes([0x01, (1 << 6) | (1 << 5) | 2]) + (4).to_bytes(4, "big") + bytes([0xB0, 0, 0, 0, 0, 0]) + bytes([93])
    sps = bytes([0x42, 0x01]) + _escaped(ptl) + b"\xa0\x1e\x20"
    assert b"\x00\x00\x03" in sps
    assert hevc_codec_string(sps) == "hev1.A2.20000000.H93.B0"
    assert codec_string_of(START + sps, "hevc") == "hev1.A2.20000000.H93.B0"
