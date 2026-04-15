---
name: create-adr
description: Create a new Architecture Decision Record (ADR)
argument-hint: Short title or description of the decision
---

# Create an ADR

You are helping the user create a new Architecture Decision Record (ADR).

## Context

ADRs document significant technical decisions scoped to a single component or driver. Unlike JEPs, ADRs are submitted alongside the implementation PR, not as separate proposals. Read `.claude/rules/jep-adr-process.md` for the full process definition.

ADR topic: $ARGUMENTS

## Steps

### 1. Determine the next ADR number

List existing files in `python/docs/source/internal/adr/` and pick the next available incrementing integer. Use the format `ADR-NNNN-short-title.md`.

### 2. Gather information

Ask the user about:

- **Context**: What situation or problem prompted this decision? What component or driver is this for?
- **Decision**: What was decided?
- **Alternatives**: What other options were considered? Why were they rejected?
- **Consequences**: What are the positive and negative outcomes of this decision?

If the user provided a description in `$ARGUMENTS`, use it as a starting point but still ask about gaps.

### 3. Create the ADR file

Create a new file at `python/docs/source/internal/adr/ADR-NNNN-short-title.md` with the following structure:

```markdown
# ADR-NNNN: Title

| Field          | Value                          |
|----------------|--------------------------------|
| **ADR**        | NNNN                           |
| **Title**      | Short descriptive title        |
| **Author(s)**  | Name <email>                   |
| **Status**     | Accepted                       |
| **Created**    | YYYY-MM-DD                     |
| **Component**  | Component or driver name       |

## Context

[Why this decision was needed]

## Design Decisions

### DD-1: Decision title

**Alternatives considered:**

1. **Option A** -- Description.
2. **Option B** -- Description.

**Decision:** Chosen option.

**Rationale:** Why this option was chosen.

## Consequences

### Positive

- [List positive outcomes]

### Negative

- [List negative outcomes or trade-offs]
```

Use the DD-N format for each design decision, consistent with the JEP convention.

### 4. Update the ADR index

Update `python/docs/source/internal/adr/index.md`:

- Remove the "*No ADRs have been submitted yet.*" placeholder if present
- Add a table listing the new ADR
- Add a `{toctree}` directive if one doesn't exist yet, including the new ADR file

### 5. Present the result

Show the user:

- The file path of the new ADR
- A reminder that ADRs are submitted alongside the implementation PR, not separately
