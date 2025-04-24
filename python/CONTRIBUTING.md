# Contributing

Thank you for your interest in contributing to Jumpstarter!

## Getting Help

- **Matrix Chat**: Join our [Matrix community](https://matrix.to/#/#jumpstarter:matrix.org)
- **GitHub Issues**: [Open an issue](https://github.com/jumpstarter-dev/jumpstarter/issues)
- **Documentation**: Visit our [documentation](https://jumpstarter.dev/)
- **Weekly Meetings**: [Google Meet](https://meet.google.com/gzd-hhbd-hpu)
- **Etherpad**: [Collaborative docs](https://etherpad.jumpstarter.dev/pad-lister)

## Getting Started

1. Fork the repository
2. Clone your fork locally
3. Set up the development environment: `make sync` and `make test`
4. Make changes on a new branch
5. Test your changes thoroughly
6. Submit a pull request

## Contribution Guidelines

### Making Changes

- Focus on a single issue
- Follow existing code style
- Add tests and update documentation

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

```shell
$ ./__templates__/create_driver.sh vendor_name driver_name "Your Name" "your.email@example.com"
```

For private drivers, consider forking our [jumpstarter-driver-template](https://github.com/jumpstarter-dev/jumpstarter-driver-template).

Test your driver: `make sync && cd packages/your_driver_package && pytest`

### Contributing Documentation

Jumpstarter uses Sphinx with Markdown. Build and preview locally:

```shell
$ make docs-test
$ make docs-serve
```

Documentation recommended practices:

- Use clear, concise language
- Include practical examples
- Break up text with headers, lists, and code blocks
- Target both beginners and advanced users