proxy.set_mock_file(
    "GET", "/api/v1/downloads/firmware.bin",
    "firmware/test.bin",
    content_type="application/octet-stream",
)
