//! Proc macros for the Jumpstarter test framework.
//!
//! Provides `#[jumpstarter_test]` which:
//! 1. Creates an `ExporterSession` from the environment
//! 2. Constructs the typed device wrapper
//! 3. Passes it to the test function
//! 4. Runs the test inside a `tokio` async runtime

use proc_macro::TokenStream;
use quote::quote;
use syn::{parse_macro_input, ItemFn};

/// Attribute macro for Jumpstarter hardware test functions.
///
/// Transforms an async function that takes a typed device wrapper into a
/// `#[tokio::test]` that automatically sets up the exporter session.
///
/// # Usage
///
/// ```ignore
/// #[jumpstarter_test]
/// async fn test_power_cycle(device: DevBoardDevice<'_>) {
///     device.power.on(()).await.unwrap();
///     device.power.off(()).await.unwrap();
/// }
/// ```
///
/// Expands to:
///
/// ```ignore
/// #[tokio::test]
/// async fn test_power_cycle() {
///     let session = jumpstarter_testing::ExporterSession::from_env()
///         .await
///         .expect("failed to connect to exporter");
///     let device = DevBoardDevice::new(&session);
///     // ... original function body ...
/// }
/// ```
#[proc_macro_attribute]
pub fn jumpstarter_test(_attr: TokenStream, item: TokenStream) -> TokenStream {
    let input = parse_macro_input!(item as ItemFn);

    let fn_name = &input.sig.ident;
    let fn_body = &input.block;
    let attrs = &input.attrs;

    // Extract the device parameter — must be exactly one parameter
    let param = match input.sig.inputs.first() {
        Some(syn::FnArg::Typed(pat_type)) => pat_type,
        _ => {
            return syn::Error::new_spanned(
                &input.sig,
                "#[jumpstarter_test] function must take exactly one typed device parameter",
            )
            .into_compile_error()
            .into();
        }
    };

    let param_name = &param.pat;
    let param_type = &param.ty;

    // Strip lifetime from the type to get the constructor path.
    // e.g., DevBoardDevice<'_> → DevBoardDevice
    let constructor_type = strip_lifetime(param_type);

    let expanded = quote! {
        #(#attrs)*
        #[tokio::test]
        async fn #fn_name() {
            let session = jumpstarter_testing::ExporterSession::from_env()
                .await
                .expect("failed to connect to Jumpstarter exporter");
            let #param_name: #param_type = #constructor_type::new(&session);
            #fn_body
        }
    };

    expanded.into()
}

/// Strip lifetime parameters from a type for use as a constructor path.
fn strip_lifetime(ty: &syn::Type) -> proc_macro2::TokenStream {
    match ty {
        syn::Type::Path(type_path) => {
            let mut path = type_path.path.clone();
            for seg in &mut path.segments {
                if let syn::PathArguments::AngleBracketed(ref mut args) = seg.arguments {
                    args.args = args
                        .args
                        .iter()
                        .filter(|arg| !matches!(arg, syn::GenericArgument::Lifetime(_)))
                        .cloned()
                        .collect();
                    if args.args.is_empty() {
                        seg.arguments = syn::PathArguments::None;
                    }
                }
            }
            quote! { #path }
        }
        _ => quote! { #ty },
    }
}
