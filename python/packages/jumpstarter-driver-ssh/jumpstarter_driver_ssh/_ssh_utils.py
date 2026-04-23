from __future__ import annotations

import os
import tempfile


def create_temp_identity_file(ssh_identity: str, logger) -> str | None:
    if not ssh_identity:
        return None

    fd = None
    temp_path = None
    try:
        fd, temp_path = tempfile.mkstemp(suffix="_ssh_key")
        os.write(fd, ssh_identity.encode())
        os.close(fd)
        fd = None
        logger.debug("Created temporary identity file: %s", temp_path)
        return temp_path
    except Exception as e:
        logger.error("Failed to create temporary identity file: %s", e)
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        if temp_path:
            try:
                os.unlink(temp_path)
            except Exception:
                pass
        raise


def cleanup_identity_file(identity_file: str | None, logger) -> None:
    if identity_file:
        try:
            os.unlink(identity_file)
            logger.debug("Cleaned up temporary identity file: %s", identity_file)
        except Exception as e:
            logger.warning("Failed to clean up identity file %s: %s", identity_file, e)
