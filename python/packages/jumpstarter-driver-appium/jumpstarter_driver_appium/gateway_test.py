from .gateway import _HOP_BY_HOP, _end_to_end


def test_headers_named_by_connection_are_hop_by_hop():
    headers = [
        (b"connection", b"keep-alive, X-Trace"),
        (b"x-trace", b"1"),
        (b"keep-alive", b"timeout=5"),
        (b"content-type", b"application/json"),
    ]
    assert _end_to_end(headers, _HOP_BY_HOP) == [(b"content-type", b"application/json")]
