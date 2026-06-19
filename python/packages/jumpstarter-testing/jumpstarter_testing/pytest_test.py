import pytest


@pytest.mark.skip(
    reason="The gRPC exporter Session (which served a unix socket for JUMPSTARTER_HOST) has "
    "been retired. Re-home this onto the FFI transport-host once the Rust core exposes a "
    "socket-serving surface (jumpstarter-testing FFI migration, Phase B)."
)
def test_env(pytester, monkeypatch):
    """Exercises the JumpstarterTest fixture's JUMPSTARTER_HOST (env) connection path."""
