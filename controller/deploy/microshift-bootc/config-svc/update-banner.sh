#!/bin/env python3
# Update login banner with Jumpstarter web UI URL

import sys
import os
import importlib.util
import importlib.machinery

def update_login_banner():
config_svc_path = '/usr/local/bin/config-svc'
if not os.path.exists(config_svc_path):
    print(f"Error: {config_svc_path} does not exist", file=sys.stderr)
    sys.exit(1)

# Try to create spec with explicit loader for files without .py extension
try:
    # Use SourceFileLoader explicitly for files without .py extension
    loader = importlib.machinery.SourceFileLoader('config_svc', config_svc_path)
    spec = importlib.util.spec_from_loader('config_svc', loader)
    
    if spec is None:
        print(f"Error: Failed to create spec for {config_svc_path}", file=sys.stderr)
        sys.exit(1)
    
    if spec.loader is None:
        print(f"Error: Failed to get loader for {config_svc_path}", file=sys.stderr)
        sys.exit(1)
    
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.update_login_banner()
except Exception as e:
    print(f"Error loading or executing {config_svc_path}: {e}", file=sys.stderr)
    sys.exit(1)


