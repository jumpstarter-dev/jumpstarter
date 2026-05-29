from hypothesis import given
from hypothesis import strategies as st

from .serde import decode_value, encode_value

json_primitives = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(2**31), max_value=2**31),
    st.floats(allow_nan=False, allow_infinity=False, min_value=-1e10, max_value=1e10),
    st.text(
        alphabet=st.characters(categories=("L", "N", "P", "S", "Z"), max_codepoint=0xFFFF),
        min_size=0,
        max_size=50,
    ),
)


json_values = st.recursive(
    json_primitives,
    lambda children: st.one_of(
        st.lists(children, max_size=5),
        st.dictionaries(
            st.text(
                alphabet=st.characters(categories=("L", "N"), max_codepoint=0x7E),
                min_size=1,
                max_size=10,
            ),
            children,
            max_size=5,
        ),
    ),
    max_leaves=20,
)


class TestEncodeDecodeRoundtrip:
    @given(value=st.text(min_size=0, max_size=100))
    def test_string_roundtrip(self, value: str) -> None:
        encoded = encode_value(value)
        decoded = decode_value(encoded)
        assert decoded == value

    @given(value=st.integers(min_value=-(2**31), max_value=2**31))
    def test_integer_roundtrip(self, value: int) -> None:
        encoded = encode_value(value)
        decoded = decode_value(encoded)
        assert decoded == value or decoded == float(value)

    @given(value=st.booleans())
    def test_boolean_roundtrip(self, value: bool) -> None:
        encoded = encode_value(value)
        decoded = decode_value(encoded)
        assert decoded == value

    def test_none_roundtrip(self) -> None:
        encoded = encode_value(None)
        decoded = decode_value(encoded)
        assert decoded is None

    @given(values=st.lists(st.text(min_size=0, max_size=20), max_size=10))
    def test_string_list_roundtrip(self, values: list[str]) -> None:
        encoded = encode_value(values)
        decoded = decode_value(encoded)
        assert decoded == values

    @given(
        data=st.dictionaries(
            st.from_regex(r"[a-zA-Z][a-zA-Z0-9]{0,10}", fullmatch=True),
            st.text(min_size=0, max_size=20),
            max_size=5,
        )
    )
    def test_string_dict_roundtrip(self, data: dict[str, str]) -> None:
        encoded = encode_value(data)
        decoded = decode_value(encoded)
        assert decoded == data

    @given(values=st.lists(st.integers(min_value=-1000, max_value=1000), max_size=10))
    def test_integer_list_roundtrip(self, values: list[int]) -> None:
        encoded = encode_value(values)
        decoded = decode_value(encoded)
        for original, restored in zip(values, decoded, strict=True):
            assert restored == original or restored == float(original)


class TestEncodeProducesProtobufValue:
    @given(value=st.text(min_size=0, max_size=50))
    def test_encode_returns_value_object(self, value: str) -> None:
        from google.protobuf import struct_pb2

        encoded = encode_value(value)
        assert isinstance(encoded, struct_pb2.Value)

    @given(value=st.booleans())
    def test_encode_boolean_returns_value(self, value: bool) -> None:
        from google.protobuf import struct_pb2

        encoded = encode_value(value)
        assert isinstance(encoded, struct_pb2.Value)
