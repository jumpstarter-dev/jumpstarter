proxy.load_mock_scenario("happy-path.yaml")

# Or with automatic cleanup:
with proxy.mock_scenario("happy-path.yaml"):
    run_tests()
