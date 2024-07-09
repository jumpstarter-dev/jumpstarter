from jumpstarter.drivers.storage import MockStorageMux


def test_mock_power():
    p = MockStorageMux(labels={"jumpstarter.dev/name": "mock"})

    p.call("dut", [])
    p.call("off", [])

    assert p.call("host", []) == "/dev/null"
