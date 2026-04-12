fn main() -> Result<(), Box<dyn std::error::Error>> {
    let proto_root = "../../protocol/proto";
    tonic_build::configure()
        .build_server(false)
        .compile_protos(
            &[
                &format!("{proto_root}/jumpstarter/v1/jumpstarter.proto"),
                &format!("{proto_root}/jumpstarter/v1/router.proto"),
            ],
            &[proto_root],
        )?;
    Ok(())
}
