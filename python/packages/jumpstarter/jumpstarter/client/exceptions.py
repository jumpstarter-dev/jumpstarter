from jumpstarter.common import exceptions


class LeaseError(exceptions.JumpstarterException):
    """Raised when a lease operation fails."""


class ControllerTooOldError(LeaseError):
    """Raised when the controller predates a feature that the request needs."""


class LeaseMemberLostError(LeaseError):
    """Raised by a call to a role whose member lost its exporter.

    `role` is the role, and `reason` is the reason that the controller gave, when they are known.
    """

    def __init__(self, message: str, *, role: str | None = None, reason: str | None = None):
        super().__init__(message)
        self.role = role
        self.reason = reason


class ForwardError(LeaseError):
    """Raised for a forward of a lease that does not operate: it is `Failed` or `Disabled`.

    `forward` is the name of the forward, `state` is its state, and `cause`
    is the message that the controller gave, when there is one.
    """

    def __init__(self, message: str, *, forward: str, state: str, cause: str | None = None):
        super().__init__(message)
        self.forward = forward
        self.state = state
        self.cause = cause
