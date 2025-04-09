# Contributing

Thank you for your interest in contributing to Jumpstarter! This document outlines the process for contributing to the project and provides guidelines to make the contribution process smooth.

## Getting Help

If you have questions or need help, here are the best ways to connect with the community:

### Community Resources

- **Matrix Chat**: Join our [Matrix community](https://matrix.to/#/#jumpstarter:matrix.org) for real-time discussions and support
- **Weekly Meetings**: Participate in our [weekly community meetings](https://meet.google.com/gzd-hhbd-hpu) to discuss development and get help
- **Etherpad**: Check our [Etherpad](https://etherpad.jumpstarter.dev/pad-lister) for meeting notes and collaborative documentation
- **GitHub Issues**: [Open an issue](https://github.com/jumpstarter-dev/jumpstarter/issues) in the repository for bug reports and feature requests
- **Documentation**: Visit our [documentation](https://jumpstarter.dev/) for comprehensive guides and tutorials

## Code of Conduct

Please be respectful and considerate of others when contributing to the project.

## Getting Started

1. Fork the repository
2. Clone your fork locally
3. Set up the development environment
4. Make your changes on a new branch
5. Test your changes thoroughly
6. Submit a pull request

## Development Setup

```bash
# Install dependencies
make sync

# Run tests
make test
```

## Contribution Guidelines

### Making Changes

- Keep changes focused and related to a single issue
- Follow the existing code style and conventions
- Add tests for new features or bug fixes
- Update documentation as needed

### Commit Messages

- Use clear and descriptive commit messages
- Reference issue numbers when applicable
- Follow conventional commit format when possible

### Pull Requests

- Provide a clear description of the changes
- Link to any relevant issues
- Ensure all tests pass before submitting
- Be responsive to feedback and questions

## Types of Contributions

### Code Contributions

We welcome contributions to the core codebase, including bug fixes, features, and improvements.

### Contributing Drivers

If you are working on a driver or adapter library of general interest, please consider submitting it to this repository, as it will become available to all Jumpstarter users.

To create a new driver scaffold, you can use the `create_driver.sh` script:

```bash
./__templates__/create_driver.sh vendor_name driver_name "Your Name" "your.email@example.com"
```

This will create the necessary files and structure for your driver in the `packages/` directory. For example:

```bash
./__templates__/create_driver.sh yepkit Ykush "Your Name" "your.email@example.com"
```

Creates:
```
packages/jumpstarter_driver_yepkit/jumpstarter_driver_yepkit/__init__.py
packages/jumpstarter_driver_yepkit/jumpstarter_driver_yepkit/client.py
packages/jumpstarter_driver_yepkit/jumpstarter_driver_yepkit/driver_test.py
packages/jumpstarter_driver_yepkit/jumpstarter_driver_yepkit/driver.py
packages/jumpstarter_driver_yepkit/.gitignore
packages/jumpstarter_driver_yepkit/pyproject.toml
packages/jumpstarter_driver_yepkit/README.md
packages/jumpstarter_driver_yepkit/examples/exporter.yaml
```

#### Driver Structure

A Jumpstarter driver typically consists of:

1. **Driver Implementation**: The core functionality that interfaces with the device or service
2. **Client Implementation**: Client code to interact with the driver
3. **Tests**: Tests to verify the driver's functionality
4. **Examples**: Example configurations showing how to use the driver
5. **Documentation**: Clear documentation on setup and usage

#### Private Drivers

If you are creating a driver or adapter library for a specific need, not of general interest, or that needs to be private, please consider forking our [jumpstarter-driver-template](https://github.com/jumpstarter-dev/jumpstarter-driver-template).

#### Driver Development Workflow

After creating your driver skeleton:

1. Implement the driver functionality according to the Jumpstarter driver interface
2. Write comprehensive tests for your driver
3. Create example configurations
4. Document the setup and usage in the README.md
5. Submit a pull request to the main Jumpstarter repository

#### Testing Your Driver

To test your driver during development:

```bash
# From the project root
make sync  # Synchronize dependencies
cd packages/your_driver_package
pytest     # Run tests for your driver
```

#### Driver Best Practices

- Follow the existing code style and conventions
- Write comprehensive documentation
- Include thorough test coverage
- Create example configurations for common use cases
- Keep dependencies minimal and well-justified

### Documentation Contributions

We welcome improvements to our documentation.

#### Documentation System Overview

Jumpstarter uses [Sphinx](https://www.sphinx-doc.org/en/master/) with Markdown support for its documentation. The documentation is organized into various sections covering different aspects of the project.

#### Setting Up Documentation Environment

To contribute to the documentation, you'll need to set up your development environment:

1. Clone the Jumpstarter repository
2. Navigate to the docs directory
3. Install dependencies (if not already installed with the main project)

#### Building the Documentation Locally

To build and preview the documentation locally:

```bash
cd docs
make html      # Build HTML documentation
make docs-serve # Serve documentation locally for preview
```

The documentation will be available at http://localhost:8000 in your web browser.

#### Documentation Structure

- `docs/source/`: Root directory for documentation source files
  - `index.md`: Main landing page
  - `*.md`: Various markdown files for documentation sections
  - `_static/`: Static assets like images and CSS
  - `_templates/`: Custom templates

#### Writing Documentation

Documentation is written in Markdown with some Sphinx-specific extensions. Common syntax includes:

```markdown
# Heading 1
## Heading 2
### Heading 3

*italic text*
**bold text**

- Bullet list item
- Another item

1. Numbered list
2. Second item

[Link text](URL)

![Image alt text](path/to/image.png)

```{note}
This is a note admonition
```

```{warning}
This is a warning admonition
```

```python
# This is a code block
def example():
    print("Hello, world!")
```
```

#### Documentation Style Guide

Please follow these guidelines when writing documentation:

1. Use clear, concise language
2. Write in the present tense
3. Use active voice when possible
4. Include practical examples
5. Break up text with headers, lists, and code blocks
6. Target both beginners and advanced users with appropriate sections
7. Provide cross-references to related documentation

#### Adding New Documentation Pages

To add a new documentation page:

1. Create a new Markdown (`.md`) file in the appropriate directory
2. Add the page to the relevant `index.md` or `toctree` directive
3. Build the documentation to ensure it appears correctly

#### Documentation Versioning

Documentation is versioned alongside the main Jumpstarter releases. When making changes, consider whether they apply to the current version or future versions.

#### Documentation Tips

- **Screenshots**: Include screenshots for complex UI operations
- **Command Examples**: Always include example commands with expected output
- **Troubleshooting**: Add common issues and their solutions
- **Links**: Link to relevant external resources when appropriate

#### Submitting Documentation Changes

Once your documentation changes are complete:

1. Build the documentation to verify there are no errors
2. Submit a pull request with your changes
3. Respond to feedback during the review process

### Example Contributions

To add a new example:

1. Create a new directory in the `examples/` folder with a descriptive name
2. Include a comprehensive `README.md` with setup and usage instructions
3. Follow the structure of existing examples
4. Ensure the example is well-documented and easy to follow
