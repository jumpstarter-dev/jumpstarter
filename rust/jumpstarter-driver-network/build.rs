fn main() -> Result<(), Box<dyn std::error::Error>> {
    tonic_build::configure()
        .build_server(false)
        .build_transport(false)
        .compile_protos(
            &["../../python/packages/jumpstarter-driver-network/proto/network/v1/network.proto"],
            &["../../python/packages/jumpstarter-driver-network/proto"],
        )?;
    Ok(())
}
