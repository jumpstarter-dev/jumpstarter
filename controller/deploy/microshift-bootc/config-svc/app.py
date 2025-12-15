#!/usr/bin/env python3
"""
Jumpstarter Configuration Web UI

A simple web service for configuring Jumpstarter deployment settings:
- Hostname configuration with smart defaults
- Jumpstarter CR management (baseDomain + image version)
- MicroShift kubeconfig download
"""

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
from functools import wraps
from io import BytesIO
from pathlib import Path

from flask import Flask, request, send_file, render_template_string, Response, jsonify

app = Flask(__name__)

# MicroShift kubeconfig path
KUBECONFIG_PATH = '/var/lib/microshift/resources/kubeadmin/kubeconfig'


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


# HTML template for forced password change
PASSWORD_REQUIRED_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Password Change Required - Jumpstarter</title>
    <link href="https://jumpstarter.dev/_static/favicon.png" rel="shortcut icon">
    <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
    <div class="container">
        <div class="banner">
            <div class="banner-text">Jumpstarter Controller Community Edition • Powered by MicroShift</div>
            <div class="logos">
                <a href="https://jumpstarter.dev/" target="_blank" rel="noopener noreferrer" class="logo-link" title="Jumpstarter - Remote device management and testing">
                    <img src="https://jumpstarter.dev/main/_static/logo-light-theme.svg" alt="Jumpstarter" class="jumpstarter-logo" />
                </a>
                <a href="https://microshift.io/" target="_blank" rel="noopener noreferrer" class="logo-link" title="MicroShift - Optimized OpenShift for the device edge">
                    <img src="https://microshift.io/images/microshift_logo_white.png" alt="MicroShift" class="microshift-logo" />
                </a>
            </div>
        </div>
        
        <div class="content-area">
            <h1>Security Setup Required</h1>
            
            {% for msg in messages %}
            <div class="message {{ msg.type }}">{{ msg.text }}</div>
            {% endfor %}
            
            <div class="warning-box">
                <h2>⚠️ Default Password Detected</h2>
                <p>You are using the default password. For security reasons, you must change the root password before accessing the configuration interface.</p>
            </div>
            
            <form id="password-change-form">
                <div id="password-messages-container"></div>
                <div class="form-group">
                    <label for="newPassword">New Root Password *</label>
                    <input type="password" id="newPassword" name="newPassword" minlength="8" autofocus>
                    <div class="hint">Minimum 8 characters (required to change from default password)</div>
                </div>
                <div class="form-group">
                    <label for="confirmPassword">Confirm Password *</label>
                    <input type="password" id="confirmPassword" name="confirmPassword" minlength="8">
                    <div class="hint">Re-enter your new password</div>
                </div>
                <div class="form-group">
                    <label for="sshKeys">SSH Authorized Keys (Optional)</label>
                    <textarea id="sshKeys" name="sshKeys" rows="6">{{ ssh_keys }}</textarea>
                    <div class="hint">One SSH public key per line. Leave empty to clear existing keys.</div>
                </div>
                <button type="submit" id="password-submit-btn">Change Password & Continue</button>
            </form>
            <script>
                (function() {
                    const form = document.getElementById('password-change-form');
                    const messagesContainer = document.getElementById('password-messages-container');
                    const submitBtn = document.getElementById('password-submit-btn');
                    
                    form.addEventListener('submit', function(e) {
                        e.preventDefault();
                        
                        const data = {
                            newPassword: document.getElementById('newPassword').value,
                            confirmPassword: document.getElementById('confirmPassword').value,
                            sshKeys: document.getElementById('sshKeys').value
                        };
                        
                        const originalText = submitBtn.textContent;
                        submitBtn.disabled = true;
                        submitBtn.textContent = 'Processing...';
                        
                        // Clear previous messages
                        messagesContainer.innerHTML = '';
                        
                        fetch('/api/change-password', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json',
                            },
                            credentials: 'same-origin',
                            body: JSON.stringify(data)
                        })
                        .then(response => response.json())
                        .then(result => {
                            // Display messages
                            result.messages.forEach(msg => {
                                const messageDiv = document.createElement('div');
                                messageDiv.className = `message ${msg.type}`;
                                messageDiv.textContent = msg.text;
                                messagesContainer.appendChild(messageDiv);
                            });
                            
                            // Update SSH keys textarea if they were updated
                            if (result.ssh_updated && result.ssh_keys !== undefined) {
                                document.getElementById('sshKeys').value = result.ssh_keys;
                            }
                            
                            // If password was changed from default, trigger redirect
                            if (result.requires_redirect) {
                                // Hide form and show redirect message
                                form.style.display = 'none';
                                messagesContainer.innerHTML = '<div class="message success">Password changed successfully! Redirecting to login with your new password...</div>';
                                
                                setTimeout(function() {
                                    fetch('/logout', {
                                        method: 'GET',
                                        headers: {
                                            'Authorization': 'Basic ' + btoa('logout:logout')
                                        }
                                    }).finally(function() {
                                        window.location.href = '/';
                                    });
                                }, 2000);
                            } else if (result.success) {
                                // Scroll to messages
                                messagesContainer.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                            }
                        })
                        .catch(error => {
                            messagesContainer.innerHTML = '<div class="message error">Failed to update: ' + error.message + '</div>';
                        })
                        .finally(() => {
                            submitBtn.disabled = false;
                            submitBtn.textContent = originalText;
                        });
                    });
                })();
            </script>
        </div>
    </div>
</body>
</html>"""

# HTML template for the main page
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Jumpstarter Configuration</title>
    <link href="https://jumpstarter.dev/_static/favicon.png" rel="shortcut icon">
    <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
    <div class="container">
        <div class="banner">
            <div class="banner-text">Jumpstarter Controller Community Edition • Powered by MicroShift</div>
            <div class="logos">
                <a href="https://jumpstarter.dev/" target="_blank" rel="noopener noreferrer" class="logo-link" title="Jumpstarter - Remote device management and testing">
                    <img src="https://jumpstarter.dev/main/_static/logo-light-theme.svg" alt="Jumpstarter" class="jumpstarter-logo" />
                </a>
                <a href="https://microshift.io/" target="_blank" rel="noopener noreferrer" class="logo-link" title="MicroShift - Optimized OpenShift for the device edge">
                    <img src="https://microshift.io/images/microshift_logo_white.png" alt="MicroShift" class="microshift-logo" />
                </a>
            </div>
        </div>
        
        <nav class="nav-bar">
            <a href="#configuration" class="nav-link">Configuration</a>
            <a href="#change-password" class="nav-link">Change Password</a>
            <a href="#system" class="nav-link">System</a>
            <a href="#microshift" class="nav-link">MicroShift</a>
        </nav>
        
        <div class="content-area">
            {% for msg in messages %}
            <div class="message {{ msg.type }}">{{ msg.text }}</div>
            {% endfor %}
            
            <div class="section" id="configuration">
                <h2>Jumpstarter Deployment Configuration</h2>
                <div id="operator-status-container"></div>
                <form method="POST" action="/configure-jumpstarter" id="jumpstarter-form">
                    <div class="form-group">
                        <label for="baseDomain">Base Domain</label>
                        <input type="text" id="baseDomain" name="baseDomain" value="{{ jumpstarter_config.base_domain }}" required>
                        <div class="hint">The base domain for Jumpstarter routes</div>
                    </div>
                    <div class="form-group">
                        <label for="image">Controller Image</label>
                        <input type="text" id="image" name="image" value="{{ jumpstarter_config.image }}" required>
                        <div class="hint">The Jumpstarter controller container image to use</div>
                    </div>
                    <div class="form-group">
                        <label for="imagePullPolicy">Image Pull Policy</label>
                        <select id="imagePullPolicy" name="imagePullPolicy" required>
                            <option value="IfNotPresent" {% if jumpstarter_config.image_pull_policy == 'IfNotPresent' %}selected{% endif %}>IfNotPresent</option>
                            <option value="Always" {% if jumpstarter_config.image_pull_policy == 'Always' %}selected{% endif %}>Always</option>
                            <option value="Never" {% if jumpstarter_config.image_pull_policy == 'Never' %}selected{% endif %}>Never</option>
                        </select>
                        <div class="hint">When to pull the container image</div>
                    </div>
                    <button type="submit" id="apply-config-btn">Apply Configuration</button>
                </form>
                
                <h2 style="margin-top: 40px;">Hostname Configuration</h2>
                <form method="POST" action="/configure-hostname">
                    <div class="form-group">
                        <label for="hostname">System Hostname</label>
                        <input type="text" id="hostname" name="hostname" value="{{ current_hostname }}" required>
                        <div class="hint">Set the system hostname</div>
                    </div>
                    <button type="submit">Update Hostname</button>
                </form>
            </div>
        
        <div class="section" id="change-password">
            <h2>Change Root Password</h2>
            <form id="main-password-change-form">
                <div id="main-password-messages-container"></div>
                <div class="form-group">
                    <label for="mainNewPassword">New Password (Optional)</label>
                    <input type="password" id="mainNewPassword" name="newPassword" minlength="8">
                    <div class="hint">Leave empty to only update SSH keys. Minimum 8 characters if provided.</div>
                </div>
                <div class="form-group">
                    <label for="mainConfirmPassword">Confirm Password (Optional)</label>
                    <input type="password" id="mainConfirmPassword" name="confirmPassword" minlength="8">
                    <div class="hint">Re-enter your new password (required if password is provided)</div>
                </div>
                <div class="form-group">
                    <label for="mainSshKeys">SSH Authorized Keys (Optional)</label>
                    <textarea id="mainSshKeys" name="sshKeys" rows="6">{{ ssh_keys }}</textarea>
                    <div class="hint">One SSH public key per line. Leave empty to clear existing keys.</div>
                </div>
                <button type="submit" id="main-password-submit-btn">Change Password</button>
            </form>
        </div>
        
        <div class="section" id="system">
            <h2>BootC Operations</h2>
            <div id="bootc-operations-container">
                <div class="form-group">
                    <label for="bootcSwitchImage">Switch to Image (Optional)</label>
                    <input type="text" id="bootcSwitchImage" name="bootcSwitchImage" placeholder="quay.io/jumpstarter-dev/microshift/bootc:latest">
                    <div class="hint">Container image reference to switch to (e.g., quay.io/jumpstarter-dev/microshift/bootc:latest)</div>
                </div>
                <div id="bootc-messages-container"></div>
                <button type="button" id="bootc-upgrade-btn" style="margin-right: 10px;">Check for Upgrades</button>
                <button type="button" id="bootc-upgrade-apply-btn" style="margin-right: 10px;">Apply Upgrade</button>
                <button type="button" id="bootc-switch-btn">Switch Image</button>
            </div>
            
            <h2 style="margin-top: 40px;">System Information</h2>
            <div id="system-stats-container">
                <div class="loading" style="padding: 40px; text-align: center;">Loading system statistics...</div>
            </div>
            
            <h2 style="margin-top: 40px;">BootC Status</h2>
            <div id="bootc-status-container">
                <div class="loading" style="padding: 40px; text-align: center;">Loading BootC status...</div>
            </div>
            
            <h2 style="margin-top: 40px;">Kernel Log</h2>
            <div id="kernel-log-container">
                <div class="loading" style="padding: 40px; text-align: center;">Loading kernel log...</div>
            </div>
        </div>
        
        <div class="section" id="microshift">
            <div class="microshift-section">
                <h2>Kubeconfig</h2>
                <p style="color: #666; font-size: 14px; margin-bottom: 15px;">
                    Download the MicroShift kubeconfig file to access the Kubernetes cluster from your local machine.
                </p>
                <a href="/kubeconfig" class="download-btn">Download Kubeconfig</a>
            </div>
            
            <div class="microshift-section">
                <h2>Routes</h2>
                <div id="route-error-container"></div>
                
                <div class="table-wrapper">
                    <table id="routes-table">
                        <thead>
                            <tr>
                                <th>Namespace</th>
                                <th>Name</th>
                                <th>Host</th>
                                <th>Service</th>
                                <th>Port</th>
                                <th>TLS</th>
                                <th>Admitted</th>
                                <th>Age</th>
                            </tr>
                        </thead>
                        <tbody id="routes-body">
                            <tr>
                                <td colspan="8" class="loading">Loading routes...</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>
            
            <div class="microshift-section">
                <h2>Pod Status</h2>
                <div id="error-container"></div>
                
                <div class="table-wrapper">
                    <table id="pods-table">
                        <thead>
                            <tr>
                                <th>Namespace</th>
                                <th>Name</th>
                                <th>Ready</th>
                                <th>Status</th>
                                <th>Restarts</th>
                                <th>Age</th>
                                <th>Node</th>
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody id="pods-body">
                            <tr>
                                <td colspan="8" class="loading">Loading pods...</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
        </div>
    </div>
    
    <script>
        // Function to show only the active section
        function showSection(sectionId) {
            // Hide all sections
            document.querySelectorAll('.section').forEach(section => {
                section.style.display = 'none';
            });
            
            // Show the target section
            const targetSection = document.querySelector(sectionId);
            if (targetSection) {
                targetSection.style.display = 'block';
            }
            
            // Update active nav link
            document.querySelectorAll('.nav-link').forEach(link => {
                link.classList.remove('active');
            });
            const activeLink = document.querySelector(`.nav-link[href="${sectionId}"]`);
            if (activeLink) {
                activeLink.classList.add('active');
            }
            
            // Update URL hash
            window.location.hash = sectionId;
        }
        
        // Handle navigation clicks
        document.querySelectorAll('a[href^="#"]').forEach(anchor => {
            anchor.addEventListener('click', function (e) {
                e.preventDefault();
                showSection(this.getAttribute('href'));
            });
        });
        
        // Note: Initial section will be set at the end after all functions are defined
        
        function checkOperatorStatus() {
            fetch('/api/operator-status')
                .then(response => response.json())
                .then(data => {
                    const statusContainer = document.getElementById('operator-status-container');
                    const form = document.getElementById('jumpstarter-form');
                    const submitBtn = document.getElementById('apply-config-btn');
                    const baseDomainInput = document.getElementById('baseDomain');
                    const imageInput = document.getElementById('image');
                    
                    if (data.ready) {
                        // Operator is ready - enable form
                        statusContainer.innerHTML = '';
                        baseDomainInput.disabled = false;
                        imageInput.disabled = false;
                        submitBtn.disabled = false;
                        form.style.opacity = '1';
                    } else {
                        // Operator not ready - disable form and show status
                        statusContainer.innerHTML = '<div class="info" style="background: #fff3cd; border: 1px solid #ffc107; margin-bottom: 15px;"><strong>⏳ ' + data.message + '</strong><br><span style="font-size: 13px;">The configuration form will be available once the operator is ready. Checking status every 5 seconds...</span></div>';
                        baseDomainInput.disabled = true;
                        imageInput.disabled = true;
                        submitBtn.disabled = true;
                        form.style.opacity = '0.6';
                    }
                })
                .catch(error => {
                    console.error('Error checking operator status:', error);
                });
        }
        
        // Check immediately on page load
        checkOperatorStatus();
        
        // Check every 5 seconds
        setInterval(checkOperatorStatus, 5000);
        
        // Handle jumpstarter form submission via API
        const jumpstarterForm = document.getElementById('jumpstarter-form');
        if (jumpstarterForm) {
            jumpstarterForm.addEventListener('submit', function(e) {
                e.preventDefault();
                
                const formData = new FormData(this);
                const data = {
                    baseDomain: formData.get('baseDomain'),
                    image: formData.get('image'),
                    imagePullPolicy: formData.get('imagePullPolicy')
                };
                
                const submitBtn = document.getElementById('apply-config-btn');
                const originalText = submitBtn.textContent;
                submitBtn.disabled = true;
                submitBtn.textContent = 'Applying...';
                
                // Find or create messages container
                let messagesContainer = document.querySelector('#configuration .messages-container');
                if (!messagesContainer) {
                    messagesContainer = document.createElement('div');
                    messagesContainer.className = 'messages-container';
                    messagesContainer.style.marginBottom = '15px';
                    jumpstarterForm.parentNode.insertBefore(messagesContainer, jumpstarterForm);
                }
                
                fetch('/api/configure-jumpstarter', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    credentials: 'same-origin',
                    body: JSON.stringify(data)
                })
                .then(response => response.json())
                .then(result => {
                    // Clear previous messages
                    messagesContainer.innerHTML = '';
                    
                    // Display messages
                    result.messages.forEach(msg => {
                        const messageDiv = document.createElement('div');
                        messageDiv.className = `message ${msg.type}`;
                        messageDiv.textContent = msg.text;
                        messagesContainer.appendChild(messageDiv);
                    });
                    
                    // If successful, update form values
                    if (result.success && result.config) {
                        document.getElementById('baseDomain').value = result.config.base_domain;
                        document.getElementById('image').value = result.config.image;
                        document.getElementById('imagePullPolicy').value = result.config.image_pull_policy;
                    }
                    
                    // Scroll to messages
                    messagesContainer.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                })
                .catch(error => {
                    messagesContainer.innerHTML = '<div class="message error">Failed to apply configuration: ' + error.message + '</div>';
                })
                .finally(() => {
                    submitBtn.disabled = false;
                    submitBtn.textContent = originalText;
                });
            });
        }
        
        // Handle password change form submission via API
        const mainPasswordForm = document.getElementById('main-password-change-form');
        if (mainPasswordForm) {
            mainPasswordForm.addEventListener('submit', function(e) {
                e.preventDefault();
                
                const data = {
                    newPassword: document.getElementById('mainNewPassword').value,
                    confirmPassword: document.getElementById('mainConfirmPassword').value,
                    sshKeys: document.getElementById('mainSshKeys').value
                };
                
                const submitBtn = document.getElementById('main-password-submit-btn');
                const messagesContainer = document.getElementById('main-password-messages-container');
                const originalText = submitBtn.textContent;
                submitBtn.disabled = true;
                submitBtn.textContent = 'Processing...';
                
                // Clear previous messages
                messagesContainer.innerHTML = '';
                
                fetch('/api/change-password', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    credentials: 'same-origin',
                    body: JSON.stringify(data)
                })
                .then(response => response.json())
                .then(result => {
                    // Display messages
                    result.messages.forEach(msg => {
                        const messageDiv = document.createElement('div');
                        messageDiv.className = `message ${msg.type}`;
                        messageDiv.textContent = msg.text;
                        messagesContainer.appendChild(messageDiv);
                    });
                    
                    // Update SSH keys textarea if they were updated
                    if (result.ssh_updated && result.ssh_keys !== undefined) {
                        document.getElementById('mainSshKeys').value = result.ssh_keys;
                    }
                    
                    // Clear password fields if password was successfully updated
                    if (result.password_updated) {
                        document.getElementById('mainNewPassword').value = '';
                        document.getElementById('mainConfirmPassword').value = '';
                    }
                    
                    // Scroll to messages
                    messagesContainer.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                })
                .catch(error => {
                    messagesContainer.innerHTML = '<div class="message error">Failed to update: ' + error.message + '</div>';
                })
                .finally(() => {
                    submitBtn.disabled = false;
                    submitBtn.textContent = originalText;
                });
            });
        }
        
        function loadSystemStats() {
            const container = document.getElementById('system-stats-container');
            if (!container) {
                console.error('system-stats-container not found');
                return;
            }
            
            fetch('/api/system-stats')
                .then(response => {
                    if (!response.ok) {
                        throw new Error('HTTP ' + response.status + ': ' + response.statusText);
                    }
                    return response.json();
                })
                .then(data => {
                    if (data.error) {
                        container.innerHTML = '<div class="error">' + data.error + '</div>';
                        return;
                    }
                    
                    const diskColor = data.disk.percent > 80 ? '#f44336' : '#ffc107';
                    const memoryColor = data.memory.percent > 80 ? '#f44336' : '#4caf50';
                    const cpuColor = data.cpu.usage > 80 ? '#f44336' : '#2196f3';
                    const networkInfo = data.network.interfaces.map(iface => iface.name + ': ' + iface.ip).join('<br>');
                    
                    // Build info boxes
                    let infoBoxes = '<div class="info"><strong>💾 Disk Usage</strong><br><div style="margin-top: 10px;">Root: ' + data.disk.used + ' / ' + data.disk.total + ' (' + data.disk.percent + '%)<br><div style="background: #e0e0e0; height: 10px; border-radius: 5px; margin-top: 5px; overflow: hidden;"><div style="background: ' + diskColor + '; width: ' + data.disk.percent + '%; height: 100%;"></div></div>Available: ' + data.disk.available + '</div></div>';
                    
                    // Add LVM PV info if available
                    if (data.lvm) {
                        const lvmColor = data.lvm.percent > 80 ? '#f44336' : '#2196f3';
                        infoBoxes += '<div class="info"><strong>💿 LVM Physical Volume</strong><br><div style="margin-top: 10px;">PV: ' + data.lvm.pv_device + '<br>VG: ' + data.lvm.vg_name + '<br>Used: ' + data.lvm.used + ' / ' + data.lvm.total + ' (' + data.lvm.percent + '%)<br><div style="background: #e0e0e0; height: 10px; border-radius: 5px; margin-top: 5px; overflow: hidden;"><div style="background: ' + lvmColor + '; width: ' + data.lvm.percent + '%; height: 100%;"></div></div>Free: ' + data.lvm.free + '</div></div>';
                    }
                    
                    infoBoxes += '<div class="info"><strong>🧠 Memory</strong><br><div style="margin-top: 10px;">Used: ' + data.memory.used + ' / ' + data.memory.total + ' (' + data.memory.percent + '%)<br><div style="background: #e0e0e0; height: 10px; border-radius: 5px; margin-top: 5px; overflow: hidden;"><div style="background: ' + memoryColor + '; width: ' + data.memory.percent + '%; height: 100%;"></div></div>Available: ' + data.memory.available + '</div></div>';
                    infoBoxes += '<div class="info"><strong>⚙️ CPU</strong><br><div style="margin-top: 10px;">Cores: ' + data.cpu.cores + '<br>Usage: ' + data.cpu.usage + '%<br><div style="background: #e0e0e0; height: 10px; border-radius: 5px; margin-top: 5px; overflow: hidden;"><div style="background: ' + cpuColor + '; width: ' + data.cpu.usage + '%; height: 100%;"></div></div></div></div>';
                    infoBoxes += '<div class="info"><strong>🖥️ System</strong><br><div style="margin-top: 10px;">Kernel: ' + data.system.kernel + '<br>Uptime: ' + data.system.uptime + '<br>Hostname: ' + data.system.hostname + '</div></div>';
                    infoBoxes += '<div class="info"><strong>🌐 Network</strong><br><div style="margin-top: 10px;">' + networkInfo + '</div></div>';
                    infoBoxes += '<div class="info"><strong>📊 Load Average</strong><br><div style="margin-top: 10px;">1 min: ' + data.system.load_1 + '<br>5 min: ' + data.system.load_5 + '<br>15 min: ' + data.system.load_15 + '</div></div>';
                    
                    container.innerHTML = '<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px;">' + infoBoxes + '</div>';
                })
                .catch(error => {
                    console.error('Error fetching system stats:', error);
                    if (container) {
                        container.innerHTML = '<div class="error">Failed to fetch system statistics: ' + error.message + '</div>';
                    }
                });
        }
        
        function loadKernelLog() {
            const container = document.getElementById('kernel-log-container');
            if (!container) {
                console.error('kernel-log-container not found');
                return;
            }
            
            fetch('/api/dmesg')
                .then(response => {
                    if (!response.ok) {
                        throw new Error('HTTP ' + response.status + ': ' + response.statusText);
                    }
                    return response.json();
                })
                .then(data => {
                    if (data.error) {
                        container.innerHTML = '<div class="error">' + data.error + '</div>';
                        return;
                    }
                    
                    if (!data.log) {
                        container.innerHTML = '<div class="error">No log data received</div>';
                        return;
                    }
                    
                    // Escape HTML and format the log
                    const logLines = data.log.split('\\n').map(line => {
                        // Escape HTML
                        const escaped = line.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
                        // Highlight error/warning lines
                        if (line.toLowerCase().includes('error') || line.toLowerCase().includes('fail')) {
                            return '<span style="color: #f44336;">' + escaped + '</span>';
                        } else if (line.toLowerCase().includes('warn')) {
                            return '<span style="color: #ff9800;">' + escaped + '</span>';
                        }
                        return escaped;
                    }).join('<br>');
                    
                    const lineCount = data.line_count || logLines.split('<br>').length;
                    container.innerHTML = '<div style="background: #1e1e1e; color: #d4d4d4; padding: 15px; border-radius: 6px; font-family: \\'Consolas\\', \\'Monaco\\', \\'Courier New\\', monospace; font-size: 12px; max-height: 600px; overflow-y: auto; line-height: 1.5;"><div style="margin-bottom: 10px; color: #888; font-size: 11px;">Showing ' + lineCount + ' lines (last 10,000 if more)</div><pre style="margin: 0; white-space: pre-wrap; word-wrap: break-word;">' + logLines + '</pre></div>';
                })
                .catch(error => {
                    console.error('Error fetching kernel log:', error);
                    if (container) {
                        container.innerHTML = '<div class="error">Failed to fetch kernel log: ' + error.message + '</div>';
                    }
                });
        }
        
        function loadBootcStatus() {
            const container = document.getElementById('bootc-status-container');
            if (!container) {
                console.error('bootc-status-container not found');
                return;
            }
            
            fetch('/api/bootc-status')
                .then(response => {
                    if (!response.ok) {
                        throw new Error('HTTP ' + response.status + ': ' + response.statusText);
                    }
                    return response.json();
                })
                .then(data => {
                    if (data.error) {
                        container.innerHTML = '<div class="error">' + data.error + '</div>';
                        return;
                    }
                    
                    let html = '<div class="info">';
                    if (data.status) {
                        html += '<strong>📦 BootC Status</strong><br><div style="margin-top: 10px;">';
                        html += '<pre style="background: #f8f9fa; padding: 10px; border-radius: 4px; overflow-x: auto; font-size: 12px; white-space: pre-wrap;">' + 
                                data.status.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</pre>';
                        html += '</div>';
                    }
                    if (data.upgrade_check) {
                        html += '<strong>🔄 Upgrade Check</strong><br><div style="margin-top: 10px;">';
                        html += '<pre style="background: #f8f9fa; padding: 10px; border-radius: 4px; overflow-x: auto; font-size: 12px; white-space: pre-wrap;">' + 
                                data.upgrade_check.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</pre>';
                        html += '</div>';
                    }
                    html += '</div>';
                    container.innerHTML = html;
                })
                .catch(error => {
                    console.error('Error fetching bootc status:', error);
                    if (container) {
                        container.innerHTML = '<div class="error">Failed to fetch BootC status: ' + error.message + '</div>';
                    }
                });
        }
        
        // BootC operation handlers
        document.addEventListener('DOMContentLoaded', function() {
            const upgradeCheckBtn = document.getElementById('bootc-upgrade-btn');
            const upgradeApplyBtn = document.getElementById('bootc-upgrade-apply-btn');
            const switchBtn = document.getElementById('bootc-switch-btn');
            const messagesContainer = document.getElementById('bootc-messages-container');
            
            if (upgradeCheckBtn) {
                upgradeCheckBtn.addEventListener('click', function() {
                    const originalText = upgradeCheckBtn.textContent;
                    upgradeCheckBtn.disabled = true;
                    upgradeCheckBtn.textContent = 'Checking...';
                    messagesContainer.innerHTML = '';
                    
                    fetch('/api/bootc-upgrade-check', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        credentials: 'same-origin'
                    })
                    .then(response => response.json())
                    .then(result => {
                        if (result.success) {
                            messagesContainer.innerHTML = '<div class="message success">Upgrade check completed. Status updated.</div>';
                            loadBootcStatus(); // Refresh status
                        } else {
                            messagesContainer.innerHTML = '<div class="message error">' + (result.error || 'Failed to check for upgrades') + '</div>';
                        }
                    })
                    .catch(error => {
                        messagesContainer.innerHTML = '<div class="message error">Error: ' + error.message + '</div>';
                    })
                    .finally(() => {
                        upgradeCheckBtn.disabled = false;
                        upgradeCheckBtn.textContent = originalText;
                    });
                });
            }
            
            if (upgradeApplyBtn) {
                upgradeApplyBtn.addEventListener('click', function() {
                    if (!confirm('Are you sure you want to apply the upgrade? This will download and install the new image.')) {
                        return;
                    }
                    
                    const originalText = upgradeApplyBtn.textContent;
                    upgradeApplyBtn.disabled = true;
                    upgradeApplyBtn.textContent = 'Upgrading...';
                    messagesContainer.innerHTML = '<div class="message info">Upgrade in progress. This may take several minutes...</div>';
                    
                    fetch('/api/bootc-upgrade', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        credentials: 'same-origin'
                    })
                    .then(response => response.json())
                    .then(result => {
                        if (result.success) {
                            messagesContainer.innerHTML = '<div class="message success">Upgrade completed successfully! ' + 
                                (result.message || '') + '</div>';
                            loadBootcStatus(); // Refresh status
                        } else {
                            messagesContainer.innerHTML = '<div class="message error">' + (result.error || 'Failed to apply upgrade') + '</div>';
                        }
                    })
                    .catch(error => {
                        messagesContainer.innerHTML = '<div class="message error">Error: ' + error.message + '</div>';
                    })
                    .finally(() => {
                        upgradeApplyBtn.disabled = false;
                        upgradeApplyBtn.textContent = originalText;
                    });
                });
            }
            
            if (switchBtn) {
                switchBtn.addEventListener('click', function() {
                    const imageInput = document.getElementById('bootcSwitchImage');
                    const image = imageInput ? imageInput.value.trim() : '';
                    
                    if (!image) {
                        messagesContainer.innerHTML = '<div class="message error">Please enter an image reference to switch to.</div>';
                        return;
                    }
                    
                    if (!confirm('Are you sure you want to switch to image: ' + image + '? This will download and install the new image.')) {
                        return;
                    }
                    
                    const originalText = switchBtn.textContent;
                    switchBtn.disabled = true;
                    switchBtn.textContent = 'Switching...';
                    messagesContainer.innerHTML = '<div class="message info">Switching to new image. This may take several minutes...</div>';
                    
                    fetch('/api/bootc-switch', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        credentials: 'same-origin',
                        body: JSON.stringify({ image: image })
                    })
                    .then(response => response.json())
                    .then(result => {
                        if (result.success) {
                            messagesContainer.innerHTML = '<div class="message success">Switch completed successfully! ' + 
                                (result.message || '') + '</div>';
                            if (imageInput) imageInput.value = '';
                            loadBootcStatus(); // Refresh status
                        } else {
                            messagesContainer.innerHTML = '<div class="message error">' + (result.error || 'Failed to switch image') + '</div>';
                        }
                    })
                    .catch(error => {
                        messagesContainer.innerHTML = '<div class="message error">Error: ' + error.message + '</div>';
                    })
                    .finally(() => {
                        switchBtn.disabled = false;
                        switchBtn.textContent = originalText;
                    });
                });
            }
        });
        
        // MicroShift pod and route functions
        let podsInterval = null;
        let routesInterval = null;
        
        function getStatusClass(status) {
            const statusLower = status.toLowerCase();
            if (statusLower === 'running') return 'status-running';
            if (statusLower === 'pending') return 'status-pending';
            if (statusLower === 'terminating') return 'status-terminating';
            if (statusLower === 'failed' || statusLower === 'error') return 'status-failed';
            if (statusLower === 'succeeded' || statusLower === 'completed') return 'status-succeeded';
            if (statusLower.includes('crashloop')) return 'status-crashloopbackoff';
            return 'status-unknown';
        }
        
        function getAdmittedClass(admitted) {
            return admitted === 'True' ? 'status-running' : 'status-failed';
        }
        
        function restartPod(namespace, podName) {
            if (!confirm(`Are you sure you want to restart pod ${podName} in namespace ${namespace}?`)) {
                return;
            }
            
            fetch(`/api/pods/${namespace}/${podName}`, {
                method: 'DELETE'
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    alert(`Pod ${podName} has been deleted and will be recreated by its controller.`);
                    updatePods();
                } else {
                    alert(`Failed to restart pod: ${data.error}`);
                }
            })
            .catch(error => {
                alert(`Error restarting pod: ${error.message}`);
            });
        }
        
        function updatePods() {
            fetch('/api/pods')
                .then(response => response.json())
                .then(data => {
                    const errorContainer = document.getElementById('error-container');
                    const tbody = document.getElementById('pods-body');
                    
                    if (data.error) {
                        errorContainer.innerHTML = '<div class="error">' + data.error + '</div>';
                        tbody.innerHTML = '<tr><td colspan="8" class="loading">Failed to load pods</td></tr>';
                        return;
                    }
                    
                    errorContainer.innerHTML = '';
                    
                    if (data.pods.length === 0) {
                        tbody.innerHTML = '<tr><td colspan="8" class="loading">No pods found</td></tr>';
                        return;
                    }
                    
                    // Sort pods: jumpstarter pods first
                    data.pods.sort((a, b) => {
                        const aHasJumpstarter = a.name.toLowerCase().includes('jumpstarter');
                        const bHasJumpstarter = b.name.toLowerCase().includes('jumpstarter');
                        if (aHasJumpstarter && !bHasJumpstarter) return -1;
                        if (!aHasJumpstarter && bHasJumpstarter) return 1;
                        return 0;
                    });
                    
                    tbody.innerHTML = data.pods.map(pod => `
                        <tr>
                            <td>${pod.namespace}</td>
                            <td>${pod.name}</td>
                            <td>${pod.ready}</td>
                            <td><span class="status-badge ${getStatusClass(pod.status)}">${pod.status}</span></td>
                            <td>${pod.restarts}</td>
                            <td>${pod.age}</td>
                            <td>${pod.node}</td>
                            <td>
                                <a href="/logs/${pod.namespace}/${pod.name}" target="_blank" class="action-icon" title="View Logs">📋</a>
                                <a href="#" onclick="restartPod('${pod.namespace}', '${pod.name}'); return false;" class="action-icon" title="Restart Pod">🔄</a>
                            </td>
                        </tr>
                    `).join('');
                })
                .catch(error => {
                    console.error('Error fetching pods:', error);
                    document.getElementById('error-container').innerHTML = 
                        '<div class="error">Failed to fetch pod data: ' + error.message + '</div>';
                });
        }
        
        function updateRoutes() {
            fetch('/api/routes')
                .then(response => response.json())
                .then(data => {
                    const errorContainer = document.getElementById('route-error-container');
                    const tbody = document.getElementById('routes-body');
                    
                    if (data.error) {
                        errorContainer.innerHTML = '<div class="error">' + data.error + '</div>';
                        tbody.innerHTML = '<tr><td colspan="8" class="loading">Failed to load routes</td></tr>';
                        return;
                    }
                    
                    errorContainer.innerHTML = '';
                    
                    if (data.routes.length === 0) {
                        tbody.innerHTML = '<tr><td colspan="8" class="loading">No routes found</td></tr>';
                        return;
                    }
                    
                    tbody.innerHTML = data.routes.map(route => `
                        <tr>
                            <td>${route.namespace}</td>
                            <td>${route.name}</td>
                            <td><a href="https://${route.host}" target="_blank" style="color: #f57c00; text-decoration: none;">${route.host}</a></td>
                            <td>${route.service}</td>
                            <td>${route.port}</td>
                            <td>${route.tls}</td>
                            <td><span class="status-badge ${getAdmittedClass(route.admitted)}">${route.admitted}</span></td>
                            <td>${route.age}</td>
                        </tr>
                    `).join('');
                })
                .catch(error => {
                    console.error('Error fetching routes:', error);
                    document.getElementById('route-error-container').innerHTML = 
                        '<div class="error">Failed to fetch route data: ' + error.message + '</div>';
                });
        }
        
        function startMicroshiftUpdates() {
            // Initial load
            updatePods();
            updateRoutes();
            
            // Start intervals
            if (podsInterval) clearInterval(podsInterval);
            if (routesInterval) clearInterval(routesInterval);
            podsInterval = setInterval(updatePods, 5000);
            routesInterval = setInterval(updateRoutes, 5000);
        }
        
        function stopMicroshiftUpdates() {
            if (podsInterval) {
                clearInterval(podsInterval);
                podsInterval = null;
            }
            if (routesInterval) {
                clearInterval(routesInterval);
                routesInterval = null;
            }
        }
        
        // Load content when sections are shown
        const originalShowSection = showSection;
        showSection = function(sectionId) {
            originalShowSection(sectionId);
            
            // Stop microshift updates when leaving the section
            stopMicroshiftUpdates();
            
            if (sectionId === '#system') {
                loadSystemStats();
                loadBootcStatus();
                loadKernelLog();
            } else if (sectionId === '#microshift') {
                startMicroshiftUpdates();
            }
        };
        
        // Set active section on page load and load its content
        const initialSection = window.location.hash || '#configuration';
        showSection(initialSection);
        
        // Explicitly load content for initial section (showSection override is now active)
        if (initialSection === '#system') {
            loadSystemStats();
            loadBootcStatus();
            loadKernelLog();
        } else if (initialSection === '#microshift') {
            startMicroshiftUpdates();
        }
    </script>
</body>
</html>"""


@app.route('/static/styles.css')
def serve_css():
    """Serve the consolidated CSS stylesheet."""
    css = """
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        html {
            scroll-behavior: smooth;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background: linear-gradient(135deg, #4c4c4c 0%, #1a1a1a 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 20px;
        }
        .container {
            background: white;
            border-radius: 12px;
            box-shadow: 0 10px 60px rgba(0,0,0,0.5), 0 0 0 1px rgba(255, 193, 7, 0.1);
            max-width: 1000px;
            width: 100%;
            padding: 40px;
        }
        .banner {
            margin: -40px -40px 30px -40px;
            padding: 25px 40px;
            background: linear-gradient(135deg, #757575 0%, #616161 100%);
            border-radius: 12px 12px 0 0;
            text-align: center;
        }
        .banner-text {
            color: white;
            font-size: 14px;
            margin-bottom: 20px;
            font-weight: 500;
        }
        .logos {
            display: flex;
            justify-content: center;
            align-items: center;
            gap: 40px;
            flex-wrap: wrap;
        }
        .logo-link {
            display: inline-block;
            transition: opacity 0.3s;
        }
        .logo-link:hover {
            opacity: 0.9;
        }
        .logo-link img {
            height: 45px;
            width: auto;
        }
        .microshift-logo {
            height: 40px !important;
            filter: brightness(0) invert(1);
        }
        .jumpstarter-logo {
            height: 40px !important;
        }
        .nav-bar {
            display: flex;
            gap: 0;
            margin: 0 -40px 30px -40px;
            border-bottom: 1px solid #e0e0e0;
            background: #fafafa;
        }
        .nav-link {
            flex: 1;
            text-align: center;
            padding: 15px 20px;
            text-decoration: none;
            color: #666;
            font-size: 14px;
            font-weight: 500;
            transition: all 0.3s;
            border-bottom: 3px solid transparent;
        }
        .nav-link:hover {
            background: #f5f5f5;
            color: #333;
            border-bottom-color: #ffc107;
        }
        .nav-link.active {
            color: #000;
            border-bottom-color: #ffc107;
            background: white;
        }
        .content-area {
            padding: 0 40px 40px 40px;
            margin: 0 -40px -40px -40px;
        }
        h2 {
            color: #333;
            font-size: 20px;
            margin-bottom: 15px;
        }
        .section {
            display: none;
            padding: 20px 0;
            animation: fadeIn 0.3s ease-in;
        }
        @keyframes fadeIn {
            from {
                opacity: 0;
                transform: translateY(10px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }
        .info {
            background: #f8f9fa;
            padding: 12px 16px;
            border-radius: 6px;
            margin-bottom: 15px;
            font-size: 14px;
            color: #555;
        }
        .info strong {
            color: #333;
        }
        .warning-box {
            background: #fff3cd;
            border: 1px solid #ffc107;
            border-radius: 6px;
            padding: 16px;
            margin-bottom: 30px;
        }
        .warning-box h2 {
            color: #856404;
            font-size: 18px;
            margin-bottom: 10px;
        }
        .warning-box p {
            color: #856404;
            font-size: 14px;
            line-height: 1.5;
        }
        .form-group {
            margin-bottom: 15px;
        }
        label {
            display: block;
            margin-bottom: 6px;
            color: #555;
            font-size: 14px;
            font-weight: 500;
        }
        input[type="text"],
        input[type="password"],
        textarea {
            width: 100%;
            padding: 10px 12px;
            border: 1px solid #ddd;
            border-radius: 6px;
            font-size: 14px;
            transition: border-color 0.3s, opacity 0.3s;
            font-family: inherit;
        }
        textarea {
            font-family: monospace;
            resize: vertical;
        }
        input[type="text"]:focus,
        input[type="password"]:focus,
        textarea:focus {
            outline: none;
            border-color: #ffc107;
            box-shadow: 0 0 0 2px rgba(255, 193, 7, 0.2);
        }
        input[type="text"]:disabled,
        input[type="password"]:disabled,
        textarea:disabled {
            background-color: #f5f5f5;
            cursor: not-allowed;
            opacity: 0.6;
        }
        select {
            width: 100%;
            padding: 10px 12px;
            border: 1px solid #ddd;
            border-radius: 6px;
            font-size: 14px;
            background-color: white;
            cursor: pointer;
            transition: border-color 0.3s;
        }
        select:focus {
            outline: none;
            border-color: #ffc107;
            box-shadow: 0 0 0 2px rgba(255, 193, 7, 0.2);
        }
        .hint {
            font-size: 12px;
            color: #888;
            margin-top: 4px;
        }
        button {
            background: #ffc107;
            color: #000;
            border: none;
            padding: 12px 24px;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.3s, opacity 0.3s;
        }
        button:hover {
            background: #ffb300;
        }
        button:disabled {
            background: #666;
            color: #999;
            cursor: not-allowed;
            opacity: 0.6;
        }
        button:disabled:hover {
            background: #666;
        }
        button[type="submit"] {
            width: 100%;
        }
        .download-btn {
            background: #ffc107;
            display: inline-block;
            text-decoration: none;
            color: #000;
            padding: 12px 24px;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 600;
            transition: background 0.3s;
        }
        .download-btn:hover {
            background: #ffb300;
        }
        .message {
            padding: 12px 16px;
            border-radius: 6px;
            margin-bottom: 20px;
            font-size: 14px;
        }
        .message.success {
            background: #d4edda;
            color: #155724;
            border: 1px solid #c3e6cb;
        }
        .message.error {
            background: #f8d7da;
            color: #721c24;
            border: 1px solid #f5c6cb;
        }
        .message.info {
            background: #d1ecf1;
            color: #0c5460;
            border: 1px solid #bee5eb;
        }
        /* MicroShift page specific styles */
        .status-badge {
            display: inline-block;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
        }
        .status-running {
            background: #d4edda;
            color: #155724;
        }
        .status-pending {
            background: #fff3cd;
            color: #856404;
        }
        .status-failed {
            background: #f8d7da;
            color: #721c24;
        }
        .status-succeeded {
            background: #d1ecf1;
            color: #0c5460;
        }
        .status-crashloopbackoff {
            background: #f8d7da;
            color: #721c24;
        }
        .status-terminating {
            background: #ffeaa7;
            color: #856404;
        }
        .status-unknown {
            background: #e2e3e5;
            color: #383d41;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
            font-size: 13px;
        }
        th {
            background: #f8f9fa;
            padding: 12px 8px;
            text-align: left;
            font-weight: 600;
            color: #333;
            border-bottom: 2px solid #dee2e6;
            position: sticky;
            top: 0;
            z-index: 10;
        }
        td {
            padding: 10px 8px;
            border-bottom: 1px solid #eee;
            color: #555;
        }
        tr:hover {
            background: #f8f9fa;
        }
        .table-wrapper {
            overflow-x: auto;
            max-height: 70vh;
            overflow-y: auto;
        }
        .loading {
            text-align: center;
            padding: 40px;
            color: #666;
        }
        .error {
            background: #f8d7da;
            color: #721c24;
            padding: 12px 16px;
            border-radius: 6px;
            margin-bottom: 20px;
        }
        .pod-count {
            color: #666;
            font-size: 14px;
            margin-bottom: 10px;
        }
        .microshift-section {
            margin-bottom: 30px;
            padding-bottom: 30px;
            border-bottom: 1px solid #eee;
        }
        .microshift-section:last-child {
            border-bottom: none;
        }
        .action-icon {
            text-decoration: none;
            font-size: 18px;
            padding: 4px 6px;
            margin: 0 2px;
            border-radius: 4px;
            transition: all 0.3s;
            display: inline-block;
            cursor: pointer;
        }
        .action-icon:hover {
            background: #fff3e0;
            transform: scale(1.2);
        }
    """
    return Response(css, mimetype='text/css')


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


@app.route('/api/change-password', methods=['POST'])
@requires_auth
def api_change_password():
    """API endpoint to handle password change request (returns JSON)."""
    data = request.get_json() if request.is_json else {}
    new_password = data.get('newPassword', request.form.get('newPassword', '')).strip()
    confirm_password = data.get('confirmPassword', request.form.get('confirmPassword', '')).strip()
    ssh_keys_value = data.get('sshKeys', request.form.get('sshKeys', '')).strip()
    
    was_default = is_default_password()
    existing_ssh_keys = get_ssh_authorized_keys()
    
    messages = []
    password_updated = False
    ssh_updated = False
    requires_redirect = False
    
    # If password is provided, validate and set it
    if new_password:
        # Validate password format and security
        password_valid, password_error = validate_password(new_password)
        if not password_valid:
            messages.append({'type': 'error', 'text': password_error})
        elif new_password != confirm_password:
            messages.append({'type': 'error', 'text': 'Passwords do not match'})
        else:
            password_success, password_message = set_root_password(new_password)
            if not password_success:
                messages.append({'type': 'error', 'text': f'Failed to set password: {password_message}'})
            else:
                password_updated = True
                messages.append({'type': 'success', 'text': 'Password changed successfully!'})
                if was_default:
                    # Update login banner on first password change
                    update_login_banner()
                    requires_redirect = True
    elif was_default:
        # If we're on the default password screen and no password provided, require it
        messages.append({'type': 'error', 'text': 'Password is required to change from default password'})
    
    # Process SSH keys (always process if form was submitted)
    ssh_success, ssh_message = set_ssh_authorized_keys(ssh_keys_value)
    if ssh_success:
        ssh_updated = True
        if ssh_keys_value:
            messages.append({'type': 'success', 'text': ssh_message})
        else:
            # Only show message if keys were cleared and there were keys before
            if existing_ssh_keys:
                messages.append({'type': 'success', 'text': ssh_message})
    else:
        messages.append({'type': 'error', 'text': f'Failed to set SSH keys: {ssh_message}'})
    
    has_errors = any(msg.get('type') == 'error' for msg in messages)
    success = not has_errors and (password_updated or ssh_updated)
    
    return jsonify({
        'success': success,
        'messages': messages,
        'password_updated': password_updated,
        'ssh_updated': ssh_updated,
        'requires_redirect': requires_redirect,
        'ssh_keys': get_ssh_authorized_keys() if ssh_updated else existing_ssh_keys
    })


@app.route('/configure-hostname', methods=['POST'])
@requires_auth
def configure_hostname():
    """Handle hostname configuration request."""
    hostname = request.form.get('hostname', '').strip()
    
    current_hostname = get_current_hostname()
    jumpstarter_config = get_jumpstarter_config()
    password_required = is_default_password()
    
    messages = []
    
    if not hostname:
        messages.append({'type': 'error', 'text': 'Hostname is required'})
    else:
        # Validate hostname format
        hostname_valid, hostname_error = validate_hostname(hostname)
        if not hostname_valid:
            messages.append({'type': 'error', 'text': f'Invalid hostname: {hostname_error}'})
        else:
            hostname_success, hostname_message = set_hostname(hostname)
            if not hostname_success:
                messages.append({'type': 'error', 'text': f'Failed to update hostname: {hostname_message}'})
            else:
                current_hostname = hostname
                messages.append({'type': 'success', 'text': f'Hostname updated successfully to: {hostname}'})
                
                # Update login banner with the new hostname
                banner_success, banner_message = update_login_banner()
                if not banner_success:
                    print(f"Warning: Failed to update login banner: {banner_message}", file=sys.stderr)
    
    return render_template_string(
        HTML_TEMPLATE,
        messages=messages,
        current_hostname=current_hostname,
        jumpstarter_config=jumpstarter_config,
        password_required=password_required
    )


@app.route('/api/configure-jumpstarter', methods=['POST'])
@requires_auth
def api_configure_jumpstarter():
    """API endpoint to handle Jumpstarter CR configuration request (returns JSON)."""
    data = request.get_json() if request.is_json else {}
    base_domain = data.get('baseDomain', request.form.get('baseDomain', '')).strip()
    image = data.get('image', request.form.get('image', '')).strip()
    image_pull_policy = data.get('imagePullPolicy', request.form.get('imagePullPolicy', 'IfNotPresent')).strip()
    
    messages = []
    success = False
    
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
                success = True
            else:
                messages.append({'type': 'error', 'text': f'Failed to apply Jumpstarter CR: {cr_message}'})
    
    return jsonify({
        'success': success,
        'messages': messages,
        'config': {
            'base_domain': base_domain,
            'image': image,
            'image_pull_policy': image_pull_policy
        } if success else None
    })


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
        password_required=password_required
    )


def get_lvm_pv_info():
    """
    Parse pvscan output to get LVM physical volume information.
    Returns dict with PV info or None if not available.
    """
    try:
        result = subprocess.run(['pvscan'], capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return None
        
        # Parse output like: "PV /dev/sda3   VG myvg1   lvm2 [62.41 GiB / 52.41 GiB free]"
        # or: "Total: 1 [62.41 GiB] / in use: 1 [62.41 GiB] / in no VG: 0 [0   ]"
        output = result.stdout.strip()
        if not output:
            return None
        
        lines = output.split('\n')
        
        # Look for PV line
        pv_device = None
        vg_name = None
        total_size = None
        free_size = None
        
        for line in lines:
            line = line.strip()
            # Match: "PV /dev/sda3   VG myvg1   lvm2 [62.41 GiB / 52.41 GiB free]"
            if line.startswith('PV '):
                parts = line.split()
                if len(parts) >= 2:
                    pv_device = parts[1]
                # Find VG name
                for i, part in enumerate(parts):
                    if part == 'VG' and i + 1 < len(parts):
                        vg_name = parts[i + 1]
                        break
                # Find size info in brackets
                bracket_match = re.search(r'\[([^\]]+)\]', line)
                if bracket_match:
                    size_info = bracket_match.group(1)
                    # Parse "62.41 GiB / 52.41 GiB free"
                    size_parts = size_info.split('/')
                    if len(size_parts) >= 1:
                        total_size = size_parts[0].strip()
                    if len(size_parts) >= 2:
                        free_match = re.search(r'([\d.]+)\s*([KMGT]i?B)', size_parts[1])
                        if free_match:
                            free_size = free_match.group(1) + ' ' + free_match.group(2)
        
        if not pv_device or not total_size:
            return None
        
        # Calculate used space and percentage
        # Parse sizes to calculate percentage
        def parse_size(size_str):
            """Parse size string like '62.41 GiB' to bytes."""
            match = re.match(r'([\d.]+)\s*([KMGT]i?)B?', size_str, re.IGNORECASE)
            if not match:
                return 0
            value = float(match.group(1))
            unit = match.group(2).upper()
            multipliers = {'K': 1024, 'M': 1024**2, 'G': 1024**3, 'T': 1024**4}
            return int(value * multipliers.get(unit, 1))
        
        total_bytes = parse_size(total_size)
        free_bytes = parse_size(free_size) if free_size else 0
        used_bytes = total_bytes - free_bytes
        percent = int((used_bytes / total_bytes * 100)) if total_bytes > 0 else 0
        
        # Format used size
        def format_size(bytes_val):
            """Format bytes to human-readable size."""
            for unit, multiplier in [('TiB', 1024**4), ('GiB', 1024**3), ('MiB', 1024**2), ('KiB', 1024)]:
                if bytes_val >= multiplier:
                    return f"{bytes_val / multiplier:.2f} {unit}"
            return f"{bytes_val} B"
        
        used_size = format_size(used_bytes)
        
        return {
            'pv_device': pv_device,
            'vg_name': vg_name or 'N/A',
            'total': total_size,
            'free': free_size or '0 B',
            'used': used_size,
            'percent': percent
        }
    except Exception as e:
        print(f"Error parsing LVM PV info: {e}", file=sys.stderr)
        return None


def get_root_filesystem():
    """
    Detect the real root filesystem mount point.
    On bootc systems, /sysroot is the real root filesystem.
    Otherwise, find the largest real block device filesystem.
    """
    # Check if /sysroot exists and is a mount point (bootc systems)
    try:
        result = subprocess.run(['findmnt', '-n', '-o', 'TARGET', '/sysroot'], 
                              capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            return '/sysroot'
    except Exception:
        pass
    
    # Fallback: parse df output to find the real root filesystem
    try:
        df_result = subprocess.run(['df', '-h'], capture_output=True, text=True, timeout=5)
        if df_result.returncode != 0:
            return '/'  # Fallback to root
        
        lines = df_result.stdout.strip().split('\n')
        if len(lines) < 2:
            return '/'  # Fallback to root
        
        # Virtual filesystem types to skip
        virtual_fs = ('tmpfs', 'overlay', 'composefs', 'devtmpfs', 'proc', 'sysfs', 
                     'devpts', 'cgroup', 'pstore', 'bpf', 'tracefs', 'debugfs',
                     'configfs', 'fusectl', 'mqueue', 'hugetlbfs', 'efivarfs', 'ramfs',
                     'nsfs', 'shm', 'vfat')
        
        # Boot partitions to skip
        boot_paths = ('/boot', '/boot/efi')
        
        best_fs = None
        best_size = 0
        
        for line in lines[1:]:  # Skip header
            parts = line.split()
            if len(parts) < 6:
                continue
            
            filesystem = parts[0]
            mount_point = parts[5]
            size_str = parts[1]
            
            # Skip virtual filesystems
            fs_type = filesystem.split('/')[-1] if '/' in filesystem else filesystem
            if any(vfs in fs_type.lower() for vfs in virtual_fs):
                continue
            
            # Skip boot partitions
            if mount_point in boot_paths:
                continue
            
            # Skip if not a block device (doesn't start with /dev)
            if not filesystem.startswith('/dev'):
                continue
            
            # Prefer LVM root volumes
            if '/mapper/' in filesystem and 'root' in filesystem.lower():
                return mount_point
            
            # Calculate size for comparison (convert to bytes for comparison)
            try:
                # Parse size like "10G", "500M", etc.
                size_val = float(size_str[:-1])
                size_unit = size_str[-1].upper()
                if size_unit == 'G':
                    size_bytes = size_val * 1024 * 1024 * 1024
                elif size_unit == 'M':
                    size_bytes = size_val * 1024 * 1024
                elif size_unit == 'K':
                    size_bytes = size_val * 1024
                else:
                    size_bytes = size_val
                
                if size_bytes > best_size:
                    best_size = size_bytes
                    best_fs = mount_point
            except (ValueError, IndexError):
                continue
        
        if best_fs:
            return best_fs
        
    except Exception:
        pass
    
    # Final fallback
    return '/'


@app.route('/api/system-stats')
@requires_auth
def get_system_stats():
    """API endpoint to get system statistics."""
    try:
        stats = {}
        
        # Disk usage - use detected root filesystem
        root_fs = get_root_filesystem()
        disk_result = subprocess.run(['df', '-h', root_fs], capture_output=True, text=True)
        disk_lines = disk_result.stdout.strip().split('\n')
        if len(disk_lines) > 1:
            disk_parts = disk_lines[1].split()
            stats['disk'] = {
                'total': disk_parts[1],
                'used': disk_parts[2],
                'available': disk_parts[3],
                'percent': int(disk_parts[4].rstrip('%'))
            }
        else:
            stats['disk'] = {'total': 'N/A', 'used': 'N/A', 'available': 'N/A', 'percent': 0}
        
        # Memory usage
        mem_result = subprocess.run(['free', '-h'], capture_output=True, text=True)
        mem_lines = mem_result.stdout.strip().split('\n')
        if len(mem_lines) > 1:
            mem_parts = mem_lines[1].split()
            # Parse percentage
            mem_total_result = subprocess.run(['free'], capture_output=True, text=True)
            mem_total_lines = mem_total_result.stdout.strip().split('\n')[1].split()
            mem_percent = int((int(mem_total_lines[2]) / int(mem_total_lines[1])) * 100)
            
            stats['memory'] = {
                'total': mem_parts[1],
                'used': mem_parts[2],
                'available': mem_parts[6] if len(mem_parts) > 6 else mem_parts[3],
                'percent': mem_percent
            }
        else:
            stats['memory'] = {'total': 'N/A', 'used': 'N/A', 'available': 'N/A', 'percent': 0}
        
        # CPU info
        cpu_count_result = subprocess.run(['nproc'], capture_output=True, text=True)
        cpu_cores = int(cpu_count_result.stdout.strip()) if cpu_count_result.returncode == 0 else 0
        
        # CPU usage - get from top
        top_result = subprocess.run(['top', '-bn1'], capture_output=True, text=True)
        cpu_usage = 0
        for line in top_result.stdout.split('\n'):
            if 'Cpu(s)' in line or '%Cpu' in line:
                # Parse line like "%Cpu(s):  2.0 us,  1.0 sy,  0.0 ni, 97.0 id,..."
                parts = line.split(',')
                for part in parts:
                    if 'id' in part:
                        idle = float(part.split()[0])
                        cpu_usage = round(100 - idle, 1)
                        break
                break
        
        stats['cpu'] = {
            'cores': cpu_cores,
            'usage': cpu_usage
        }
        
        # System info
        kernel_result = subprocess.run(['uname', '-r'], capture_output=True, text=True)
        kernel = kernel_result.stdout.strip()
        
        hostname = get_current_hostname()
        
        # Uptime
        uptime_result = subprocess.run(['uptime', '-p'], capture_output=True, text=True)
        uptime = uptime_result.stdout.strip().replace('up ', '')
        
        # Load average
        loadavg_result = subprocess.run(['cat', '/proc/loadavg'], capture_output=True, text=True)
        loadavg_parts = loadavg_result.stdout.strip().split()
        
        stats['system'] = {
            'kernel': kernel,
            'hostname': hostname,
            'uptime': uptime,
            'load_1': loadavg_parts[0] if len(loadavg_parts) > 0 else '0',
            'load_5': loadavg_parts[1] if len(loadavg_parts) > 1 else '0',
            'load_15': loadavg_parts[2] if len(loadavg_parts) > 2 else '0'
        }
        
        # Network interfaces
        ip_result = subprocess.run(['ip', '-4', 'addr', 'show'], capture_output=True, text=True)
        interfaces = []
        current_iface = None
        # Prefixes to skip (container/virtual interfaces)
        skip_prefixes = ('veth', 'docker', 'br-', 'cni', 'flannel', 'cali')
        
        for line in ip_result.stdout.split('\n'):
            line = line.strip()
            if line and line[0].isdigit() and ':' in line:
                # Interface line
                parts = line.split(':')
                if len(parts) >= 2:
                    iface_name = parts[1].strip().split('@')[0]
                    # Skip virtual/container interfaces
                    if not iface_name.startswith(skip_prefixes):
                        current_iface = iface_name
                    else:
                        current_iface = None
            elif 'inet ' in line and current_iface:
                # IP line
                ip_addr = line.split()[1].split('/')[0]
                if ip_addr != '127.0.0.1':  # Skip localhost
                    interfaces.append({
                        'name': current_iface,
                        'ip': ip_addr
                    })
                current_iface = None
        
        stats['network'] = {
            'interfaces': interfaces
        }
        
        # LVM Physical Volume information
        lvm_info = get_lvm_pv_info()
        if lvm_info:
            stats['lvm'] = lvm_info
        
        return jsonify(stats)
        
    except Exception as e:
        return jsonify({'error': f'Error gathering system statistics: {str(e)}'}), 500


@app.route('/api/bootc-status')
@requires_auth
def get_bootc_status():
    """API endpoint to get BootC status and upgrade check information."""
    try:
        status_output = ''
        upgrade_check_output = ''
        
        # Get bootc status
        try:
            status_result = subprocess.run(
                ['bootc', 'status'],
                capture_output=True,
                text=True,
                timeout=10
            )
            if status_result.returncode == 0:
                status_output = status_result.stdout.strip()
            else:
                status_output = f"Error: {status_result.stderr.strip()}"
        except FileNotFoundError:
            status_output = "bootc command not found"
        except subprocess.TimeoutExpired:
            status_output = "Command timed out"
        except Exception as e:
            status_output = f"Error: {str(e)}"
        
        # Get upgrade check
        try:
            upgrade_result = subprocess.run(
                ['bootc', 'upgrade', '--check'],
                capture_output=True,
                text=True,
                timeout=30
            )
            if upgrade_result.returncode == 0:
                upgrade_check_output = upgrade_result.stdout.strip()
            else:
                upgrade_check_output = f"Error: {upgrade_result.stderr.strip()}"
        except FileNotFoundError:
            upgrade_check_output = "bootc command not found"
        except subprocess.TimeoutExpired:
            upgrade_check_output = "Command timed out"
        except Exception as e:
            upgrade_check_output = f"Error: {str(e)}"
        
        return jsonify({
            'status': status_output,
            'upgrade_check': upgrade_check_output
        })
        
    except Exception as e:
        return jsonify({'error': f'Error getting BootC status: {str(e)}'}), 500


@app.route('/api/bootc-upgrade-check', methods=['POST'])
@requires_auth
def bootc_upgrade_check():
    """API endpoint to check for BootC upgrades."""
    try:
        result = subprocess.run(
            ['bootc', 'upgrade', '--check'],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode == 0:
            return jsonify({
                'success': True,
                'output': result.stdout.strip(),
                'message': 'Upgrade check completed'
            })
        else:
            return jsonify({
                'success': False,
                'error': result.stderr.strip() or 'Upgrade check failed'
            }), 400
            
    except FileNotFoundError:
        return jsonify({'success': False, 'error': 'bootc command not found'}), 404
    except subprocess.TimeoutExpired:
        return jsonify({'success': False, 'error': 'Command timed out'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': f'Error: {str(e)}'}), 500


@app.route('/api/bootc-upgrade', methods=['POST'])
@requires_auth
def bootc_upgrade():
    """API endpoint to apply BootC upgrade."""
    try:
        # Run bootc upgrade (this may take a while)
        result = subprocess.run(
            ['bootc', 'upgrade'],
            capture_output=True,
            text=True,
            timeout=600  # 10 minutes timeout for upgrade
        )
        
        if result.returncode == 0:
            return jsonify({
                'success': True,
                'output': result.stdout.strip(),
                'message': 'Upgrade completed successfully. Reboot may be required.'
            })
        else:
            return jsonify({
                'success': False,
                'error': result.stderr.strip() or 'Upgrade failed'
            }), 400
            
    except FileNotFoundError:
        return jsonify({'success': False, 'error': 'bootc command not found'}), 404
    except subprocess.TimeoutExpired:
        return jsonify({'success': False, 'error': 'Command timed out (upgrade may still be in progress)'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': f'Error: {str(e)}'}), 500


@app.route('/api/bootc-switch', methods=['POST'])
@requires_auth
def bootc_switch():
    """API endpoint to switch BootC to a different image."""
    try:
        data = request.get_json() if request.is_json else {}
        image = data.get('image', '').strip()
        
        if not image:
            return jsonify({'success': False, 'error': 'Image reference is required'}), 400
        
        # Validate image format (basic check)
        if not (image.startswith('quay.io/') or image.startswith('docker.io/') or 
                ':' in image or '/' in image):
            return jsonify({'success': False, 'error': 'Invalid image reference format'}), 400
        
        # Run bootc switch (this may take a while)
        result = subprocess.run(
            ['bootc', 'switch', image],
            capture_output=True,
            text=True,
            timeout=600  # 10 minutes timeout for switch
        )
        
        if result.returncode == 0:
            return jsonify({
                'success': True,
                'output': result.stdout.strip(),
                'message': f'Switched to {image} successfully. Reboot may be required.'
            })
        else:
            return jsonify({
                'success': False,
                'error': result.stderr.strip() or 'Switch failed'
            }), 400
            
    except FileNotFoundError:
        return jsonify({'success': False, 'error': 'bootc command not found'}), 404
    except subprocess.TimeoutExpired:
        return jsonify({'success': False, 'error': 'Command timed out (switch may still be in progress)'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': f'Error: {str(e)}'}), 500


@app.route('/api/dmesg')
@requires_auth
def get_dmesg():
    """API endpoint to get kernel log (dmesg)."""
    try:
        # Run dmesg command to get kernel log
        result = subprocess.run(
            ['dmesg'],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode != 0:
            return jsonify({'error': f'Failed to get dmesg: {result.stderr.strip()}'}), 500
        
        # Return the log (limit to last 10000 lines to avoid huge responses)
        log_lines = result.stdout.strip().split('\n')
        if len(log_lines) > 10000:
            log_lines = log_lines[-10000:]
        
        return jsonify({
            'log': '\n'.join(log_lines),
            'line_count': len(log_lines)
        })
        
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Command timed out'}), 500
    except Exception as e:
        return jsonify({'error': f'Error getting dmesg: {str(e)}'}), 500


@app.route('/api/operator-status')
@requires_auth
def get_operator_status():
    """API endpoint to check if the Jumpstarter operator is ready."""
    try:
        # Path to MicroShift kubeconfig
        kubeconfig_path = KUBECONFIG_PATH
        
        # Check if kubeconfig exists
        if not os.path.exists(kubeconfig_path):
            return jsonify({'ready': False, 'message': 'MicroShift kubeconfig not found. Waiting for MicroShift to start...'}), 200
        
        # Check if jumpstarter-operator pod is running and ready
        result = subprocess.run(
            ['oc', '--kubeconfig', kubeconfig_path, 'get', 'pods', '-n', 'jumpstarter-operator-system', '-o', 'json'],
            capture_output=True,
            text=True,
            check=True,
            timeout=10
        )
        
        pods_data = json.loads(result.stdout)
        
        # Look for the operator controller manager pod
        for pod in pods_data.get('items', []):
            pod_name = pod.get('metadata', {}).get('name', '')
            if 'jumpstarter-operator-controller-manager' in pod_name:
                # Check if pod is running and ready
                status = pod.get('status', {})
                phase = status.get('phase', '')
                container_statuses = status.get('containerStatuses', [])
                
                if phase == 'Running' and container_statuses:
                    all_ready = all(c.get('ready', False) for c in container_statuses)
                    if all_ready:
                        return jsonify({'ready': True, 'message': 'Jumpstarter operator is ready'}), 200
                    else:
                        return jsonify({'ready': False, 'message': 'Jumpstarter operator is starting...'}), 200
                else:
                    return jsonify({'ready': False, 'message': f'Jumpstarter operator status: {phase}'}), 200
        
        # Operator pod not found
        return jsonify({'ready': False, 'message': 'Waiting for Jumpstarter operator to deploy...'}), 200
        
    except subprocess.CalledProcessError as e:
        # Namespace might not exist yet
        return jsonify({'ready': False, 'message': 'Waiting for Jumpstarter operator to deploy...'}), 200
    except subprocess.TimeoutExpired:
        return jsonify({'ready': False, 'message': 'Timeout checking operator status'}), 200
    except Exception as e:
        return jsonify({'ready': False, 'message': 'Checking operator status...'}), 200


@app.route('/api/pods')
@requires_auth
def get_pods():
    """API endpoint to get pod status as JSON."""
    try:
        # Path to MicroShift kubeconfig
        kubeconfig_path = KUBECONFIG_PATH
        
        # Check if kubeconfig exists
        if not os.path.exists(kubeconfig_path):
            return jsonify({'error': 'MicroShift kubeconfig not found. Is MicroShift running?'}), 503
        
        # Run oc get pods -A -o json with explicit kubeconfig
        result = subprocess.run(
            ['oc', '--kubeconfig', kubeconfig_path, 'get', 'pods', '-A', '-o', 'json'],
            capture_output=True,
            text=True,
            check=True,
            timeout=10
        )
        
        pods_data = json.loads(result.stdout)
        pods_list = []
        
        for pod in pods_data.get('items', []):
            metadata = pod.get('metadata', {})
            spec = pod.get('spec', {})
            status = pod.get('status', {})
            
            # Calculate ready containers
            container_statuses = status.get('containerStatuses', [])
            ready_count = sum(1 for c in container_statuses if c.get('ready', False))
            total_count = len(container_statuses)
            
            # Calculate total restarts
            restarts = sum(c.get('restartCount', 0) for c in container_statuses)
            
            # Check if pod is terminating (has deletionTimestamp)
            if metadata.get('deletionTimestamp'):
                phase = 'Terminating'
            else:
                # Determine pod phase/status
                phase = status.get('phase', 'Unknown')
                
                # Check for more specific status from container states
                for container in container_statuses:
                    state = container.get('state', {})
                    if 'waiting' in state:
                        reason = state['waiting'].get('reason', '')
                        if reason:
                            phase = reason
                            break
            
            # Calculate age
            creation_time = metadata.get('creationTimestamp', '')
            age = calculate_age(creation_time)
            
            pods_list.append({
                'namespace': metadata.get('namespace', 'default'),
                'name': metadata.get('name', 'unknown'),
                'ready': f"{ready_count}/{total_count}",
                'status': phase,
                'restarts': restarts,
                'age': age,
                'node': spec.get('nodeName', 'N/A')
            })
        
        return jsonify({'pods': pods_list})
        
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() if e.stderr else str(e)
        return jsonify({'error': f'Failed to get pods: {error_msg}'}), 500
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Command timed out'}), 500
    except Exception as e:
        return jsonify({'error': f'Error: {str(e)}'}), 500


@app.route('/api/routes')
@requires_auth
def get_routes():
    """API endpoint to get OpenShift routes as JSON."""
    try:
        # Path to MicroShift kubeconfig
        kubeconfig_path = KUBECONFIG_PATH
        
        # Check if kubeconfig exists
        if not os.path.exists(kubeconfig_path):
            return jsonify({'error': 'MicroShift kubeconfig not found. Is MicroShift running?'}), 503
        
        # Run oc get routes -A -o json with explicit kubeconfig
        result = subprocess.run(
            ['oc', '--kubeconfig', kubeconfig_path, 'get', 'routes', '-A', '-o', 'json'],
            capture_output=True,
            text=True,
            check=True,
            timeout=10
        )
        
        routes_data = json.loads(result.stdout)
        routes_list = []
        
        for route in routes_data.get('items', []):
            metadata = route.get('metadata', {})
            spec = route.get('spec', {})
            status = route.get('status', {})
            
            # Get route host
            host = spec.get('host', 'N/A')
            
            # Get target service and port
            to = spec.get('to', {})
            service_name = to.get('name', 'N/A')
            
            port = spec.get('port', {})
            target_port = port.get('targetPort', 'N/A') if port else 'N/A'
            
            # Get TLS configuration
            tls = spec.get('tls', {})
            tls_termination = tls.get('termination', 'None') if tls else 'None'
            
            # Get ingress status
            ingresses = status.get('ingress', [])
            admitted = 'False'
            if ingresses:
                for ingress in ingresses:
                    conditions = ingress.get('conditions', [])
                    for condition in conditions:
                        if condition.get('type') == 'Admitted':
                            admitted = 'True' if condition.get('status') == 'True' else 'False'
                            break
            
            # Calculate age
            creation_time = metadata.get('creationTimestamp', '')
            age = calculate_age(creation_time)
            
            routes_list.append({
                'namespace': metadata.get('namespace', 'default'),
                'name': metadata.get('name', 'unknown'),
                'host': host,
                'service': service_name,
                'port': str(target_port),
                'tls': tls_termination,
                'admitted': admitted,
                'age': age
            })
        
        return jsonify({'routes': routes_list})
        
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() if e.stderr else str(e)
        return jsonify({'error': f'Failed to get routes: {error_msg}'}), 500
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Command timed out'}), 500
    except Exception as e:
        return jsonify({'error': f'Error: {str(e)}'}), 500


@app.route('/api/pods/<namespace>/<pod_name>', methods=['DELETE'])
@requires_auth
def delete_pod(namespace, pod_name):
    """API endpoint to delete a pod (causing it to restart)."""
    try:
        # Path to MicroShift kubeconfig
        kubeconfig_path = KUBECONFIG_PATH
        
        # Check if kubeconfig exists
        if not os.path.exists(kubeconfig_path):
            return jsonify({'success': False, 'error': 'MicroShift kubeconfig not found. Is MicroShift running?'}), 503
        
        # Run oc delete pod with explicit kubeconfig
        subprocess.run(
            ['oc', '--kubeconfig', kubeconfig_path, 'delete', 'pod', pod_name, '-n', namespace],
            capture_output=True,
            text=True,
            check=True,
            timeout=10
        )
        
        return jsonify({'success': True, 'message': f'Pod {pod_name} deleted successfully'})
        
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() if e.stderr else str(e)
        return jsonify({'success': False, 'error': f'Failed to delete pod: {error_msg}'}), 500
    except subprocess.TimeoutExpired:
        return jsonify({'success': False, 'error': 'Command timed out'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': f'Error: {str(e)}'}), 500


@app.route('/logs/<namespace>/<pod_name>')
@requires_auth
def stream_logs(namespace, pod_name):
    """Stream pod logs in real-time."""
    kubeconfig_path = KUBECONFIG_PATH
    
    # Check if kubeconfig exists
    if not os.path.exists(kubeconfig_path):
        return "MicroShift kubeconfig not found. Is MicroShift running?", 503
    
    def generate():
        """Generator function to stream logs."""
        process = None
        try:
            # Start oc logs -f process
            process = subprocess.Popen(
                ['oc', '--kubeconfig', kubeconfig_path, 'logs', '-f', '-n', namespace, pod_name],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            
            # Stream output line by line
            for line in iter(process.stdout.readline, ''):
                if not line:
                    break
                yield f"{line}"
                
        except Exception as e:
            yield f"Error streaming logs: {str(e)}\n"
        finally:
            # Clean up process when connection closes
            if process:
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except Exception:
                    process.kill()
    
    # Return streaming response with HTML wrapper
    html_header = f"""<!DOCTYPE html>
<html>
<head>
    <title>Logs: {namespace}/{pod_name}</title>
    <style>
        body {{
            margin: 0;
            padding: 20px;
            background: #1e1e1e;
            color: #d4d4d4;
            font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
            font-size: 13px;
            line-height: 1.5;
        }}
        .header {{
            background: #2d2d30;
            padding: 15px;
            margin: -20px -20px 20px -20px;
            border-bottom: 2px solid #ffc107;
        }}
        .header h1 {{
            margin: 0;
            color: #ffc107;
            font-size: 18px;
        }}
        .header .info {{
            color: #888;
            font-size: 12px;
            margin-top: 5px;
        }}
        pre {{
            margin: 0;
            white-space: pre-wrap;
            word-wrap: break-word;
        }}
        .log-line {{
            border-left: 2px solid transparent;
            padding-left: 10px;
        }}
        .log-line:hover {{
            background: #252526;
            border-left-color: #ffc107;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>📋 Pod Logs</h1>
        <div class="info">Namespace: {namespace} | Pod: {pod_name}</div>
    </div>
    <pre><div class="log-line">"""
    
    html_footer = """</div></pre>
<script>
    // Auto-scroll to bottom
    window.scrollTo(0, document.body.scrollHeight);
</script>
</body>
</html>"""
    
    def generate_with_html():
        yield html_header
        for line in generate():
            yield line.replace('<', '&lt;').replace('>', '&gt;')
        yield html_footer
    
    return Response(generate_with_html(), mimetype='text/html')


@app.route('/kubeconfig')
@requires_auth
def download_kubeconfig():
    """Serve the kubeconfig file for download with nip.io hostname and insecure TLS."""
    kubeconfig_path = Path(KUBECONFIG_PATH)
    
    if not kubeconfig_path.exists():
        return "Kubeconfig file not found", 404
    
    try:
        # Read the original kubeconfig
        with open(kubeconfig_path, 'r') as f:
            kubeconfig_content = f.read()
        
        # Always use nip.io format based on default route IP
        default_ip = get_default_route_ip()
        if default_ip:
            nip_hostname = f"jumpstarter.{default_ip}.nip.io"
        else:
            # Fallback to current hostname if IP detection fails
            nip_hostname = get_current_hostname()
        
        # Extract the original server hostname (likely localhost) before replacing
        # This is needed for tls-server-name to match the certificate
        original_server_match = re.search(r'server:\s+https://([^:]+):(\d+)', kubeconfig_content)
        original_hostname = 'localhost'  # Default fallback
        if original_server_match:
            original_hostname = original_server_match.group(1)
        
        # Replace localhost with the nip.io hostname
        kubeconfig_content = re.sub(
            r'server:\s+https://localhost:(\d+)',
            f'server: https://{nip_hostname}:\\1',
            kubeconfig_content
        )
        
        # Keep the CA certificate fields (certificate-authority-data or certificate-authority)
        # They are needed for certificate chain verification
        
        # Remove insecure-skip-tls-verify if it exists (we'll replace it with tls-server-name)
        kubeconfig_content = re.sub(
            r'^\s+insecure-skip-tls-verify:\s+.*\n',
            '',
            kubeconfig_content,
            flags=re.MULTILINE
        )
        
        # Add tls-server-name to verify the CA but allow hostname mismatch
        # This tells the client to verify the certificate as if it were issued for the original hostname
        # (e.g., localhost), even though we're connecting via nip.io hostname
        kubeconfig_content = re.sub(
            r'(server:\s+https://[^\n]+\n)',
            f'\\1    tls-server-name: {original_hostname}\n',
            kubeconfig_content
        )
        
        # Create a BytesIO object to send as file
        kubeconfig_bytes = BytesIO(kubeconfig_content.encode('utf-8'))
        kubeconfig_bytes.seek(0)
        
        return send_file(
            kubeconfig_bytes,
            as_attachment=True,
            download_name='kubeconfig',
            mimetype='application/octet-stream'
        )
    except Exception as e:
        return f"Error reading kubeconfig: {str(e)}", 500


def calculate_age(creation_timestamp):
    """Calculate age from Kubernetes timestamp."""
    if not creation_timestamp:
        return 'N/A'
    
    try:
        from datetime import datetime, timezone
        
        # Parse ISO 8601 timestamp
        created = datetime.fromisoformat(creation_timestamp.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        delta = now - created
        
        # Format age
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return f'{seconds}s'
        elif seconds < 3600:
            return f'{seconds // 60}m'
        elif seconds < 86400:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            return f'{hours}h{minutes}m' if minutes > 0 else f'{hours}h'
        else:
            days = seconds // 86400
            hours = (seconds % 86400) // 3600
            return f'{days}d{hours}h' if hours > 0 else f'{days}d'
    except Exception as e:
        print(f"Error calculating age: {e}", file=sys.stderr)
        return 'N/A'


def get_default_route_ip():
    """Get the IP address of the default route interface."""
    try:
        # Get default route
        result = subprocess.run(
            ['ip', 'route', 'show', 'default'],
            capture_output=True,
            text=True,
            check=True
        )
        
        # Parse output: "default via X.X.X.X dev ethX ..."
        lines = result.stdout.strip().split('\n')
        if not lines:
            return None
        
        parts = lines[0].split()
        if len(parts) < 5:
            return None
        
        # Find the device name
        dev_idx = parts.index('dev') if 'dev' in parts else None
        if dev_idx is None or dev_idx + 1 >= len(parts):
            return None
        
        dev_name = parts[dev_idx + 1]
        
        # Get IP address for this device
        result = subprocess.run(
            ['ip', '-4', 'addr', 'show', dev_name],
            capture_output=True,
            text=True,
            check=True
        )
        
        # Parse: "    inet 192.168.1.10/24 ..."
        for line in result.stdout.split('\n'):
            line = line.strip()
            if line.startswith('inet '):
                ip_with_mask = line.split()[1]
                ip = ip_with_mask.split('/')[0]
                return ip.replace('.', '-')  # Format for nip.io
        
        return None
    except Exception as e:
        print(f"Error getting default route IP: {e}", file=sys.stderr)
        return None


def get_current_hostname():
    """Get the current system hostname."""
    try:
        return socket.gethostname()
    except Exception as e:
        print(f"Error getting hostname: {e}", file=sys.stderr)
        return "unknown"


def get_jumpstarter_config():
    """Get the current Jumpstarter CR configuration from the cluster."""
    default_ip = get_default_route_ip()
    default_base_domain = f"jumpstarter.{default_ip}.nip.io" if default_ip else "jumpstarter.local"
    
    defaults = {
        'base_domain': default_base_domain,
        'image': 'quay.io/jumpstarter-dev/jumpstarter-controller:latest',
        'image_pull_policy': 'IfNotPresent'
    }
    
    try:
        # Path to MicroShift kubeconfig
        kubeconfig_path = KUBECONFIG_PATH
        
        # Check if kubeconfig exists
        if not os.path.exists(kubeconfig_path):
            return defaults
        
        # Try to get existing Jumpstarter CR
        result = subprocess.run(
            ['oc', '--kubeconfig', kubeconfig_path, 'get', 'jumpstarter', 'jumpstarter', '-n', 'default', '-o', 'json'],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            cr_data = json.loads(result.stdout)
            spec = cr_data.get('spec', {})
            controller = spec.get('controller', {})
            
            return {
                'base_domain': spec.get('baseDomain', defaults['base_domain']),
                'image': controller.get('image', defaults['image']),
                'image_pull_policy': controller.get('imagePullPolicy', defaults['image_pull_policy'])
            }
        else:
            # CR doesn't exist yet, return defaults
            return defaults
            
    except Exception as e:
        print(f"Error getting Jumpstarter config: {e}", file=sys.stderr)
        return defaults


def set_hostname(hostname):
    """Set the system hostname using hostnamectl."""
    try:
        subprocess.run(
            ['hostnamectl', 'set-hostname', hostname],
            capture_output=True,
            text=True,
            check=True
        )
        return True, "Success"
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() if e.stderr else str(e)
        print(f"Error setting hostname: {error_msg}", file=sys.stderr)
        return False, error_msg
    except Exception as e:
        print(f"Error setting hostname: {e}", file=sys.stderr)
        return False, str(e)


def set_root_password(password):
    """Set the root user password using chpasswd."""
    try:
        # Use chpasswd to set password (more reliable than passwd for scripting)
        process = subprocess.Popen(
            ['chpasswd'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        stdout, stderr = process.communicate(input=f'root:{password}\n')
        
        if process.returncode != 0:
            error_msg = stderr.strip() if stderr else "Unknown error"
            print(f"Error setting root password: {error_msg}", file=sys.stderr)
            return False, error_msg
        
        return True, "Success"
    except Exception as e:
        print(f"Error setting root password: {e}", file=sys.stderr)
        return False, str(e)


def get_ssh_authorized_keys():
    """Read existing SSH authorized keys from /root/.ssh/authorized_keys."""
    ssh_dir = Path('/root/.ssh')
    authorized_keys_path = ssh_dir / 'authorized_keys'
    
    if authorized_keys_path.exists():
        try:
            with open(authorized_keys_path, 'r') as f:
                return f.read().strip()
        except Exception as e:
            print(f"Error reading authorized_keys: {e}", file=sys.stderr)
            return ""
    return ""


def set_ssh_authorized_keys(keys_content):
    """Set SSH authorized keys in /root/.ssh/authorized_keys with proper permissions."""
    ssh_dir = Path('/root/.ssh')
    authorized_keys_path = ssh_dir / 'authorized_keys'
    
    try:
        # Create .ssh directory if it doesn't exist
        ssh_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        
        # Write authorized_keys file
        keys_content = keys_content.strip()
        if keys_content:
            with open(authorized_keys_path, 'w') as f:
                f.write(keys_content)
                if not keys_content.endswith('\n'):
                    f.write('\n')
            
            # Set proper permissions: .ssh directory = 700, authorized_keys = 600
            os.chmod(ssh_dir, 0o700)
            os.chmod(authorized_keys_path, 0o600)
            
            return True, "SSH authorized keys updated successfully"
        else:
            # If empty, remove the file if it exists
            if authorized_keys_path.exists():
                authorized_keys_path.unlink()
            # Ensure .ssh directory still has correct permissions
            os.chmod(ssh_dir, 0o700)
            return True, "SSH authorized keys cleared"
    except Exception as e:
        print(f"Error setting SSH authorized keys: {e}", file=sys.stderr)
        return False, str(e)


def update_login_banner():
    """Update the login banner with the web UI URL."""
    try:
        default_ip = get_default_route_ip()
        if default_ip:
            hostname = f"jumpstarter.{default_ip}.nip.io"
            port = 8880
            url = f"http://{hostname}:{port}"
            
            # Format URL line to fit properly in the box (62 chars content width)
            url_line = f"  → {url}"
            
            banner = f"""
╔══════════════════════════════════════════════════════════════════╗
║                                                                  ║
║  Jumpstarter Controller Community Edition                        ║
║  Powered by MicroShift                                           ║
║                                                                  ║
║  Web Configuration UI:                                           ║
║  {url_line:<64}║
║                                                                  ║
║  Login with:  root / <your-password>                             ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝

"""
            
            # Write to /etc/issue for pre-login banner
            with open('/etc/issue', 'w') as f:
                f.write(banner)
            
            return True, "Success"
        else:
            return False, "Could not determine IP address"
    except Exception as e:
        print(f"Error updating login banner: {e}", file=sys.stderr)
        return False, str(e)


def apply_jumpstarter_cr(base_domain, image, image_pull_policy='IfNotPresent'):
    """Apply Jumpstarter Custom Resource using oc."""
    try:
        # Path to MicroShift kubeconfig
        kubeconfig_path = KUBECONFIG_PATH
        
        # Check if kubeconfig exists
        if not os.path.exists(kubeconfig_path):
            return False, 'MicroShift kubeconfig not found. Is MicroShift running?'
        
        # Build the CR YAML
        cr = {
            'apiVersion': 'operator.jumpstarter.dev/v1alpha1',
            'kind': 'Jumpstarter',
            'metadata': {
                'name': 'jumpstarter',
                'namespace': 'default'
            },
            'spec': {
                'baseDomain': base_domain,
                'controller': {
                    'grpc': {
                        'endpoints': [
                            {
                                'address': f'grpc.{base_domain}',
                                'route': {
                                    'enabled': True
                                }
                            }
                        ]
                    },
                    'image': image,
                    'imagePullPolicy': image_pull_policy,
                    'replicas': 1
                },
                'routers': {
                    'grpc': {
                        'endpoints': [
                            {
                                'address': f'router.{base_domain}',
                                'route': {
                                    'enabled': True
                                }
                            }
                        ]
                    },
                    'image': image,
                    'imagePullPolicy': image_pull_policy,
                    'replicas': 1
                },
                'useCertManager': True
            }
        }
        
        # Write CR to temporary file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            yaml_content = json_to_yaml(cr)
            f.write(yaml_content)
            temp_file = f.name
        
        try:
            # Apply using oc with explicit kubeconfig
            result = subprocess.run(
                ['oc', '--kubeconfig', kubeconfig_path, 'apply', '-f', temp_file],
                capture_output=True,
                text=True,
                check=True
            )
            return True, result.stdout.strip()
        finally:
            # Clean up temp file
            try:
                os.unlink(temp_file)
            except Exception:
                pass
                
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() if e.stderr else str(e)
        print(f"Error applying Jumpstarter CR: {error_msg}", file=sys.stderr)
        return False, error_msg
    except Exception as e:
        print(f"Error applying Jumpstarter CR: {e}", file=sys.stderr)
        return False, str(e)


def json_to_yaml(obj, indent=0):
    """Convert a JSON object to YAML format (simple implementation)."""
    lines = []
    indent_str = '  ' * indent
    
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, (dict, list)):
                lines.append(f"{indent_str}{key}:")
                lines.append(json_to_yaml(value, indent + 1))
            else:
                lines.append(f"{indent_str}{key}: {yaml_value(value)}")
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                lines.append(f"{indent_str}-")
                lines.append(json_to_yaml(item, indent + 1))
            else:
                lines.append(f"{indent_str}- {yaml_value(item)}")
    
    return '\n'.join(lines)


def yaml_value(value):
    """Format a value for YAML output."""
    if value is None:
        return 'null'
    elif isinstance(value, bool):
        return 'true' if value else 'false'
    elif isinstance(value, str):
        # Quote strings that contain special characters
        if ':' in value or '#' in value or value.startswith('-'):
            return f'"{value}"'
        return value
    else:
        return str(value)


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

