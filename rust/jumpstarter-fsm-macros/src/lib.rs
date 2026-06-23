//! Derive macro for the typestate FSM core.
//!
//! `#[derive(StateMachine)]` on the hand-written wrapper enum generates the wrapper
//! plumbing: `Live` impls for the non-`#[terminal]` states, `From<Handle<Ctx, State>>` for
//! every variant (so a hand-written transition can finish with `.into()`), and the `Fsm`
//! impl. The generated `Fsm::apply` routes each live variant to that state's hand-written
//! `apply(self, Signal) -> Wrapper`; terminal variants are absorbing. The signal type is
//! inferred from the enum name (`LeaseState` ⟹ `LeaseSignal`); override with
//! `#[machine(signal = ..)]`.
//!
//! There is deliberately no attribute macro for the transitions themselves: each state writes
//! its own `apply` match, so the signal-to-transition mapping is explicit and exhaustive.

use proc_macro::TokenStream;
use quote::quote;
use syn::{
    parse_macro_input, Attribute, Data, DeriveInput, Error, Fields, GenericArgument, Ident,
    PathArguments, Type, Variant,
};

/// Derive the wrapper plumbing for a hand-written typestate enum.
///
/// Applied to an enum whose every variant holds one typed state handle, e.g.
///
/// ```ignore
/// #[derive(Clone, StateMachine)]
/// pub enum LeaseState {
///     Created(Lease<Created>),
///     Ready(Lease<Ready>),
///     #[terminal] Done(Lease<Done>),
///     #[terminal] Failed(Lease<Failed>),
/// }
/// ```
///
/// it generates `impl Live` for every non-`#[terminal]` variant's state, `From<Handle<..>>`
/// for every variant, and `impl Fsm`. `apply` calls each live state's hand-written `apply`
/// and leaves terminal states unchanged. The signal type is inferred from the enum name
/// (`LeaseState` ⟹ `LeaseSignal`); override with `#[machine(signal = ..)]`.
#[proc_macro_derive(StateMachine, attributes(machine, terminal))]
pub fn derive_state_machine(input: TokenStream) -> TokenStream {
    let input = parse_macro_input!(input as DeriveInput);
    let wrapper = input.ident.clone();

    let signal = match signal_override(&input.attrs) {
        Ok(Some(s)) => s,
        Ok(None) => sibling(&wrapper, "Signal"),
        Err(e) => return e.to_compile_error().into(),
    };

    let Data::Enum(data) = &input.data else {
        return Error::new_spanned(&wrapper, "StateMachine can only be derived for an enum")
            .to_compile_error()
            .into();
    };

    let mut live_impls = Vec::new();
    let mut from_impls = Vec::new();
    let mut apply_arms = Vec::new();
    let mut terminal_pats = Vec::new();

    for variant in &data.variants {
        let vname = &variant.ident;
        let inner = match variant_inner_type(variant) {
            Ok(t) => t,
            Err(e) => return e.to_compile_error().into(),
        };

        // Every variant can be type-erased into the wrapper. This `From` is what a
        // hand-written transition uses to finish: `self.into_state(Next).into()`.
        from_impls.push(quote! {
            impl ::core::convert::From<#inner> for #wrapper {
                fn from(inner: #inner) -> Self {
                    #wrapper::#vname(inner)
                }
            }
        });

        let terminal = variant.attrs.iter().any(|a| a.path().is_ident("terminal"));
        if terminal {
            terminal_pats.push(quote! { #wrapper::#vname(_) });
            apply_arms.push(quote! { #wrapper::#vname(inner) => #wrapper::#vname(inner), });
        } else {
            let state = match variant_state_ident(variant) {
                Ok(s) => s,
                Err(e) => return e.to_compile_error().into(),
            };
            live_impls.push(quote! { impl Live for #state {} });
            apply_arms.push(quote! { #wrapper::#vname(inner) => inner.apply(signal), });
        }
    }

    quote! {
        #(#live_impls)*
        #(#from_impls)*

        impl Fsm for #wrapper {
            type Signal = #signal;

            /// Route a signal to whichever state we are in. Live states dispatch to their
            /// hand-written `apply`; terminal states ignore every signal.
            fn apply(self, signal: #signal) -> #wrapper {
                match self { #(#apply_arms)* }
            }

            fn is_terminal(&self) -> bool {
                matches!(self, #(#terminal_pats)|*)
            }
        }
    }
    .into()
}

/// Read an optional `#[machine(signal = ..)]` override.
fn signal_override(attrs: &[Attribute]) -> syn::Result<Option<Ident>> {
    let mut signal = None;
    if let Some(attr) = attrs.iter().find(|a| a.path().is_ident("machine")) {
        attr.parse_nested_meta(|meta| {
            if meta.path.is_ident("signal") {
                signal = Some(meta.value()?.parse()?);
                Ok(())
            } else {
                Err(meta.error("expected `signal`"))
            }
        })?;
    }
    Ok(signal)
}

/// `LeaseState` + "Signal" ⟹ `LeaseSignal` (a trailing `State` on the wrapper is dropped
/// before appending the suffix).
fn sibling(wrapper: &Ident, suffix: &str) -> Ident {
    let s = wrapper.to_string();
    let base = s.strip_suffix("State").unwrap_or(&s);
    Ident::new(&format!("{base}{suffix}"), wrapper.span())
}

/// From a variant `Ready(Lease<Ready>)`, recover the inner field type `Lease<Ready>`.
fn variant_inner_type(v: &Variant) -> syn::Result<&Type> {
    match &v.fields {
        Fields::Unnamed(f) if f.unnamed.len() == 1 => Ok(&f.unnamed[0].ty),
        _ => Err(Error::new_spanned(
            v,
            "each variant must hold exactly one `Handle<Ctx, State>` field",
        )),
    }
}

/// From a variant `Ready(Lease<Ready>)` / `Ready(Handle<Ctx, Ready>)`, recover the inner
/// state ident `Ready` (the last generic argument).
fn variant_state_ident(v: &Variant) -> syn::Result<Ident> {
    let ty = variant_inner_type(v)?;
    if let Type::Path(tp) = ty {
        if let Some(seg) = tp.path.segments.last() {
            if let PathArguments::AngleBracketed(ab) = &seg.arguments {
                if let Some(GenericArgument::Type(Type::Path(inner))) = ab.args.last() {
                    if let Some(s) = inner.path.segments.last() {
                        return Ok(s.ident.clone());
                    }
                }
            }
        }
    }
    Err(Error::new_spanned(
        ty,
        "expected a `Handle<Ctx, State>` (or alias) field type",
    ))
}
