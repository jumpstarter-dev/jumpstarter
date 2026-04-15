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

| JEP  | Title                                     | Status | Author(s)               |
|------|-------------------------------------------|--------|-------------------------|
| 0000 | [JEP Process](JEP-0000-jep-process.md)   | Active | Jumpstarter Maintainers |

### Standards Track JEPs

| JEP  | Title | Status | Author(s) |
|------|-------|--------|-----------|
| *none yet* | | | |

### Informational JEPs

| JEP  | Title | Status | Author(s) |
|------|-------|--------|-----------|
| *none yet* | | | |

## Related: Architecture Decision Records (ADRs)

For technical decisions scoped to a single component or driver (e.g., choosing a
control interface for a new driver), use an Architecture Decision Record instead
of a JEP. ADRs live in `python/docs/source/internal/adr/` and are submitted
alongside the implementation PR. See [JEP-0000](JEP-0000-jep-process.md) for
guidance on when to use a JEP vs an ADR.

## Status Key

| Status         | Meaning                                              |
|----------------|------------------------------------------------------|
| Draft          | Author is still writing; not yet open for review     |
| Proposed       | PR is open and under community discussion            |
| Accepted       | Design approved; implementation may begin            |
| Implementing   | Implementation in progress                           |
| Implemented    | Reference implementation merged                      |
| Final          | Complete and authoritative                           |
| Rejected       | Declined (record preserved)                          |
| Deferred       | Sound but not a current priority                     |
| Withdrawn      | Author voluntarily withdrew                          |
| Superseded     | Replaced by a newer JEP                             |

## For AI Agents

This section provides conventions for AI agents working with JEPs.

### Document structure

JEP files are Markdown documents with a metadata table at the top. The metadata
table uses pipe-delimited rows with bold field names. Required fields:
`JEP`, `Title`, `Author(s)`, `Status`, `Type`, `Created`.

### Section markers

Sections in the JEP template are marked `*(mandatory)*`, `*(optional)*`, or
`*(mandatory for Standards Track)*`. When helping an author draft a JEP, ensure
all mandatory sections are filled in. Optional sections may be omitted entirely.

### Design Decisions format

Each design decision uses a numbered `DD-N` subsection under `## Design Decisions`
with the following structure:

```text
### DD-N: Decision title

**Alternatives considered:**

1. **Option A** — Description.
2. **Option B** — Description.

**Decision:** Chosen option.

**Rationale:** Why this option was chosen.
```

This matches the ADR convention used elsewhere in the project (see
`python/docs/source/internal/adr/`).

### File naming

JEP files are named `JEP-NNNN-short-title.md` where `NNNN` is the next
available incrementing integer (zero-padded to 4 digits). The template file
is `JEP-NNNN-template.md`.

### JEP numbering

The JEP number is an incrementing integer assigned sequentially; it is not
derived from the pull request number. JEP-0000 through JEP-0009 are
reserved for process and meta-JEPs.

```{toctree}
:hidden:

JEP-0000-jep-process.md
```
