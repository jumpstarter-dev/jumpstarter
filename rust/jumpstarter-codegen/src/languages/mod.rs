//! Per-language code generators.
//!
//! [`LanguageGenerator`] is the seam each target language implements to turn an
//! [`InterfaceRef`](crate::ir::InterfaceRef) into source files: a **driver template**
//! (the server side the author implements) and a **typed client**. Generators are pure
//! string-building (no template engine), mirroring the jep-14 Python generators.
//!
//! The generators themselves ([`rust::RustGenerator`], [`java::JavaGenerator`]) are
//! compiling stubs here; the parallel codegen phase fills in their bodies. This module
//! and the trait are the stable contract those generators code against.

use std::collections::BTreeMap;

use crate::ir::InterfaceRef;

pub mod rust;
pub mod java;

/// A per-language code generator.
///
/// Each method returns a map of **relative file path → file contents** so a caller can
/// write a multi-file output tree deterministically (the `BTreeMap` keeps paths sorted).
pub trait LanguageGenerator {
    /// The language identifier (e.g. `"rust"`, `"java"`).
    fn name(&self) -> &str;

    /// Generate the server-side **driver template** for an interface: the typed service
    /// the author implements plus the Jumpstarter adapter that bridges it to the core's
    /// driver-backend seam.
    fn generate_driver(&self, iface: &InterfaceRef) -> BTreeMap<String, String>;

    /// Generate the **typed client** for an interface: the per-method wrappers a consumer
    /// calls to drive the driver through the core.
    fn generate_client(&self, iface: &InterfaceRef) -> BTreeMap<String, String>;
}
