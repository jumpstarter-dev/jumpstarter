from hypothesis import given
from hypothesis import strategies as st

from .tls import TLSConfigV1Alpha1


class TestTLSConfigRoundtrip:
    @given(
        ca=st.text(min_size=0, max_size=200),
        insecure=st.booleans(),
    )
    def test_roundtrip_through_dict(self, ca: str, insecure: bool) -> None:
        original = TLSConfigV1Alpha1(ca=ca, insecure=insecure)
        dumped = original.model_dump()
        restored = TLSConfigV1Alpha1.model_validate(dumped)
        assert restored == original

    @given(
        ca=st.text(min_size=0, max_size=200),
        insecure=st.booleans(),
    )
    def test_roundtrip_through_json(self, ca: str, insecure: bool) -> None:
        original = TLSConfigV1Alpha1(ca=ca, insecure=insecure)
        json_str = original.model_dump_json()
        restored = TLSConfigV1Alpha1.model_validate_json(json_str)
        assert restored == original

    @given(insecure=st.booleans())
    def test_default_ca_is_empty_string(self, insecure: bool) -> None:
        config = TLSConfigV1Alpha1(insecure=insecure)
        assert config.ca == ""
