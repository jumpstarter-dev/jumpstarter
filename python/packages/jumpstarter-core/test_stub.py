"""Smoke test for the native ``jumpstarter_core`` extension. This package is a maturin cdylib with
no other Python tests; the stub gives ``make pkg-test-jumpstarter-core`` a real check that the
extension builds and loads (and carries the FFI symbols the pure-Python packages depend on)."""


def test_jumpstarter_core_loads():
    import jumpstarter_core

    # A few FFI symbols the jumpstarter packages rely on (codec FFI + the foreign-host surface).
    for sym in ("StreamCompressor", "StreamDecompressor", "DriverError", "load_exporter_spec"):
        assert hasattr(jumpstarter_core, sym), f"jumpstarter_core missing {sym}"
