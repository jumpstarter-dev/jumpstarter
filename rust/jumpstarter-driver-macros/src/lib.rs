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
use quote::quote;
use syn::{parse_macro_input, Data, DeriveInput, Fields, LitStr};

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
