//! Shared serde helpers that reproduce Go encoding/json edge cases
//! (sigs.k8s.io/yaml routes YAML through encoding/json).

use serde::{Deserialize, Deserializer};

/// `skip_serializing_if` predicate for Go `bool` fields tagged `omitempty`:
/// `false` is the zero value and is omitted.
pub(crate) fn is_false(b: &bool) -> bool {
    !*b
}

/// `skip_serializing_if` predicate for Go `int32` fields tagged `omitempty`:
/// `0` is the zero value and is omitted.
pub(crate) fn is_zero_i32(v: &i32) -> bool {
    *v == 0
}

/// Deserializes an explicit YAML/JSON `null` (or a missing value routed here
/// via `#[serde(default)]`) to `T::default()`. Mirrors Go, where `null`
/// decodes to a nil slice/map exactly like an absent key.
pub(crate) fn null_default<'de, D, T>(deserializer: D) -> Result<T, D::Error>
where
    D: Deserializer<'de>,
    T: Default + Deserialize<'de>,
{
    Ok(Option::<T>::deserialize(deserializer)?.unwrap_or_default())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde::Deserialize;

    #[derive(Debug, Default, PartialEq, Deserialize)]
    struct Holder {
        #[serde(default, deserialize_with = "null_default")]
        items: Vec<String>,
    }

    #[test]
    fn null_and_missing_both_default() {
        let missing: Holder = serde_yaml_ng::from_str("{}").unwrap();
        assert_eq!(missing, Holder::default());
        let null: Holder = serde_yaml_ng::from_str("items: null").unwrap();
        assert_eq!(null, Holder::default());
        let present: Holder = serde_yaml_ng::from_str("items: [a]").unwrap();
        assert_eq!(present.items, vec!["a".to_string()]);
    }
}
