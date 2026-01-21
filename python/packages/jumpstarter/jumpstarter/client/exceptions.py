from jumpstarter.common import exceptions


class LeaseError(exceptions.JumpstarterException):
    """Raised when a lease operation fails."""
