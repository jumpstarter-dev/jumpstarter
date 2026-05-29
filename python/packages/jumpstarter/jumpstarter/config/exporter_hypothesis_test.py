from typing import Literal

from hypothesis import given
from hypothesis import strategies as st

from .exporter import HookInstanceConfigV1Alpha1


class TestHookInstanceConfigConstruction:
    @given(
        script=st.text(min_size=1, max_size=500),
        timeout=st.integers(min_value=1, max_value=86400),
        on_failure=st.sampled_from(["warn", "endLease", "exit"]),
    )
    def test_valid_construction(
        self, script: str, timeout: int, on_failure: Literal["warn", "endLease", "exit"]
    ) -> None:
        config = HookInstanceConfigV1Alpha1(script=script, timeout=timeout, onFailure=on_failure)
        assert config.script == script
        assert config.timeout == timeout
        assert config.on_failure == on_failure

    @given(
        script=st.text(min_size=1, max_size=500),
        timeout=st.integers(min_value=1, max_value=86400),
        on_failure=st.sampled_from(["warn", "endLease", "exit"]),
    )
    def test_roundtrip_through_dict(
        self, script: str, timeout: int, on_failure: Literal["warn", "endLease", "exit"]
    ) -> None:
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
