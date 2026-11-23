from jumpstarter.common import exceptions


class LeaseError(exceptions.JumpstarterException):
    """Raised when a lease operation fails."""


class ControllerTooOldError(LeaseError):
    """Raised when the controller predates a feature that the request needs."""


class LeaseMemberLostError(LeaseError):
    """Raised by a call to a role whose member lost its exporter."""
