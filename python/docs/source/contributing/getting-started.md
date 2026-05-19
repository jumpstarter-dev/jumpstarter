# Getting Started

0. Get familiar with the [Introduction](../introduction/index.md)
1. Follow the [development environment](development-environment.md) setup
2. Make changes on a new branch
3. Test your changes thoroughly
4. Submit a pull request

If you have questions, reach out in our Matrix chat or open an issue on GitHub.

## Making Changes

- Focus on a single issue
- Follow code style (validate with `make lint`, fix with `make lint-fix`)
- Perform static type checking with (`make pkg-ty-${package_name}`)
- Add tests and update documentation
- Verify all tests pass (`make pkg-test-${package_name}` or `make test`)

## Commit Messages

- Use clear, descriptive messages
- Reference issue numbers when applicable
- Follow conventional commit format when possible

## Pull Requests

- Provide a clear description
- Link to relevant issues
- Ensure all tests pass

## Types of Contributions

### Code

We welcome bug fixes, features, and improvements to the core codebase.

### Drivers

To create a new driver scaffold:

```console
$ ./__templates__/create_driver.sh driver_package DriverClass "Your Name" "your.email@example.com"
```

For private drivers, consider forking our
[jumpstarter-driver-template](https://github.com/jumpstarter-dev/jumpstarter-driver-template).

Test your driver: `make pkg-test-${package_name}`

### Documentation

Jumpstarter uses Sphinx with Markdown. Build and preview locally:

```console
$ make docs-serve
```

### Jumpstarter Enhancement Proposals

For significant changes that affect multiple components, change public APIs, or
require community consensus, follow the
[{term}`JEP` process](jeps/index.md).
