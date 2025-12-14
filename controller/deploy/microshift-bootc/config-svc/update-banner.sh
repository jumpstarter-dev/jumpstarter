#!/bin/bash
# Update login banner with Jumpstarter web UI URL

python3 << 'EOF'
import sys
import os
sys.path.insert(0, '/usr/local/bin')

# Import and call the update function
import importlib.util

config_svc_path = '/usr/local/bin/config-svc'
if not os.path.exists(config_svc_path):
    print(f"Error: {config_svc_path} does not exist", file=sys.stderr)
    sys.exit(1)

spec = importlib.util.spec_from_file_location('config_svc', config_svc_path)
if spec is None:
    print(f"Error: Failed to create spec for {config_svc_path}", file=sys.stderr)
    sys.exit(1)

if spec.loader is None:
    print(f"Error: Failed to get loader for {config_svc_path}", file=sys.stderr)
    sys.exit(1)

module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.update_login_banner()
EOF

