#!/usr/bin/env python3
"""
Jumpstarter Configuration Web UI

A simple web service for configuring Jumpstarter deployment settings:
- Hostname configuration with smart defaults
- Jumpstarter CR management (baseDomain + image version)
- MicroShift kubeconfig download
"""

import os
import sys

from flask import Flask

# Import route registrationfunctions
from api import register_api_routes
from routes import register_ui_routes
from system import update_login_banner

# Create Flask app
app = Flask(__name__)

# Register all routes
register_ui_routes(app)
register_api_routes(app)


def main():
    """Main entry point."""
    port = int(os.environ.get('PORT', 8080))
    
    print(f"Starting Jumpstarter Configuration UI on port {port}...", file=sys.stderr)
    print(f"Access the UI at http://localhost:{port}/", file=sys.stderr)
    
    # Update login banner on startup
    banner_success, banner_message = update_login_banner()
    if banner_success:
        print("Login banner updated with web UI URL", file=sys.stderr)
    else:
        print(f"Warning: Could not update login banner: {banner_message}", file=sys.stderr)
    
    app.run(host='0.0.0.0', port=port, debug=False)


if __name__ == '__main__':
    main()

