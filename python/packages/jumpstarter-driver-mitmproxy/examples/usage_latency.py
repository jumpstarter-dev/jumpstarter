proxy.set_mock_with_latency(
    "GET", "/api/v1/status",
    body={"status": "online"},
    latency_ms=3000,
)
