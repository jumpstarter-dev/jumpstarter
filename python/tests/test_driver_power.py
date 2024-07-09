from jumpstarter.drivers.power import PowerReading, MockPower


def test_mock_power():
    p = MockPower(labels={"jumpstarter.dev/name": "mock"})
    assert p.call("on", [])
    assert p.call("off", [])
    assert next(p.streaming_call("read", [])) == PowerReading(5.0, 2.0)
