use pyo3::prelude::*;

pub mod jumpstarter {
    pub mod v1 {
        tonic::include_proto!("jumpstarter.v1");

        pub(crate) const FILE_DESCRIPTOR_SET: &[u8] =
            tonic::include_file_descriptor_set!("jumpstarter_descriptor");
    }
}

pub mod google {
    pub mod protobuf {
        tonic::include_proto!("google.protobuf");
    }
}

pub mod exporter;

#[pymodule]
fn jumpstarter_rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<exporter::Session>()?;
    Ok(())
}
