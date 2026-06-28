//! Proc macros for native Jumpstarter clients.
//!
//! `#[derive(DriverClient)]` turns a clap-derived enum into a driver-client CLI: each variant
//! becomes an `@export`-style driver call (variant name → method, snake-cased; variant fields →
//! JSON args), and the macro generates the typed `dispatch` + a `run` that parses the args with
//! clap and invokes the call. This is the native (Rust) analog of a Python `DriverClient`'s click
//! group — the client author writes the command surface declaratively, the macro writes the
//! `driver_call` plumbing.
//!
//! ```ignore
//! #[derive(clap::Parser, DriverClient)]
//! #[client(class = "rust:powerclient")]
//! enum PowerClient {
//!     /// Turn the power on.
//!     On,
//!     /// Turn the power off.
//!     Off,
//!     /// Set the voltage.
//!     SetVoltage { millivolts: i64 },
//! }
//! // generates: PowerClient::CLIENT_CLASS, PowerClient::dispatch(session, uuid),
//! //            PowerClient::run(args, session, uuid).
//! ```

use proc_macro::TokenStream;
use quote::{format_ident, quote};
use syn::{parse_macro_input, Data, DeriveInput, Expr, ExprLit, Fields, ItemImpl, Lit, LitStr, MetaNameValue, Type};

/// Pull in everything `build.rs` generated for a proto-first driver crate — the whole `src/lib.rs`
/// wiring in one line: `jumpstarter_driver_runtime::interface!();`. Expands to the include of the
/// `jumpstarter_generated.rs` aggregator (the `proto` module + the typed client + the
/// `<short>_host!`/`<short>_client!` macros) that `jumpstarter_codegen::build::driver_interface`
/// writes into `OUT_DIR`. Re-exported as `jumpstarter_driver_runtime::interface!`.
#[proc_macro]
pub fn interface(_input: TokenStream) -> TokenStream {
    quote! {
        include!(concat!(env!("OUT_DIR"), "/jumpstarter_generated.rs"));
    }
    .into()
}

#[proc_macro_derive(DriverClient, attributes(client))]
pub fn derive_driver_client(input: TokenStream) -> TokenStream {
    let input = parse_macro_input!(input as DeriveInput);
    let name = &input.ident;

    // #[client(class = "rust:...")]
    let mut class: Option<LitStr> = None;
    for attr in &input.attrs {
        if attr.path().is_ident("client") {
            let _ = attr.parse_nested_meta(|meta| {
                if meta.path.is_ident("class") {
                    class = Some(meta.value()?.parse()?);
                }
                Ok(())
            });
        }
    }
    let class = match class {
        Some(c) => c,
        None => {
            return syn::Error::new_spanned(
                name,
                "DriverClient requires #[client(class = \"rust:...\")]",
            )
            .to_compile_error()
            .into()
        }
    };

    let variants = match &input.data {
        Data::Enum(e) => &e.variants,
        _ => {
            return syn::Error::new_spanned(name, "DriverClient can only be derived for enums")
                .to_compile_error()
                .into()
        }
    };

    // One match arm per variant: (method_name, [json args]).
    let arms = variants.iter().map(|v| {
        let vname = &v.ident;
        let method = to_snake_case(&vname.to_string());
        match &v.fields {
            Fields::Unit => quote! { Self::#vname => (#method, ::std::vec::Vec::new()), },
            Fields::Named(named) => {
                let idents: Vec<_> = named.named.iter().map(|f| f.ident.clone().unwrap()).collect();
                quote! {
                    Self::#vname { #(#idents),* } => (
                        #method,
                        ::std::vec![ #( ::serde_json::to_value(#idents).unwrap_or(::serde_json::Value::Null) ),* ],
                    ),
                }
            }
            Fields::Unnamed(_) => syn::Error::new_spanned(
                vname,
                "DriverClient: use unit or named-field variants (tuple variants are unsupported)",
            )
            .to_compile_error(),
        }
    });

    quote! {
        impl #name {
            /// The `jumpstarter.dev/client` label this native client drives.
            pub const CLIENT_CLASS: &'static str = #class;

            /// Map the parsed command to its driver call and invoke it.
            pub async fn dispatch(
                self,
                session: &::jumpstarter_core::ClientSession,
                uuid: &str,
            ) -> ::std::result::Result<::serde_json::Value, ::jumpstarter_core::error::DriverCallError> {
                let (method, args): (&str, ::std::vec::Vec<::serde_json::Value>) = match self { #(#arms)* };
                let args_json = ::serde_json::to_string(&args).unwrap_or_else(|_| "[]".to_string());
                let result = session
                    .driver_call(uuid.to_string(), method.to_string(), args_json)
                    .await?;
                ::std::result::Result::Ok(
                    ::serde_json::from_str(&result).unwrap_or(::serde_json::Value::Null),
                )
            }

            /// Parse `args` (the subcommand + its options) with clap and run the call. Returns a
            /// process exit code. Requires `Self: clap::Parser` (derive `clap::Parser` too).
            pub async fn run(
                args: &[::std::string::String],
                session: &::jumpstarter_core::ClientSession,
                uuid: &str,
            ) -> i32 {
                let argv = ::std::iter::once(::std::string::String::from("j"))
                    .chain(args.iter().cloned());
                match <Self as ::clap::Parser>::try_parse_from(argv) {
                    ::std::result::Result::Ok(cmd) => match cmd.dispatch(session, uuid).await {
                        ::std::result::Result::Ok(value) => {
                            if !value.is_null() {
                                println!("{}", value);
                            }
                            0
                        }
                        ::std::result::Result::Err(e) => {
                            eprintln!("Error: {}", e);
                            1
                        }
                    },
                    ::std::result::Result::Err(e) => {
                        let _ = e.print();
                        if e.use_stderr() {
                            2
                        } else {
                            0
                        }
                    }
                }
            }
        }
    }
    .into()
}

// NOTE: the per-crate host entrypoint is the codegen-generated `<short>_host!` macro_rules (emitted
// by jumpstarter-codegen's RustGenerator, with the client class / descriptor / server type baked in),
// so the author's whole `main` is `<crate>::<short>_host!(MyDriver::default())`. No generic host
// proc-macro is needed here — `jumpstarter_driver_runtime::run_host` is the library primitive it
// expands to.

/// Auto-register a driver impl. Put `#[jumpstarter_driver_runtime::driver(client = "…")]` on an
/// `impl <Interface> for <Driver>` and the driver is collected into the crate's host registry (the
/// Rust analog of the JVM `@JumpstarterDriver` annotation), so the host binary's whole `src/main.rs`
/// is `jumpstarter_driver_runtime::host_main!();`. The server type + descriptor are derived by
/// convention from the interface trait + the crate's `proto` module; `client` is the default client.
#[proc_macro_attribute]
pub fn driver(attr: TokenStream, item: TokenStream) -> TokenStream {
    let meta = parse_macro_input!(attr as MetaNameValue);
    let client = match &meta.value {
        Expr::Lit(ExprLit {
            lit: Lit::Str(s), ..
        }) if meta.path.is_ident("client") => s.value(),
        _ => {
            return syn::Error::new_spanned(&meta, "expected `#[driver(client = \"…\")]`")
                .to_compile_error()
                .into()
        }
    };

    let imp = parse_macro_input!(item as ItemImpl);
    let trait_name = match &imp.trait_ {
        Some((_, path, _)) => path.segments.last().unwrap().ident.to_string(),
        None => {
            return syn::Error::new_spanned(&imp, "`#[driver]` goes on `impl <Interface> for <Driver>`")
                .to_compile_error()
                .into()
        }
    };
    let driver_ident = match &*imp.self_ty {
        Type::Path(tp) => tp.path.segments.last().unwrap().ident.clone(),
        _ => {
            return syn::Error::new_spanned(&imp.self_ty, "`#[driver]` needs a named driver type")
                .to_compile_error()
                .into()
        }
    };
    let self_ty = &imp.self_ty;

    // Convention from the interface trait name + the crate's generated `proto` module:
    //   PowerInterface -> proto::power_interface_server::PowerInterfaceServer + proto::FILE_DESCRIPTOR_SET
    let server_mod = format_ident!("{}_server", to_snake_case(&trait_name));
    let server_type = format_ident!("{trait_name}Server");
    let serve_fn = format_ident!("__jmp_driver_serve_{}", driver_ident);

    quote! {
        #imp

        #[doc(hidden)]
        const _: () = {
            #[allow(non_snake_case)]
            fn #serve_fn(
                name: ::std::string::String,
            ) -> ::std::pin::Pin<::std::boxed::Box<
                dyn ::std::future::Future<
                        Output = ::std::io::Result<
                            ::std::sync::Arc<dyn ::jumpstarter_transport::DriverBackend>,
                        >,
                    > + ::std::marker::Send,
            >> {
                ::std::boxed::Box::pin(async move {
                    ::jumpstarter_driver_runtime::serve_driver(
                        &name,
                        #client,
                        crate::proto::FILE_DESCRIPTOR_SET.to_vec(),
                        crate::proto::#server_mod::#server_type::new(
                            <#self_ty as ::std::default::Default>::default(),
                        ),
                    )
                    .await
                })
            }

            ::jumpstarter_driver_runtime::inventory::submit! {
                ::jumpstarter_driver_runtime::DriverRegistration {
                    client_class: #client,
                    descriptor: crate::proto::FILE_DESCRIPTOR_SET,
                    serve: #serve_fn,
                }
            }
        };
    }
    .into()
}

/// Auto-register a client CLI. Put `#[jumpstarter_client::client_cli]` on a typed CLI (a clap
/// subcommand type with `async fn run(args, session, uuid) -> i32`) and it's collected into the crate's
/// client registry (the client-side mirror of the host `#[driver]`, and the Rust analog of the JVM
/// `@JumpstarterClientCli`), so the client binary's whole `src/client.rs` is
/// `jumpstarter_client::client_main!();`. Only CLI-exposing clients are registered — a plain
/// client library needs nothing. The descriptor is taken by convention from the crate's `proto` module.
#[proc_macro_attribute]
pub fn client_cli(_attr: TokenStream, item: TokenStream) -> TokenStream {
    let input = parse_macro_input!(item as DeriveInput);
    let cli = &input.ident;
    let run_fn = format_ident!("__jmp_client_run_{}", cli);

    quote! {
        #input

        #[doc(hidden)]
        const _: () = {
            #[allow(non_snake_case)]
            fn #run_fn<'a>(
                args: &'a [::std::string::String],
                session: &'a ::jumpstarter_client::ClientSession,
                uuid: &'a str,
            ) -> ::std::pin::Pin<::std::boxed::Box<dyn ::std::future::Future<Output = i32> + 'a>> {
                ::std::boxed::Box::pin(#cli::run(args, session, uuid))
            }

            ::jumpstarter_client::inventory::submit! {
                ::jumpstarter_client::ClientRegistration {
                    descriptor: crate::proto::FILE_DESCRIPTOR_SET,
                    run: #run_fn,
                }
            }
        };
    }
    .into()
}

/// `OnOff` → `on_off`, `On` → `on`.
fn to_snake_case(ident: &str) -> String {
    let mut out = String::new();
    for (i, ch) in ident.char_indices() {
        if ch.is_uppercase() {
            if i != 0 {
                out.push('_');
            }
            out.extend(ch.to_lowercase());
        } else {
            out.push(ch);
        }
    }
    out
}
