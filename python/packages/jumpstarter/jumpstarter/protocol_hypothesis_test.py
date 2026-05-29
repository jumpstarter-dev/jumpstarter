from hypothesis import given
from hypothesis import strategies as st
from jumpstarter_protocol.jumpstarter.client.v1 import client_pb2
from jumpstarter_protocol.jumpstarter.v1 import (
    common_pb2,
    jumpstarter_pb2,
    kubernetes_pb2,
    router_pb2,
)

safe_text = st.text(min_size=0, max_size=50)

safe_key = st.from_regex(r"[a-zA-Z][a-zA-Z0-9._-]{0,20}", fullmatch=True)
safe_value = st.from_regex(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,20}", fullmatch=True)
label_maps = st.dictionaries(safe_key, safe_value, max_size=5)


class TestKubernetesMessages:
    @given(
        key=safe_text,
        operator=st.sampled_from(["In", "NotIn", "Exists", "DoesNotExist"]),
        values=st.lists(safe_text, max_size=5),
    )
    def test_label_selector_requirement_roundtrip(self, key: str, operator: str, values: list[str]) -> None:
        msg = kubernetes_pb2.LabelSelectorRequirement(key=key, operator=operator, values=values)
        serialized = msg.SerializeToString()
        restored = kubernetes_pb2.LabelSelectorRequirement()
        restored.ParseFromString(serialized)
        assert restored.key == key
        assert restored.operator == operator
        assert list(restored.values) == values

    @given(labels=label_maps)
    def test_label_selector_match_labels_roundtrip(self, labels: dict[str, str]) -> None:
        msg = kubernetes_pb2.LabelSelector(match_labels=labels)
        serialized = msg.SerializeToString()
        restored = kubernetes_pb2.LabelSelector()
        restored.ParseFromString(serialized)
        assert dict(restored.match_labels) == labels

    @given(
        seconds=st.one_of(st.none(), st.integers(min_value=0, max_value=2**40)),
        nanos=st.one_of(st.none(), st.integers(min_value=0, max_value=999_999_999)),
    )
    def test_time_roundtrip(self, seconds: int | None, nanos: int | None) -> None:
        kwargs = {}
        if seconds is not None:
            kwargs["seconds"] = seconds
        if nanos is not None:
            kwargs["nanos"] = nanos
        msg = kubernetes_pb2.Time(**kwargs)
        serialized = msg.SerializeToString()
        restored = kubernetes_pb2.Time()
        restored.ParseFromString(serialized)
        assert msg == restored

    @given(
        type_val=st.one_of(st.none(), safe_text),
        status_val=st.one_of(st.none(), st.sampled_from(["True", "False", "Unknown"])),
        reason=st.one_of(st.none(), safe_text),
        message=st.one_of(st.none(), safe_text),
    )
    def test_condition_roundtrip(
        self,
        type_val: str | None,
        status_val: str | None,
        reason: str | None,
        message: str | None,
    ) -> None:
        kwargs = {}
        if type_val is not None:
            kwargs["type"] = type_val
        if status_val is not None:
            kwargs["status"] = status_val
        if reason is not None:
            kwargs["reason"] = reason
        if message is not None:
            kwargs["message"] = message
        msg = kubernetes_pb2.Condition(**kwargs)
        serialized = msg.SerializeToString()
        restored = kubernetes_pb2.Condition()
        restored.ParseFromString(serialized)
        assert msg == restored


class TestCommonEnums:
    @given(
        status=st.sampled_from(
            [
                common_pb2.EXPORTER_STATUS_UNSPECIFIED,
                common_pb2.EXPORTER_STATUS_OFFLINE,
                common_pb2.EXPORTER_STATUS_AVAILABLE,
                common_pb2.EXPORTER_STATUS_BEFORE_LEASE_HOOK,
                common_pb2.EXPORTER_STATUS_LEASE_READY,
                common_pb2.EXPORTER_STATUS_AFTER_LEASE_HOOK,
                common_pb2.EXPORTER_STATUS_BEFORE_LEASE_HOOK_FAILED,
                common_pb2.EXPORTER_STATUS_AFTER_LEASE_HOOK_FAILED,
            ]
        )
    )
    def test_exporter_status_is_valid_enum_value(self, status: int) -> None:
        descriptor = common_pb2.DESCRIPTOR.enum_types_by_name["ExporterStatus"]
        assert descriptor.values_by_number[status].number == status

    @given(
        source=st.sampled_from(
            [
                common_pb2.LOG_SOURCE_UNSPECIFIED,
                common_pb2.LOG_SOURCE_DRIVER,
                common_pb2.LOG_SOURCE_BEFORE_LEASE_HOOK,
                common_pb2.LOG_SOURCE_AFTER_LEASE_HOOK,
                common_pb2.LOG_SOURCE_SYSTEM,
            ]
        )
    )
    def test_log_source_is_valid_enum_value(self, source: int) -> None:
        descriptor = common_pb2.DESCRIPTOR.enum_types_by_name["LogSource"]
        assert descriptor.values_by_number[source].number == source


class TestRouterMessages:
    @given(
        payload=st.binary(min_size=0, max_size=200),
        frame_type=st.sampled_from(
            [
                router_pb2.FRAME_TYPE_DATA,
                router_pb2.FRAME_TYPE_RST_STREAM,
                router_pb2.FRAME_TYPE_PING,
                router_pb2.FRAME_TYPE_GOAWAY,
            ]
        ),
    )
    def test_stream_request_roundtrip(self, payload: bytes, frame_type: int) -> None:
        msg = router_pb2.StreamRequest(payload=payload, frame_type=frame_type)
        serialized = msg.SerializeToString()
        restored = router_pb2.StreamRequest()
        restored.ParseFromString(serialized)
        assert restored.payload == payload
        assert restored.frame_type == frame_type

    @given(
        payload=st.binary(min_size=0, max_size=200),
        frame_type=st.sampled_from(
            [
                router_pb2.FRAME_TYPE_DATA,
                router_pb2.FRAME_TYPE_RST_STREAM,
                router_pb2.FRAME_TYPE_PING,
                router_pb2.FRAME_TYPE_GOAWAY,
            ]
        ),
    )
    def test_stream_response_roundtrip(self, payload: bytes, frame_type: int) -> None:
        msg = router_pb2.StreamResponse(payload=payload, frame_type=frame_type)
        serialized = msg.SerializeToString()
        restored = router_pb2.StreamResponse()
        restored.ParseFromString(serialized)
        assert restored.payload == payload
        assert restored.frame_type == frame_type


class TestControllerServiceMessages:
    @given(labels=label_maps, uuid=safe_text)
    def test_register_request_roundtrip(self, labels: dict[str, str], uuid: str) -> None:
        report = jumpstarter_pb2.DriverInstanceReport(uuid=uuid, labels=labels)
        msg = jumpstarter_pb2.RegisterRequest(labels=labels, reports=[report])
        serialized = msg.SerializeToString()
        restored = jumpstarter_pb2.RegisterRequest()
        restored.ParseFromString(serialized)
        assert dict(restored.labels) == labels
        assert len(restored.reports) == 1
        assert restored.reports[0].uuid == uuid

    @given(uuid=safe_text)
    def test_register_response_roundtrip(self, uuid: str) -> None:
        msg = jumpstarter_pb2.RegisterResponse(uuid=uuid)
        serialized = msg.SerializeToString()
        restored = jumpstarter_pb2.RegisterResponse()
        restored.ParseFromString(serialized)
        assert restored.uuid == uuid

    @given(reason=safe_text)
    def test_unregister_request_roundtrip(self, reason: str) -> None:
        msg = jumpstarter_pb2.UnregisterRequest(reason=reason)
        serialized = msg.SerializeToString()
        restored = jumpstarter_pb2.UnregisterRequest()
        restored.ParseFromString(serialized)
        assert restored.reason == reason

    @given(lease_name=safe_text)
    def test_listen_request_roundtrip(self, lease_name: str) -> None:
        msg = jumpstarter_pb2.ListenRequest(lease_name=lease_name)
        serialized = msg.SerializeToString()
        restored = jumpstarter_pb2.ListenRequest()
        restored.ParseFromString(serialized)
        assert restored.lease_name == lease_name

    @given(router_endpoint=safe_text, router_token=safe_text)
    def test_listen_response_roundtrip(self, router_endpoint: str, router_token: str) -> None:
        msg = jumpstarter_pb2.ListenResponse(router_endpoint=router_endpoint, router_token=router_token)
        serialized = msg.SerializeToString()
        restored = jumpstarter_pb2.ListenResponse()
        restored.ParseFromString(serialized)
        assert restored.router_endpoint == router_endpoint
        assert restored.router_token == router_token

    @given(
        leased=st.booleans(),
        lease_name=st.one_of(st.none(), safe_text),
        client_name=st.one_of(st.none(), safe_text),
    )
    def test_status_response_roundtrip(
        self,
        leased: bool,
        lease_name: str | None,
        client_name: str | None,
    ) -> None:
        kwargs: dict = {"leased": leased}
        if lease_name is not None:
            kwargs["lease_name"] = lease_name
        if client_name is not None:
            kwargs["client_name"] = client_name
        msg = jumpstarter_pb2.StatusResponse(**kwargs)
        serialized = msg.SerializeToString()
        restored = jumpstarter_pb2.StatusResponse()
        restored.ParseFromString(serialized)
        assert restored.leased == leased

    @given(lease_name=safe_text)
    def test_dial_request_roundtrip(self, lease_name: str) -> None:
        msg = jumpstarter_pb2.DialRequest(lease_name=lease_name)
        serialized = msg.SerializeToString()
        restored = jumpstarter_pb2.DialRequest()
        restored.ParseFromString(serialized)
        assert restored.lease_name == lease_name

    @given(router_endpoint=safe_text, router_token=safe_text)
    def test_dial_response_roundtrip(self, router_endpoint: str, router_token: str) -> None:
        msg = jumpstarter_pb2.DialResponse(router_endpoint=router_endpoint, router_token=router_token)
        serialized = msg.SerializeToString()
        restored = jumpstarter_pb2.DialResponse()
        restored.ParseFromString(serialized)
        assert restored.router_endpoint == router_endpoint
        assert restored.router_token == router_token

    @given(
        exporter_uuid=safe_text,
        driver_instance_uuid=safe_text,
        severity=safe_text,
        message=safe_text,
    )
    def test_audit_stream_request_roundtrip(
        self,
        exporter_uuid: str,
        driver_instance_uuid: str,
        severity: str,
        message: str,
    ) -> None:
        msg = jumpstarter_pb2.AuditStreamRequest(
            exporter_uuid=exporter_uuid,
            driver_instance_uuid=driver_instance_uuid,
            severity=severity,
            message=message,
        )
        serialized = msg.SerializeToString()
        restored = jumpstarter_pb2.AuditStreamRequest()
        restored.ParseFromString(serialized)
        assert restored.exporter_uuid == exporter_uuid
        assert restored.driver_instance_uuid == driver_instance_uuid
        assert restored.severity == severity
        assert restored.message == message

    @given(
        status=st.sampled_from(
            [
                common_pb2.EXPORTER_STATUS_UNSPECIFIED,
                common_pb2.EXPORTER_STATUS_OFFLINE,
                common_pb2.EXPORTER_STATUS_AVAILABLE,
            ]
        ),
        message=st.one_of(st.none(), safe_text),
        release_lease=st.one_of(st.none(), st.booleans()),
    )
    def test_report_status_request_roundtrip(
        self,
        status: int,
        message: str | None,
        release_lease: bool | None,
    ) -> None:
        kwargs: dict = {"status": status}
        if message is not None:
            kwargs["message"] = message
        if release_lease is not None:
            kwargs["release_lease"] = release_lease
        msg = jumpstarter_pb2.ReportStatusRequest(**kwargs)
        serialized = msg.SerializeToString()
        restored = jumpstarter_pb2.ReportStatusRequest()
        restored.ParseFromString(serialized)
        assert restored.status == status


class TestExporterServiceMessages:
    @given(uuid=safe_text, method=safe_text)
    def test_driver_call_request_roundtrip(self, uuid: str, method: str) -> None:
        msg = jumpstarter_pb2.DriverCallRequest(uuid=uuid, method=method)
        serialized = msg.SerializeToString()
        restored = jumpstarter_pb2.DriverCallRequest()
        restored.ParseFromString(serialized)
        assert restored.uuid == uuid
        assert restored.method == method

    @given(uuid=safe_text)
    def test_driver_call_response_roundtrip(self, uuid: str) -> None:
        msg = jumpstarter_pb2.DriverCallResponse(uuid=uuid)
        serialized = msg.SerializeToString()
        restored = jumpstarter_pb2.DriverCallResponse()
        restored.ParseFromString(serialized)
        assert restored.uuid == uuid

    @given(uuid=safe_text, method=safe_text)
    def test_streaming_driver_call_request_roundtrip(self, uuid: str, method: str) -> None:
        msg = jumpstarter_pb2.StreamingDriverCallRequest(uuid=uuid, method=method)
        serialized = msg.SerializeToString()
        restored = jumpstarter_pb2.StreamingDriverCallRequest()
        restored.ParseFromString(serialized)
        assert restored.uuid == uuid
        assert restored.method == method

    @given(
        uuid=safe_text,
        severity=safe_text,
        message=safe_text,
        source=st.one_of(
            st.none(),
            st.sampled_from(
                [
                    common_pb2.LOG_SOURCE_UNSPECIFIED,
                    common_pb2.LOG_SOURCE_DRIVER,
                    common_pb2.LOG_SOURCE_BEFORE_LEASE_HOOK,
                    common_pb2.LOG_SOURCE_AFTER_LEASE_HOOK,
                    common_pb2.LOG_SOURCE_SYSTEM,
                ]
            ),
        ),
    )
    def test_log_stream_response_roundtrip(self, uuid: str, severity: str, message: str, source: int | None) -> None:
        kwargs: dict = {"uuid": uuid, "severity": severity, "message": message}
        if source is not None:
            kwargs["source"] = source
        msg = jumpstarter_pb2.LogStreamResponse(**kwargs)
        serialized = msg.SerializeToString()
        restored = jumpstarter_pb2.LogStreamResponse()
        restored.ParseFromString(serialized)
        assert restored.uuid == uuid
        assert restored.severity == severity
        assert restored.message == message

    @given(
        endpoint=safe_text,
        certificate=safe_text,
        client_certificate=safe_text,
        client_private_key=safe_text,
    )
    def test_endpoint_roundtrip(
        self,
        endpoint: str,
        certificate: str,
        client_certificate: str,
        client_private_key: str,
    ) -> None:
        msg = jumpstarter_pb2.Endpoint(
            endpoint=endpoint,
            certificate=certificate,
            client_certificate=client_certificate,
            client_private_key=client_private_key,
        )
        serialized = msg.SerializeToString()
        restored = jumpstarter_pb2.Endpoint()
        restored.ParseFromString(serialized)
        assert restored.endpoint == endpoint
        assert restored.certificate == certificate
        assert restored.client_certificate == client_certificate
        assert restored.client_private_key == client_private_key

    @given(
        uuid=safe_text,
        labels=label_maps,
    )
    def test_get_report_response_roundtrip(self, uuid: str, labels: dict[str, str]) -> None:
        msg = jumpstarter_pb2.GetReportResponse(uuid=uuid, labels=labels)
        serialized = msg.SerializeToString()
        restored = jumpstarter_pb2.GetReportResponse()
        restored.ParseFromString(serialized)
        assert restored.uuid == uuid
        assert dict(restored.labels) == labels

    @given(name=safe_text)
    def test_get_lease_request_roundtrip(self, name: str) -> None:
        msg = jumpstarter_pb2.GetLeaseRequest(name=name)
        serialized = msg.SerializeToString()
        restored = jumpstarter_pb2.GetLeaseRequest()
        restored.ParseFromString(serialized)
        assert restored.name == name

    @given(name=safe_text)
    def test_request_lease_response_roundtrip(self, name: str) -> None:
        msg = jumpstarter_pb2.RequestLeaseResponse(name=name)
        serialized = msg.SerializeToString()
        restored = jumpstarter_pb2.RequestLeaseResponse()
        restored.ParseFromString(serialized)
        assert restored.name == name

    @given(name=safe_text)
    def test_release_lease_request_roundtrip(self, name: str) -> None:
        msg = jumpstarter_pb2.ReleaseLeaseRequest(name=name)
        serialized = msg.SerializeToString()
        restored = jumpstarter_pb2.ReleaseLeaseRequest()
        restored.ParseFromString(serialized)
        assert restored.name == name

    @given(names=st.lists(safe_text, max_size=10))
    def test_list_leases_response_roundtrip(self, names: list[str]) -> None:
        msg = jumpstarter_pb2.ListLeasesResponse(names=names)
        serialized = msg.SerializeToString()
        restored = jumpstarter_pb2.ListLeasesResponse()
        restored.ParseFromString(serialized)
        assert list(restored.names) == names

    @given(
        status=st.sampled_from(
            [
                common_pb2.EXPORTER_STATUS_UNSPECIFIED,
                common_pb2.EXPORTER_STATUS_OFFLINE,
                common_pb2.EXPORTER_STATUS_AVAILABLE,
                common_pb2.EXPORTER_STATUS_LEASE_READY,
            ]
        ),
        status_version=st.integers(min_value=0, max_value=2**32),
    )
    def test_get_status_response_roundtrip(self, status: int, status_version: int) -> None:
        msg = jumpstarter_pb2.GetStatusResponse(status=status, status_version=status_version)
        serialized = msg.SerializeToString()
        restored = jumpstarter_pb2.GetStatusResponse()
        restored.ParseFromString(serialized)
        assert restored.status == status
        assert restored.status_version == status_version

    @given(success=st.booleans(), message=st.one_of(st.none(), safe_text))
    def test_end_session_response_roundtrip(self, success: bool, message: str | None) -> None:
        kwargs: dict = {"success": success}
        if message is not None:
            kwargs["message"] = message
        msg = jumpstarter_pb2.EndSessionResponse(**kwargs)
        serialized = msg.SerializeToString()
        restored = jumpstarter_pb2.EndSessionResponse()
        restored.ParseFromString(serialized)
        assert restored.success == success


class TestClientServiceMessages:
    @given(
        name=safe_text,
        labels=label_maps,
        status=st.sampled_from(
            [
                common_pb2.EXPORTER_STATUS_UNSPECIFIED,
                common_pb2.EXPORTER_STATUS_AVAILABLE,
                common_pb2.EXPORTER_STATUS_OFFLINE,
            ]
        ),
        status_message=safe_text,
    )
    def test_exporter_roundtrip(
        self,
        name: str,
        labels: dict[str, str],
        status: int,
        status_message: str,
    ) -> None:
        msg = client_pb2.Exporter(
            name=name,
            labels=labels,
            status=status,
            status_message=status_message,
        )
        serialized = msg.SerializeToString()
        restored = client_pb2.Exporter()
        restored.ParseFromString(serialized)
        assert restored.name == name
        assert dict(restored.labels) == labels
        assert restored.status == status
        assert restored.status_message == status_message

    @given(
        name=safe_text,
        selector=safe_text,
        tags=label_maps,
    )
    def test_lease_roundtrip(
        self,
        name: str,
        selector: str,
        tags: dict[str, str],
    ) -> None:
        msg = client_pb2.Lease(name=name, selector=selector, tags=tags)
        serialized = msg.SerializeToString()
        restored = client_pb2.Lease()
        restored.ParseFromString(serialized)
        assert restored.name == name
        assert restored.selector == selector
        assert dict(restored.tags) == tags

    @given(name=safe_text)
    def test_get_exporter_request_roundtrip(self, name: str) -> None:
        msg = client_pb2.GetExporterRequest(name=name)
        serialized = msg.SerializeToString()
        restored = client_pb2.GetExporterRequest()
        restored.ParseFromString(serialized)
        assert restored.name == name

    @given(
        parent=safe_text,
        page_size=st.integers(min_value=0, max_value=1000),
        page_token=safe_text,
        filter_str=safe_text,
    )
    def test_list_exporters_request_roundtrip(
        self,
        parent: str,
        page_size: int,
        page_token: str,
        filter_str: str,
    ) -> None:
        msg = client_pb2.ListExportersRequest(
            parent=parent,
            page_size=page_size,
            page_token=page_token,
            filter=filter_str,
        )
        serialized = msg.SerializeToString()
        restored = client_pb2.ListExportersRequest()
        restored.ParseFromString(serialized)
        assert restored.parent == parent
        assert restored.page_size == page_size
        assert restored.page_token == page_token
        assert restored.filter == filter_str

    @given(name=safe_text)
    def test_get_lease_request_roundtrip(self, name: str) -> None:
        msg = client_pb2.GetLeaseRequest(name=name)
        serialized = msg.SerializeToString()
        restored = client_pb2.GetLeaseRequest()
        restored.ParseFromString(serialized)
        assert restored.name == name

    @given(
        parent=safe_text,
        page_size=st.integers(min_value=0, max_value=1000),
        only_active=st.one_of(st.none(), st.booleans()),
        tag_filter=safe_text,
    )
    def test_list_leases_request_roundtrip(
        self,
        parent: str,
        page_size: int,
        only_active: bool | None,
        tag_filter: str,
    ) -> None:
        kwargs: dict = {"parent": parent, "page_size": page_size, "tag_filter": tag_filter}
        if only_active is not None:
            kwargs["only_active"] = only_active
        msg = client_pb2.ListLeasesRequest(**kwargs)
        serialized = msg.SerializeToString()
        restored = client_pb2.ListLeasesRequest()
        restored.ParseFromString(serialized)
        assert restored.parent == parent
        assert restored.page_size == page_size
        assert restored.tag_filter == tag_filter

    @given(name=safe_text)
    def test_delete_lease_request_roundtrip(self, name: str) -> None:
        msg = client_pb2.DeleteLeaseRequest(name=name)
        serialized = msg.SerializeToString()
        restored = client_pb2.DeleteLeaseRequest()
        restored.ParseFromString(serialized)
        assert restored.name == name

    @given(parent=safe_text)
    def test_rotate_token_request_roundtrip(self, parent: str) -> None:
        msg = client_pb2.RotateTokenRequest(parent=parent)
        serialized = msg.SerializeToString()
        restored = client_pb2.RotateTokenRequest()
        restored.ParseFromString(serialized)
        assert restored.parent == parent

    @given(token=safe_text)
    def test_rotate_token_response_roundtrip(self, token: str) -> None:
        msg = client_pb2.RotateTokenResponse(token=token)
        serialized = msg.SerializeToString()
        restored = client_pb2.RotateTokenResponse()
        restored.ParseFromString(serialized)
        assert restored.token == token


class TestDriverInstanceReport:
    @given(
        uuid=safe_text,
        parent_uuid=st.one_of(st.none(), safe_text),
        labels=label_maps,
        description=st.one_of(st.none(), safe_text),
        methods_description=label_maps,
    )
    def test_roundtrip(
        self,
        uuid: str,
        parent_uuid: str | None,
        labels: dict[str, str],
        description: str | None,
        methods_description: dict[str, str],
    ) -> None:
        kwargs: dict = {
            "uuid": uuid,
            "labels": labels,
            "methods_description": methods_description,
        }
        if parent_uuid is not None:
            kwargs["parent_uuid"] = parent_uuid
        if description is not None:
            kwargs["description"] = description
        msg = jumpstarter_pb2.DriverInstanceReport(**kwargs)
        serialized = msg.SerializeToString()
        restored = jumpstarter_pb2.DriverInstanceReport()
        restored.ParseFromString(serialized)
        assert restored.uuid == uuid
        assert dict(restored.labels) == labels
        assert dict(restored.methods_description) == methods_description


class TestEmptyMessagesRoundtrip:
    def test_unregister_response_roundtrip(self) -> None:
        msg = jumpstarter_pb2.UnregisterResponse()
        serialized = msg.SerializeToString()
        restored = jumpstarter_pb2.UnregisterResponse()
        restored.ParseFromString(serialized)
        assert msg == restored

    def test_status_request_roundtrip(self) -> None:
        msg = jumpstarter_pb2.StatusRequest()
        serialized = msg.SerializeToString()
        restored = jumpstarter_pb2.StatusRequest()
        restored.ParseFromString(serialized)
        assert msg == restored

    def test_report_status_response_roundtrip(self) -> None:
        msg = jumpstarter_pb2.ReportStatusResponse()
        serialized = msg.SerializeToString()
        restored = jumpstarter_pb2.ReportStatusResponse()
        restored.ParseFromString(serialized)
        assert msg == restored

    def test_reset_request_roundtrip(self) -> None:
        msg = jumpstarter_pb2.ResetRequest()
        serialized = msg.SerializeToString()
        restored = jumpstarter_pb2.ResetRequest()
        restored.ParseFromString(serialized)
        assert msg == restored

    def test_reset_response_roundtrip(self) -> None:
        msg = jumpstarter_pb2.ResetResponse()
        serialized = msg.SerializeToString()
        restored = jumpstarter_pb2.ResetResponse()
        restored.ParseFromString(serialized)
        assert msg == restored

    def test_list_leases_request_roundtrip(self) -> None:
        msg = jumpstarter_pb2.ListLeasesRequest()
        serialized = msg.SerializeToString()
        restored = jumpstarter_pb2.ListLeasesRequest()
        restored.ParseFromString(serialized)
        assert msg == restored

    def test_get_status_request_roundtrip(self) -> None:
        msg = jumpstarter_pb2.GetStatusRequest()
        serialized = msg.SerializeToString()
        restored = jumpstarter_pb2.GetStatusRequest()
        restored.ParseFromString(serialized)
        assert msg == restored

    def test_end_session_request_roundtrip(self) -> None:
        msg = jumpstarter_pb2.EndSessionRequest()
        serialized = msg.SerializeToString()
        restored = jumpstarter_pb2.EndSessionRequest()
        restored.ParseFromString(serialized)
        assert msg == restored

    def test_release_lease_response_roundtrip(self) -> None:
        msg = jumpstarter_pb2.ReleaseLeaseResponse()
        serialized = msg.SerializeToString()
        restored = jumpstarter_pb2.ReleaseLeaseResponse()
        restored.ParseFromString(serialized)
        assert msg == restored


class TestPaginatedResponseMessages:
    @given(
        exporter_count=st.integers(min_value=0, max_value=10),
        next_page_token=safe_text,
    )
    def test_list_exporters_response_roundtrip(self, exporter_count: int, next_page_token: str) -> None:
        exporters = [
            client_pb2.Exporter(name=f"exp-{i}", labels={}, status=common_pb2.EXPORTER_STATUS_AVAILABLE)
            for i in range(exporter_count)
        ]
        msg = client_pb2.ListExportersResponse(exporters=exporters, next_page_token=next_page_token)
        serialized = msg.SerializeToString()
        restored = client_pb2.ListExportersResponse()
        restored.ParseFromString(serialized)
        assert len(restored.exporters) == exporter_count
        assert restored.next_page_token == next_page_token
        for i, exp in enumerate(restored.exporters):
            assert exp.name == f"exp-{i}"

    @given(
        names=st.lists(safe_text, max_size=10),
        labels=label_maps,
        status=st.sampled_from(
            [
                common_pb2.EXPORTER_STATUS_UNSPECIFIED,
                common_pb2.EXPORTER_STATUS_AVAILABLE,
                common_pb2.EXPORTER_STATUS_OFFLINE,
            ]
        ),
        next_page_token=safe_text,
    )
    def test_list_exporters_response_with_fuzzed_exporters(
        self,
        names: list[str],
        labels: dict[str, str],
        status: int,
        next_page_token: str,
    ) -> None:
        exporters = [client_pb2.Exporter(name=n, labels=labels, status=status) for n in names]
        msg = client_pb2.ListExportersResponse(exporters=exporters, next_page_token=next_page_token)
        serialized = msg.SerializeToString()
        restored = client_pb2.ListExportersResponse()
        restored.ParseFromString(serialized)
        assert len(restored.exporters) == len(names)
        for i, exp in enumerate(restored.exporters):
            assert exp.name == names[i]
            assert exp.status == status

    @given(
        lease_count=st.integers(min_value=0, max_value=10),
        next_page_token=safe_text,
    )
    def test_list_leases_response_roundtrip(self, lease_count: int, next_page_token: str) -> None:
        leases = [client_pb2.Lease(name=f"lease-{i}", selector="board=rpi4") for i in range(lease_count)]
        msg = client_pb2.ListLeasesResponse(leases=leases, next_page_token=next_page_token)
        serialized = msg.SerializeToString()
        restored = client_pb2.ListLeasesResponse()
        restored.ParseFromString(serialized)
        assert len(restored.leases) == lease_count
        assert restored.next_page_token == next_page_token
        for i, lease in enumerate(restored.leases):
            assert lease.name == f"lease-{i}"

    @given(
        names=st.lists(safe_text, max_size=10),
        selectors=st.lists(safe_text, max_size=10),
        tags=label_maps,
        next_page_token=safe_text,
    )
    def test_list_leases_response_with_fuzzed_leases(
        self,
        names: list[str],
        selectors: list[str],
        tags: dict[str, str],
        next_page_token: str,
    ) -> None:
        count = min(len(names), len(selectors))
        leases = [client_pb2.Lease(name=names[i], selector=selectors[i], tags=tags) for i in range(count)]
        msg = client_pb2.ListLeasesResponse(leases=leases, next_page_token=next_page_token)
        serialized = msg.SerializeToString()
        restored = client_pb2.ListLeasesResponse()
        restored.ParseFromString(serialized)
        assert len(restored.leases) == count
        assert restored.next_page_token == next_page_token

    def test_empty_list_exporters_response_roundtrip(self) -> None:
        msg = client_pb2.ListExportersResponse()
        serialized = msg.SerializeToString()
        restored = client_pb2.ListExportersResponse()
        restored.ParseFromString(serialized)
        assert len(restored.exporters) == 0
        assert restored.next_page_token == ""

    def test_empty_list_leases_response_roundtrip(self) -> None:
        msg = client_pb2.ListLeasesResponse()
        serialized = msg.SerializeToString()
        restored = client_pb2.ListLeasesResponse()
        restored.ParseFromString(serialized)
        assert len(restored.leases) == 0
        assert restored.next_page_token == ""
