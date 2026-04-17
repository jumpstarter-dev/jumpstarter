---
name: propose-jep
description: Create a new Jumpstarter Enhancement Proposal (JEP)
argument-hint: Short title or description of the proposal
---

# Propose a JEP

You are helping the user create a new Jumpstarter Enhancement Proposal (JEP).

## Context

JEPs are design documents for substantial changes to the Jumpstarter project — changes that affect multiple components, alter public APIs or protocols, or require community consensus. Read `.cursor/rules/jep-adr-process.mdc` for the full process definition.

JEP topic: $ARGUMENTS

## Steps

### 1. Determine the next JEP number

List existing files in `python/docs/source/internal/jeps/` and pick the next available incrementing integer. JEP-0000 through JEP-0009 are reserved for process/meta-JEPs, so start from JEP-0010 for regular proposals.

### 2. Gather information

Before writing the JEP, ask the user clarifying questions to understand:

- **What problem does this solve?** — The motivation section needs a concrete problem description.
- **Who is affected?** — Which components, drivers, or user workflows are impacted?
- **What are the alternatives?** — Each design decision needs at least two alternatives considered.
- **What are the compatibility implications?** — Does this break existing APIs, protocols, or workflows?

If the user provided a description in `$ARGUMENTS`, use it as a starting point but still ask about gaps.

### 3. Create the JEP file

Copy the template from `python/docs/source/internal/jeps/JEP-NNNN-template.md` and create a new file at `python/docs/source/internal/jeps/JEP-NNNN-short-title.md` where:

- `NNNN` is the zero-padded next number
- `short-title` is a descriptive slug derived from the proposal title

Fill in:

- The metadata table with the JEP number, title, author (ask the user), status `Draft`, type, and today's date
- All mandatory sections based on the information gathered
- Mark optional sections that need further input with TODO comments

### 4. Update the JEP index

Add the new JEP to the appropriate table in `python/docs/source/internal/jeps/README.md` (Process, Standards Track, or Informational).

Add the new JEP file to the `{toctree}` directive at the bottom of `python/docs/source/internal/jeps/README.md`.

### 5. Present the result

Show the user:

- The file path of the new JEP
- A summary of sections that are complete vs need further work
- Remind them to set status to `Discussion` when the PR is ready for review
- Remind them to apply the `jep` label to their pull request
