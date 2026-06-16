//! Kubernetes-style status-condition helpers, ported from
//! `python/packages/jumpstarter/jumpstarter/common/condition.py` (itself a port of
//! `k8s.io/apimachinery .../conditions.go`). These drive the lease acquisition FSM.

use jumpstarter_protocol::v1::Condition;

fn matches(c: &Condition, condition_type: &str, reason: Option<&str>) -> bool {
    c.r#type.as_deref() == Some(condition_type)
        && match reason {
            Some(r) => c.reason.as_deref() == Some(r),
            None => true,
        }
}

/// True iff the first condition of `condition_type` (optionally also matching
/// `reason`) has `status == status`.
pub fn present_and_equal(
    conditions: &[Condition],
    condition_type: &str,
    status: &str,
    reason: Option<&str>,
) -> bool {
    for c in conditions {
        if c.r#type.as_deref() == Some(condition_type)
            && (reason.is_none() || c.reason.as_deref() == reason)
        {
            return c.status.as_deref() == Some(status);
        }
    }
    false
}

/// The `message` of the first condition of `condition_type` (optionally matching
/// `reason`), if present.
pub fn message<'a>(
    conditions: &'a [Condition],
    condition_type: &str,
    reason: Option<&str>,
) -> Option<&'a str> {
    conditions
        .iter()
        .find(|c| matches(c, condition_type, reason))
        .and_then(|c| c.message.as_deref())
}

/// True iff a condition of `condition_type` has status `"True"`.
pub fn is_true(conditions: &[Condition], condition_type: &str) -> bool {
    present_and_equal(conditions, condition_type, "True", None)
}

/// True iff a condition of `condition_type` has status `"False"`.
pub fn is_false(conditions: &[Condition], condition_type: &str) -> bool {
    present_and_equal(conditions, condition_type, "False", None)
}

#[cfg(test)]
pub(crate) fn cond(t: &str, status: &str, reason: Option<&str>, msg: Option<&str>) -> Condition {
    Condition {
        r#type: Some(t.to_string()),
        status: Some(status.to_string()),
        reason: reason.map(String::from),
        message: msg.map(String::from),
        observed_generation: None,
        last_transition_time: None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn true_false_and_message() {
        let cs = vec![
            cond("Ready", "False", Some("Pending"), Some("waiting")),
            cond("Pending", "True", None, Some("scheduling")),
        ];
        assert!(is_false(&cs, "Ready"));
        assert!(!is_true(&cs, "Ready"));
        assert!(is_true(&cs, "Pending"));
        assert_eq!(message(&cs, "Pending", None), Some("scheduling"));
        assert_eq!(message(&cs, "Ready", None), Some("waiting"));
        assert_eq!(message(&cs, "Nope", None), None);
    }

    #[test]
    fn present_and_equal_with_reason() {
        let cs = vec![cond(
            "Unsatisfiable",
            "True",
            Some("NoExporter"),
            Some("none online"),
        )];
        assert!(present_and_equal(
            &cs,
            "Unsatisfiable",
            "True",
            Some("NoExporter")
        ));
        assert!(!present_and_equal(
            &cs,
            "Unsatisfiable",
            "True",
            Some("Other")
        ));
        // Without a reason filter, the first matching type decides.
        assert!(is_true(&cs, "Unsatisfiable"));
    }

    #[test]
    fn first_matching_type_wins() {
        // Mirrors the Python helper: the first condition of the type decides,
        // even if a later one of the same type differs.
        let cs = vec![
            cond("Ready", "True", None, None),
            cond("Ready", "False", None, None),
        ];
        assert!(present_and_equal(&cs, "Ready", "True", None));
    }
}
