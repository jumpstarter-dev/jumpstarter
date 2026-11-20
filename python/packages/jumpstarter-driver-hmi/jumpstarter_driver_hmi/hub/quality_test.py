"""The quality model (``quality.py``): where the stream starts, and when the shared encode steps down and up.

Pure code, a clock of the test's own.
"""

from __future__ import annotations

import pytest

from . import quality
from .quality import (
    LEVELS,
    PRESETS,
    Adapter,
    PathSample,
    Rung,
    auto_bit_rate,
    ladder,
    level_rung,
    scaled,
    top_rung,
)


def test_the_bit_rate_that_suits_a_picture_is_8_mbit_at_1080p_in_proportion_to_the_pixels():
    assert auto_bit_rate(1920, 1080) == 8_000_000
    assert auto_bit_rate(1080, 1920) == 8_000_000  # a screen held upright
    assert auto_bit_rate(1080, 2340) == 9_800_000
    assert auto_bit_rate(1280, 720) in (3_600_000, 3_700_000)
    assert auto_bit_rate(160, 120) == 1_000_000  # never below 1 Mbit/s
    assert auto_bit_rate(7680, 4320) == 20_000_000  # nor above 20
    assert auto_bit_rate(1920, 1080, 60) > auto_bit_rate(1920, 1080, 30)  # a little more for more pictures a second


def test_the_picture_is_scaled_down_to_an_even_size_as_an_encoders_scaler_does_it():
    assert scaled(1080, 2340, 0) == (1080, 2340)
    assert scaled(1080, 2340, 3000) == (1080, 2340)
    assert scaled(1080, 2340, 1280) == (590, 1280)
    assert scaled(1280, 2856, 1280) == (572, 1280)
    assert scaled(1920, 1080, 960) == (960, 540)
    assert scaled(3, 1000, 100) == (2, 100)  # never below 2 pixels


def test_a_rung_is_a_named_tuple_that_still_works_as_a_plain_tuple():
    rung = Rung(640, 1_000_000, 15)
    assert rung == (640, 1_000_000, 15) and tuple(rung) == (640, 1_000_000, 15)
    assert (rung.max_size, rung.bit_rate, rung.max_fps) == (640, 1_000_000, 15)
    size, rate, fps = rung  # code that unpacks one keeps working
    assert (size, rate, fps) == (640, 1_000_000, 15)
    assert Rung._fields == ("max_size", "bit_rate", "max_fps")
    assert {rung: 1}[(640, 1_000_000, 15)] == 1  # ty: ignore[invalid-argument-type]  (it hashes as the tuple does)
    assert all(isinstance(p, Rung) for p in PRESETS.values())


def test_the_levels_are_the_presets_and_high():
    assert LEVELS == ("minimal", "low", "medium", "high")
    assert list(PRESETS) == ["minimal", "low", "medium"]


def test_nothing_about_a_particular_kind_of_device_or_its_tools_is_in_the_model():
    assert not hasattr(quality, "parse_wm_size")  # a platform's size parser lives with that platform's own package
    assert not hasattr(quality, "re")


def test_the_top_rung_is_the_devices_own_size_unless_the_operator_capped_something():
    assert top_rung((1920, 1080), None, None, None) == (0, 8_000_000, 30)
    assert top_rung(None, None, None, None) == (0, 8_000_000, 30)  # not known yet: 1080p
    # the rate suits the picture
    assert top_rung((1080, 2340), 1280, None, None) == (1280, auto_bit_rate(590, 1280), 30)
    # a rate cap below what suits it, and a number of pictures
    assert top_rung((1920, 1080), None, 3_000_000, 24) == (0, 3_000_000, 24)
    assert top_rung((1920, 1080), None, 50_000_000, None) == (0, 8_000_000, 30)  # a cap above it changes nothing
    assert isinstance(top_rung(None, None, None, None), Rung)


def test_the_ladder_goes_from_the_lowest_preset_to_the_top_and_never_above_it():
    rungs = ladder(Rung(0, 8_000_000, 30))
    assert rungs[0] == (640, 1_000_000, 15) and rungs[-1] == (0, 8_000_000, 30)
    assert [r.bit_rate for r in rungs] == sorted(r.bit_rate for r in rungs)
    small = ladder(Rung(960, 2_000_000, 24))
    assert small[-1] == (960, 2_000_000, 24) and all(r[0] <= 960 and r[1] <= 2_000_000 and r[2] <= 24 for r in small)
    assert len(set(small)) == len(small)  # rungs that come to the same thing are one
    assert all(isinstance(r, Rung) for r in rungs)


def test_a_plain_tuple_is_a_top_too_so_tuple_code_keeps_working():
    assert ladder((0, 8_000_000, 30))[-1] == (0, 8_000_000, 30)  # ty: ignore[invalid-argument-type]
    assert level_rung("low", (0, 8_000_000, 30)) == (960, 2_000_000, 24)  # ty: ignore[invalid-argument-type]


def test_a_named_level_is_a_preset_capped_by_the_top_and_high_is_the_top():
    top = Rung(0, 8_000_000, 30)
    assert level_rung("low", top) == (960, 2_000_000, 24)
    assert level_rung("high", top) == top
    assert level_rung("medium", Rung(960, 2_000_000, 24)) == (960, 2_000_000, 24)


# ---- the adapter


def calm(viewer: int = 1) -> PathSample:
    return PathSample(viewer, events=0, peak=0.05, keyframes_only=False)


def troubled(viewer: int = 1, events: int = 3) -> PathSample:
    return PathSample(viewer, events=events, peak=1.0, keyframes_only=False)


def keyframes(viewer: int = 1) -> PathSample:
    return PathSample(viewer, events=0, peak=0.0, keyframes_only=True)


def adapter(**kw) -> Adapter:
    return Adapter(min_interval_s=kw.pop("min_interval_s", 10), quiet_s=kw.pop("quiet_s", 30), **kw)


def decide(a: Adapter, now, recent, quiet=None, *, level=3, top=3):
    return a.decide(now, level=level, top=top, recent=recent, quiet=recent if quiet is None else quiet)


def test_the_only_viewer_that_cannot_keep_up_is_the_whole_audience_and_the_encode_steps_down():
    a = adapter()
    d = decide(a, 100, [troubled()])
    assert d is not None and d.direction == "down" and "not keeping up" in d.why


def test_one_slow_viewer_among_several_never_steps_the_encode_down():
    a = adapter()
    assert decide(a, 100, [troubled(1), calm(2), calm(3)]) is None  # one of three
    assert decide(a, 100, [troubled(1), troubled(2), calm(3)]).direction == "down"  # two of three: most
    assert decide(a, 100, [troubled(1), calm(2)]) is None  # one of two is not most


def test_a_viewer_on_key_pictures_only_is_slow_though_its_queue_is_calm():
    a = adapter()
    assert decide(a, 100, [keyframes()]).direction == "down"
    assert decide(a, 100, [keyframes(1), calm(2), calm(3)]) is None


def test_a_single_overflow_is_not_trouble_but_two_are():
    a = adapter()
    assert decide(a, 100, [troubled(events=1)]) is None
    assert decide(a, 100, [troubled(events=2)]).direction == "down"


def test_there_is_no_step_down_from_the_lowest_rung_and_none_within_the_interval_of_the_last_step():
    a = adapter()
    assert decide(a, 100, [troubled()], level=0) is None
    a.stepped(100, "down")
    assert decide(a, 105, [troubled()]) is None  # 5 s after a step: too soon
    assert decide(a, 111, [troubled()]).direction == "down"


def test_the_encode_steps_up_only_after_a_quiet_spell_with_headroom_and_never_above_the_top():
    a = adapter()
    a.stepped(100, "down")
    assert decide(a, 120, [calm()], level=1) is None  # not quiet for long enough yet
    up = decide(a, 131, [calm()], level=1)
    assert up is not None and up.direction == "up" and "room" in up.why
    assert decide(a, 200, [calm()], level=3, top=3) is None  # already at the top


def test_headroom_is_more_than_the_absence_of_trouble():
    a = adapter()
    a.stepped(100, "down")
    # no overflow, but the queue got to 60% of its limit
    nearly_full = PathSample(1, events=0, peak=0.6, keyframes_only=False)
    assert decide(a, 200, [nearly_full], level=1) is None
    assert decide(a, 200, [calm()], level=1).direction == "up"
    # one viewer had an overflow in the quiet spell
    assert decide(a, 200, [calm(1), troubled(2, events=1)], level=1) is None


def test_a_step_up_that_does_not_hold_doubles_the_wait_for_the_next_one_so_it_does_not_flap():
    a = adapter(quiet_s=30, min_interval_s=10)
    a.stepped(100, "down")
    assert decide(a, 131, [calm()], level=1).direction == "up"
    a.stepped(131, "up")
    assert decide(a, 145, [troubled()], level=2).direction == "down"  # the path did not have the room
    a.stepped(145, "down")
    assert a.failures == 1 and a.quiet_needed_s == 60
    assert decide(a, 190, [calm()], level=1) is None  # 45 s: enough before, not now
    assert decide(a, 206, [calm()], level=1).direction == "up"
    a.stepped(206, "up")
    a.stepped(215, "down")
    assert a.failures == 2 and a.quiet_needed_s == 120
    for _ in range(10):  # it never waits more than 8 times as long
        a.stepped(1000, "up")
        a.stepped(1001, "down")
    assert a.quiet_needed_s == 30 * 8


def test_a_long_steady_spell_forgives_the_failures():
    a = adapter(quiet_s=30)
    a.stepped(100, "down")
    a.stepped(110, "up")
    a.stepped(115, "down")
    assert a.failures == 1
    a.stepped(2000, "up")
    decide(a, 2000 + 4 * 60 + 1, [calm()], level=3)
    assert a.failures == 0


def test_nothing_is_decided_with_no_viewers_and_a_reset_forgets_the_history():
    a = adapter()
    assert decide(a, 100, []) is None
    a.stepped(100, "down")
    a.stepped(110, "up")
    a.stepped(115, "down")
    a.reset()
    assert a.failures == 0 and decide(a, 116, [troubled()]).direction == "down"


@pytest.mark.parametrize(("since", "expected"), [(0.0, 0.001), (3.0, 3.0), (60.0, 20.0)])
def test_trouble_counts_from_the_last_step_but_not_further_back_than_the_window(since, expected):
    a = adapter()
    a.stepped(1000.0, "down")
    assert a.recent_window_s(1000.0 + since) == pytest.approx(expected)
