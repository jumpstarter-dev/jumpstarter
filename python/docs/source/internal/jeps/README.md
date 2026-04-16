# Jumpstarter Enhancement Proposals (JEPs)

This directory contains the Jumpstarter Enhancement Proposals — design documents
that describe significant changes to the Jumpstarter project.

## What is a JEP?

A JEP is a design document that proposes a new feature, process change, or
architectural decision for the Jumpstarter hardware-in-the-loop testing
framework. JEPs provide a transparent, structured process for the community to
propose, discuss, and decide on substantial changes.

For the full process definition, see [JEP-0000](JEP-0000-jep-process.md).

## Quick Start

1. Read [JEP-0000](JEP-0000-jep-process.md) to understand when a JEP is needed.
2. Socialize your idea in [Matrix](https://matrix.to/#/#jumpstarter:matrix.org)
   or at the [weekly meeting](https://meet.google.com/gzd-hhbd-hpu).
3. Create a branch and add your JEP markdown file to the `python/docs/source/internal/jeps/` directory
   using the [JEP-NNNN-template.md](JEP-NNNN-template.md) as a starting point.
4. Open a pull request. The PR serves as the primary venue for discussion,
   allowing inline review comments on the JEP text.

## JEP Index

### Process JEPs

| JEP  | Title                                  | Status | Author(s)               |
| ---- | -------------------------------------- | ------ | ----------------------- |
| 0000 | [JEP Process](JEP-0000-jep-process.md) | Active | Jumpstarter Maintainers |

### Standards Track JEPs

| JEP  | Title                                                | Status      | Author(s)            |
| ---- | ---------------------------------------------------- | ----------- | -------------------- |
| 0010 | [Renode Integration](JEP-0010-renode-integration.md) | Implemented | @vtz (Vinicius Zein) |

### Informational JEPs

| JEP        | Title | Status | Author(s) |
| ---------- | ----- | ------ | --------- |
| *none yet* |       |        |           |

## Related: Architecture Decision Records (ADRs)

For technical decisions scoped to a single component or driver (e.g., choosing a
control interface for a new driver), use an Architecture Decision Record instead
of a JEP. ADRs live in `python/docs/source/internal/adr/` and are submitted
alongside the implementation PR. See [JEP-0000](JEP-0000-jep-process.md) for
guidance on when to use a JEP vs an ADR.

## Status Key

> **Note:** [JEP-0000](JEP-0000-jep-process.md) is the canonical source for
> lifecycle states and their definitions.

| Status       | Meaning                                          |
| ------------ | ------------------------------------------------ |
| Draft        | Author is still writing; not yet open for review |
| Proposed     | PR is open and under community discussion        |
| Accepted     | Design approved; implementation may begin        |
| Implementing | Implementation in progress                       |
| Implemented  | Reference implementation merged                  |
| Final        | Complete and authoritative                       |
| Rejected     | Declined (record preserved)                      |
| Deferred     | Sound but not a current priority                 |
| Withdrawn    | Author voluntarily withdrew                      |
| Active       | Living document, actively maintained (Process JEPs only) |
| Superseded   | Replaced by a newer JEP                          |

## For AI Agents

For detailed conventions on creating JEPs and ADRs, see the agent rule files
in `.cursor/rules/jep-adr-process.mdc` (or `.claude/rules/jep-adr-process.md`).

Key references:

- **Template**: `JEP-NNNN-template.md` in this directory
- **Canonical process definition**: [JEP-0000](JEP-0000-jep-process.md)
- **File naming**: `JEP-NNNN-short-title.md` (zero-padded 4-digit number)
- **JEP numbering**: Incrementing integers, not derived from PR numbers.
  JEP-0000 through JEP-0009 are reserved for process/meta-JEPs.

```{toctree}
:hidden:

JEP-0000-jep-process.md
JEP-0010-renode-integration.md
```
