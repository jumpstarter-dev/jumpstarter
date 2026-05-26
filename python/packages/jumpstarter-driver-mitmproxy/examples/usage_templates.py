proxy.set_mock_template("GET", "/api/v1/weather", {
    "temp_f": "{{random_int(60, 95)}}",
    "condition": "{{random_choice('sunny', 'rain')}}",
    "timestamp": "{{now_iso}}",
    "request_id": "{{uuid}}",
})
