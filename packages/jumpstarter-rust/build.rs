use std::{env, path::PathBuf};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let out_dir = PathBuf::from(env::var("OUT_DIR").unwrap());
    tonic_build::configure()
        .compile_well_known_types(true)
        .type_attribute("DriverCallResponse", "#[derive(pyo3::FromPyObject)]")
        .type_attribute(
            "StreamingDriverCallResponse",
            "#[derive(pyo3::FromPyObject)]",
        )
        .type_attribute("StreamResponse", "#[derive(pyo3::FromPyObject)]")
        .file_descriptor_set_path(out_dir.join("jumpstarter_descriptor.bin"))
        .compile_protos(
            &[
                "proto/jumpstarter/v1/jumpstarter.proto",
                "proto/jumpstarter/v1/router.proto",
            ],
            &["proto"],
        )?;
    Ok(())
}
