from uuid import UUID

from hypothesis import given
from hypothesis import strategies as st

from .metadata import Metadata
from jumpstarter.testing_strategies import label_key, label_value


class TestMetadataConstruction:
    @given(
        labels=st.dictionaries(keys=label_key, values=label_value, max_size=10),
    )
    def test_labels_preserved(self, labels: dict[str, str]) -> None:
        m = Metadata(labels=labels)
        assert m.labels == labels

    @given(
        labels=st.dictionaries(keys=label_key, values=label_value, max_size=10),
    )
    def test_uuid_is_valid(self, labels: dict[str, str]) -> None:
        m = Metadata(labels=labels)
        assert isinstance(m.uuid, UUID)

    def test_two_instances_have_different_uuids(self) -> None:
        m1 = Metadata()
        m2 = Metadata()
        assert m1.uuid != m2.uuid


class TestMetadataNameProperty:
    @given(name=label_value.filter(lambda s: len(s) > 0))
    def test_name_returns_label_value(self, name: str) -> None:
        m = Metadata(labels={"jumpstarter.dev/name": name})
        assert m.name == name

    @given(
        labels=st.dictionaries(
            keys=label_key.filter(lambda k: k != "jumpstarter.dev/name"),
            values=label_value,
            max_size=10,
        ),
    )
    def test_name_returns_unknown_when_missing(self, labels: dict[str, str]) -> None:
        m = Metadata(labels=labels)
        assert m.name == "unknown"
