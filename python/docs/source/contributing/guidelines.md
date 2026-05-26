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

## Documentation Snippets

Code snippets in documentation are extracted into standalone files under
`docs/source/examples/` and referenced using `{literalinclude}` directives.
Each extracted file has a corresponding test in `docs/source/examples/tests/`
that validates it.

### Converting a snippet

1. Copy the inline code block from the `.md` file into a new file under
   `examples/`, using a subdirectory that mirrors the doc path (for example,
   `examples/introduction/` for files in `introduction/`).
2. Replace the inline code block with a literalinclude directive:
   ````
   ```{literalinclude} ../examples/introduction/my_example.py
   :language: python
   ```
   ````
3. Write a test in `examples/tests/` that validates the extracted file:
   - **Python**: import and execute the module, or use `compile()` for
     scripts that require runtime context (environment variables, hardware).
   - **YAML**: parse with `yaml.safe_load()` and validate against the
     appropriate Pydantic model (for example, `ExporterConfigV1Alpha1` or
     `HookConfigV1Alpha1`).
   - **Bash**: use `compile()` is not applicable; use `bash -n` for syntax
     checking.
4. Run the tests: `make docs-snippet-test` (from the `python/` directory)
   or directly with `pytest docs/source/examples/tests/ -v`.

### Test fixtures

The `conftest.py` in `examples/tests/` provides an `examples_root` fixture
that resolves to the `examples/` directory. Use it to build paths to extracted
files.

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
