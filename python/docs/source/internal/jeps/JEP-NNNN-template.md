---
orphan: true
---

# JEP-NNNN: Your Short, Descriptive Title

<!--
  Use this template to create your JEP. Copy it to a new file named
  JEP-NNNN-short-title.md (where NNNN is the next available incrementing
  integer) and place it in the python/docs/source/internal/jeps/ directory. Open a pull
  request against the main branch.

  Guidance:
  - Keep the JEP focused on a single proposal. Split multi-part ideas.
  - Hardware-related proposals must address the Hardware Considerations section.
  - Protocol/API changes must include backward compatibility analysis.
  - Write as if teaching the feature to a new Jumpstarter contributor.
  - Each design decision should document alternatives considered and rationale
    (following the ADR pattern used in the project).
  - Sections marked (mandatory) must be filled in. Sections marked (optional)
    may be omitted if not applicable.
-->

| Field             | Value                                        |
| ----------------- | -------------------------------------------- |
| **JEP**           | NNNN *(next available incrementing integer)* |
| **Title**         | *Your short, descriptive title*              |
| **Author(s)**     | *@github-handle (Full Name \<email\>)*       |
| **Status**        | Draft                                        |
| **Type**          | Standards Track \| Informational \| Process  |
| **Created**       | *YYYY-MM-DD*                                 |
| **Updated**       | *YYYY-MM-DD*                                 |
| **Discussion**    | *Link to Matrix thread or GitHub issue*      |
| **Requires**      | *JEP-NNNN (if depends on another JEP)*       |
| **Supersedes**    | *JEP-NNNN (if replacing a previous JEP)*     |
| **Superseded-By** | *JEP-NNNN (filled in later if applicable)*   |

---

## Abstract *(mandatory)*

<!--
  One paragraph (3-5 sentences) summarizing the proposal. A reader should be
  able to decide whether this JEP is relevant to them from the abstract alone.
-->

## Motivation *(mandatory)*

<!--
  Why is this change needed? What problem does it solve? Who benefits?

  Describe the problem in concrete terms. Include specific scenarios that
  Jumpstarter users, driver authors, or operators encounter today. If this
  addresses a gap in HiL testing workflows, explain the current workaround
  and its limitations.

  Do not describe the solution here — that belongs in the Proposal section.
-->

### User Stories *(optional)*

<!--
  Describe 2-3 concrete scenarios from the perspective of a Jumpstarter user.

  Example format:
  - **As a** CI pipeline author, **I want to** lease a headunit by label
    rather than by name, **so that** my pipeline isn't blocked when one
    specific device is offline.
-->

## Proposal *(mandatory)*

<!--
  Explain the proposal as if it were already implemented and you are teaching
  it to another Jumpstarter contributor. This means:

  - Introduce new concepts by name.
  - Show what the user/developer experience looks like (CLI commands, config
    files, Python API calls, YAML manifests).
  - Explain how it interacts with existing Jumpstarter components (drivers,
    exporters, operator, CLI, protocol).
  - Use code examples, configuration snippets, and diagrams where they help.

  For Standards Track JEPs, this section should be detailed enough that
  someone could begin implementation from it.
-->

### API / Protocol Changes *(if applicable)*

<!--
  If this JEP modifies gRPC .proto definitions, operator CRDs, driver
  interfaces, or the jmp CLI surface:

  - Show the before/after of any changed message types, RPCs, or CRD fields.
  - Specify whether changes are additive (backward compatible) or breaking.
  - For breaking changes, describe the migration path.
-->

### Hardware Considerations *(if applicable)*

<!--
  Jumpstarter operates at the hardware-software boundary. If this proposal
  involves physical hardware, address:

  - What hardware is required or affected (e.g., SBCs, USB devices, CAN
    interfaces, serial adapters, power relays)?
  - Are there timing constraints (e.g., USB/IP latency, boot ROM timeouts)?
  - Does this require privileged access (e.g., /dev/kvm, /dev/ttyUSB*)?
  - How does this behave when hardware is unavailable or in a degraded state?
  - Are there power/thermal/physical safety implications?
-->

## Design Decisions *(mandatory for Standards Track)*

<!--
  Document each significant design decision using the pattern below.
  This follows the Architecture Decision Record (ADR) convention used
  elsewhere in the project (see python/docs/source/internal/adr/).

  For each decision, state what was decided, what alternatives were
  considered, and why the chosen approach was preferred. This section
  is the most important part of the JEP for long-term project memory —
  future contributors will refer to it to understand *why* things are
  the way they are.
-->

### DD-1: *Decision title*

**Alternatives considered:**

1. **Option A** — Brief description.
2. **Option B** — Brief description.

**Decision:** Option A.

**Rationale:** Explain why this option was chosen over the alternatives.
Reference specific project constraints, prior art, or technical tradeoffs.

<!--
  Add more DD-N subsections as needed. Each decision should be
  independently understandable.
-->

## Design Details *(mandatory for Standards Track)*

<!--
  The technical heart of the JEP. Cover:

  - Architecture and component interaction.
  - Data flow and state management.
  - Error handling and failure modes.
  - Concurrency and thread-safety considerations.
  - Security implications (especially for remote access features).

  Use diagrams (Mermaid, ASCII art, or image references) for complex
  interactions.
-->

## Test Plan *(mandatory for Standards Track)*

<!--
  How will this feature be tested? Jumpstarter's HiL nature means pure unit
  tests are often insufficient. Address each level that applies:

  ### Unit Tests
  What can be tested in isolation without hardware?

  ### Integration Tests
  What requires a running Jumpstarter environment (operator, exporter, etc.)?

  ### Hardware-in-the-Loop Tests
  What requires actual physical hardware? Specify the hardware needed and
  whether it's available in the project's CI infrastructure.

  ### Manual Verification
  What, if anything, requires manual testing? How should a reviewer verify
  the implementation?
-->

## Graduation Criteria *(optional)*

<!--
  For features that should be introduced incrementally (e.g., behind a
  feature flag, as an experimental driver, or as a beta CLI command):

  ### Experimental
  - What signals indicate the feature is ready for broader testing?
  - What feedback are you looking for?

  ### Stable
  - What criteria must be met before removing the experimental designation?
  - How long should the feature be in experimental before promotion?
-->

## Backward Compatibility *(mandatory for Standards Track)*

<!--
  - Does this change break existing users, drivers, exporters, or deployments?
  - If yes, what is the migration path?
  - Can old and new versions coexist during transition?
  - How does this interact with the Jumpstarter operator's upgrade path?
  - For protocol changes: is the wire format backward compatible?
-->

## Consequences *(mandatory)*

<!--
  Summarize the expected outcomes of this proposal, following ADR convention.
-->

### Positive

<!--
  What benefits does this proposal deliver? Be specific.
-->

### Negative

<!--
  What downsides or costs does this proposal introduce? Be honest.
-->

### Risks *(optional)*

<!--
  What could go wrong? What assumptions might prove incorrect?
-->

## Rejected Alternatives *(mandatory)*

<!--
  What other designs were considered? Why were they not chosen?

  This section is critical — it shows reviewers that the design space was
  explored and helps future contributors understand why the chosen approach
  was preferred. Include brief descriptions of alternatives and the reasons
  they were rejected.

  Note: If you used the Design Decisions section above with alternatives
  for each decision, you may keep this section brief and refer back to
  those decisions. Use this section for higher-level alternatives that
  don't fit into individual design decisions (e.g., "we considered not
  doing this at all").
-->

## Prior Art *(optional)*

<!--
  Are similar features present in other HiL frameworks, testing tools, or
  infrastructure projects? What can Jumpstarter learn from their approach?

  Consider:
  - Other HiL/SiL frameworks (e.g., dSPACE, NI TestStand, LAVA)
  - Kubernetes patterns (operators, CRDs, controllers)
  - Embedded testing tools (Robot Framework, pytest-embedded)
  - Remote access solutions (if relevant)
-->

## Unresolved Questions *(optional)*

<!--
  What aspects of the design are still open? List specific questions that
  should be resolved during the JEP review process. Questions that can wait
  until implementation should be noted as such.
-->

## Future Possibilities *(optional)*

<!--
  What natural extensions or follow-up work does this proposal enable? This
  helps reviewers evaluate the long-term trajectory without overloading the
  current JEP. Be explicit that these are NOT part of the current proposal.
-->

## Implementation History

<!--
  Updated as the implementation progresses. Record major milestones:

  - YYYY-MM-DD: JEP proposed (PR #NNN)
  - YYYY-MM-DD: JEP accepted
  - YYYY-MM-DD: Initial implementation merged (PR #NNN)
  - YYYY-MM-DD: Feature stabilized
  - YYYY-MM-DD: JEP marked Final
-->

## References

<!--
  Links to related GitHub issues, PRs, external documentation, specs,
  or discussions that informed this JEP.
-->

---

*This JEP is licensed under the
[Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0),
consistent with the Jumpstarter project.*
