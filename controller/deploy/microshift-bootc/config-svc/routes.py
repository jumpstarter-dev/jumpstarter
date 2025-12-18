"""Main UI route handlers for Jumpstarter Configuration UI."""

from flask import render_template, render_template_string, request, Response
from pathlib import Path

from auth import requires_auth, is_default_password, validate_hostname
from system import (
    get_current_hostname, get_jumpstarter_config,
    get_ssh_authorized_keys, apply_jumpstarter_cr
)


def register_ui_routes(app):
    """Register all UI routes with the Flask app."""
    
    # Load templates once
    templates_dir = Path(__file__).parent / 'templates'
    
    with open(templates_dir / 'password_required.html', 'r') as f:
        PASSWORD_REQUIRED_TEMPLATE = f.read()
    
    with open(templates_dir / 'index.html', 'r') as f:
        HTML_TEMPLATE = f.read()
    
    with open(templates_dir / 'styles.css', 'r') as f:
        CSS_CONTENT = f.read()
    
    @app.route('/static/styles.css')
    def serve_css():
        """Serve the consolidated CSS stylesheet."""
        return Response(CSS_CONTENT, mimetype='text/css')
    
    @app.route('/logout')
    def logout():
        """Logout endpoint that forces re-authentication."""
        return Response(
            'Logged out. Please close this dialog to log in again.',
            401,
            {'WWW-Authenticate': 'Basic realm="Jumpstarter Configuration"'}
        )
    
    @app.route('/')
    @requires_auth
    def index():
        """Serve the main configuration page."""
        current_hostname = get_current_hostname()
        jumpstarter_config = get_jumpstarter_config()
        password_required = is_default_password()
        ssh_keys = get_ssh_authorized_keys()
        
        # Force password change if still using default
        if password_required:
            return render_template_string(
                PASSWORD_REQUIRED_TEMPLATE,
                messages=[],
                current_hostname=current_hostname,
                ssh_keys=ssh_keys
            )
        
        return render_template_string(
            HTML_TEMPLATE,
            messages=[],
            current_hostname=current_hostname,
            jumpstarter_config=jumpstarter_config,
            password_required=password_required,
            ssh_keys=ssh_keys
        )
    
    @app.route('/configure-jumpstarter', methods=['POST'])
    @requires_auth
    def configure_jumpstarter():
        """Handle Jumpstarter CR configuration request (legacy HTML form submission)."""
        base_domain = request.form.get('baseDomain', '').strip()
        image = request.form.get('image', '').strip()
        image_pull_policy = request.form.get('imagePullPolicy', 'IfNotPresent').strip()
        
        current_hostname = get_current_hostname()
        jumpstarter_config = get_jumpstarter_config()
        password_required = is_default_password()
        
        messages = []
        
        if not base_domain:
            messages.append({'type': 'error', 'text': 'Base domain is required'})
        else:
            # Validate base domain format (same as hostname validation)
            domain_valid, domain_error = validate_hostname(base_domain)
            if not domain_valid:
                messages.append({'type': 'error', 'text': f'Invalid base domain: {domain_error}'})
            elif not image:
                messages.append({'type': 'error', 'text': 'Controller image is required'})
            else:
                # Apply the Jumpstarter CR
                cr_success, cr_message = apply_jumpstarter_cr(base_domain, image, image_pull_policy)
                
                if cr_success:
                    msg = f'Jumpstarter configuration applied successfully! Base Domain: {base_domain}, Image: {image}'
                    messages.append({'type': 'success', 'text': msg})
                    # Update config to show what was just applied
                    jumpstarter_config = {
                        'base_domain': base_domain,
                        'image': image,
                        'image_pull_policy': image_pull_policy
                    }
                else:
                    messages.append({'type': 'error', 'text': f'Failed to apply Jumpstarter CR: {cr_message}'})
        
        return render_template_string(
            HTML_TEMPLATE,
            messages=messages,
            current_hostname=current_hostname,
            jumpstarter_config=jumpstarter_config,
            password_required=password_required,
            ssh_keys=get_ssh_authorized_keys()
        )

