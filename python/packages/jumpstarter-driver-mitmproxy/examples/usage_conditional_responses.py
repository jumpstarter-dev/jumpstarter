proxy.set_mock_conditional("POST", "/api/auth", [
    {
        "match": {"body_json": {"username": "admin", "password": "secret"}},
        "status": 200,
        "body": {"token": "mock-token-001"},
    },
    {"status": 401, "body": {"error": "unauthorized"}},
])
