---
alwaysApply: false
---

# JEP and ADR Process

This rule helps with creating Jumpstarter Enhancement Proposals (JEPs) and Architecture Decision Records (ADRs).

## When to Use

- **JEP**: Cross-cutting changes that affect multiple components, change public APIs or protocols, or require community consensus.
- **ADR**: Scoped technical decisions within a single component or driver that should be documented for posterity.

## Creating a JEP

1. **Choose the next JEP number**: Look at existing files in `python/docs/source/internal/jeps/` and pick the next available incrementing integer. JEP-0000 through JEP-0009 are reserved for process/meta-JEPs, so start from JEP-0010 for regular proposals.

2. **Create the file**: Copy the template from `python/docs/source/internal/jeps/JEP-NNNN-template.md` to `python/docs/source/internal/jeps/JEP-NNNN-short-title.md`, replacing `NNNN` with the zero-padded number and `short-title` with a descriptive slug.

3. **Fill in the metadata table**:
   - Set the JEP number (incrementing integer, NOT the PR number)
   - Set the title, author(s) with GitHub handle and email
   - Set the type: `Standards Track`, `Informational`, or `Process`
   - Set the status to `Draft` initially, then `Proposed` when the PR is ready for review
   - Set the created date to today

4. **Fill in all mandatory sections**:
   - Abstract (3-5 sentences)
   - Motivation (concrete problem description)
   - Proposal (written as if teaching the feature)
   - Design Decisions (use DD-N pattern with alternatives and rationale)
   - Design Details (architecture, data flow, error handling)
   - Test Plan (unit, integration, HiL, manual)
   - Backward Compatibility
   - Consequences (positive and negative)
   - Rejected Alternatives

5. **Open a PR** against main with the `jep` label.

## Creating an ADR

1. Create the ADR file in `python/docs/source/internal/adr/` following the existing ADR format in that directory.

2. ADRs are submitted alongside the implementation PR, not as separate PRs.

3. Each ADR should document:
   - Context: Why the decision was needed
   - Decision: What was decided
   - Alternatives considered
   - Consequences (positive and negative)

## Design Decision Format (DD-N)

Both JEPs and ADRs use this format for individual decisions:

```markdown
### DD-N: Decision title

**Alternatives considered:**

1. **Option A** — Brief description.
2. **Option B** — Brief description.

**Decision:** Option A.

**Rationale:** Explain why this option was chosen.
```

## Key Rules

- JEP numbers are incrementing integers, NOT derived from PR numbers
- JEPs live in `python/docs/source/internal/jeps/`
- ADRs live in `python/docs/source/internal/adr/`
- All JEPs should be merged as PRs so the documentation is part of the Jumpstarter docs/source
- Rejected JEPs are normally not merged, but can be merged with "Rejected" status if there is an architectural reason to preserve them
- The license for all documents is Apache-2.0
