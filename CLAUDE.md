# Claude AI Instructions

This file provides instructions for Claude AI when working with the Jumpstarter project.

## Project Rules and Guidelines

Important project-specific rules and guidelines are located in the `.claude/rules` directory:

- **`.claude/rules/project-structure.md`**: Understanding the monorepo structure, workspace configuration, package organization, and development workflows. Read this to understand how the project is organized and where files should be located.

- **`.claude/rules/creating-new-drivers.md`**: Guidelines for creating new driver packages, including naming conventions, required information, and the driver creation process. Read this when tasked with creating or modifying drivers.

- **`.claude/rules/releasing-operator.md`**: Step-by-step guide for releasing a new version of the Jumpstarter operator, including which files to update, image tag conventions, bundle generation, and community-operators contribution workflow.

- **`.claude/rules/jep-process.md`**: Process for creating Jumpstarter Enhancement Proposals (JEPs), including when to use them, numbering conventions, required sections, and the design decision format. Read this when proposing or reviewing cross-cutting changes or features that require community consensus.

- **`.claude/rules/driver-compatibility.md`**: Backwards-compatibility invariants between independently-released clients and exporters: `client()` class path stability, RPC and resource handle surfaces, client API deprecation, and the opendal migration pattern. Read this when modifying driver client classes, exported RPC methods, or migrating drivers away from opendal.

## When to Read These Rules

- **Always**: Read `project-structure.md` when working with files, packages, or understanding the codebase layout
- **When creating drivers**: Read `creating-new-drivers.md` before creating, improving, or documenting driver packages
- **When releasing the operator**: Read `releasing-operator.md` before preparing a new operator version for OLM
- **When creating JEPs**: Read `jep-process.md` before proposing enhancements that affect multiple components, change public APIs, or require community discussion
- **When changing driver/client interfaces**: Read `driver-compatibility.md` before changing `client()` paths, RPC methods, public client APIs, or removing opendal from a driver
- **When modifying structure**: Consult both files when making changes that affect project organization

## Key Information

- All Python code is located under the `python/` directory
- The project uses UV workspace for dependency management
- Driver creation script: `./python/__templates__/create_driver.sh`
- Testing: Use `make pkg-test-<package_name>` for package-specific tests
- Linting: Use `make lint-fix` to fix linting issues
- Type checking: Use `make pkg-ty-<package_name>` for type checking

Please refer to the detailed rules in `.claude/rules/` for comprehensive guidance.
