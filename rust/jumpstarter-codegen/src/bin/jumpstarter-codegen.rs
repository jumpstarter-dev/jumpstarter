//! `jumpstarter-codegen` — generate per-language driver clients / stub skeletons from a compiled
//! `FileDescriptorSet`. Build systems invoke this so generated code is never committed.
//!
//! ```text
//! jumpstarter-codegen --descriptor-set power.desc --language kotlin --kind client --out gen/
//!     [--service PowerInterface]   # restrict to one service (default: all in the set)
//! ```
//!
//! `--language`: `rust` | `java` | `kotlin` (java and kotlin share the JVM generator).
//! `--kind`: `client` (typed client) | `stub` (driver impl skeleton).

use std::path::PathBuf;
use std::process::exit;

use jumpstarter_codegen::engine::interfaces_from_descriptor_set;
use jumpstarter_codegen::languages::java::JavaGenerator;
use jumpstarter_codegen::languages::rust::RustGenerator;
use jumpstarter_codegen::languages::LanguageGenerator;

fn main() {
    let mut descriptor_set: Option<PathBuf> = None;
    let mut language = String::new();
    let mut kind = String::from("client");
    let mut out_dir: Option<PathBuf> = None;
    let mut service: Option<String> = None;

    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        let mut next = || args.next().unwrap_or_else(|| fail(&format!("{arg} needs a value")));
        match arg.as_str() {
            "--descriptor-set" => descriptor_set = Some(PathBuf::from(next())),
            "--language" => language = next(),
            "--kind" => kind = next(),
            "--out" => out_dir = Some(PathBuf::from(next())),
            "--service" => service = Some(next()),
            "-h" | "--help" => {
                eprintln!("usage: jumpstarter-codegen --descriptor-set <f> --language <rust|java|kotlin> --kind <client|stub> --out <dir> [--service <Name>]");
                return;
            }
            other => fail(&format!("unknown argument {other}")),
        }
    }

    let descriptor_set = descriptor_set.unwrap_or_else(|| fail("--descriptor-set is required"));
    let out_dir = out_dir.unwrap_or_else(|| fail("--out is required"));

    let generator: Box<dyn LanguageGenerator> = match language.as_str() {
        "rust" => Box::new(RustGenerator),
        // java and kotlin both target the JVM generator (grpc-java stubs + a Kotlin client).
        "java" | "kotlin" => Box::new(JavaGenerator),
        other => fail(&format!("unknown --language {other:?} (rust|java|kotlin)")),
    };

    let bytes = std::fs::read(&descriptor_set)
        .unwrap_or_else(|e| fail(&format!("read {}: {e}", descriptor_set.display())));
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
