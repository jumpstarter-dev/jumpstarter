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
//! `--kind`: `client` (typed client) | `stub` (driver impl skeleton / interface base).
//! `--proto` + `-I`: compile the `.proto` in-process (protox — no system protoc needed), the
//! alternative to a pre-compiled `--descriptor-set`.
//! `--python-package`: the dotted package the generated Python modules will live in (qualifies the
//! driver base's `client()` import path).

use std::path::PathBuf;
use std::process::exit;

use jumpstarter_codegen::engine::interfaces_from_descriptor_set;
use jumpstarter_codegen::languages::java::JavaGenerator;
use jumpstarter_codegen::languages::python::PythonGenerator;
use jumpstarter_codegen::languages::rust::RustGenerator;
use jumpstarter_codegen::languages::LanguageGenerator;

fn main() {
    let mut descriptor_set: Option<PathBuf> = None;
    let mut proto: Option<PathBuf> = None;
    let mut includes: Vec<PathBuf> = Vec::new();
    let mut language = String::new();
    let mut kind = String::from("client");
    let mut out_dir: Option<PathBuf> = None;
    let mut service: Option<String> = None;
    let mut python_package: Option<String> = None;

    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        let mut next = || args.next().unwrap_or_else(|| fail(&format!("{arg} needs a value")));
        match arg.as_str() {
            "--descriptor-set" => descriptor_set = Some(PathBuf::from(next())),
            "--proto" => proto = Some(PathBuf::from(next())),
            "-I" | "--include" => includes.push(PathBuf::from(next())),
            "--language" => language = next(),
            "--kind" => kind = next(),
            "--out" => out_dir = Some(PathBuf::from(next())),
            "--service" => service = Some(next()),
            "--python-package" => python_package = Some(next()),
            "-h" | "--help" => {
                eprintln!("usage: jumpstarter-codegen (--descriptor-set <f> | --proto <f> [-I <dir>]...) --language <rust|java|kotlin|python> --kind <client|stub> --out <dir> [--service <Name>] [--python-package <pkg>]");
                return;
            }
            other => fail(&format!("unknown argument {other}")),
        }
    }

    let out_dir = out_dir.unwrap_or_else(|| fail("--out is required"));

    // The serialized FileDescriptorSet: read pre-compiled, or compile the .proto in-process.
    let bytes: Vec<u8> = match (&descriptor_set, &proto) {
        (Some(path), None) => std::fs::read(path)
            .unwrap_or_else(|e| fail(&format!("read {}: {e}", path.display()))),
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
        other => fail(&format!("unknown --language {other:?} (rust|java|kotlin|python)")),
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
