import time


def eventually(fn, *, timeout=5, interval=0.05):
    """Poll ``fn`` until it succeeds or *timeout* seconds elapse.

    Similar to Ginkgo's ``Eventually``: calls *fn* repeatedly, catching
    ``AssertionError`` and ``Exception``, sleeping *interval* seconds between
    attempts.  If *fn* does not succeed before *timeout*, the last captured
    exception is re-raised.

    Args:
        fn: A callable (typically a lambda wrapping an assertion).
        timeout: Maximum time in seconds to keep retrying (default 5).
        interval: Time in seconds between attempts (default 0.05).

    Example::

        from jumpstarter_testing import eventually

        mock.write_gatt_char = AsyncMock()
        stream.send(b"hello")
        eventually(mock.write_gatt_char.assert_called)
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            fn()
            return
        except (AssertionError, Exception):
            if time.monotonic() >= deadline:
                raise
            time.sleep(interval)
