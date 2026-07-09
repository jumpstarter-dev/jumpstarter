//! Dumps the four `jumpstarter.dev/v1alpha1` CRDs generated from the Rust
//! types as multi-document YAML, in the same shape the Go controller-gen
//! output uses (one `---`-prefixed document per CRD).
//!
//! Usage: `cargo run -p jumpstarter-controller-api --example dump_crds`
//!
//! Note: the deployed CRDs remain the Go-generated YAML in
//! `controller/deploy/operator/config/crd/bases/` until the operator phase;
//! this output is a verification artifact (see `tests/crd_parity.rs`).

use kube::CustomResourceExt;

use jumpstarter_controller_api::access_policy::ExporterAccessPolicy;
use jumpstarter_controller_api::client::Client;
use jumpstarter_controller_api::exporter::Exporter;
use jumpstarter_controller_api::lease::Lease;

fn main() {
    let crds = [
        serde_yaml_ng::to_string(&Client::crd()).expect("serialize Client CRD"),
        serde_yaml_ng::to_string(&Exporter::crd()).expect("serialize Exporter CRD"),
        serde_yaml_ng::to_string(&ExporterAccessPolicy::crd())
            .expect("serialize ExporterAccessPolicy CRD"),
        serde_yaml_ng::to_string(&Lease::crd()).expect("serialize Lease CRD"),
    ];
    for crd in crds {
        println!("---");
        print!("{crd}");
    }
}
