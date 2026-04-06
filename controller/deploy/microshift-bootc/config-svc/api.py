"""API route handlers for Jumpstarter Configuration UI."""

import json
import os
import re
import subprocess
import sys
from io import BytesIO
from pathlib import Path

from flask import jsonify, request, Response, send_file

from auth import requires_auth, is_default_password, validate_hostname, validate_password
from system import (
    get_current_hostname, get_jumpstarter_config, set_root_password,
    get_ssh_authorized_keys, set_ssh_authorized_keys, update_login_banner,
    apply_jumpstarter_cr, calculate_age, get_default_route_ip,
    get_lvm_pv_info, get_root_filesystem, KUBECONFIG_PATH
)


def register_api_routes(app):
    """Register all API routes with the Flask app."""
    
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

