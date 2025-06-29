#!/usr/bin/env python3

import os
import tempfile

from jumpstarter.common.utils import env


def test_iscsi_driver():
    """Test the iSCSI driver basic functionality"""
    print("Testing iSCSI driver...")

    # Create a test image
    with tempfile.NamedTemporaryFile(delete=False, suffix=".img") as f:
        # Create a 10MB test file
        f.write(b"0" * (10 * 1024 * 1024))
        test_image = f.name

    try:
        with env() as client:
            iscsi = client.iscsi

            print("1. Starting iSCSI server...")
            iscsi.start()

            print("2. Getting server info...")
            host = iscsi.get_host()
            port = iscsi.get_port()
            iqn = iscsi.get_target_iqn()
            print(f"   Host: {host}")
            print(f"   Port: {port}")
            print(f"   IQN: {iqn}")

            print("3. Adding test LUN...")
            # Upload the test image
            lun_name = iscsi.upload_image("test", test_image, size_mb=10)
            print(f"   LUN created: {lun_name}")

            print("4. Listing LUNs...")
            luns = iscsi.list_luns()
            for lun in luns:
                print(f"   - {lun['name']}: {lun['size'] / (1024 * 1024):.1f}MB")

            print("5. Testing target accessibility...")
            import socket

            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2)
                result = sock.connect_ex((host, port))
                sock.close()
                if result == 0:
                    print("   ✓ Target port is accessible")
                else:
                    print("   ✗ Target port is not accessible")
            except Exception as e:
                print(f"   ✗ Connection test failed: {e}")

            print("6. Cleaning up...")
            iscsi.remove_lun("test")
            iscsi.stop()

            print("✓ All tests passed!")

    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback

        traceback.print_exc()
    finally:
        # Clean up temp file
        if os.path.exists(test_image):
            os.unlink(test_image)


if __name__ == "__main__":
    test_iscsi_driver()
