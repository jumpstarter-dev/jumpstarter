from hypothesis import given, settings
from hypothesis import strategies as st

from .exporter import HookInstanceConfigV1Alpha1
from .tls import TLSConfigV1Alpha1


class TestTLSConfigRoundtrip:
    @given(
        ca=st.text(min_size=0, max_size=200),
        insecure=st.booleans(),
    )
    @settings(max_examples=50)
    def test_roundtrip_through_dict(self, ca: str, insecure: bool) -> None:
        original = TLSConfigV1Alpha1(ca=ca, insecure=insecure)
        dumped = original.model_dump()
        restored = TLSConfigV1Alpha1.model_validate(dumped)
        assert restored == original

    @given(
        ca=st.text(min_size=0, max_size=200),
        insecure=st.booleans(),
    )
    @settings(max_examples=50)
    def test_roundtrip_through_json(self, ca: str, insecure: bool) -> None:
        original = TLSConfigV1Alpha1(ca=ca, insecure=insecure)
        json_str = original.model_dump_json()
        restored = TLSConfigV1Alpha1.model_validate_json(json_str)
        assert restored == original

    @given(insecure=st.booleans())
    @settings(max_examples=20)
    def test_default_ca_is_empty_string(self, insecure: bool) -> None:
        config = TLSConfigV1Alpha1(insecure=insecure)
        assert config.ca == ""


class TestHookInstanceConfigConstruction:
    @given(
        script=st.text(min_size=1, max_size=500),
        timeout=st.integers(min_value=1, max_value=86400),
        on_failure=st.sampled_from(["warn", "endLease", "exit"]),
    )
    @settings(max_examples=50)
    def test_valid_construction(self, script: str, timeout: int, on_failure: str) -> None:
        config = HookInstanceConfigV1Alpha1(script=script, timeout=timeout, onFailure=on_failure)
        assert config.script == script
        assert config.timeout == timeout
        assert config.on_failure == on_failure

    @given(
        script=st.text(min_size=1, max_size=500),
        timeout=st.integers(min_value=1, max_value=86400),
        on_failure=st.sampled_from(["warn", "endLease", "exit"]),
    )
    @settings(max_examples=50)
    def test_roundtrip_through_dict(self, script: str, timeout: int, on_failure: str) -> None:
        original = HookInstanceConfigV1Alpha1(script=script, timeout=timeout, onFailure=on_failure)
        dumped = original.model_dump(by_alias=True)
        restored = HookInstanceConfigV1Alpha1.model_validate(dumped)
        assert restored.script == original.script
        assert restored.timeout == original.timeout
        assert restored.on_failure == original.on_failure

    def test_default_timeout_is_120(self) -> None:
        config = HookInstanceConfigV1Alpha1(script="echo test")
        assert config.timeout == 120

    def test_default_on_failure_is_warn(self) -> None:
        config = HookInstanceConfigV1Alpha1(script="echo test")
        assert config.on_failure == "warn"
