# Guidelines

## Documentation

- Use clear, concise language
- Include practical examples
- Break up text with headers, lists, and code blocks
- Target both beginners and advanced users
- For third-party tools (`pytest`, `kubectl`, `cert-manager`, etc.), link to the
  official documentation on first mention rather than defining them inline
- The [glossary](../glossary.md) is reserved for Jumpstarter-specific terms only
  (entities, concepts, CLI commands). Do not add well-known industry terms or
  third-party project names to it
- Use ASCII hyphens (`-`) instead of en-dash or em-dash characters

## AI Assistants

This project accepts contributions from AI assistants, although you should be
careful when creating code from AI assistants, and figure out if the code you
are submitting could infringe any licensing, for example, reusing code from
other incompatible GPL licenses, you should do your due diligence.

### Cursor AI

This project includes cursor rules to help Cursor AI understand our codebase
and development patterns. When working with Cursor AI:

- **Driver Creation**: If asked to create a new driver, Cursor will guide you
  through the process using our `create_driver.sh` script
- **Code Style**: Cursor will follow our established patterns and conventions
- **Testing**: Cursor will remind you to add tests and run our test suite

The cursor rules are located in `.cursor/rules/` directory, with specific
guidance for driver creation in `.cursor/rules/creating-new-drivers.mdc`.

### Claude Code

This project also includes Claude Code configuration in the `.claude/`
directory. When working with Claude Code:

- **Project Rules**: The `.claude/rules/` directory contains rules for project
  structure, driver creation, {term}`operator` releases, and the {term}`JEP` process. Claude
  Code loads these automatically.
- **CLAUDE.md**: The root `CLAUDE.md` provides project-level instructions
  including key commands for testing (`make pkg-test-<package_name>`), linting
  (`make lint-fix`), and type checking (`make pkg-ty-<package_name>`).
- **Code Style**: Claude Code follows TDD practices - writing failing tests
  first, then minimal implementation code.
- **Driver Creation**: When asked to create a new driver, Claude Code follows
  the guidelines in `.claude/rules/creating-new-drivers.md`.
