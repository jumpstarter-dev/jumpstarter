#!/bin/bash
# Update login banner with Jumpstarter web UI URL

python3 << 'EOF'
import sys
sys.path.insert(0, '/usr/local/bin')

# Import and call the update function
import importlib.util
spec = importlib.util.spec_from_file_location('config_svc', '/usr/local/bin/config-svc')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.update_login_banner()
EOF

