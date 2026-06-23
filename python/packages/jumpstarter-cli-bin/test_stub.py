"""Smoke test for the native ``jmp`` binary. This package is a maturin bin wheel with no other
Python tests; the stub gives ``make pkg-test-jumpstarter-cli-bin`` a real check that installing the
package puts the binary on PATH."""

import shutil


def test_jmp_binary_installed():
    assert shutil.which("jmp") is not None, "jmp binary not on PATH after install"
