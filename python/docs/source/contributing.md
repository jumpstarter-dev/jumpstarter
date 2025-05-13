# Contributing

Thank you for your interest in contributing to Jumpstarter, we are an open community and we welcome contributions.

## Getting Help

- **Matrix Chat**: Join our [Matrix community](https://matrix.to/#/#jumpstarter:matrix.org)
- **GitHub Issues**: [Open an issue](https://github.com/jumpstarter-dev/jumpstarter/issues)
- **Documentation**: Visit our [documentation](https://jumpstarter.dev/)
- **Weekly Meetings**: [Google Meet](https://meet.google.com/gzd-hhbd-hpu)
- **Etherpad**: [Collaborative docs](https://etherpad.jumpstarter.dev/pad-lister)

## Getting Started

1. Follow our [development environment setup guide](./contributing/development-environment.md)
2. Make changes on a new branch
3. Test your changes thoroughly
4. Submit a pull request

If you have any questions, feel free to reach out in our Matrix chat or open an issue on GitHub.

## Contribution Guidelines

### Making Changes

- Focus on a single issue.
- Follow existing code style (validate with `make lint`, fix with `make lint-fix`)
- Perform static type checking with (`make mypy-pkg-${package_name}`)
- Add tests and update documentation. Every new driver or feature should include tests and be documented.
- Verify that all tests pass (`make test-pkg-${package_name}` or `make test`)

### Commit Messages

- Use clear, descriptive messages
- Reference issue numbers when applicable
- Follow conventional commit format when possible

### Pull Requests

- Provide a clear description
- Link to relevant issues
- Ensure all tests pass

## Types of Contributions

### Code Contributions
We welcome bug fixes, features, and improvements to the core codebase.

### Contributing Drivers

To create a new driver scaffold:

```console
$ ./__templates__/create_driver.sh driver_package DriverClass "Your Name" "your.email@example.com"
```

For private drivers, consider forking our [jumpstarter-driver-template](https://github.com/jumpstarter-dev/jumpstarter-driver-template).

Test your driver: `make pkg-test-${package_name}`

### Contributing Documentation

Jumpstarter uses Sphinx with Markdown. Build and preview locally:

```console
$ make docs-serve
```

Documentation recommended practices:

- Use clear, concise language
- Include practical examples
- Break up text with headers, lists, and code blocks
- Target both beginners and advanced users

```{toctree}
:maxdepth: 1
:hidden:

contributing/development-environment.md
```
