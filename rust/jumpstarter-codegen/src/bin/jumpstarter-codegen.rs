//! `jumpstarter-codegen` — generate per-language driver clients / stub skeletons from a compiled
//! `FileDescriptorSet` or directly from a `.proto` file. Build systems invoke this so generated
//! code is never committed.
//!
//! ```text
//! jumpstarter-codegen --descriptor-set power.desc --language kotlin --kind client --out gen/
//! jumpstarter-codegen --proto interfaces/proto/.../power.proto -I interfaces/proto \
//!     --language python --kind stub --python-package pkg._generated --out gen/
//!     [--service PowerInterface]   # restrict to one service (default: all in the set)
//! ```
//!
//! `--language`: `rust` | `java` | `kotlin` (java and kotlin share the JVM generator) | `python`.
//! `--kind`: `client` (typed client) | `stub` (driver impl skeleton / interface base) | `device`
//! (typed device wrapper mirroring an exporter config's driver tree — see below).
//! `--proto` + `-I`: compile the `.proto` in-process (protox — no system protoc needed), the
//! alternative to a pre-compiled `--descriptor-set`.
//! `--python-package`: the dotted package the generated Python modules will live in (qualifies the
//! driver base's `client()` import path).
//!
//! Device mode (`--kind device`) resolves interfaces from committed `.proto` files only — it
//! never loads driver code:
//!
//! ```text
//! jumpstarter-codegen --exporter-config exporter.yaml --language python --kind device \
//!     --registry interfaces/registry --proto-root interfaces/proto --out gen/ \
//!     [--client-config client.yaml] [--select <iface-FQN>=<selector>]... \
//!     [--device-name Name] [--strict]
//! ```

use std::path::PathBuf;
use std::process::exit;

use jumpstarter_codegen::device::{generate_device, DeviceOptions};
use jumpstarter_codegen::engine::interfaces_from_descriptor_set;
use jumpstarter_codegen::languages::java::JavaGenerator;
use jumpstarter_codegen::languages::python::PythonGenerator;
use jumpstarter_codegen::languages::rust::RustGenerator;
use jumpstarter_codegen::languages::LanguageGenerator;
use jumpstarter_codegen::resolver::resolve_device;
use jumpstarter_config::{ClientConfig, DriverRegistry, ExporterConfig, YamlConfig};

fn main() {
    let mut descriptor_set: Option<PathBuf> = None;
    let mut proto: Option<PathBuf> = None;
    let mut includes: Vec<PathBuf> = Vec::new();
    let mut language = String::new();
    let mut kind = String::from("client");
    let mut out_dir: Option<PathBuf> = None;
    let mut service: Option<String> = None;
    let mut python_package: Option<String> = None;
    let mut exporter_config: Option<PathBuf> = None;
    let mut registry_paths: Vec<PathBuf> = Vec::new();
    let mut proto_root: Option<PathBuf> = None;
    let mut client_config: Option<PathBuf> = None;
    let mut selects: Vec<(String, String)> = Vec::new();
    let mut device_name: Option<String> = None;
    let mut strict = false;

    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        let mut next = || {
            args.next()
                .unwrap_or_else(|| fail(&format!("{arg} needs a value")))
        };
        match arg.as_str() {
            "--descriptor-set" => descriptor_set = Some(PathBuf::from(next())),
            "--proto" => proto = Some(PathBuf::from(next())),
            "-I" | "--include" => includes.push(PathBuf::from(next())),
            "--language" => language = next(),
            "--kind" => kind = next(),
            "--out" => out_dir = Some(PathBuf::from(next())),
            "--service" => service = Some(next()),
            "--python-package" => python_package = Some(next()),
            "--exporter-config" => exporter_config = Some(PathBuf::from(next())),
            "--registry" => registry_paths.push(PathBuf::from(next())),
            "--proto-root" => proto_root = Some(PathBuf::from(next())),
            "--client-config" => client_config = Some(PathBuf::from(next())),
            "--select" => {
                let value = next();
                let (fqn, selector) = value
                    .split_once('=')
                    .unwrap_or_else(|| fail("--select takes <interface-FQN>=<selector>"));
                selects.push((fqn.to_string(), selector.to_string()));
            }
            "--device-name" => device_name = Some(next()),
            "--strict" => strict = true,
            "-h" | "--help" => {
                eprintln!("usage: jumpstarter-codegen (--descriptor-set <f> | --proto <f> [-I <dir>]...) --language <rust|java|kotlin|python> --kind <client|stub> --out <dir> [--service <Name>] [--python-package <pkg>]");
                eprintln!("       jumpstarter-codegen --exporter-config <yaml> --language <rust|java|kotlin|python> --kind device --out <dir> [--registry <file|dir>]... [--proto-root <dir>] [--client-config <yaml>] [--select <FQN>=<selector>]... [--device-name <Name>] [--strict]");
                return;
            }
            other => fail(&format!("unknown argument {other}")),
        }
    }

    let out_dir = out_dir.unwrap_or_else(|| fail("--out is required"));

    if kind == "device" {
        let config_path = exporter_config
            .unwrap_or_else(|| fail("--kind device requires --exporter-config <yaml>"));
        let config = ExporterConfig::load(&config_path)
            .unwrap_or_else(|e| fail(&format!("load {}: {e}", config_path.display())));

        let mut registry = DriverRegistry::default();
        for path in &registry_paths {
            let loaded = DriverRegistry::load_path(path)
                .unwrap_or_else(|e| fail(&format!("load registry {}: {e}", path.display())));
            registry.merge(loaded);
        }
        let proto_root =
            proto_root.unwrap_or_else(|| fail("--kind device requires --proto-root <dir>"));

        // Client overrides: `drivers.select` from --client-config, then --select flags on top.
        let mut select = std::collections::BTreeMap::new();
        if let Some(path) = &client_config {
            let cc = ClientConfig::load(path)
                .unwrap_or_else(|e| fail(&format!("load client config {}: {e}", path.display())));
            select.extend(cc.drivers.select);
        }
        select.extend(selects);

        let device = resolve_device(&config, &registry, &proto_root, strict)
            .unwrap_or_else(|e| fail(&format!("resolve exporter config: {e}")));
        let opts = DeviceOptions {
            device_name,
            select,
            python_package,
        };
        let (files, warnings) = generate_device(&device, &language, &opts)
            .unwrap_or_else(|e| fail(&format!("generate device: {e}")));
        for warning in &warnings {
            eprintln!("warning: {warning}");
        }
        std::fs::create_dir_all(&out_dir)
            .unwrap_or_else(|e| fail(&format!("create {}: {e}", out_dir.display())));
        for (name, source) in files {
            let path = out_dir.join(&name);
            std::fs::write(&path, source)
                .unwrap_or_else(|e| fail(&format!("write {}: {e}", path.display())));
            eprintln!("wrote {}", path.display());
        }
        return;
    }

    // The serialized FileDescriptorSet: read pre-compiled, or compile the .proto in-process.
    let bytes: Vec<u8> = match (&descriptor_set, &proto) {
        (Some(path), None) => {
            std::fs::read(path).unwrap_or_else(|e| fail(&format!("read {}: {e}", path.display())))
        }
        (None, Some(proto)) => {
            if includes.is_empty() {
                // Default include path: the proto's own directory.
                if let Some(parent) = proto.parent() {
                    includes.push(parent.to_path_buf());
                }
            }
            let fds = protox::compile([proto.as_path()], includes.iter().map(PathBuf::as_path))
                .unwrap_or_else(|e| fail(&format!("compile {}: {e}", proto.display())));
            prost::Message::encode_to_vec(&fds)
        }
        (Some(_), Some(_)) => fail("--descriptor-set and --proto are mutually exclusive"),
        (None, None) => fail("one of --descriptor-set or --proto is required"),
    };

    let generator: Box<dyn LanguageGenerator> = match language.as_str() {
        "rust" => Box::new(RustGenerator),
        // java and kotlin both target the JVM generator (grpc-java stubs + a Kotlin client).
        "java" | "kotlin" => Box::new(JavaGenerator),
        "python" => Box::new(PythonGenerator::new(python_package, bytes.clone())),
        other => fail(&format!(
            "unknown --language {other:?} (rust|java|kotlin|python)"
        )),
    };

    let interfaces = interfaces_from_descriptor_set(&bytes)
        .unwrap_or_else(|e| fail(&format!("parse descriptor set: {e}")));

    std::fs::create_dir_all(&out_dir)
        .unwrap_or_else(|e| fail(&format!("create {}: {e}", out_dir.display())));

    let mut wrote = 0usize;
    for iface in &interfaces {
        if let Some(want) = &service {
            if &iface.service_name != want {
                continue;
            }
        }
        let files = match kind.as_str() {
            "client" => generator.generate_client(iface),
            "stub" => generator.generate_driver(iface),
            other => fail(&format!("unknown --kind {other:?} (client|stub)")),
        };
        for (name, source) in files {
            let path = out_dir.join(&name);
            std::fs::write(&path, source)
                .unwrap_or_else(|e| fail(&format!("write {}: {e}", path.display())));
            eprintln!("wrote {}", path.display());
            wrote += 1;
        }
    }
    if wrote == 0 {
        fail("no interfaces matched (check --service / the descriptor set)");
    }
}

fn fail(msg: &str) -> ! {
    eprintln!("jumpstarter-codegen: {msg}");
    exit(2)
}
