proxy.set_mock_sequence("GET", "/api/v1/auth/token", [
    {"status": 200, "body": {"token": "aaa"}, "repeat": 3},
    {"status": 401, "body": {"error": "expired"}, "repeat": 1},
    {"status": 200, "body": {"token": "bbb"}},
])
