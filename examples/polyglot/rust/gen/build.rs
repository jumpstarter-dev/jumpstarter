fn main() -> Result<(), Box<dyn std::error::Error>> {
    let proto_dirs = [
        "../../../../python/packages/jumpstarter-driver-power/proto",
        "../../../../python/packages/jumpstarter-driver-opendal/proto",
        "../../../../python/packages/jumpstarter-driver-network/proto",
    ];

    tonic_build::configure()
        .build_server(false)
        .compile_protos(
            &[
                "../../../../python/packages/jumpstarter-driver-power/proto/power/v1/power.proto",
                "../../../../python/packages/jumpstarter-driver-opendal/proto/storage_mux/v1/storage_mux.proto",
                "../../../../python/packages/jumpstarter-driver-network/proto/network/v1/network.proto",
            ],
            &proto_dirs,
        )?;
    Ok(())
}
