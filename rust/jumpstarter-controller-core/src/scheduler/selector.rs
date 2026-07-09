//! Label-selector parsing, matching, and formatting — cluster-free ports of the
//! Kubernetes `k8s.io/apimachinery/pkg/labels` selector machinery plus the
//! controller's own `ParseLabelSelector` (go: lease_helpers.go:83-175).
//!
//! Three surfaces are reproduced here, all wire-visible:
//!
//! - [`parse_label_selector`] — port of `ParseLabelSelector`
//!   (go: lease_helpers.go:83-175), which parses a selector *string* into a
//!   `metav1.LabelSelector`, accumulating `!=` requirements for the same key
//!   into a single `NotIn` (the "!= bug fix") and rejecting duplicate equality
//!   requirements with conflicting values. It builds on a faithful port of the
//!   `labels` package lexer/parser (go: selector.go:520-930).
//! - [`selector_matches`] / [`selector_is_empty`] — the evaluation side of
//!   `metav1.LabelSelectorAsSelector(...).Matches(...)`
//!   (go: helpers.go:36-72, selector.go), used by the scheduler to test whether
//!   an exporter/client matches a selector.
//! - [`format_label_selector`] — port of `metav1.FormatLabelSelector`
//!   (go: helpers.go:171-182), whose output crosses the wire in the
//!   `SelectorMismatch` condition message and `Lease.ToProtobuf`. Requirements
//!   are sorted by key (`ByKey`), values sorted within `In`/`NotIn`, empty
//!   selectors render as `"<none>"`, invalid operators as `"<error>"`.
//!
//! Verified against `k8s.io/apimachinery@v0.35.0` (the version pinned by
//! `controller/go.mod`).

use std::collections::{BTreeMap, BTreeSet};

use k8s_openapi::apimachinery::pkg::apis::meta::v1::{LabelSelector, LabelSelectorRequirement};

// Operator string constants as they appear in `LabelSelectorRequirement.operator`.
// go: k8s.io/apimachinery/pkg/apis/meta/v1/types.go (LabelSelectorOperator).
const OP_IN: &str = "In";
const OP_NOT_IN: &str = "NotIn";
const OP_EXISTS: &str = "Exists";
const OP_DOES_NOT_EXIST: &str = "DoesNotExist";

// ---------------------------------------------------------------------------
// Matching (LabelSelectorAsSelector(...).Matches)
// ---------------------------------------------------------------------------

/// Whether the selector selects everything (matches every object), i.e. the Go
/// `labels.Selector.Empty()` on the result of `LabelSelectorAsSelector`: true
/// when there are neither `matchLabels` nor `matchExpressions`.
///
/// go: helpers.go:40-42 (`len(ps.MatchLabels)+len(ps.MatchExpressions) == 0`)
pub fn selector_is_empty(selector: &LabelSelector) -> bool {
    selector
        .match_labels
        .as_ref()
        .is_none_or(BTreeMap::is_empty)
        && selector
            .match_expressions
            .as_ref()
            .is_none_or(Vec::is_empty)
}

/// Whether `labels` satisfy every requirement of `selector`, mirroring
/// `metav1.LabelSelectorAsSelector(selector).Matches(labels)`.
///
/// An empty selector matches everything. An unknown operator makes the whole
/// selector fail to match (Go surfaces that as a conversion *error* at
/// `LabelSelectorAsSelector`, which the scheduler never reaches for
/// CRD-validated selectors; treating it as a non-match keeps this function
/// total).
///
/// go: helpers.go:36-72 + selector.go (`Requirement.Matches`)
pub fn selector_matches(selector: &LabelSelector, labels: &BTreeMap<String, String>) -> bool {
    if let Some(match_labels) = &selector.match_labels {
        for (key, value) in match_labels {
            if labels.get(key) != Some(value) {
                return false;
            }
        }
    }
    if let Some(exprs) = &selector.match_expressions {
        for expr in exprs {
            if !requirement_matches(expr, labels) {
                return false;
            }
        }
    }
    true
}

fn requirement_matches(expr: &LabelSelectorRequirement, labels: &BTreeMap<String, String>) -> bool {
    let values = expr.values.as_deref().unwrap_or(&[]);
    match expr.operator.as_str() {
        OP_IN => labels
            .get(&expr.key)
            .is_some_and(|v| values.iter().any(|x| x == v)),
        OP_NOT_IN => labels
            .get(&expr.key)
            .is_none_or(|v| !values.iter().any(|x| x == v)),
        OP_EXISTS => labels.contains_key(&expr.key),
        OP_DOES_NOT_EXIST => !labels.contains_key(&expr.key),
        _ => false,
    }
}

// ---------------------------------------------------------------------------
// Formatting (FormatLabelSelector)
// ---------------------------------------------------------------------------

/// Port of `metav1.FormatLabelSelector` (go: helpers.go:171-182): renders the
/// selector as its `labels.Selector.String()` form — requirements sorted by
/// key, values sorted within `In`/`NotIn` — returning `"<none>"` for the empty
/// selector and `"<error>"` if any expression carries an unsupported operator.
///
/// go: helpers.go:171-182, selector.go:344-437
pub fn format_label_selector(selector: &LabelSelector) -> String {
    // Build (key, rendered-requirement) pairs, then sort by key (`ByKey`).
    let mut requirements: Vec<(String, String)> = Vec::new();

    if let Some(match_labels) = &selector.match_labels {
        for (key, value) in match_labels {
            // matchLabels become Equals requirements: "key=value".
            requirements.push((key.clone(), format!("{key}={value}")));
        }
    }

    if let Some(exprs) = &selector.match_expressions {
        for expr in exprs {
            let rendered = match expr.operator.as_str() {
                OP_IN => format!("{} in ({})", expr.key, format_values(expr)),
                OP_NOT_IN => format!("{} notin ({})", expr.key, format_values(expr)),
                OP_EXISTS => expr.key.clone(),
                OP_DOES_NOT_EXIST => format!("!{}", expr.key),
                _ => return "<error>".to_owned(),
            };
            requirements.push((expr.key.clone(), rendered));
        }
    }

    if requirements.is_empty() {
        return "<none>".to_owned();
    }

    // `sort.Sort(ByKey)` orders by key; a stable sort matches it for the
    // distinct-key case and is deterministic when keys repeat.
    requirements.sort_by(|a, b| a.0.cmp(&b.0));
    requirements
        .into_iter()
        .map(|(_, rendered)| rendered)
        .collect::<Vec<_>>()
        .join(",")
}

/// Joins an `In`/`NotIn` requirement's values with `,`, sorting when there is
/// more than one (`safeSort`), matching `Requirement.String()`.
fn format_values(expr: &LabelSelectorRequirement) -> String {
    let mut values: Vec<&str> = expr
        .values
        .as_deref()
        .unwrap_or(&[])
        .iter()
        .map(String::as_str)
        .collect();
    if values.len() > 1 {
        values.sort_unstable();
    }
    values.join(",")
}

// ---------------------------------------------------------------------------
// String parsing (ParseLabelSelector + the labels lexer/parser)
// ---------------------------------------------------------------------------

/// Error from [`parse_label_selector`]. Messages are worded like the Go
/// originals so the wire-visible strings match.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum ParseLabelSelectorError {
    /// A lexer/parser error, wrapped like Go's
    /// `fmt.Errorf("failed to parse label selector: %w", err)`.
    #[error("failed to parse label selector: {0}")]
    Parse(String),
    /// The same key received two `=` requirements with differing values.
    /// go: lease_helpers.go:114
    #[error("invalid selector: label {key} cannot have multiple equality requirements with different values ({existing} and {new})")]
    ConflictingEquality {
        /// The offending label key.
        key: String,
        /// The value already recorded for the key.
        existing: String,
        /// The conflicting new value.
        new: String,
    },
    /// A `!=` requirement carried other than exactly one value.
    /// go: lease_helpers.go:128
    #[error("invalid selector: != operator requires exactly one value")]
    NotEqualsArity,
    /// An operator the controller's converter does not handle (e.g. `==`, `>`).
    /// go: lease_helpers.go:158
    #[error("unsupported label selector operator: {0}")]
    UnsupportedOperator(String),
}

/// Port of `ParseLabelSelector` (go: lease_helpers.go:83-175): parses a
/// selector *string* into a `metav1.LabelSelector`.
///
/// Notable ported behaviors:
/// - `!=` requirements for the same key accumulate into a single `NotIn`
///   expression, de-duplicating values while preserving first-seen order.
/// - a lone `=` with a single value goes into `matchLabels`; a repeated `=`
///   on the same key with a *different* value is rejected, with the *same*
///   value accepted idempotently.
/// - `In`/`NotIn`/`Exists`/`DoesNotExist` become `matchExpressions`.
///
/// go: lease_helpers.go:83-175
pub fn parse_label_selector(input: &str) -> Result<LabelSelector, ParseLabelSelectorError> {
    // First parse with the labels-package grammar (supports `!=`).
    let requirements = parse_requirements(input).map_err(ParseLabelSelectorError::Parse)?;

    let mut match_labels: BTreeMap<String, String> = BTreeMap::new();
    let mut match_expressions: Vec<LabelSelectorRequirement> = Vec::new();
    // Accumulate `!=` requirements by key, preserving insertion order.
    let mut not_equals_keys: Vec<String> = Vec::new();
    let mut not_equals_by_key: BTreeMap<String, Vec<String>> = BTreeMap::new();

    for req in requirements {
        match req.op {
            Op::Equals => {
                // For exact match with a single value, use matchLabels; multiple
                // values would use In (labels grammar only yields one for `=`).
                if req.values.len() == 1 {
                    let value = req.values.into_iter().next().unwrap();
                    if let Some(existing) = match_labels.get(&req.key) {
                        if *existing != value {
                            return Err(ParseLabelSelectorError::ConflictingEquality {
                                key: req.key,
                                existing: existing.clone(),
                                new: value,
                            });
                        }
                    }
                    match_labels.insert(req.key, value);
                } else {
                    match_expressions.push(LabelSelectorRequirement {
                        key: req.key,
                        operator: OP_IN.to_owned(),
                        values: Some(req.values),
                    });
                }
            }
            Op::NotEquals => {
                if req.values.len() != 1 {
                    return Err(ParseLabelSelectorError::NotEqualsArity);
                }
                let value = req.values.into_iter().next().unwrap();
                let entry = not_equals_by_key.entry(req.key.clone()).or_insert_with(|| {
                    not_equals_keys.push(req.key.clone());
                    Vec::new()
                });
                if !entry.contains(&value) {
                    entry.push(value);
                }
            }
            Op::In => match_expressions.push(LabelSelectorRequirement {
                key: req.key,
                operator: OP_IN.to_owned(),
                values: Some(req.values),
            }),
            Op::NotIn => match_expressions.push(LabelSelectorRequirement {
                key: req.key,
                operator: OP_NOT_IN.to_owned(),
                values: Some(req.values),
            }),
            Op::Exists => match_expressions.push(LabelSelectorRequirement {
                key: req.key,
                operator: OP_EXISTS.to_owned(),
                values: Some(Vec::new()),
            }),
            Op::DoesNotExist => match_expressions.push(LabelSelectorRequirement {
                key: req.key,
                operator: OP_DOES_NOT_EXIST.to_owned(),
                values: Some(Vec::new()),
            }),
            Op::DoubleEquals | Op::GreaterThan | Op::LessThan => {
                return Err(ParseLabelSelectorError::UnsupportedOperator(
                    req.op.selection_name().to_owned(),
                ));
            }
        }
    }

    // Emit accumulated NotEquals as NotIn expressions (go: lease_helpers.go:162-169).
    for key in not_equals_keys {
        let values = not_equals_by_key.remove(&key).unwrap_or_default();
        match_expressions.push(LabelSelectorRequirement {
            key,
            operator: OP_NOT_IN.to_owned(),
            values: Some(values),
        });
    }

    Ok(LabelSelector {
        match_labels: (!match_labels.is_empty()).then_some(match_labels),
        match_expressions: (!match_expressions.is_empty()).then_some(match_expressions),
    })
}

// ---------------------------------------------------------------------------
// labels-package lexer + recursive-descent parser
// (go: k8s.io/apimachinery/pkg/labels/selector.go:520-930)
// ---------------------------------------------------------------------------

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Token {
    Error,
    EndOfString,
    ClosedPar,
    Comma,
    DoesNotExist,
    DoubleEquals,
    Equals,
    GreaterThan,
    Identifier,
    In,
    LessThan,
    NotEquals,
    NotIn,
    OpenPar,
}

/// Selection operators, mirroring `k8s.io/apimachinery/pkg/selection`.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Op {
    In,
    NotIn,
    Equals,
    DoubleEquals,
    NotEquals,
    Exists,
    DoesNotExist,
    GreaterThan,
    LessThan,
}

impl Op {
    /// The `selection.Operator` string, as it appears in Go error messages.
    fn selection_name(self) -> &'static str {
        match self {
            Op::In => "in",
            Op::NotIn => "notin",
            Op::Equals => "=",
            Op::DoubleEquals => "==",
            Op::NotEquals => "!=",
            Op::Exists => "exists",
            Op::DoesNotExist => "!",
            Op::GreaterThan => "gt",
            Op::LessThan => "lt",
        }
    }
}

struct Requirement {
    key: String,
    op: Op,
    values: Vec<String>,
}

fn string_to_token(s: &str) -> Option<Token> {
    Some(match s {
        ")" => Token::ClosedPar,
        "," => Token::Comma,
        "!" => Token::DoesNotExist,
        "==" => Token::DoubleEquals,
        "=" => Token::Equals,
        ">" => Token::GreaterThan,
        "in" => Token::In,
        "<" => Token::LessThan,
        "!=" => Token::NotEquals,
        "notin" => Token::NotIn,
        "(" => Token::OpenPar,
        _ => return None,
    })
}

fn is_whitespace(ch: u8) -> bool {
    matches!(ch, b' ' | b'\t' | b'\r' | b'\n')
}

fn is_special_symbol(ch: u8) -> bool {
    matches!(ch, b'=' | b'!' | b'(' | b')' | b',' | b'>' | b'<')
}

struct Lexer<'a> {
    s: &'a [u8],
    pos: usize,
}

impl<'a> Lexer<'a> {
    fn read(&mut self) -> u8 {
        if self.pos < self.s.len() {
            let b = self.s[self.pos];
            self.pos += 1;
            b
        } else {
            self.pos += 1;
            0
        }
    }

    fn unread(&mut self) {
        self.pos -= 1;
    }

    fn scan_id_or_keyword(&mut self) -> (Token, String) {
        let mut buffer: Vec<u8> = Vec::new();
        loop {
            let ch = self.read();
            if ch == 0 {
                break;
            } else if is_special_symbol(ch) || is_whitespace(ch) {
                self.unread();
                break;
            } else {
                buffer.push(ch);
            }
        }
        let s = String::from_utf8_lossy(&buffer).into_owned();
        match string_to_token(&s) {
            Some(tok) => (tok, s),
            None => (Token::Identifier, s),
        }
    }

    fn scan_special_symbol(&mut self) -> (Token, String) {
        let mut last: Option<(Token, String)> = None;
        let mut buffer: Vec<u8> = Vec::new();
        loop {
            let ch = self.read();
            if ch == 0 {
                break;
            } else if is_special_symbol(ch) {
                buffer.push(ch);
                let cur = String::from_utf8_lossy(&buffer).into_owned();
                if let Some(tok) = string_to_token(&cur) {
                    last = Some((tok, cur));
                } else if last.is_some() {
                    self.unread();
                    break;
                }
            } else {
                self.unread();
                break;
            }
        }
        match last {
            Some(item) => item,
            None => (
                Token::Error,
                format!(
                    "error expected: keyword found '{}'",
                    String::from_utf8_lossy(&buffer)
                ),
            ),
        }
    }

    fn skip_whitespaces(&mut self, mut ch: u8) -> u8 {
        loop {
            if !is_whitespace(ch) {
                return ch;
            }
            ch = self.read();
        }
    }

    fn lex(&mut self) -> (Token, String) {
        let first = self.read();
        let ch = self.skip_whitespaces(first);
        if ch == 0 {
            (Token::EndOfString, String::new())
        } else if is_special_symbol(ch) {
            self.unread();
            self.scan_special_symbol()
        } else {
            self.unread();
            self.scan_id_or_keyword()
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum ParserContext {
    KeyAndOperator,
    Values,
}

struct ScannedItem {
    tok: Token,
    literal: String,
}

struct Parser {
    scanned: Vec<ScannedItem>,
    position: usize,
}

impl Parser {
    fn contextualize(context: ParserContext, tok: Token) -> Token {
        if context == ParserContext::Values && matches!(tok, Token::In | Token::NotIn) {
            Token::Identifier
        } else {
            tok
        }
    }

    fn lookahead(&self, context: ParserContext) -> (Token, String) {
        let item = &self.scanned[self.position];
        (Self::contextualize(context, item.tok), item.literal.clone())
    }

    fn consume(&mut self, context: ParserContext) -> (Token, String) {
        self.position += 1;
        let item = &self.scanned[self.position - 1];
        (Self::contextualize(context, item.tok), item.literal.clone())
    }

    fn parse(&mut self) -> Result<Vec<Requirement>, String> {
        let mut requirements: Vec<Requirement> = Vec::new();
        loop {
            let (tok, lit) = self.lookahead(ParserContext::Values);
            match tok {
                Token::Identifier | Token::DoesNotExist => {
                    let r = self
                        .parse_requirement()
                        .map_err(|e| format!("unable to parse requirement: {e}"))?;
                    requirements.push(r);
                    let (t, l) = self.consume(ParserContext::Values);
                    match t {
                        Token::EndOfString => return Ok(requirements),
                        Token::Comma => {
                            let (t2, l2) = self.lookahead(ParserContext::Values);
                            if t2 != Token::Identifier && t2 != Token::DoesNotExist {
                                return Err(format!(
                                    "found '{l2}', expected: identifier after ','"
                                ));
                            }
                        }
                        _ => {
                            return Err(format!("found '{l}', expected: ',' or 'end of string'"));
                        }
                    }
                }
                Token::EndOfString => return Ok(requirements),
                _ => {
                    return Err(format!(
                        "found '{lit}', expected: !, identifier, or 'end of string'"
                    ));
                }
            }
        }
    }

    fn parse_requirement(&mut self) -> Result<Requirement, String> {
        let (key, operator) = self.parse_key_and_infer_operator()?;
        if operator == Op::Exists || operator == Op::DoesNotExist {
            return Ok(Requirement {
                key,
                op: operator,
                values: Vec::new(),
            });
        }
        let operator = self.parse_operator()?;
        let values = match operator {
            Op::In | Op::NotIn => self.parse_values()?,
            _ => self.parse_exact_value()?,
        };
        Ok(Requirement {
            key,
            op: operator,
            values,
        })
    }

    fn parse_key_and_infer_operator(&mut self) -> Result<(String, Op), String> {
        let mut operator = Op::Equals; // placeholder; overwritten below
        let mut inferred_does_not_exist = false;
        let (mut tok, mut literal) = self.consume(ParserContext::Values);
        if tok == Token::DoesNotExist {
            inferred_does_not_exist = true;
            operator = Op::DoesNotExist;
            let next = self.consume(ParserContext::Values);
            tok = next.0;
            literal = next.1;
        }
        if tok != Token::Identifier {
            return Err(format!("found '{literal}', expected: identifier"));
        }
        validate_label_key(&literal)?;
        // If nothing follows the key (end / comma), infer Exists — unless we
        // already inferred DoesNotExist from a leading `!`. Otherwise leave the
        // placeholder in `operator`; `parse_operator` will read the real binary
        // operator next (the placeholder is never observed by the caller).
        let (t, _) = self.lookahead(ParserContext::Values);
        if (t == Token::EndOfString || t == Token::Comma) && !inferred_does_not_exist {
            operator = Op::Exists;
        }
        Ok((literal, operator))
    }

    fn parse_operator(&mut self) -> Result<Op, String> {
        let (tok, lit) = self.consume(ParserContext::KeyAndOperator);
        let op = match tok {
            Token::In => Op::In,
            Token::Equals => Op::Equals,
            Token::DoubleEquals => Op::DoubleEquals,
            Token::GreaterThan => Op::GreaterThan,
            Token::LessThan => Op::LessThan,
            Token::NotIn => Op::NotIn,
            Token::NotEquals => Op::NotEquals,
            _ => {
                return Err(format!(
                    "found '{lit}', expected: in, notin, =, ==, !=, gt, lt"
                ));
            }
        };
        Ok(op)
    }

    fn parse_values(&mut self) -> Result<Vec<String>, String> {
        let (tok, lit) = self.consume(ParserContext::Values);
        if tok != Token::OpenPar {
            return Err(format!("found '{lit}' expected: '('"));
        }
        let (tok, lit) = self.lookahead(ParserContext::Values);
        match tok {
            Token::Identifier | Token::Comma => {
                let set = self.parse_identifiers_list()?;
                let (closing, _) = self.consume(ParserContext::Values);
                if closing != Token::ClosedPar {
                    return Err(format!("found '{lit}', expected: ')'"));
                }
                Ok(set)
            }
            Token::ClosedPar => {
                self.consume(ParserContext::Values);
                Ok(vec![String::new()])
            }
            _ => Err(format!("found '{lit}', expected: ',', ')' or identifier")),
        }
    }

    fn parse_identifiers_list(&mut self) -> Result<Vec<String>, String> {
        let mut set: BTreeSet<String> = BTreeSet::new();
        loop {
            let (tok, lit) = self.consume(ParserContext::Values);
            match tok {
                Token::Identifier => {
                    set.insert(lit);
                    let (tok2, lit2) = self.lookahead(ParserContext::Values);
                    match tok2 {
                        Token::Comma => continue,
                        Token::ClosedPar => return Ok(set.into_iter().collect()),
                        _ => return Err(format!("found '{lit2}', expected: ',' or ')'")),
                    }
                }
                Token::Comma => {
                    if set.is_empty() {
                        set.insert(String::new());
                    }
                    let (tok2, _) = self.lookahead(ParserContext::Values);
                    if tok2 == Token::ClosedPar {
                        set.insert(String::new());
                        return Ok(set.into_iter().collect());
                    }
                    if tok2 == Token::Comma {
                        set.insert(String::new());
                    }
                }
                _ => return Err(format!("found '{lit}', expected: ',', or identifier")),
            }
        }
    }

    fn parse_exact_value(&mut self) -> Result<Vec<String>, String> {
        let (tok, _) = self.lookahead(ParserContext::Values);
        if tok == Token::EndOfString || tok == Token::Comma {
            return Ok(vec![String::new()]);
        }
        let (tok, lit) = self.consume(ParserContext::Values);
        if tok == Token::Identifier {
            return Ok(vec![lit]);
        }
        Err(format!("found '{lit}', expected: identifier"))
    }
}

/// Validate a label key like Go's `validateLabelKey` (a qualified name,
/// optionally `prefix/name`). Kept permissive but rejecting clearly-invalid
/// keys; the tested selectors all use simple valid keys.
///
/// go: selector.go `validateLabelKey` → `content.IsLabelKey`
fn validate_label_key(key: &str) -> Result<(), String> {
    if is_qualified_name(key) {
        Ok(())
    } else {
        Err(format!(
            "key: Invalid value: {key:?}: name part must be non-empty"
        ))
    }
}

/// Anchored qualified-name check: `[prefix/]name`, each segment matching
/// `([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9]` with the DNS-subdomain prefix
/// rule, byte-wise like Go regexp on UTF-8.
fn is_qualified_name(value: &str) -> bool {
    let (prefix, name) = match value.split_once('/') {
        Some((p, n)) => (Some(p), n),
        None => (None, value),
    };
    if let Some(prefix) = prefix {
        if prefix.is_empty() || prefix.len() > 253 || !is_dns1123_subdomain(prefix) {
            return false;
        }
    }
    !name.is_empty() && name.len() <= 63 && matches_qualified_name_segment(name)
}

fn matches_qualified_name_segment(value: &str) -> bool {
    fn ext(c: u8) -> bool {
        c.is_ascii_alphanumeric() || matches!(c, b'-' | b'_' | b'.')
    }
    match value.as_bytes() {
        [] => false,
        [c] => c.is_ascii_alphanumeric(),
        [first, mid @ .., last] => {
            first.is_ascii_alphanumeric()
                && last.is_ascii_alphanumeric()
                && mid.iter().copied().all(ext)
        }
    }
}

fn is_dns1123_subdomain(value: &str) -> bool {
    fn lower_alnum(c: u8) -> bool {
        c.is_ascii_lowercase() || c.is_ascii_digit()
    }
    !value.is_empty()
        && value.split('.').all(|label| match label.as_bytes() {
            [] => false,
            [c] => lower_alnum(*c),
            [first, mid @ .., last] => {
                lower_alnum(*first)
                    && lower_alnum(*last)
                    && mid.iter().all(|&c| lower_alnum(c) || c == b'-')
            }
        })
}

/// Runs the labels lexer + parser over `selector`, returning parsed
/// requirements sorted by key (go: selector.go `parse` → `sort.Sort(ByKey)`).
fn parse_requirements(selector: &str) -> Result<Vec<Requirement>, String> {
    let mut lexer = Lexer {
        s: selector.as_bytes(),
        pos: 0,
    };
    let mut scanned: Vec<ScannedItem> = Vec::new();
    loop {
        let (tok, literal) = lexer.lex();
        let is_end = tok == Token::EndOfString;
        // Surface lexer errors immediately, matching the parser reaching an
        // Error token where an identifier/operator was expected.
        if tok == Token::Error {
            return Err(format!("found '{literal}', expected: identifier"));
        }
        scanned.push(ScannedItem { tok, literal });
        if is_end {
            break;
        }
    }

    let mut parser = Parser {
        scanned,
        position: 0,
    };
    let mut requirements = parser.parse()?;
    requirements.sort_by(|a, b| a.key.cmp(&b.key));
    Ok(requirements)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ls(match_labels: &[(&str, &str)], exprs: Vec<LabelSelectorRequirement>) -> LabelSelector {
        LabelSelector {
            match_labels: (!match_labels.is_empty()).then(|| {
                match_labels
                    .iter()
                    .map(|(k, v)| (k.to_string(), v.to_string()))
                    .collect()
            }),
            match_expressions: (!exprs.is_empty()).then_some(exprs),
        }
    }

    fn labels(pairs: &[(&str, &str)]) -> BTreeMap<String, String> {
        pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect()
    }

    // helper: extract the sole matchExpression
    fn only_expr(sel: &LabelSelector) -> &LabelSelectorRequirement {
        let exprs = sel.match_expressions.as_ref().expect("match_expressions");
        assert_eq!(exprs.len(), 1, "expected exactly one expression");
        &exprs[0]
    }

    fn match_label(sel: &LabelSelector, key: &str) -> Option<String> {
        sel.match_labels.as_ref().and_then(|m| m.get(key).cloned())
    }

    // -- ParseLabelSelector (go: lease_helpers_test.go:40-337) ---------------

    // go: lease_helpers_test.go:42-48
    #[test]
    fn parse_single_key_value() {
        let sel = parse_label_selector("app=myapp").unwrap();
        assert_eq!(match_label(&sel, "app").as_deref(), Some("myapp"));
        assert!(sel.match_expressions.is_none());
    }

    // go: lease_helpers_test.go:50-57
    #[test]
    fn parse_multiple_key_values() {
        let sel = parse_label_selector("app=myapp,env=prod").unwrap();
        assert_eq!(match_label(&sel, "app").as_deref(), Some("myapp"));
        assert_eq!(match_label(&sel, "env").as_deref(), Some("prod"));
        assert!(sel.match_expressions.is_none());
    }

    // go: lease_helpers_test.go:59-65
    #[test]
    fn parse_with_spaces() {
        let sel = parse_label_selector("app = myapp , env = prod").unwrap();
        assert_eq!(match_label(&sel, "app").as_deref(), Some("myapp"));
        assert_eq!(match_label(&sel, "env").as_deref(), Some("prod"));
    }

    // go: lease_helpers_test.go:69-77
    #[test]
    fn parse_not_equals() {
        let sel = parse_label_selector("revision!=v3").unwrap();
        let expr = only_expr(&sel);
        assert_eq!(expr.key, "revision");
        assert_eq!(expr.operator, OP_NOT_IN);
        assert_eq!(expr.values.as_deref(), Some(&["v3".to_string()][..]));
    }

    // go: lease_helpers_test.go:79-88
    #[test]
    fn parse_not_equals_with_other() {
        let sel = parse_label_selector("board-type=qc8775,revision!=v3").unwrap();
        assert_eq!(match_label(&sel, "board-type").as_deref(), Some("qc8775"));
        let expr = only_expr(&sel);
        assert_eq!(expr.key, "revision");
        assert_eq!(expr.operator, OP_NOT_IN);
        assert_eq!(expr.values.as_deref(), Some(&["v3".to_string()][..]));
    }

    // go: lease_helpers_test.go:90-114
    #[test]
    fn parse_multiple_not_equals() {
        let sel = parse_label_selector("revision!=v3,board-type!=qc8774").unwrap();
        let exprs = sel.match_expressions.unwrap();
        assert_eq!(exprs.len(), 2);
        let rev = exprs.iter().find(|e| e.key == "revision").unwrap();
        let board = exprs.iter().find(|e| e.key == "board-type").unwrap();
        assert_eq!(rev.operator, OP_NOT_IN);
        assert_eq!(rev.values.as_deref(), Some(&["v3".to_string()][..]));
        assert_eq!(board.operator, OP_NOT_IN);
        assert_eq!(board.values.as_deref(), Some(&["qc8774".to_string()][..]));
    }

    // go: lease_helpers_test.go:118-126
    #[test]
    fn parse_in_operator() {
        let sel = parse_label_selector("env in (prod,staging)").unwrap();
        let expr = only_expr(&sel);
        assert_eq!(expr.key, "env");
        assert_eq!(expr.operator, OP_IN);
        let values = expr.values.clone().unwrap();
        assert!(values.contains(&"prod".to_string()));
        assert!(values.contains(&"staging".to_string()));
    }

    // go: lease_helpers_test.go:128-136
    #[test]
    fn parse_notin_operator() {
        let sel = parse_label_selector("env notin (dev,test)").unwrap();
        let expr = only_expr(&sel);
        assert_eq!(expr.key, "env");
        assert_eq!(expr.operator, OP_NOT_IN);
        let values = expr.values.clone().unwrap();
        assert!(values.contains(&"dev".to_string()));
        assert!(values.contains(&"test".to_string()));
    }

    // go: lease_helpers_test.go:140-148
    #[test]
    fn parse_exists() {
        let sel = parse_label_selector("app").unwrap();
        let expr = only_expr(&sel);
        assert_eq!(expr.key, "app");
        assert_eq!(expr.operator, OP_EXISTS);
        assert_eq!(expr.values.as_deref(), Some(&[][..]));
    }

    // go: lease_helpers_test.go:150-158
    #[test]
    fn parse_does_not_exist() {
        let sel = parse_label_selector("!app").unwrap();
        let expr = only_expr(&sel);
        assert_eq!(expr.key, "app");
        assert_eq!(expr.operator, OP_DOES_NOT_EXIST);
        assert_eq!(expr.values.as_deref(), Some(&[][..]));
    }

    // go: lease_helpers_test.go:162-170
    #[test]
    fn parse_mixed_labels_and_expressions() {
        let sel = parse_label_selector("app=myapp,env!=prod").unwrap();
        assert_eq!(match_label(&sel, "app").as_deref(), Some("myapp"));
        let expr = only_expr(&sel);
        assert_eq!(expr.key, "env");
        assert_eq!(expr.operator, OP_NOT_IN);
    }

    // go: lease_helpers_test.go:172-178
    #[test]
    fn parse_all_operator_types() {
        let sel =
            parse_label_selector("app=myapp,revision!=v3,env in (prod,staging),!debug").unwrap();
        assert_eq!(match_label(&sel, "app").as_deref(), Some("myapp"));
        assert_eq!(sel.match_expressions.as_ref().unwrap().len(), 3);
    }

    // go: lease_helpers_test.go:182-188
    #[test]
    fn parse_empty() {
        let sel = parse_label_selector("").unwrap();
        assert!(sel.match_labels.is_none());
        assert!(sel.match_expressions.is_none());
    }

    // go: lease_helpers_test.go:190-196
    #[test]
    fn parse_special_chars_in_values() {
        let sel = parse_label_selector("version=v1.2.3,label=my-label").unwrap();
        assert_eq!(match_label(&sel, "version").as_deref(), Some("v1.2.3"));
        assert_eq!(match_label(&sel, "label").as_deref(), Some("my-label"));
    }

    // go: lease_helpers_test.go:198-204
    #[test]
    fn parse_underscores_in_keys() {
        let sel = parse_label_selector("board_type=qc8775,device_id=123").unwrap();
        assert_eq!(match_label(&sel, "board_type").as_deref(), Some("qc8775"));
        assert_eq!(match_label(&sel, "device_id").as_deref(), Some("123"));
    }

    // go: lease_helpers_test.go:208-212
    #[test]
    fn parse_invalid_syntax_errors() {
        assert!(parse_label_selector("invalid===syntax").is_err());
    }

    // go: lease_helpers_test.go:214-220
    #[test]
    fn parse_conflicting_equality_errors() {
        let err = parse_label_selector("a=1,a=2").unwrap_err();
        let msg = err.to_string();
        assert!(
            msg.contains("cannot have multiple equality requirements"),
            "{msg}"
        );
        assert!(msg.contains('a'), "{msg}");
    }

    // go: lease_helpers_test.go:222-227
    #[test]
    fn parse_repeated_equality_same_value_ok() {
        let sel = parse_label_selector("a=1,a=1").unwrap();
        assert_eq!(match_label(&sel, "a").as_deref(), Some("1"));
    }

    // go: lease_helpers_test.go:229-237
    #[test]
    fn parse_combine_not_equals_into_notin() {
        let sel = parse_label_selector("key!=value1,key!=value2").unwrap();
        let expr = only_expr(&sel);
        assert_eq!(expr.key, "key");
        assert_eq!(expr.operator, OP_NOT_IN);
        let values = expr.values.clone().unwrap();
        assert_eq!(values.len(), 2);
        assert!(values.contains(&"value1".to_string()));
        assert!(values.contains(&"value2".to_string()));
    }

    // go: lease_helpers_test.go:239-247
    #[test]
    fn parse_dedup_notin_values() {
        let sel = parse_label_selector("key!=value1,key!=value1").unwrap();
        let expr = only_expr(&sel);
        assert_eq!(expr.operator, OP_NOT_IN);
        assert_eq!(expr.values.as_deref(), Some(&["value1".to_string()][..]));
    }

    // go: lease_helpers_test.go:249-255
    #[test]
    fn parse_dedup_notin_preserve_unique() {
        let sel = parse_label_selector("key!=value1,key!=value2,key!=value1").unwrap();
        let expr = only_expr(&sel);
        let values = expr.values.clone().unwrap();
        assert_eq!(values.len(), 2);
        assert!(values.contains(&"value1".to_string()));
        assert!(values.contains(&"value2".to_string()));
    }

    // go: lease_helpers_test.go:256-264
    #[test]
    fn parse_dedup_notin_empty_values() {
        let sel = parse_label_selector("key!=,key!=").unwrap();
        let expr = only_expr(&sel);
        assert_eq!(expr.key, "key");
        assert_eq!(expr.operator, OP_NOT_IN);
        assert_eq!(expr.values.as_deref(), Some(&["".to_string()][..]));
    }

    // -- round-trip compatibility (go: lease_helpers_test.go:267-336) --------

    // go: lease_helpers_test.go:268-293
    #[test]
    fn parse_matches_after_conversion() {
        let sel = parse_label_selector("board-type=qc8775,revision!=v3").unwrap();
        // Should NOT match: revision == v3
        assert!(!selector_matches(
            &sel,
            &labels(&[("board-type", "qc8775"), ("revision", "v3")])
        ));
        // Should match: revision == v2
        assert!(selector_matches(
            &sel,
            &labels(&[("board-type", "qc8775"), ("revision", "v2")])
        ));
    }

    // go: lease_helpers_test.go:295-306
    #[test]
    fn format_stable_across_round_trip_dup_notequals() {
        let sel1 = parse_label_selector("key!=value1,key!=value1").unwrap();
        let formatted = format_label_selector(&sel1);
        let sel2 = parse_label_selector(&formatted).unwrap();
        let formatted2 = format_label_selector(&sel2);
        assert_eq!(formatted2, formatted);
    }

    // go: lease_helpers_test.go:308-319
    #[test]
    fn format_stable_across_round_trip_dup_empty_notequals() {
        let sel1 = parse_label_selector("key!=,key!=").unwrap();
        let formatted = format_label_selector(&sel1);
        let sel2 = parse_label_selector(&formatted).unwrap();
        let formatted2 = format_label_selector(&sel2);
        assert_eq!(formatted2, formatted);
    }

    // go: lease_helpers_test.go:321-335
    #[test]
    fn matches_not_equals_operator() {
        let sel = parse_label_selector("revision!=v3").unwrap();
        assert!(selector_matches(&sel, &labels(&[("revision", "v2")])));
        assert!(selector_matches(&sel, &labels(&[("revision", "v4")])));
        assert!(!selector_matches(&sel, &labels(&[("revision", "v3")])));
        assert!(!selector_matches(
            &sel,
            &labels(&[("revision", "v3"), ("other", "value")])
        ));
        // NotIn matches when the key is absent (Go semantics).
        assert!(selector_matches(&sel, &labels(&[("other", "value")])));
    }

    // -- FormatLabelSelector (golden strings captured from apimachinery v0.35.0)

    #[test]
    fn format_matches_go_golden() {
        assert_eq!(format_label_selector(&ls(&[("dut", "a")], vec![])), "dut=a");
        assert_eq!(
            format_label_selector(&ls(
                &[("b", "2"), ("a", "1")],
                vec![LabelSelectorRequirement {
                    key: "env".into(),
                    operator: OP_IN.into(),
                    values: Some(vec!["staging".into(), "prod".into()]),
                }],
            )),
            "a=1,b=2,env in (prod,staging)"
        );
        assert_eq!(
            format_label_selector(&ls(
                &[],
                vec![LabelSelectorRequirement {
                    key: "revision".into(),
                    operator: OP_NOT_IN.into(),
                    values: Some(vec!["v3".into()]),
                }],
            )),
            "revision notin (v3)"
        );
        assert_eq!(format_label_selector(&ls(&[], vec![])), "<none>");
        assert_eq!(
            format_label_selector(&ls(
                &[],
                vec![
                    LabelSelectorRequirement {
                        key: "app".into(),
                        operator: OP_EXISTS.into(),
                        values: None,
                    },
                    LabelSelectorRequirement {
                        key: "debug".into(),
                        operator: OP_DOES_NOT_EXIST.into(),
                        values: None,
                    },
                ],
            )),
            "app,!debug"
        );
        assert_eq!(
            format_label_selector(&ls(
                &[],
                vec![LabelSelectorRequirement {
                    key: "k".into(),
                    operator: OP_NOT_IN.into(),
                    values: Some(vec![String::new()]),
                }],
            )),
            "k notin ()"
        );
    }

    // -- matching basics -----------------------------------------------------

    #[test]
    fn matches_empty_selector_matches_everything() {
        let sel = ls(&[], vec![]);
        assert!(selector_is_empty(&sel));
        assert!(selector_matches(&sel, &labels(&[])));
        assert!(selector_matches(&sel, &labels(&[("x", "y")])));
    }

    #[test]
    fn matches_in_exists_does_not_exist() {
        let sel = ls(
            &[("foo", "bar")],
            vec![
                LabelSelectorRequirement {
                    key: "env".into(),
                    operator: OP_IN.into(),
                    values: Some(vec!["prod".into(), "stg".into()]),
                },
                LabelSelectorRequirement {
                    key: "present".into(),
                    operator: OP_EXISTS.into(),
                    values: None,
                },
                LabelSelectorRequirement {
                    key: "absent".into(),
                    operator: OP_DOES_NOT_EXIST.into(),
                    values: None,
                },
            ],
        );
        assert!(selector_matches(
            &sel,
            &labels(&[("foo", "bar"), ("env", "prod"), ("present", "x")])
        ));
        // fails: env not in set
        assert!(!selector_matches(
            &sel,
            &labels(&[("foo", "bar"), ("env", "dev"), ("present", "x")])
        ));
        // fails: absent key present
        assert!(!selector_matches(
            &sel,
            &labels(&[
                ("foo", "bar"),
                ("env", "prod"),
                ("present", "x"),
                ("absent", "z")
            ])
        ));
    }
}
