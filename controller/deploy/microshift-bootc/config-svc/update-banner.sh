#!/bin/env python3
# Update login banner with Jumpstarter web UI URL

import sys
import os

# Add config-svc module directory to Python path
sys.path.insert(0, '/usr/local/lib/config-svc')

try:
    # Import the system module which contains update_login_banner()
    from system import update_login_banner
    
    # Call the update function
    success, message = update_login_banner()
    
    if not success:
        print(f"Warning: Failed to update login banner: {message}", file=sys.stderr)
        sys.exit(1)
    
    print("Login banner updated successfully", file=sys.stdout)
    sys.exit(0)
    
except ImportError as e:
    print(f"Error: Failed to import system module: {e}", file=sys.stderr)
    print("Make sure config-svc modules are installed at /usr/local/lib/config-svc/", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"Error updating login banner: {e}", file=sys.stderr)
    sys.exit(1)


