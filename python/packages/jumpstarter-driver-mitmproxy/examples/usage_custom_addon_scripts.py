proxy.set_mock_addon(
    "GET", "/streaming/audio/channel/*",
    "hls_audio_stream",
    addon_config={"segment_duration_s": 6},
)
