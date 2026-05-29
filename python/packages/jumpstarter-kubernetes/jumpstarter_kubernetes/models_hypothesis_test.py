from hypothesis import given
from hypothesis import strategies as st

from .clusters import V1Alpha1ClusterInfo, V1Alpha1ClusterList, V1Alpha1JumpstarterInstance
from .exporters import V1Alpha1ExporterDevice, V1Alpha1ExporterList
from .json import JsonBaseModel
from .list import V1Alpha1List

safe_text = st.text(
    alphabet=st.characters(categories=("L", "N"), max_codepoint=0x7E),
    min_size=0,
    max_size=30,
)

non_empty_text = st.text(
    alphabet=st.characters(categories=("L", "N"), max_codepoint=0x7E),
    min_size=1,
    max_size=30,
)

safe_key = st.from_regex(r"[a-zA-Z][a-zA-Z0-9._-]{0,20}", fullmatch=True)
safe_value = st.from_regex(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,20}", fullmatch=True)
label_maps = st.dictionaries(safe_key, safe_value, max_size=3)


class TestExporterDeviceModel:
    @given(labels=label_maps, uuid=non_empty_text)
    def test_construction_roundtrip(self, labels: dict[str, str], uuid: str) -> None:
        device = V1Alpha1ExporterDevice(labels=labels, uuid=uuid)
        assert device.labels == labels
        assert device.uuid == uuid

    @given(labels=label_maps, uuid=non_empty_text)
    def test_json_roundtrip(self, labels: dict[str, str], uuid: str) -> None:
        device = V1Alpha1ExporterDevice(labels=labels, uuid=uuid)
        json_str = device.dump_json()
        restored = V1Alpha1ExporterDevice.model_validate_json(json_str)
        assert restored.labels == device.labels
        assert restored.uuid == device.uuid

    @given(labels=label_maps, uuid=non_empty_text)
    def test_yaml_roundtrip_produces_string(self, labels: dict[str, str], uuid: str) -> None:
        device = V1Alpha1ExporterDevice(labels=labels, uuid=uuid)
        yaml_str = device.dump_yaml()
        assert isinstance(yaml_str, str)
        assert len(yaml_str) > 0


class TestJumpstarterInstanceModel:
    @given(
        installed=st.booleans(),
        version=st.one_of(st.none(), non_empty_text),
        namespace=st.one_of(st.none(), non_empty_text),
        status=st.one_of(st.none(), non_empty_text),
        has_crds=st.booleans(),
        error=st.one_of(st.none(), non_empty_text),
        basedomain=st.one_of(st.none(), non_empty_text),
        controller_endpoint=st.one_of(st.none(), non_empty_text),
        router_endpoint=st.one_of(st.none(), non_empty_text),
    )
    def test_construction_roundtrip(
        self,
        installed: bool,
        version: str | None,
        namespace: str | None,
        status: str | None,
        has_crds: bool,
        error: str | None,
        basedomain: str | None,
        controller_endpoint: str | None,
        router_endpoint: str | None,
    ) -> None:
        instance = V1Alpha1JumpstarterInstance(
            installed=installed,
            version=version,
            namespace=namespace,
            status=status,
            hasCrds=has_crds,
            error=error,
            basedomain=basedomain,
            controllerEndpoint=controller_endpoint,
            routerEndpoint=router_endpoint,
        )
        assert instance.installed == installed
        assert instance.version == version
        assert instance.namespace == namespace
        assert instance.has_crds == has_crds
        assert instance.error == error

    @given(installed=st.booleans(), has_crds=st.booleans())
    def test_json_roundtrip(self, installed: bool, has_crds: bool) -> None:
        instance = V1Alpha1JumpstarterInstance(installed=installed, hasCrds=has_crds)
        json_str = instance.dump_json()
        restored = V1Alpha1JumpstarterInstance.model_validate_json(json_str)
        assert restored.installed == instance.installed
        assert restored.has_crds == instance.has_crds

    @given(installed=st.booleans())
    def test_api_version_and_kind_are_fixed(self, installed: bool) -> None:
        instance = V1Alpha1JumpstarterInstance(installed=installed)
        assert instance.api_version == "jumpstarter.dev/v1alpha1"
        assert instance.kind == "JumpstarterInstance"


class TestClusterInfoModel:
    @given(
        name=non_empty_text,
        cluster=non_empty_text,
        server=non_empty_text,
        user=non_empty_text,
        namespace=non_empty_text,
        is_current=st.booleans(),
        cluster_type=st.sampled_from(["kind", "minikube", "remote"]),
        accessible=st.booleans(),
        version=st.one_of(st.none(), non_empty_text),
        error=st.one_of(st.none(), non_empty_text),
        ji_installed=st.booleans(),
    )
    def test_construction_roundtrip(
        self,
        name: str,
        cluster: str,
        server: str,
        user: str,
        namespace: str,
        is_current: bool,
        cluster_type: str,
        accessible: bool,
        version: str | None,
        error: str | None,
        ji_installed: bool,
    ) -> None:
        ji = V1Alpha1JumpstarterInstance(installed=ji_installed)
        info = V1Alpha1ClusterInfo(
            name=name,
            cluster=cluster,
            server=server,
            user=user,
            namespace=namespace,
            isCurrent=is_current,
            type=cluster_type,
            accessible=accessible,
            version=version,
            jumpstarter=ji,
            error=error,
        )
        assert info.name == name
        assert info.cluster == cluster
        assert info.server == server
        assert info.is_current == is_current
        assert info.type == cluster_type
        assert info.accessible == accessible
        assert info.jumpstarter.installed == ji_installed

    @given(
        name=non_empty_text,
        cluster_type=st.sampled_from(["kind", "minikube", "remote"]),
        accessible=st.booleans(),
        is_current=st.booleans(),
    )
    def test_json_roundtrip(
        self,
        name: str,
        cluster_type: str,
        accessible: bool,
        is_current: bool,
    ) -> None:
        ji = V1Alpha1JumpstarterInstance(installed=False)
        info = V1Alpha1ClusterInfo(
            name=name,
            cluster="c",
            server="s",
            user="u",
            namespace="ns",
            isCurrent=is_current,
            type=cluster_type,
            accessible=accessible,
            jumpstarter=ji,
        )
        json_str = info.dump_json()
        restored = V1Alpha1ClusterInfo.model_validate_json(json_str)
        assert restored.name == info.name
        assert restored.type == info.type
        assert restored.accessible == info.accessible
        assert restored.is_current == info.is_current

    @given(
        name=non_empty_text,
        cluster_type=st.sampled_from(["kind", "minikube", "remote"]),
    )
    def test_api_version_and_kind_are_fixed(self, name: str, cluster_type: str) -> None:
        ji = V1Alpha1JumpstarterInstance(installed=False)
        info = V1Alpha1ClusterInfo(
            name=name,
            cluster="c",
            server="s",
            user="u",
            namespace="ns",
            isCurrent=True,
            type=cluster_type,
            accessible=True,
            jumpstarter=ji,
        )
        assert info.api_version == "jumpstarter.dev/v1alpha1"
        assert info.kind == "ClusterInfo"


class TestClusterListModel:
    @given(
        count=st.integers(min_value=0, max_value=5),
        cluster_type=st.sampled_from(["kind", "minikube", "remote"]),
    )
    def test_list_construction(self, count: int, cluster_type: str) -> None:
        ji = V1Alpha1JumpstarterInstance(installed=False)
        items = [
            V1Alpha1ClusterInfo(
                name=f"cluster-{i}",
                cluster="c",
                server="s",
                user="u",
                namespace="ns",
                isCurrent=(i == 0),
                type=cluster_type,
                accessible=True,
                jumpstarter=ji,
            )
            for i in range(count)
        ]
        cluster_list = V1Alpha1ClusterList(items=items)
        assert len(cluster_list.items) == count
        assert cluster_list.kind == "ClusterList"


class TestExporterListModel:
    def test_empty_list_construction(self) -> None:
        exporter_list = V1Alpha1ExporterList(items=[])
        assert len(exporter_list.items) == 0
        assert exporter_list.kind == "ExporterList"


class TestV1Alpha1ListModel:
    @given(items=st.lists(non_empty_text, max_size=10))
    def test_generic_list_holds_items(self, items: list[str]) -> None:
        v1_list = V1Alpha1List[str](items=items)
        assert v1_list.items == items
        assert v1_list.api_version == "jumpstarter.dev/v1alpha1"
        assert v1_list.kind == "List"


class TestJsonBaseModel:
    def test_dump_json_returns_string(self) -> None:
        class Sample(JsonBaseModel):
            value: str

        s = Sample(value="test")
        result = s.dump_json()
        assert isinstance(result, str)
        assert "test" in result

    def test_dump_yaml_returns_string(self) -> None:
        class Sample(JsonBaseModel):
            value: str

        s = Sample(value="test")
        result = s.dump_yaml()
        assert isinstance(result, str)
        assert "test" in result
