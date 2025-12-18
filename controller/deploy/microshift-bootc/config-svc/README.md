# Jumpstarter Configuration Service

A modular web service for configuring Jumpstarter deployment settings on MicroShift.

## Features

- Hostname configuration with smart defaults
- Jumpstarter CR management (baseDomain + image version)
- MicroShift kubeconfig download
- System monitoring and status
- Pod and route management
- BootC operations support

## Project Structure

```
config-svc/
├── __init__.py              # Package initialization
├── app.py                   # Main application entry point
├── auth.py                  # Authentication and validation logic
├── system.py                # System utility functions
├── api.py                   # API route handlers
├── routes.py                # Main UI route handlers
├── templates/               # HTML and CSS templates
│   ├── index.html          # Main page template
│   ├── password_required.html  # Password change page
│   └── styles.css          # Application styles
├── pyproject.toml          # Project configuration and dependencies
├── config-svc.service      # Systemd service file
├── update-banner.service   # Banner update service
└── update-banner.sh        # Banner update script
```

## Module Organization

### `auth.py`
Authentication and validation utilities:
- `validate_hostname()` - RFC 1123 hostname validation
- `validate_password()` - Password security validation
- `check_auth()` - PAM-based authentication
- `requires_auth()` - Flask authentication decorator
- `is_default_password()` - Default password check

### `system.py`
System utility functions:
- `get_current_hostname()` - Get system hostname
- `get_jumpstarter_config()` - Retrieve Jumpstarter CR configuration
- `set_root_password()` - Set root user password
- `get/set_ssh_authorized_keys()` - Manage SSH keys
- `update_login_banner()` - Update system login banner
- `apply_jumpstarter_cr()` - Apply Jumpstarter Custom Resource
- `get_lvm_pv_info()` - Get LVM physical volume info
- `get_root_filesystem()` - Detect root filesystem
- `calculate_age()` - Calculate Kubernetes resource age
- `get_default_route_ip()` - Get default route IP address

### `api.py`
API route handlers:
- `/api/change-password` - Password and SSH key management
- `/api/configure-jumpstarter` - Jumpstarter CR configuration
- `/api/system-stats` - System statistics
- `/api/bootc-status` - BootC status information
- `/api/bootc-upgrade-check` - Check for BootC upgrades
- `/api/bootc-upgrade` - Apply BootC upgrade
- `/api/bootc-switch` - Switch BootC image
- `/api/dmesg` - Kernel log
- `/api/operator-status` - Jumpstarter operator status
- `/api/pods` - Pod listing
- `/api/routes` - Route listing
- `/api/pods/<namespace>/<pod_name>` - Delete pod
- `/logs/<namespace>/<pod_name>` - Stream pod logs
- `/kubeconfig` - Download kubeconfig

### `routes.py`
Main UI route handlers:
- `/` - Main configuration page
- `/static/styles.css` - CSS stylesheet
- `/logout` - Logout endpoint
- `/configure-jumpstarter` - Legacy form submission handler

### `app.py`
Main application entry point that:
- Creates Flask application
- Registers all routes
- Updates login banner
- Starts the web server

## Installation

### Using pip

```bash
cd config-svc
pip install -e .
```

### Using pyproject.toml

The application is configured using `pyproject.toml` with:
- Project metadata
- Dependencies (Flask 2.3+)
- Optional dependencies (python-pam for PAM auth)
- Build system configuration
- Tool configurations (black, isort, pylint, mypy)

## Running

### Direct execution

```bash
python3 app.py
```

### Using systemd

```bash
systemctl enable --now config-svc.service
```

### Environment Variables

- `PORT` - Server port (default: 8080)

## Dependencies

### Required
- Python 3.9+
- Flask 2.3+

### Optional
- python-pam 2.0+ (for PAM authentication, falls back to subprocess)

### System Commands
The application requires the following system commands:
- `oc` - OpenShift CLI
- `bootc` - BootC CLI
- `pvscan` - LVM commands
- `df`, `free`, `top`, `uptime` - System monitoring
- Standard Linux utilities

## Development

### Code Style

The project uses:
- Black for code formatting (line length: 120)
- isort for import sorting
- pylint for linting
- mypy for type checking

### Running Linters

```bash
black app.py auth.py system.py api.py routes.py
isort app.py auth.py system.py api.py routes.py
pylint app.py auth.py system.py api.py routes.py
mypy app.py auth.py system.py api.py routes.py
```

## Security

- HTTP Basic Authentication required for all endpoints
- Password validation (min 8 chars, no special characters)
- SSH key management with proper permissions
- Hostname validation per RFC 1123
- Default password change enforcement

## License

Apache License 2.0

## Links

- Homepage: https://jumpstarter.dev
- Documentation: https://docs.jumpstarter.dev
- Repository: https://github.com/jumpstarter-dev/jumpstarter-controller

