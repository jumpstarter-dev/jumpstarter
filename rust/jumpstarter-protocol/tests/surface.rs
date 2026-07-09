//! Surface + wire-contract smoke tests for the generated protocol bindings.
//!
//! These guard the compatibility contract in
//! `specs/rust-core/09-rust-core-requirements.md` §2.1: a proto regeneration that
//! drops a service, renames a wire enum, or changes a numeric tag must fail here
//! rather than silently breaking interop with the Go controller / Python peers.

use jumpstarter_protocol::{client_v1, v1};

/// All four wire services must generate both client and server stubs
/// (spec §2.1: a Rust core must be able to act as either peer).
#[test]
fn all_services_have_client_and_server_stubs() {
    // Referencing the types is the compile-time assertion; the asserts keep the
    // bindings used so the test cannot be optimized away.
    fn assert_exists<T>() {}
    assert_exists::<
        v1::controller_service_client::ControllerServiceClient<tonic::transport::Channel>,
    >();
    assert_exists::<v1::exporter_service_client::ExporterServiceClient<tonic::transport::Channel>>(
    );
    assert_exists::<v1::router_service_client::RouterServiceClient<tonic::transport::Channel>>();
    assert_exists::<v1::resource_service_client::ResourceServiceClient<tonic::transport::Channel>>(
    );
    assert_exists::<client_v1::client_service_client::ClientServiceClient<tonic::transport::Channel>>(
    );

    // Server traits exist (referenced as generic bounds; never called).
    fn _assert_server_traits<C, E, R, Res, Cl>()
    where
        C: v1::controller_service_server::ControllerService,
        E: v1::exporter_service_server::ExporterService,
        R: v1::router_service_server::RouterService,
        Res: v1::resource_service_server::ResourceService,
        Cl: client_v1::client_service_server::ClientService,
    {
    }
}

/// The native byte-stream envelope `StreamData{bytes payload = 1}` round-trips — the framing the
/// native bidi byte plane (`@exportstream` + `ResourceService.Open`) carries, replacing the
/// `RouterService` `StreamRequest`/`StreamResponse{payload, frame_type}` frames.
#[test]
fn stream_data_roundtrips() {
    use prost::Message;

    let frame = v1::StreamData {
        payload: b"chunk".to_vec(),
    };
    let bytes = frame.encode_to_vec();
    assert_eq!(
        v1::StreamData::decode(bytes.as_slice()).unwrap().payload,
        b"chunk"
    );
}

/// `ExporterStatus` numeric tags are a hard wire contract
/// (`protocol/proto/jumpstarter/v1/common.proto:10-19`).
#[test]
fn exporter_status_tags_match_wire() {
    assert_eq!(v1::ExporterStatus::Unspecified as i32, 0);
    assert_eq!(v1::ExporterStatus::Offline as i32, 1);
    assert_eq!(v1::ExporterStatus::Available as i32, 2);
    assert_eq!(v1::ExporterStatus::BeforeLeaseHook as i32, 3);
    assert_eq!(v1::ExporterStatus::LeaseReady as i32, 4);
    assert_eq!(v1::ExporterStatus::AfterLeaseHook as i32, 5);
    assert_eq!(v1::ExporterStatus::BeforeLeaseHookFailed as i32, 6);
    assert_eq!(v1::ExporterStatus::AfterLeaseHookFailed as i32, 7);
}

/// Router `FrameType` tags are a hard wire contract
/// (`protocol/proto/jumpstarter/v1/router.proto:19-28`). Note the non-contiguous
/// values: DATA=0, RST_STREAM=3, PING=6, GOAWAY=7.
#[test]
fn frame_type_tags_match_wire() {
    assert_eq!(v1::FrameType::Data as i32, 0x00);
    assert_eq!(v1::FrameType::RstStream as i32, 0x03);
    assert_eq!(v1::FrameType::Ping as i32, 0x06);
    assert_eq!(v1::FrameType::Goaway as i32, 0x07);
}

/// A `DriverInstanceReport` round-trips through prost encode/decode, including the optional
/// `descriptor_set` bytes — the native per-interface gRPC surface (each driver ships its
/// self-contained `FileDescriptorSet`, which the client/core decode to build native routes). The
/// former generic `DriverCall`/`google.protobuf.Value` messages were removed in the native cutover.
#[test]
fn driver_instance_report_with_descriptor_set_roundtrips() {
    use prost::Message;

    let report = v1::DriverInstanceReport {
        uuid: "abc-123".to_string(),
        parent_uuid: Some("root".to_string()),
        descriptor_set: Some(vec![0x0a, 0x05, b'h', b'e', b'l', b'l', b'o']),
        ..Default::default()
    };

    let bytes = report.encode_to_vec();
    let decoded = v1::DriverInstanceReport::decode(bytes.as_slice()).expect("decode");

    assert_eq!(decoded.uuid, "abc-123");
    assert_eq!(decoded.parent_uuid.as_deref(), Some("root"));
    assert_eq!(
        decoded.descriptor_set.as_deref(),
        Some(&[0x0a, 0x05, b'h', b'e', b'l', b'l', b'o'][..])
    );
}
