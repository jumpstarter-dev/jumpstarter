"""When a source is asked for a key picture: the policy on its own, on a clock of the test's own (no hub, no source)."""

from __future__ import annotations

from .keyframes import KeyframeGate

WINDOW = 2.0


def gate(**options) -> KeyframeGate:
    return KeyframeGate(WINDOW, **options)


def test_the_first_request_is_sent_at_once_and_so_is_one_outside_the_window():
    g = gate()
    assert g.request(100.0, 1) is True and g.asked == 1 and g.unanswered
    g.key_came()
    assert g.request(102.5, 1) is True and g.asked == 2


def test_a_request_while_a_key_picture_is_on_its_way_is_shared_not_sent_again():
    g = gate()
    assert g.request(100.0, 1) is True
    assert g.request(100.5, 2) is False and g.request(101.0, 3) is False  # the key picture on its way serves them too
    assert g.shared == 2 and g.coalesced == 0 and g.forwarded == 0 and g.asked == 1
    assert not g.pending


def test_a_shared_request_is_made_again_when_the_window_ends_and_the_key_picture_never_came():
    g = gate()
    g.request(100.0, 1)
    g.request(100.5, 2)
    assert g.poll(101.9, waiting=True) is False  # the window is not over
    assert g.poll(102.1, waiting=True) is True  # and it never came: asked again, for all of them
    assert g.retries == 1 and g.asked == 2
    assert g.poll(102.2, waiting=True) is False  # once in a window
    # and again a window later, for as long as someone waits
    assert g.poll(104.2, waiting=True) is True and g.retries == 2


def test_the_retry_after_a_window_shorter_than_the_retry_time_waits_for_the_retry_time():
    g = KeyframeGate(0.2, retry_after_s=1.0)
    g.request(100.0, 1)
    assert g.poll(100.5, waiting=True) is False  # the window is over, but a request is given a second to be answered
    assert g.poll(101.0, waiting=True) is True


def test_a_different_viewer_after_the_last_request_was_answered_is_sent_at_once():
    g = gate()
    g.request(100.0, 1)
    g.key_came()  # the key picture went by
    assert g.request(100.5, 2) is True  # inside the window, but it is not that request repeated
    assert g.forwarded == 1 and g.coalesced == 0 and g.asked == 2


def test_the_same_viewer_again_inside_the_window_is_the_request_repeated_and_waits_for_the_window_to_end():
    g = gate()
    g.request(100.0, 1)
    g.key_came()
    assert g.request(100.5, 1) is False
    assert g.coalesced == 1 and g.forwarded == 0 and g.pending
    assert g.poll(101.5, waiting=True) is False  # not yet
    assert g.poll(102.1, waiting=True) is True and not g.pending  # sent when the window ended
    assert g.asked == 2


def test_a_request_for_nobody_in_particular_counts_as_the_same_viewer_asking_again():
    g = gate()
    g.request(100.0, 1)
    g.key_came()
    assert g.request(100.5) is False and g.request(100.6, 0) is False
    assert g.coalesced == 2 and g.forwarded == 0


def test_a_held_request_is_dropped_when_a_key_picture_came_and_nobody_waits_any_more():
    g = gate()
    g.request(100.0, 1)
    g.key_came()
    g.request(100.5, 1)  # held
    g.key_came()  # a key picture comes meanwhile, whoever asked for it
    assert g.poll(102.5, waiting=False) is False
    assert g.cancelled == 1 and not g.pending and g.asked == 1


def test_nothing_is_retried_for_a_key_picture_nobody_waits_for():
    g = gate()
    g.request(100.0, 1)
    assert g.poll(110.0, waiting=False) is False and g.retries == 0
    g.key_came()
    assert g.poll(120.0, waiting=True) is False  # answered: nothing to ask again


def test_a_send_is_ungated_and_counts_as_a_request_on_its_way():
    g = gate()
    g.send(100.0, 4)
    g.send(100.1, 4)  # a source that must be asked whatever the window
    assert g.asked == 2 and g.unanswered
    assert g.request(100.2, 5) is False and g.shared == 1


def test_a_reset_forgets_what_was_asked_of_the_old_source():
    g = gate()
    g.request(100.0, 1)
    g.request(100.2, 1)
    g.reset()
    assert not g.pending and not g.unanswered
    assert g.request(100.3, 2) is True  # not held by the old window


def test_the_counters_say_what_was_decided():
    g = gate()
    g.request(100.0, 1)
    g.request(100.1, 2)
    g.key_came()
    g.request(100.2, 1)
    assert g.stats() == {"asked": 1, "coalesced": 1, "cancelled": 0, "shared": 1, "forwarded": 0, "retries": 0}
