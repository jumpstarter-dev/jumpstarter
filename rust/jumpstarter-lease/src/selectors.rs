//! Kubernetes label-selector parsing and matching
//! (`python/.../client/selectors.py`).
//!
//! Used in two places:
//! - [`extract_match_labels_filter`] — the matchLabels-only portion of a selector
//!   is the only part the controller can filter `ListLeases` on server-side; the
//!   matchExpressions are enforced client-side.
//! - [`selector_contains`] — client-side `-l` filtering for `get`/`delete leases`.
//!   Note this is a *containment* test of the lease's stored selector string against
//!   the requirement, NOT an evaluation of labels (spec 08 §7.6).

use std::collections::HashSet;
use std::sync::OnceLock;

use regex::Regex;

/// A parsed label selector: ordered matchLabels plus matchExpressions
/// `(key, operator, values)` with operators `in`/`notin`/`!exists`/`!=`/`exists`.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct LabelSelector {
    /// Insertion-ordered `key=value` equality requirements (Python preserves dict
    /// order, which determines [`extract_match_labels_filter`]'s output order).
    pub match_labels: Vec<(String, String)>,
    pub match_expressions: Vec<(String, String, Vec<String>)>,
}

impl LabelSelector {
    fn label(&self, key: &str) -> Option<&str> {
        self.match_labels
            .iter()
            .find(|(k, _)| k == key)
            .map(|(_, v)| v.as_str())
    }
}

struct Patterns {
    in_op: Regex,
    notin_op: Regex,
    not_exists: Regex,
    not_equal: Regex,
    equal: Regex,
    bare_key: Regex,
}

fn patterns() -> &'static Patterns {
    static P: OnceLock<Patterns> = OnceLock::new();
    P.get_or_init(|| Patterns {
        in_op: Regex::new(r"^([a-zA-Z0-9_./-]+)\s+in\s+\(([^)]*)\)$").unwrap(),
        notin_op: Regex::new(r"^([a-zA-Z0-9_./-]+)\s+notin\s+\(([^)]*)\)$").unwrap(),
        not_exists: Regex::new(r"^!\s*([a-zA-Z0-9_./-]+)$").unwrap(),
        not_equal: Regex::new(r"^([a-zA-Z0-9_./-]+)\s*!=\s*(.+)$").unwrap(),
        equal: Regex::new(r"^([a-zA-Z0-9_./-]+)\s*==?\s*(.+)$").unwrap(),
        bare_key: Regex::new(r"^[a-zA-Z0-9_./-]+$").unwrap(),
    })
}

/// Split a selector on commas that are *not* inside parentheses
/// (Python `re.split(r",(?![^()]*\))", selector)`).
fn split_top_level(selector: &str) -> Vec<&str> {
    let mut parts = Vec::new();
    let mut depth = 0i32;
    let mut start = 0usize;
    for (i, c) in selector.char_indices() {
        match c {
            '(' => depth += 1,
            ')' => {
                if depth > 0 {
                    depth -= 1;
                }
            }
            ',' if depth == 0 => {
                parts.push(&selector[start..i]);
                start = i + 1;
            }
            _ => {}
        }
    }
    parts.push(&selector[start..]);
    parts
}

/// Parse a label selector into matchLabels + matchExpressions
/// (`selectors.py:parse_label_selector`). Unrecognized parts are silently dropped.
pub fn parse_label_selector(selector: &str) -> LabelSelector {
    let mut out = LabelSelector::default();
    if selector.trim().is_empty() {
        return out;
    }
    let p = patterns();
    for part in split_top_level(selector) {
        let part = part.trim();
        if part.is_empty() {
            continue;
        }
        if let Some(c) = p.in_op.captures(part) {
            let values = c[2].split(',').map(|v| v.trim().to_string()).collect();
            out.match_expressions
                .push((c[1].to_string(), "in".to_string(), values));
        } else if let Some(c) = p.notin_op.captures(part) {
            let values = c[2].split(',').map(|v| v.trim().to_string()).collect();
            out.match_expressions
                .push((c[1].to_string(), "notin".to_string(), values));
        } else if let Some(c) = p.not_exists.captures(part) {
            out.match_expressions
                .push((c[1].to_string(), "!exists".to_string(), Vec::new()));
        } else if let Some(c) = p.not_equal.captures(part) {
            out.match_expressions.push((
                c[1].to_string(),
                "!=".to_string(),
                vec![c[2].trim().to_string()],
            ));
        } else if let Some(c) = p.equal.captures(part) {
            out.match_labels
                .push((c[1].to_string(), c[2].trim().to_string()));
        } else if p.bare_key.is_match(part) {
            out.match_expressions
                .push((part.to_string(), "exists".to_string(), Vec::new()));
        }
    }
    out
}

/// Extract only the matchLabels portion of a selector, re-rendered as
/// `k=v,k=v` (insertion order), or `None` if there are none. This is the
/// server-filterable subset sent to `ListLeases` (`selectors.py:54-67`).
pub fn extract_match_labels_filter(selector: Option<&str>) -> Option<String> {
    let selector = selector?;
    if selector.is_empty() {
        return None;
    }
    let parsed = parse_label_selector(selector);
    if parsed.match_labels.is_empty() {
        return None;
    }
    Some(
        parsed
            .match_labels
            .iter()
            .map(|(k, v)| format!("{k}={v}"))
            .collect::<Vec<_>>()
            .join(","),
    )
}

/// Whether `selector` contains every requirement in `requirements`
/// (`selectors.py:selector_contains`). Empty/blank requirements match everything.
pub fn selector_contains(selector: &str, requirements: &str) -> bool {
    if requirements.trim().is_empty() {
        return true;
    }
    let req = parse_label_selector(requirements);
    let sel = parse_label_selector(selector);

    // All required matchLabels must be present with the same value.
    for (key, value) in &req.match_labels {
        if sel.label(key) != Some(value.as_str()) {
            return false;
        }
    }

    // All required matchExpressions must appear with same key, operator, and
    // set-equal values.
    for (r_key, r_op, r_vals) in &req.match_expressions {
        let r_set: HashSet<&str> = r_vals.iter().map(String::as_str).collect();
        let found = sel.match_expressions.iter().any(|(s_key, s_op, s_vals)| {
            s_key == r_key
                && s_op == r_op
                && s_vals.iter().map(String::as_str).collect::<HashSet<_>>() == r_set
        });
        if !found {
            return false;
        }
    }
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_match_labels_and_expressions() {
        let s = parse_label_selector("board=rk3588, env != prod, region in (us, eu), !legacy, gpu");
        assert_eq!(s.match_labels, vec![("board".into(), "rk3588".into())]);
        assert_eq!(
            s.match_expressions,
            vec![
                ("env".into(), "!=".into(), vec!["prod".into()]),
                ("region".into(), "in".into(), vec!["us".into(), "eu".into()]),
                ("legacy".into(), "!exists".into(), vec![]),
                ("gpu".into(), "exists".into(), vec![]),
            ]
        );
    }

    #[test]
    fn double_equals_is_match_label() {
        let s = parse_label_selector("a==b");
        assert_eq!(s.match_labels, vec![("a".into(), "b".into())]);
    }

    #[test]
    fn empty_selector_is_empty() {
        assert_eq!(parse_label_selector("   "), LabelSelector::default());
    }

    #[test]
    fn extract_only_match_labels() {
        assert_eq!(
            extract_match_labels_filter(Some("board=rpi,env=test,region in (us)")).as_deref(),
            Some("board=rpi,env=test")
        );
        assert_eq!(extract_match_labels_filter(Some("!legacy")), None);
        assert_eq!(extract_match_labels_filter(Some("")), None);
        assert_eq!(extract_match_labels_filter(None), None);
    }

    #[test]
    fn contains_is_true_for_blank_requirements() {
        assert!(selector_contains("board=x", ""));
        assert!(selector_contains("board=x", "   "));
    }

    #[test]
    fn contains_checks_labels_and_expressions() {
        let sel = "board=rk3588,region in (us, eu)";
        assert!(selector_contains(sel, "board=rk3588"));
        assert!(selector_contains(sel, "region in (eu, us)")); // set-equal, order-insensitive
        assert!(!selector_contains(sel, "board=other"));
        assert!(!selector_contains(sel, "region in (us)")); // not set-equal
        assert!(!selector_contains(sel, "missing=1"));
    }

    // Ported from the deleted Python `client/selectors_test.py`: a `!exists`
    // requirement and whitespace tolerance around `=`.
    #[test]
    fn contains_not_exists_requirement() {
        assert!(selector_contains("board=rpi,!experimental", "!experimental"));
        assert!(!selector_contains("board=rpi", "!experimental"));
    }

    #[test]
    fn contains_tolerates_whitespace_around_equals() {
        assert!(selector_contains("board=rpi", "board = rpi"));
        assert!(selector_contains("board=rpi", "board =rpi"));
    }
}
