"""Authentication and validation utilities for Jumpstarter Configuration UI."""

import re
import subprocess
import sys
from functools import wraps

from flask import request, Response


def validate_hostname(hostname):
    """
    Validate hostname according to RFC 1123 standards.
    
    Rules:
    - Total length <= 253 characters
    - Each label 1-63 characters
    - Labels match /^[a-z0-9]([a-z0-9-]*[a-z0-9])?$/i (case-insensitive)
    - No leading/trailing hyphen in labels
    - Reject empty or illegal characters
    - Optionally reject trailing dot
    
    Returns: (is_valid: bool, error_message: str)
    """
    if not hostname:
        return False, "Hostname cannot be empty"
    
    # Remove trailing dot if present (optional rejection)
    if hostname.endswith('.'):
        hostname = hostname.rstrip('.')
    
    # Check total length
    if len(hostname) > 253:
        return False, f"Hostname too long: {len(hostname)} characters (maximum 253)"
    
    # Split into labels
    labels = hostname.split('.')
    
    # Check each label
    label_pattern = re.compile(r'^[a-z0-9]([a-z0-9-]*[a-z0-9])?$', re.IGNORECASE)
    
    for i, label in enumerate(labels):
        if not label:
            return False, f"Empty label at position {i+1} (consecutive dots not allowed)"
        
        if len(label) > 63:
            return False, f"Label '{label}' too long: {len(label)} characters (maximum 63)"
        
        if not label_pattern.match(label):
            return False, f"Label '{label}' contains invalid characters. Labels must start and end with alphanumeric characters and can contain hyphens in between"
        
        # Additional check: no leading/trailing hyphen (pattern should catch this, but be explicit)
        if label.startswith('-') or label.endswith('-'):
            return False, f"Label '{label}' cannot start or end with a hyphen"
    
    return True, ""


def validate_password(password):
    """
    Validate password to prevent chpasswd injection and enforce security.
    
    Rules:
    - Reject newline characters ('\n')
    - Reject colon characters (':')
    - Minimum length: 8 characters
    - Maximum length: 128 characters (reasonable limit)
    
    Returns: (is_valid: bool, error_message: str)
    """
    if not password:
        return False, "Password cannot be empty"
    
    # Check for forbidden characters
    if '\n' in password:
        return False, "Password cannot contain newline characters"
    
    if ':' in password:
        return False, "Password cannot contain colon characters"
    
    # Check length
    if len(password) < 8:
        return False, f"Password too short: {len(password)} characters (minimum 8)"
    
    if len(password) > 128:
        return False, f"Password too long: {len(password)} characters (maximum 128)"
    
    return True, ""


def check_auth(username, password):
    """Check if a username/password combination is valid using PAM."""
    if username != 'root':
        return False
    
    try:
        # Try using PAM authentication first
        import pam
        p = pam.pam()
        return p.authenticate(username, password)
    except ImportError:
        # Fallback: use subprocess to authenticate via su
        try:
            result = subprocess.run(
                ['su', username, '-c', 'true'],
                input=password.encode(),
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except Exception as e:
            print(f"Authentication error: {e}", file=sys.stderr)
            return False


def is_default_password():
    """Check if the root password is still the default 'jumpstarter'."""
    return check_auth('root', 'jumpstarter')


def authenticate():
    """Send a 401 response that enables basic auth."""
    return Response(
        'Authentication required. Please login with root credentials.',
        401,
        {'WWW-Authenticate': 'Basic realm="Jumpstarter Configuration"'}
    )


def requires_auth(f):
    """Decorator to require HTTP Basic Authentication."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

