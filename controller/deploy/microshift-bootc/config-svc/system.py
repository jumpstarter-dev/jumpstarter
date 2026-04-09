"""System utility functions for Jumpstarter Configuration UI."""

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# MicroShift kubeconfig path
KUBECONFIG_PATH = '/var/lib/microshift/resources/kubeadmin/kubeconfig'


def calculate_age(creation_timestamp):
    """Calculate age from Kubernetes timestamp."""
    if not creation_timestamp:
        return 'N/A'
    
    try:
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

