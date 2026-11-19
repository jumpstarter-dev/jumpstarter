from contextvars import ContextVar

# Set by the exporter session for each driver call it serves.
session_env_var: ContextVar[dict[str, str] | None] = ContextVar("session_env", default=None)


def session_env() -> dict[str, str]:
    """Environment variables that let a subprocess reach the session serving the current driver call.

    The same JUMPSTARTER_HOST, JMP_DRIVERS_ALLOW, LEASE_NAME and CLIENT_NAME that lifecycle
    hooks get, so `j` and `jumpstarter.utils.env` work in a command the driver runs.
    Empty when the session does not belong to a lease, as with a local exporter.
    """
    return dict(session_env_var.get() or {})
